from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.ready_requirement_generation import (
    ReadyRequirementGenerationDependencies,
    handle_ready_requirement_generation,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector


TEST_USER_ID = "user_ready_requirement_generation_handler"


def _workspace_inputs(tmp_path: Path, *, name: str) -> dict[str, Any]:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("空白学习页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    requirements = lesson.learning_requirements.model_copy(
        update={
            "theme": "通用主题",
            "learning_goal": "围绕一个通用主题生成第一版板书。",
            "level": "根据用户背景动态调整。",
            "known_background": "用户已经说明必要背景。",
            "target_depth": "先建立结构化理解。",
            "output_preference": "Markdown 板书",
            "success_criteria": "能复述主线并提出后续问题。",
            "action_type": "generate_board",
            "action_instruction": "生成第一版板书",
        }
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="需求已清晰",
        reason="需求已达到生成第一版板书的最低条件。",
        missing_items=[],
        can_start=True,
        summary="用户想学习一个通用主题。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="通用主题",
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
        next_question="",
        ready_for_board=True,
    )
    requirement_stamp = requirement_history.record_update(
        requirements=requirements,
        clarification=clarification,
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "store": store,
        "requirements": requirements,
        "clarification": clarification,
        "requirement_history": requirement_history,
        "requirement_stamp": requirement_stamp,
    }


def _success_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="已生成第一版板书。",
        new_document=build_document(
            title="第一版板书",
            content_text="# 第一版板书\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已生成第一版板书。",
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


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _deps(calls: dict[str, Any], *, outcome: BoardDocumentEditOutcome) -> ReadyRequirementGenerationDependencies:
    def generate_from_requirements(**kwargs):
        lesson = kwargs["lesson"]
        calls.setdefault("generate", []).append(kwargs)
        calls["state_before_board"] = workspace_state.STORE.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        )
        return outcome

    def post_board_generation_message(**kwargs):
        calls.setdefault("post_generation", []).append(kwargs)
        return "板书已经就绪，要我按它从开头讲起吗？", "chatbot_post_board_generation"

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)
        chatbot_module._save_workspace_for_user(**kwargs)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        stamp = kwargs.get("requirement_stamp")
        return SimpleNamespace(
            requirement_run_id=stamp.run_id if stamp else None,
            requirement_version_id=stamp.version_id if stamp else None,
            requirement_phase=stamp.phase if stamp else None,
            requirement_cleared=kwargs.get("requirement_cleared", False),
            active_requirement_sheet=None if kwargs.get("requirement_cleared", False) else kwargs["requirements"],
            board_decision=kwargs["board_decision"],
            board_document_operation_status=kwargs.get("board_document_operation_status"),
            board_document_operation_failure_reason=kwargs.get("board_document_operation_failure_reason"),
        )

    return ReadyRequirementGenerationDependencies(
        with_task_details=chatbot_module._with_task_details,
        prepare_requirement_for_board_generation=chatbot_module._prepare_initial_requirement_for_board_generation,
        checkpoint_requirement_before_generation=chatbot_module._checkpoint_initial_requirement_before_generation,
        generate_from_requirements=generate_from_requirements,
        post_board_generation_message=post_board_generation_message,
        requirement_history_metadata=chatbot_module._requirement_history_metadata,
        task_metadata=chatbot_module._task_metadata,
        reference_metadata=chatbot_module._reference_metadata,
        board_document_quality_metadata=chatbot_module._board_document_quality_metadata,
        board_document_failure_metadata=chatbot_module._board_document_failure_metadata,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def test_handler_success_freezes_before_board_commits_then_consumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _workspace_inputs(tmp_path, name="handler_success")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_ready_requirement_generation(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=ChatRequest(message="我已经说明目标、水平和输出形式"),
            requirements=inputs["requirements"],
            learning_clarification=inputs["clarification"],
            chatbot_message="需求已经够清楚。",
            resource_summary="",
            resource_resolution=ResourceResolution(matches=[]),
            selected_reference=None,
            requirement_history=inputs["requirement_history"],
            requirement_stamp=inputs["requirement_stamp"],
            solver_metadata={"solver": "metadata"},
            deps=_deps(calls, outcome=_success_outcome()),
        )

    lesson = inputs["lesson"]
    commit = lesson.history_graph.commits[-1]
    store = inputs["store"]
    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)

    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].run_id == runs[0]["id"]
    assert collector.steps[0].version_id == versions[0]["id"]
    assert collector.steps[1].version_id == response.requirement_version_id
    assert collector.steps[2].decision == "board_editor"
    assert collector.steps[3].commit_id == commit.id
    assert collector.steps[3].run_id == response.requirement_run_id
    assert collector.steps[3].version_id == response.requirement_version_id

    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert calls["state_before_board"]["status"] == "frozen"
    assert calls["generate"][0]["requirement_run_id"] == response.requirement_run_id
    assert calls["generate"][0]["frozen_requirement_version_id"] == response.requirement_version_id
    assert calls["response"][0]["requirement_stamp"].phase == "consumed"
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "ready_requirement_sheet"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["solver"] == "metadata"


def test_handler_failure_saves_failure_event_and_keeps_frozen_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _workspace_inputs(tmp_path, name="handler_failure")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls: dict[str, Any] = {}
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        response = handle_ready_requirement_generation(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=ChatRequest(message="我想学习一个主题，目标已经完整"),
            requirements=inputs["requirements"],
            learning_clarification=inputs["clarification"],
            chatbot_message="需求已经够清楚。",
            resource_summary="",
            resource_resolution=ResourceResolution(matches=[]),
            selected_reference=None,
            requirement_history=inputs["requirement_history"],
            requirement_stamp=inputs["requirement_stamp"],
            deps=_deps(calls, outcome=_failed_outcome()),
        )

    lesson = inputs["lesson"]
    store = inputs["store"]
    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)

    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[3].decision == "generation_failed"
    assert collector.steps[3].reason == "板书文档编辑 AI 没有返回生成结果。"
    assert collector.steps[3].run_id == response.requirement_run_id
    assert collector.steps[3].version_id == response.requirement_version_id

    assert len(lesson.history_graph.commits) == initial_commit_count
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.active_requirement_sheet is not None
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "板书文档编辑 AI 没有返回生成结果。"
    assert calls["state_before_board"]["status"] == "frozen"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["frozen_version_id"] == response.requirement_version_id
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    failure_metadata = json.loads(events[-1]["metadata_json"])
    assert failure_metadata["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert NodeId.INITIAL_BOARD_COMMIT.value not in _node_values(collector)


def test_handler_rejects_non_ready_or_non_generate_board(tmp_path: Path) -> None:
    inputs = _workspace_inputs(tmp_path, name="handler_guard")
    calls: dict[str, Any] = {}
    deps = _deps(calls, outcome=_success_outcome())
    not_ready = inputs["clarification"].model_copy(update={"ready_for_board": False})
    non_generation = inputs["requirements"].model_copy(update={"action_type": None})

    with pytest.raises(ValueError, match="ready clarification"):
        handle_ready_requirement_generation(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=ChatRequest(message="继续聊"),
            requirements=inputs["requirements"],
            learning_clarification=not_ready,
            chatbot_message="",
            resource_summary="",
            resource_resolution=ResourceResolution(matches=[]),
            selected_reference=None,
            requirement_history=inputs["requirement_history"],
            requirement_stamp=inputs["requirement_stamp"],
            deps=deps,
        )

    with pytest.raises(ValueError, match="generate_board action"):
        handle_ready_requirement_generation(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=ChatRequest(message="继续聊"),
            requirements=non_generation,
            learning_clarification=inputs["clarification"],
            chatbot_message="",
            resource_summary="",
            resource_resolution=ResourceResolution(matches=[]),
            selected_reference=None,
            requirement_history=inputs["requirement_history"],
            requirement_stamp=inputs["requirement_stamp"],
            deps=deps,
        )

    assert "generate" not in calls
