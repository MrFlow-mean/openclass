from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardPatchValidationResult,
    BoardTaskRequirementSheet,
    ChatRequest,
    DiffPreviewItem,
    SelectionRef,
)
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_write_trace"


def _workspace_with_existing_board(*, awaiting_confirmation: bool = False):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n这一段已有内容。\n",
        ),
    )
    if awaiting_confirmation:
        lesson.board_task_requirements = BoardTaskRequirementSheet(
            target_hint="缺失主题",
            location_status="content_absent",
            requested_action="write",
            question_or_topic="补充缺失主题",
            confirmation_status="awaiting",
            progress=100,
            missing_items=[],
        )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _board_task_run_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM board_task_runs
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, id
            """,
            (TEST_USER_ID, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="这一段已有内容。",
        heading_path=["已有板书", "目标范围"],
    )


def _focus(lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-target",
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="这一段已有内容。",
        confidence=0.95,
        reason="选区已经定位到目标范围。",
        display_label="目标范围",
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


def _patch_write_route(monkeypatch: pytest.MonkeyPatch, lesson) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {"edit": [], "directive": [], "route": []}
    focus = _focus(lesson)
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: FocusResolution(
            focus=focus,
            candidates=[focus],
            status="selected",
            question="选区已经定位到目标范围。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="目标范围",
            location_status="selected",
            requested_action="write",
            question_or_topic="补充通用说明",
            progress=100,
            missing_items=[],
        ),
    )

    def _route(**kwargs):
        calls["route"].append(kwargs)
        return BoardTaskRouteDecision(
            route="write",
            location_status="found",
            target_focus=focus,
            reason="已定位可扩写的板书内容。",
            write_proposal="补充通用说明",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_route_decision", _route)

    def _directive(**kwargs):
        calls["directive"].append(kwargs)
        return (
            "AI生成：已写入，并开始围绕新内容讲解。",
            "chatbot_board_directed",
            {"status": "approved", "target_excerpt": kwargs["target_excerpt"]},
        )

    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _directive)
    return calls


def _success_outcome(lesson, *, chatbot_message: str = "AI生成：已补充通用说明。") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=chatbot_message,
        new_document=build_document(
            title=lesson.board_document.title,
            content_text="# 已有板书\n\n## 目标范围\n这一段已有内容。\n\n补充后的通用说明。\n",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已补充内容。"),
        assistant_message_source="board_document_editor_ai",
        operation="append_section",
        summary="已补充通用说明。",
        section_titles=["目标范围"],
        changed=True,
        operation_status="succeeded",
        patch_validation=BoardPatchValidationResult(status="pass", applied_operations=1),
        diff_preview=[
            DiffPreviewItem(
                op="insert_block",
                heading_path=["已有板书", "目标范围"],
                before_text="这一段已有内容。",
                after_text="补充后的通用说明。",
                summary="补充目标范围。",
            )
        ],
        patch_risk_level="low",
    )


def _failed_outcome(lesson) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：这次没有安全写入。",
        new_document=lesson.board_document,
        board_decision=BoardDecision(action="no_change", reason="没有安全变更。"),
        assistant_message_source="board_document_editor_ai",
        operation="append_section",
        summary="Board task write did not produce a safe document change.",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="no_safe_change",
    )


def _process(lesson_id: str, *, message: str = "请在这段后面补充一段通用说明"):
    return chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=message, selection=_selection()),
        user_id=TEST_USER_ID,
    )


def test_board_task_write_direct_success_trace_records_execute_commit_and_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="write_direct_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_write_route(monkeypatch, lesson)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: calls["edit"].append(kwargs) or _success_outcome(lesson))

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id)

    updated_lesson = response.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-4].decision == "ready"
    assert collector.steps[-3].decision == "succeeded"
    assert collector.steps[-2].decision == "committed"
    assert collector.steps[-2].commit_id == commit.id
    assert collector.steps[-2].run_id == response.board_task_run_id
    assert collector.steps[-2].version_id == response.board_task_version_id
    assert response.chatbot_message == "AI生成：已补充通用说明。"
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert "补充后的通用说明" in updated_lesson.board_document.content_text
    assert commit.label == "Board task write"
    assert commit.metadata["board_task_route"] == "write"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_patch_validation"]["status"] == "pass"
    assert response.board_patch_diff[0].op == "insert_block"
    assert len(calls["edit"]) == 1
    assert calls["directive"] == []


def test_board_task_write_confirmed_success_trace_records_directive_commit_and_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board(awaiting_confirmation=True)
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="write_confirmed_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_write_route(monkeypatch, lesson)
    monkeypatch.setattr(
        chatbot_module,
        "edit_existing_document",
        lambda **kwargs: calls["edit"].append(kwargs) or _success_outcome(lesson, chatbot_message=""),
    )

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id, message="好的，先扩写")

    updated_lesson = response.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert response.chatbot_message == "AI生成：已写入，并开始围绕新内容讲解。"
    assert response.board_task_phase == "consumed"
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["assistant_message_source"] == "chatbot_board_directed"
    assert commit.metadata["board_explanation_directive"]["status"] == "approved"
    assert commit.metadata["board_task_phase"] == "ready"
    assert len(calls["directive"]) == 1


def test_board_task_write_failure_trace_records_failure_after_durable_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="write_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_write_route(monkeypatch, lesson)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: calls["edit"].append(kwargs) or _failed_outcome(lesson))

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id)

    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-2].decision == "execution_failed"
    assert response.chatbot_message == "AI生成：这次没有安全写入。"
    assert response.active_board_task_sheet is not None
    assert response.board_task_phase == "ready"
    assert response.board_document_operation_status == "failed"
    assert events[-1]["event_type"] == "execution_failed"
    assert NodeId.BOARD_WRITE_EXECUTE.value not in nodes
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes


def test_board_task_write_save_failure_keeps_trace_without_durable_commit_or_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="write_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_write_route(monkeypatch, lesson)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: calls["edit"].append(kwargs) or _success_outcome(lesson))

    def _raise_on_save(**kwargs):
        if kwargs["board_task_history"] and any(op.get("type") == "update_board_task_run" and op.get("status") == "consumed" for op in kwargs["board_task_history"].operations):
            raise RuntimeError("save failed")
        return original_save(**kwargs)

    original_save = chatbot_module._save_workspace_for_user
    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _process(lesson_id)

    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
    ]
    runs = _board_task_run_rows(store, lesson_id)
    assert runs == []
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_board_task_write_response_failure_keeps_durable_commit_without_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="write_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_write_route(monkeypatch, lesson)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: calls["edit"].append(kwargs) or _success_outcome(lesson))

    def _raise_response(**kwargs):
        raise RuntimeError("response failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _process(lesson_id)

    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    commit = saved_lesson.history_graph.commits[-1]
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
    ]
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert collector.steps[-1].commit_id == commit.id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
