from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    ChatbotReply,
    InitialLearningWorkModeDecision,
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.rich_document import build_document


TEST_USER_ID = "user_remaining_generation_contracts_wave8"


def _workspace_with_blank_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("空白学习页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _success_outcome(*, title: str, summary: str) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=summary,
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary=summary,
        section_titles=["起点"],
        changed=True,
        operation_status="succeeded",
    )


def _requirement_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_requirement_runs
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, id
            """,
            (TEST_USER_ID, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _version_kinds(store: SqliteCourseStore, lesson_id: str) -> list[str]:
    return [
        row["change_kind"]
        for row in store.list_learning_requirement_versions(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
    ]


def _event_types(store: SqliteCourseStore, lesson_id: str) -> list[str]:
    return [
        row["event_type"]
        for row in store.list_learning_requirement_events(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
    ]


def _patch_post_generation_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经生成，要从开头讲吗？"),
    )


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this generation contract")


def test_explicit_board_generation_action_start_contract_freezes_before_board_editor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="explicit_start_contract")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _fail_if_called("generate_chatbot_reply"),
    )
    _patch_post_generation_reply(monkeypatch)
    captured: dict[str, Any] = {}

    def _generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return _success_outcome(title="API 开始生成板书", summary="已生成 API 开始生成板书。")

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _generate_from_requirements)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    runs = _requirement_rows(store, lesson_id)

    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert captured["kwargs"].get("reference_context") is None
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert _version_kinds(store, lesson_id) == ["forced_frozen"]
    assert _event_types(store, lesson_id) == ["created", "forced_frozen", "consumed"]
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "start"
    assert commit.metadata["task_requirement_sheet"]["action_type"] == "generate_board"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert "initial_learning_work_mode" not in commit.metadata


def test_generation_control_contract_forces_current_requirement_without_chatbot_board_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="generation_control_contract")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _fail_if_called("generate_chatbot_reply"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: LearningRequirementUpdate(
            progress=65,
            summary="用户允许系统基于当前需求直接生成。",
            key_facts=[
                LearningRequirementKeyFact(
                    label="学习需求",
                    value="生成一份通用学习材料",
                    evidence="来自当前回合。",
                    category="output",
                )
            ],
            checklist=[
                LearningRequirementChecklistItem(
                    title="允许系统决定未指定细节",
                    is_clear=True,
                    evidence="用户要求直接生成。",
                )
            ],
            missing_items=["细节偏好"],
            next_question="还要补充什么细节？",
            ready_for_board=False,
        ),
    )
    _patch_post_generation_reply(monkeypatch)
    captured: dict[str, Any] = {}

    def _generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return _success_outcome(title="直接生成板书", summary="已根据当前需求生成板书。")

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _generate_from_requirements)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="都行，看你发挥，直接生成"),
        user_id=TEST_USER_ID,
    )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]

    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert captured["kwargs"].get("reference_context") is None
    assert response.learning_clarification.ready_for_board is True
    assert response.learning_clarification.forced_start is True
    assert response.learning_clarification.missing_items == []
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert _version_kinds(store, lesson_id) == ["forced_frozen"]
    assert _event_types(store, lesson_id) == ["created", "forced_frozen", "consumed"]
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "explicit_board_request"
    assert commit.metadata["assistant_message_source"] == "chatbot_post_board_generation"
    assert commit.metadata["board_editor_message"] == "已根据当前需求生成板书。"
    assert commit.metadata["resource_resolution_status"] == "none"


def test_knowledge_board_minimal_contract_stays_separate_from_start_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="knowledge_board_contract")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            topic="一个通用主题",
            reason="用户提出了一个聚焦的新知识学习请求。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    _patch_post_generation_reply(monkeypatch)
    captured: dict[str, Any] = {}

    def _generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return _success_outcome(title="聚焦知识板书", summary="已生成聚焦知识板书。")

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _generate_from_requirements)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="直接为我讲解一个通用主题", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]

    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert captured["kwargs"].get("reference_context") is None
    assert response.learning_clarification.work_mode == "knowledge_board"
    assert response.learning_clarification.ready_for_board is True
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert _version_kinds(store, lesson_id) == ["frozen"]
    assert _event_types(store, lesson_id) == ["created", "frozen", "consumed"]
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "knowledge_board_minimal_requirement"
    assert commit.metadata["board_generation_action"] != "start"
    assert commit.metadata["initial_learning_work_mode"]["work_mode"] == "knowledge_board"
    assert commit.metadata["task_requirement_sheet"]["work_mode"] == "knowledge_board"
