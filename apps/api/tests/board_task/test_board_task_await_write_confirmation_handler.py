from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardTaskRequirementSheet,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_await_write_confirmation import (
    BoardTaskAwaitWriteConfirmationDependencies,
    handle_board_task_await_write_confirmation,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_await_write_confirmation_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("等待写入确认处理器")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 已有部分\n这里是已有内容。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="已有板书任务",
        learning_goal="围绕已有板书完成用户指定动作",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100,
        label="可执行",
        reason="已有板书任务已经完整。",
        ready_for_board=False,
        summary="已有板书任务已经完整。",
    )


def _board_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="缺失主题",
        location_status="missing",
        requested_action="explain",
        question_or_topic="讲解缺失主题",
        confirmation_status="none",
        progress=100,
        missing_items=[],
    )


def _route_decision() -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="await_write_confirmation",
        location_status="content_absent",
        reason="当前板书没有对应内容，需要确认是否扩写。",
        write_proposal="讲解缺失主题",
    )


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(TEST_USER_ID, lesson_id),
    )


def _board_task_history(lesson_id: str) -> BoardTaskHistoryRecorder:
    return BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_board_task_history_state_for_user(TEST_USER_ID, lesson_id),
    )


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


def _deps(
    calls: dict[str, Any],
    *,
    store: SqliteCourseStore,
    fail_save: bool = False,
    fail_response: bool = False,
) -> BoardTaskAwaitWriteConfirmationDependencies:
    def _activate(lesson, board_task):
        calls.setdefault("activate", []).append(board_task)
        chatbot_module._activate_board_task_requirements(lesson, board_task)

    def _emit(**kwargs):
        calls.setdefault("emit", []).append(kwargs)

    def _message(**kwargs):
        calls.setdefault("message", []).append(kwargs)
        return "AI生成：当前板书没有这部分内容，要先扩写吗？", "board_task_clarification"

    def _normalize(package):
        calls.setdefault("normalize", []).append(package)
        workspace_state.normalize_package_state(package)

    def _save(**kwargs):
        calls.setdefault("save", []).append(kwargs)
        if fail_save:
            raise RuntimeError("save failed")
        chatbot_module._save_workspace_for_user(**kwargs)
        lesson_id = kwargs["workspace"].packages[0].lessons[-1].id
        calls["versions_after_save"] = store.list_board_task_versions(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )

    def _response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        if fail_response:
            raise RuntimeError("response failed")
        return chatbot_module._response(**kwargs)

    return BoardTaskAwaitWriteConfirmationDependencies(
        activate_board_task_requirements=_activate,
        emit_board_task_update=_emit,
        build_clarification_message=_message,
        commit_operations=chatbot_module.commit_operations,
        board_task_metadata=chatbot_module._board_task_metadata,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
        build_response=_response,
    )


def _call_handler(
    *,
    workspace,
    package,
    lesson,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    calls: dict[str, Any],
    store: SqliteCourseStore,
    fail_save: bool = False,
    fail_response: bool = False,
):
    lesson.board_task_requirements = board_task
    board_task_history.record_update(sheet=board_task, status="ready")
    return handle_board_task_await_write_confirmation(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="讲解缺失主题"),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        board_task_history=board_task_history,
        requirement_history=_requirement_history(lesson.id),
        route_decision=_route_decision(),
        interaction_metadata={"interaction_context": "none"},
        board_search_evidence_metadata={"board_search_evidence": {"status": "content_absent"}},
        decision_trace_metadata={
            "decision_trace": {
                "role_executed": "board_task_route_decider",
                "document_changed": False,
                "reason": "当前板书没有对应内容，需要确认是否扩写。",
            }
        },
        deps=_deps(calls, store=store, fail_save=fail_save, fail_response=fail_response),
    )


def test_handler_preserves_current_main_await_write_confirmation_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_context()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_await_write_confirmation_handler")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = _call_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            board_task_history=board_task_history,
            calls=calls,
            store=store,
        )

    commit = lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson.id)
    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    persisted_sheet = json.loads(versions[-1]["sheet_json"])

    assert response.chatbot_message == "AI生成：当前板书没有这部分内容，要先扩写吗？"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet == lesson.board_task_requirements
    assert response.active_board_task_sheet.requested_action == "write"
    assert response.active_board_task_sheet.location_status == "content_absent"
    assert response.active_board_task_sheet.confirmation_status == "awaiting"
    assert response.active_board_task_sheet.progress == 100
    assert response.active_board_task_sheet.missing_items == []
    assert response.active_board_task_sheet.clarification_question == ""
    assert response.board_task_phase == "awaiting_confirmation"
    assert runs[0]["status"] == "awaiting_confirmation"
    assert runs[0]["active_version_id"] == versions[-1]["id"]
    assert [version["status"] for version in versions] == ["ready", "awaiting_confirmation"]
    assert [event["event_type"] for event in events] == ["created", "ready", "awaiting_confirmation"]
    assert persisted_sheet["requested_action"] == "write"
    assert persisted_sheet["location_status"] == "content_absent"
    assert persisted_sheet["confirmation_status"] == "awaiting"
    assert calls["versions_after_save"][-1]["id"] == versions[-1]["id"]
    assert [len(calls[name]) for name in ("activate", "emit", "message", "normalize", "save", "response")] == [
        1,
        1,
        1,
        1,
        1,
        1,
    ]
    assert calls["message"][0]["context"] == "板书里没有对应内容。请询问用户是否要先扩写板书，再继续学习。"
    assert commit.label == "Board write confirmation"
    assert commit.metadata["assistant_message_source"] == "board_task_clarification"
    assert commit.metadata["interaction_context"] == "none"
    assert commit.metadata["board_search_evidence"] == {"status": "content_absent"}
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
    assert commit.metadata["board_task_cleared"] is False
    assert commit.metadata["board_task_phase"] == "awaiting_confirmation"
    assert commit.metadata["board_task_sheet"]["requested_action"] == "write"
    assert commit.metadata["board_task_sheet"]["location_status"] == "content_absent"
    assert commit.metadata["board_task_sheet"]["confirmation_status"] == "awaiting"
    assert commit.metadata["decision_trace"]["role_executed"] == "board_task_route_decider"
    assert _node_values(collector) == [
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert NodeId.BOARD_TASK_COLLECT.value not in _node_values(collector)
    assert collector.steps[0].decision == "awaiting_confirmation"
    assert collector.steps[0].commit_id == commit.id
    assert collector.steps[0].run_id == response.board_task_run_id
    assert collector.steps[0].version_id == response.board_task_version_id


def test_handler_save_failure_records_no_terminal_trace_or_response_assemble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_context()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_await_write_confirmation_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _call_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                board_task_history=board_task_history,
                calls=calls,
                store=store,
                fail_save=True,
            )

    assert _board_task_run_rows(store, lesson.id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id) == []
    assert [len(calls[name]) for name in ("activate", "emit", "message", "normalize", "save")] == [1, 1, 1, 1, 1]
    assert "response" not in calls
    assert _node_values(collector) == []
    assert NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_handler_response_failure_skips_response_assemble_after_durable_await(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_context()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_await_write_confirmation_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _call_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                board_task_history=board_task_history,
                calls=calls,
                store=store,
                fail_response=True,
            )

    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert [version["status"] for version in versions] == ["ready", "awaiting_confirmation"]
    assert len(calls["response"]) == 1
    assert _node_values(collector) == [NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value]
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_handler_rejects_non_await_write_confirmation_route_before_commit_or_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_context()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_await_write_confirmation_guard")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}
    route_decision = BoardTaskRouteDecision(
        route="clarify_location",
        location_status="missing",
        reason="还不能定位目标位置。",
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="route='await_write_confirmation'"):
            handle_board_task_await_write_confirmation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="讲解缺失主题"),
                requirements=_requirements(),
                learning_clarification=_clarification(),
                resources=[],
                board_task=_board_task(),
                board_task_history=board_task_history,
                requirement_history=_requirement_history(lesson.id),
                route_decision=route_decision,
                deps=_deps(calls, store=store),
            )

    assert calls == {}
    assert lesson.history_graph.commits[-1].label != "Board write confirmation"
    assert _node_values(collector) == []
