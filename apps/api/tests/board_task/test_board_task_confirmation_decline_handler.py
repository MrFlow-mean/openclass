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
from app.services.chat.paths.board_task_confirmation_decline import (
    BoardTaskConfirmationDeclineDependencies,
    handle_board_task_confirmation_decline,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_confirmation_decline_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("确认拒绝处理器测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 起点\n这一段已有内容。\n",
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
        reason="已有板书任务已经进入扩写确认。",
        ready_for_board=False,
        summary="已有板书任务已经进入扩写确认。",
    )


def _awaiting_write_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="缺失主题",
        location_status="content_absent",
        requested_action="write",
        question_or_topic="补充缺失主题",
        confirmation_status="awaiting",
        progress=100,
        missing_items=[],
    )


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _seed_awaiting_board_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace, package, lesson = _workspace_context()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_confirmation_decline")
    monkeypatch.setattr(workspace_state, "STORE", store)
    task = _awaiting_write_task()
    lesson.board_task_requirements = task
    initial_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    initial_history.record_update(
        sheet=task,
        status="awaiting_confirmation",
        change_summary="Awaiting learner confirmation before writing new board content.",
    )
    workspace_state.save_workspace_for_user_with_histories(
        TEST_USER_ID,
        workspace,
        requirement_history_operations=[],
        board_task_history_operations=initial_history.operations,
    )
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=workspace_state.load_board_task_history_state_for_user(TEST_USER_ID, lesson.id),
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    return workspace, package, lesson, store, task, requirement_history, board_task_history


def _deps(*, save_workspace_for_user=chatbot_module._save_workspace_for_user):
    return BoardTaskConfirmationDeclineDependencies(
        board_task_metadata=chatbot_module._board_task_metadata,
        commit_operations=commit_operations,
        normalize_package_state=workspace_state.normalize_package_state,
        save_workspace_for_user=save_workspace_for_user,
        build_response=chatbot_module._response,
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


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


def test_confirmation_decline_marks_task_not_executed_after_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, store, task, requirement_history, board_task_history = _seed_awaiting_board_task(
        monkeypatch,
        tmp_path,
    )

    with bind_workflow_trace_collector() as collector:
        response = handle_board_task_confirmation_decline(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="不用"),
            requirements=_requirements(),
            learning_clarification=_clarification(),
            existing_task=task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            source_interaction_metadata={"source_interaction_route": "new_task"},
            deps=_deps(),
        )

    commit = lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson.id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert response.chatbot_message == ""
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet is None
    assert response.board_task_phase == "not_executed"
    assert lesson.board_task_requirements is None
    assert runs[0]["status"] == "not_executed"
    assert runs[0]["archived_at"] is not None
    assert events[-1]["event_type"] == "not_executed"
    assert json.loads(events[-1]["metadata_json"]) == {"reason": "用户取消了扩写确认。"}
    assert commit.label == "Board task cancelled"
    assert commit.metadata["assistant_message_source"] == "board_task_cancelled"
    assert commit.metadata["source_interaction_route"] == "new_task"
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
    assert commit.metadata["board_task_cleared"] is True
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_WRITE_CONFIRMATION_HANDLE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "awaiting_confirmation"
    assert collector.steps[1].decision == "declined"
    assert collector.steps[1].run_id == response.board_task_run_id
    assert collector.steps[1].version_id == response.board_task_version_id
    assert collector.steps[1].commit_id == commit.id


def test_confirmation_decline_save_failure_skips_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, store, task, requirement_history, board_task_history = _seed_awaiting_board_task(
        monkeypatch,
        tmp_path,
    )

    def _raise_save(**kwargs):
        raise RuntimeError("save failed")

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            handle_board_task_confirmation_decline(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="不用"),
                requirements=_requirements(),
                learning_clarification=_clarification(),
                existing_task=task,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
                deps=_deps(save_workspace_for_user=_raise_save),
            )

    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert _node_values(collector) == [NodeId.BOARD_TASK_COLLECT.value]
    assert NodeId.BOARD_WRITE_CONFIRMATION_HANDLE.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
    assert events[-1]["event_type"] == "awaiting_confirmation"
    assert all(event["event_type"] != "not_executed" for event in events)


def test_confirmation_decline_response_failure_skips_response_assemble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, store, task, requirement_history, board_task_history = _seed_awaiting_board_task(
        monkeypatch,
        tmp_path,
    )

    deps = BoardTaskConfirmationDeclineDependencies(
        board_task_metadata=chatbot_module._board_task_metadata,
        commit_operations=commit_operations,
        normalize_package_state=workspace_state.normalize_package_state,
        save_workspace_for_user=chatbot_module._save_workspace_for_user,
        build_response=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            handle_board_task_confirmation_decline(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="不用"),
                requirements=_requirements(),
                learning_clarification=_clarification(),
                existing_task=task,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
                deps=deps,
            )

    runs = _board_task_run_rows(store, lesson.id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert runs[0]["status"] == "not_executed"
    assert events[-1]["event_type"] == "not_executed"
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_WRITE_CONFIRMATION_HANDLE.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
