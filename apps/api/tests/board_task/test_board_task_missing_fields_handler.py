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
from app.services.chat.paths.board_task_missing_fields import (
    BoardTaskMissingFieldsDependencies,
    handle_board_task_missing_fields,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
)


TEST_USER_ID = "user_board_task_missing_fields_handler"


def _workspace_inputs():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(title="已有板书", content_text="# 已有板书\n\n这一段已有内容。\n"),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id

    assert lesson.learning_requirements is not None
    requirements = lesson.learning_requirements.model_copy(deep=True)
    clarification = LearningClarificationStatus(
        progress=100,
        label="已有板书任务",
        reason="已有板书请求进入四字段任务清单。",
        missing_items=[],
        can_start=True,
        ready_for_board=True,
        summary="已有板书请求。",
    )
    return workspace, package, lesson, requirements, clarification


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


def _board_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        requested_action="explain",
        target_hint="",
        question_or_topic="",
        missing_items=["目标位置", "问题内容"],
        progress=40,
        clarification_question="请告诉我要处理板书里的哪里，以及你想围绕它问什么。",
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


def _node_values(collector: WorkflowTraceCollector | None) -> list[str]:
    if collector is None:
        return []
    return [step.node_id.value for step in collector.steps]


def _deps(
    calls: dict[str, Any],
    *,
    store: SqliteCourseStore,
    fail_save: bool = False,
) -> BoardTaskMissingFieldsDependencies:
    def _message(**kwargs):
        calls.setdefault("message", []).append(kwargs)
        return "AI生成：请告诉我要处理板书里的哪里。", "board_task_clarification"

    def _save(**kwargs):
        calls.setdefault("save", []).append(kwargs)
        calls["trace_before_save"] = _node_values(current_workflow_trace_collector())
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
        return chatbot_module._response(**kwargs)

    return BoardTaskMissingFieldsDependencies(
        commit_operations=chatbot_module.commit_operations,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        build_clarification_message=_message,
        save_workspace_for_user=_save,
        build_response=_response,
    )


def _call_handler(
    *,
    workspace,
    package,
    lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    calls: dict[str, Any],
    store: SqliteCourseStore,
    fail_save: bool = False,
):
    stamp = board_task_history.record_update(sheet=board_task)
    return handle_board_task_missing_fields(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="解释一下"),
        requirements=requirements,
        learning_clarification=clarification,
        resources=[],
        board_task=board_task,
        board_task_history=board_task_history,
        board_task_stamp=stamp,
        requirement_history=_requirement_history(lesson.id),
        interaction_metadata={"interaction_context": "none"},
        decision_trace_metadata={
            "decision_trace": {
                "role_executed": "board_task_manager",
                "document_changed": False,
                "reason": board_task.clarification_question,
            }
        },
        deps=_deps(calls, store=store, fail_save=fail_save),
    )


def test_handler_keeps_board_task_collecting_and_preserves_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_missing_fields_handler")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task = _board_task()
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = _call_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            board_task=board_task,
            board_task_history=board_task_history,
            calls=calls,
            store=store,
        )

    commit = lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson.id)
    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    persisted_sheet = json.loads(versions[0]["sheet_json"])

    assert response.chatbot_message == "AI生成：请告诉我要处理板书里的哪里。"
    assert response.active_board_task_sheet == board_task
    assert response.active_board_task_sheet.progress == 40
    assert response.active_board_task_sheet.missing_items == ["目标位置", "问题内容"]
    assert response.active_board_task_sheet.clarification_question == board_task.clarification_question
    assert response.board_task_phase == "collecting"
    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements == board_task
    assert runs[0]["status"] == "collecting"
    assert runs[0]["active_version_id"] == versions[0]["id"]
    assert versions[0]["status"] == "collecting"
    assert persisted_sheet["missing_items"] == ["目标位置", "问题内容"]
    assert persisted_sheet["clarification_question"] == board_task.clarification_question
    assert [event["event_type"] for event in events] == ["created"]
    assert calls["trace_before_save"] == [NodeId.BOARD_TASK_COLLECT.value]
    assert calls["versions_after_save"][0]["id"] == versions[0]["id"]
    assert len(calls["message"]) == 1
    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert commit.label == "Board task clarification"
    assert commit.metadata["assistant_message_source"] == "board_task_clarification"
    assert commit.metadata["board_task_route"] == "clarify_location"
    assert commit.metadata["board_task_cleared"] is False
    assert commit.metadata["decision_trace"]["role_executed"] == "board_task_manager"
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TASK_CLARIFY_FIELDS.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "collecting"
    assert collector.steps[0].run_id == response.board_task_run_id
    assert collector.steps[0].version_id == response.board_task_version_id
    assert collector.steps[1].decision == "missing_fields"
    assert collector.steps[1].reason == board_task.clarification_question
    assert collector.steps[1].commit_id == commit.id


def test_handler_save_failure_records_no_terminal_trace_or_response_assemble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_missing_fields_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    board_task = _board_task()
    board_task_history = _board_task_history(lesson.id)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _call_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                requirements=requirements,
                clarification=clarification,
                board_task=board_task,
                board_task_history=board_task_history,
                calls=calls,
                store=store,
                fail_save=True,
            )

    assert lesson.board_task_requirements == board_task
    assert lesson.board_task_requirements.progress == 40
    assert lesson.board_task_requirements.missing_items == ["目标位置", "问题内容"]
    assert lesson.board_task_requirements.clarification_question == board_task.clarification_question
    assert _board_task_run_rows(store, lesson.id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id) == []
    assert calls["trace_before_save"] == [NodeId.BOARD_TASK_COLLECT.value]
    assert len(calls["message"]) == 1
    assert len(calls["save"]) == 1
    assert "response" not in calls
    assert _node_values(collector) == [NodeId.BOARD_TASK_COLLECT.value]
    assert NodeId.BOARD_TASK_CLARIFY_FIELDS.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_handler_rejects_ready_board_task_before_commit_or_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_missing_fields_ready_guard")
    monkeypatch.setattr(workspace_state, "STORE", store)
    ready_task = _board_task().model_copy(update={"progress": 100, "missing_items": []})
    board_task_history = _board_task_history(lesson.id)
    stamp = board_task_history.record_update(sheet=ready_task)
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="collecting board task"):
            handle_board_task_missing_fields(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="解释一下"),
                requirements=requirements,
                learning_clarification=clarification,
                resources=[],
                board_task=ready_task,
                board_task_history=board_task_history,
                board_task_stamp=stamp,
                requirement_history=_requirement_history(lesson.id),
                deps=_deps(calls, store=store),
            )

    assert lesson.board_task_requirements is None
    assert calls == {}
    assert _node_values(collector) == []
