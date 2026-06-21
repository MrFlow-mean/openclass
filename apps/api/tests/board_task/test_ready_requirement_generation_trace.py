from __future__ import annotations

import json
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
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.resource_library import build_resource_item
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
)


TEST_USER_ID = "user_ready_requirement_generation_trace"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}
INITIAL_GENERATION_TRACE_NODES = {
    NodeId.INITIAL_REQUIREMENT_READY.value,
    NodeId.INITIAL_REQUIREMENT_FREEZE.value,
    NodeId.INITIAL_BOARD_GENERATE.value,
    NodeId.INITIAL_GENERATION_FAILED.value,
    NodeId.INITIAL_BOARD_COMMIT.value,
}


def _workspace_with_blank_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("空白学习页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _workspace_with_resource(tmp_path: Path):
    workspace, lesson_id = _workspace_with_blank_lesson()
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    resource_path = tmp_path / "resource.md"
    resource_path.write_text("# 资料章节\n这是一段可用于生成板书的通用资料正文。", encoding="utf-8")
    resource = build_resource_item(resource_path, "resource.md")
    resource.scope_lesson_id = lesson.id
    package.resources.append(resource)
    return workspace, lesson_id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _ready_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="用户想学习一个通用主题。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用主题",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        missing_items=[],
        next_question="",
        ready_for_board=True,
        action_type="generate_board",
        action_instruction="生成第一版板书",
    )


def _success_outcome(*, title: str = "第一版板书", summary: str = "已生成第一版板书。") -> BoardDocumentEditOutcome:
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


def _failed_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="板书生成失败，请稍后重试。",
        new_document=build_document(title="空白学习页", content_text=""),
        board_decision=BoardDecision(action="no_change", reason="板书生成失败。"),
        assistant_message_source="board_document_editor_ai",
        operation=None,
        summary="板书文档编辑 AI 没有返回生成结果。",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="板书文档编辑 AI 没有返回生成结果。",
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


def _requirement_run_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
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


def _patch_ready_generation_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _ready_requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经就绪，要我按它从开头讲起吗？"),
    )


def test_ready_requirement_generation_success_trace_records_durable_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="ready_generation_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    captured: dict[str, Any] = {}

    def _fake_generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return _success_outcome()

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _fake_generate_from_requirements)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我已经说明目标、水平和输出形式"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)

    assert nodes == [
        *_trace_prefix(),
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    ready_step = collector.steps[6]
    freeze_step = collector.steps[7]
    generate_step = collector.steps[8]
    commit_step = collector.steps[9]
    assert ready_step.decision == "ready"
    assert ready_step.run_id == runs[0]["id"]
    assert ready_step.version_id == versions[0]["id"]
    assert freeze_step.decision == "frozen"
    assert freeze_step.run_id == response.requirement_run_id
    assert freeze_step.version_id == response.requirement_version_id
    assert generate_step.decision == "board_editor"
    assert generate_step.run_id == response.requirement_run_id
    assert generate_step.version_id == response.requirement_version_id
    assert commit_step.decision == "committed"
    assert commit_step.commit_id == commit.id
    assert commit_step.run_id == response.requirement_run_id
    assert commit_step.version_id == response.requirement_version_id

    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "ready_requirement_sheet"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert NodeId.INITIAL_GENERATION_FAILED.value not in nodes


def test_ready_requirement_generation_failure_trace_keeps_frozen_run_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="ready_generation_failure")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    captured: dict[str, Any] = {}

    def _failed_generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return _failed_outcome()

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _failed_generate_from_requirements)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我想学习一个主题，目标已经完整"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)

    assert nodes == [
        *_trace_prefix(),
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    failure_step = collector.steps[9]
    assert failure_step.decision == "generation_failed"
    assert failure_step.reason == "板书文档编辑 AI 没有返回生成结果。"
    assert failure_step.run_id == response.requirement_run_id
    assert failure_step.version_id == response.requirement_version_id

    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.active_requirement_sheet is not None
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "板书文档编辑 AI 没有返回生成结果。"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert runs[0]["status"] == "frozen"
    assert runs[0]["frozen_version_id"] == response.requirement_version_id
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    failure_metadata = json.loads(events[-1]["metadata_json"])
    assert failure_metadata["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(lesson.history_graph.commits[-1].metadata))
    assert NodeId.INITIAL_BOARD_COMMIT.value not in nodes


def test_explicit_board_generation_start_keeps_existing_trace_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="explicit_start_scope")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    monkeypatch.setattr(chatbot_module, "generate_from_requirements", lambda **kwargs: _success_outcome())

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="开始生成板书", board_generation_action="start"),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.requirement_phase == "consumed"
    assert response.board_decision.action == "edit_board"
    assert commit.metadata["board_generation_action"] == "start"
    assert INITIAL_GENERATION_TRACE_NODES.isdisjoint(nodes)


def test_confirmed_resource_generation_uses_ready_generation_trace_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource(tmp_path)
    store = _store_with_workspace(tmp_path, workspace, name="confirmed_resource_scope")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: pytest.fail("generate_from_requirements should wait for resource confirmation"),
    )

    first_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="根据上传资料生成板书"),
        user_id=TEST_USER_ID,
    )
    assert first_response.reference_prompt is not None

    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: _success_outcome(title="确认资料板书", summary="已根据确认资料生成板书。"),
    )

    with bind_workflow_trace_collector() as collector:
        confirmed_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(
                message="根据上传资料生成板书",
                resource_reference_action="confirm",
                resource_reference_resource_id=first_response.reference_prompt.resource_id,
                resource_reference_chapter_id=first_response.reference_prompt.chapter_id,
            ),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    commit = confirmed_response.course_package.lessons[-1].history_graph.commits[-1]
    assert confirmed_response.requirement_phase == "consumed"
    assert confirmed_response.board_decision.action == "edit_board"
    assert confirmed_response.selected_reference is not None
    assert commit.metadata["board_generation_action"] == "resource_reference_confirm"
    assert nodes == [
        *_trace_prefix(),
        NodeId.RESOURCE_CONFIRMED_GENERATE.value,
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
