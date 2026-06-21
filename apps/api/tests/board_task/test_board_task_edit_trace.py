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
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_edit_trace"


def _workspace_with_existing_board():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n原始内容需要被改写。\n",
        ),
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


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="原始内容需要被改写。",
        heading_path=["已有板书", "目标范围"],
    )


def _focus(lesson_id: str, document_id: str) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document_id,
        segment_id="seg-target",
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="原始内容需要被改写。",
        confidence=0.95,
        reason="选区定位到目标段落。",
        display_label="目标范围",
    )


def _patch_edit_route(monkeypatch: pytest.MonkeyPatch, *, focus: BoardFocusRef) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="原始内容需要被改写。",
            location_status="selected",
            requested_action="edit",
            question_or_topic="改写目标段落",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: FocusResolution(focus=focus, candidates=[focus], status="selected", question=""),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: pytest.fail("local edit route decision should be used after focus resolution"),
    )


def _patch_whole_document_edit_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="全文",
            location_status="resolved",
            requested_action="edit",
            question_or_topic="整体改写当前板书",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: pytest.fail("whole-document edit should use the synthetic whole-document focus"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: pytest.fail("local whole-document edit route decision should be used"),
    )


def _success_outcome(lesson, *, changed_text: str = "改写后的内容。") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：已改写目标段落。",
        new_document=build_document(
            title=lesson.board_document.title,
            content_text=f"# 已有板书\n\n## 目标范围\n{changed_text}\n",
            document_id=lesson.board_document.id,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已改写目标段落。"),
        assistant_message_source="board_document_editor_ai",
        operation="board_patch",
        summary="已改写目标段落。",
        section_titles=["目标范围"],
        changed=True,
        operation_status="succeeded",
        patch_validation=BoardPatchValidationResult(status="pass", applied_operations=1),
        diff_preview=[
            DiffPreviewItem(
                op="update_block_content",
                heading_path=["已有板书", "目标范围"],
                before_text="原始内容需要被改写。",
                after_text=changed_text,
                summary="改写目标段落。",
            )
        ],
        patch_risk_level="low",
    )


def _whole_document_success_outcome(
    lesson,
    *,
    changed_text: str = "全文改写后的目标段落。",
) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：已整体改写当前板书。",
        new_document=build_document(
            title=lesson.board_document.title,
            content_text=f"# 已有板书\n\n## 目标范围\n{changed_text}\n",
            document_id=lesson.board_document.id,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已整体改写当前板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已整体改写当前板书。",
        section_titles=["目标范围"],
        changed=True,
        operation_status="succeeded",
        patch_validation=BoardPatchValidationResult(status="pass", applied_operations=1),
        diff_preview=[
            DiffPreviewItem(
                op="update_block_content",
                heading_path=["已有板书", "目标范围"],
                before_text="原始内容需要被改写。",
                after_text=changed_text,
                summary="整体改写后保留目标范围结构。",
            )
        ],
        patch_risk_level="medium",
    )


def _failed_outcome(lesson) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：这次没有安全改写板书。",
        new_document=lesson.board_document,
        board_decision=BoardDecision(action="no_change", reason="没有产生安全改写。"),
        assistant_message_source="board_document_editor_ai",
        operation="board_patch",
        summary="没有产生安全改写。",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="没有产生安全改写。",
    )


def test_board_task_edit_success_trace_persists_commit_consume_and_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    focus = _focus(lesson.id, lesson.board_document.id)
    _patch_edit_route(monkeypatch, focus=focus)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _success_outcome(lesson))
    commit_metadata_at_creation: dict[str, Any] = {}
    original_commit_operations = chatbot_module.commit_operations

    def _capture_commit_metadata(*args, **kwargs):
        result = original_commit_operations(*args, **kwargs)
        commit_metadata_at_creation.clear()
        commit_metadata_at_creation.update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    monkeypatch.setattr(chatbot_module, "commit_operations", _capture_commit_metadata)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="请改写这段", selection=_selection()),
            user_id=TEST_USER_ID,
        )

    updated_lesson = response.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)

    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_EDIT_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-4].decision == "ready"
    assert collector.steps[-3].decision == "succeeded"
    assert collector.steps[-2].decision == "committed"
    assert collector.steps[-2].commit_id == commit.id
    assert collector.steps[-2].run_id == response.board_task_run_id
    assert collector.steps[-2].version_id == response.board_task_version_id
    assert "改写后的内容" in updated_lesson.board_document.content_text
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["board_task_route"] == "edit"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata == commit_metadata_at_creation
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_patch_validation"]["status"] == "pass"
    assert commit.metadata["board_patch_diff"][0]["op"] == "update_block_content"
    assert response.board_patch_diff[0].op == "update_block_content"


def test_board_task_edit_whole_document_authorizes_replace_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_whole_document_authorized")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_whole_document_edit_route(monkeypatch)
    edit_calls: list[dict[str, Any]] = []

    def _edit(**kwargs):
        edit_calls.append(kwargs)
        return _whole_document_success_outcome(lesson)

    monkeypatch.setattr(chatbot_module, "edit_existing_document", _edit)

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="请把全文整体改写得更清晰"),
        user_id=TEST_USER_ID,
    )

    updated_lesson = response.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]

    assert len(edit_calls) == 1
    edit_call = edit_calls[0]
    assert edit_call["target_scope"] == "whole_document"
    assert edit_call["allow_replace_document"] is True
    assert edit_call["selection_excerpt"] == ""
    assert edit_call["focus"].match_id == f"whole_document:{lesson.board_document.id}"
    assert response.board_task_phase == "consumed"
    assert commit.metadata["board_edit_operation"] == "replace_document"
    assert commit.metadata["target_scope"] == "whole_document"
    assert commit.metadata["board_task_route"] == "edit"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["board_task_decision"]["target_scope"] == "whole_document"
    assert commit.metadata["resolved_focus"]["match_id"] == f"whole_document:{lesson.board_document.id}"
    assert "全文改写后的目标段落" in updated_lesson.board_document.content_text


def test_board_task_edit_whole_document_recent_focus_metadata_uses_changed_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_whole_document_recent_focus")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_whole_document_edit_route(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "edit_existing_document",
        lambda **kwargs: _whole_document_success_outcome(lesson, changed_text="精确改写后的目标段落。"),
    )

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="请把整个板书整体改写"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    resolved_focus = commit.metadata["resolved_focus"]
    recent_focus = commit.metadata["recent_board_edit_focus"]

    assert resolved_focus["match_id"] == f"whole_document:{lesson.board_document.id}"
    assert recent_focus is not None
    assert recent_focus["match_id"].startswith("recent:")
    assert not recent_focus["match_id"].startswith("whole_document:")
    assert recent_focus["heading_path"] == ["已有板书", "目标范围"]
    assert recent_focus["excerpt"] == "精确改写后的目标段落。"
    assert recent_focus["display_label"] == "已有板书 / 目标范围"
    assert recent_focus["score_breakdown"] == {"recent_board_edit_focus": 0.95}


def test_board_task_edit_failure_trace_records_failure_after_durable_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    focus = _focus(lesson.id, lesson.board_document.id)
    _patch_edit_route(monkeypatch, focus=focus)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _failed_outcome(lesson))

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="请改写这段", selection=_selection()),
            user_id=TEST_USER_ID,
        )

    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-2].decision == "execution_failed"
    assert response.active_board_task_sheet is not None
    assert response.board_task_phase == "ready"
    assert response.board_document_operation_status == "failed"
    assert events[-1]["event_type"] == "execution_failed"
    assert NodeId.BOARD_EDIT_EXECUTE.value not in nodes
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes


def test_board_task_edit_failure_does_not_record_response_when_response_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_failure_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    focus = _focus(lesson.id, lesson.board_document.id)
    _patch_edit_route(monkeypatch, focus=focus)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _failed_outcome(lesson))

    def _raise_response(**kwargs):
        raise RuntimeError("response build failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response build failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="请改写这段", selection=_selection()),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert events[-1]["event_type"] == "execution_failed"


def test_board_task_edit_success_does_not_record_response_when_response_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="edit_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    focus = _focus(lesson.id, lesson.board_document.id)
    _patch_edit_route(monkeypatch, focus=focus)
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _success_outcome(lesson))

    def _raise_response(**kwargs):
        raise RuntimeError("response build failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response build failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="请改写这段", selection=_selection()),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_EDIT_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    runs = _board_task_run_rows(store, lesson_id)
    assert runs[0]["status"] == "consumed"
