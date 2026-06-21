from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardTaskRequirementSheet,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services import chatbot as chatbot_module
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_await_write_confirmation import (
    BoardTaskAwaitWriteConfirmationDependencies,
    handle_board_task_await_write_confirmation_terminal,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
)


TEST_USER_ID = "user_board_task_await_write_confirmation_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("Await write confirmation handler")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="Existing board",
            content_text="# Existing board\n\n## Known section\nCurrent content.\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="Existing-board task",
        learning_goal="Handle an existing-board task",
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
        label="Ready",
        reason="The existing-board task is executable.",
        ready_for_board=False,
        summary="The existing-board task is executable.",
    )


def _board_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="missing concept",
        location_status="resolved",
        requested_action="explain",
        question_or_topic="missing concept",
        confirmation_status="none",
        progress=100,
        missing_items=[],
    )


def _route_decision() -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="await_write_confirmation",
        location_status="content_absent",
        reason="The board does not contain the requested content.",
        write_proposal="Add a short explanation of the missing concept.",
        target_scope="append",
    )


def _histories(lesson):
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    return requirement_history, board_task_history


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _trace_snapshot() -> list[str]:
    collector = current_workflow_trace_collector()
    if collector is None:
        return []
    return _node_values(collector)


def _make_deps(
    *,
    lesson,
    response_builder=None,
    fail_save: bool = False,
) -> tuple[BoardTaskAwaitWriteConfirmationDependencies, dict[str, Any]]:
    calls: list[str] = []
    emitted_updates: list[dict[str, Any]] = []
    commit_metadata_at_creation: dict[str, Any] = {}
    save_snapshots: list[dict[str, Any]] = []

    def _activate(target_lesson, board_task):
        calls.append("activate")
        target_lesson.learning_requirements = None
        target_lesson.board_task_requirements = board_task

    def _emit(**kwargs):
        calls.append("emit")
        emitted_updates.append(
            {
                "sheet": kwargs["sheet"].model_dump(mode="json"),
                "stamp": kwargs["stamp"],
            }
        )

    def _message(**kwargs):
        calls.append("message")
        board_task = kwargs["board_task"]
        return (
            f"Should I expand the board with {board_task.question_or_topic} before continuing?",
            "chatbot_board_task_clarification",
        )

    def _commit(*args, **kwargs):
        calls.append("commit")
        result = commit_operations(*args, **kwargs)
        commit_metadata_at_creation.clear()
        commit_metadata_at_creation.update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    def _normalize(package):
        calls.append("normalize")
        workspace_state.normalize_package_state(package)

    def _save(**kwargs):
        calls.append("save")
        board_task_history = kwargs["board_task_history"]
        save_snapshots.append(
            {
                "latest_commit_id": lesson.history_graph.commits[-1].id,
                "latest_label": lesson.history_graph.commits[-1].label,
                "operations": [dict(operation) for operation in board_task_history.operations],
                "trace_at_save": _trace_snapshot(),
            }
        )
        if fail_save:
            raise RuntimeError("save failed")

    def _response(**kwargs):
        calls.append("response")
        if response_builder is not None:
            return response_builder(**kwargs)
        return chatbot_module._response(**kwargs)

    deps = BoardTaskAwaitWriteConfirmationDependencies(
        activate_board_task_requirements=_activate,
        emit_board_task_update=_emit,
        generate_board_task_clarification_message=_message,
        decision_trace_metadata=chatbot_module.decision_trace_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
        commit_operations=_commit,
        build_response=_response,
    )
    return deps, {
        "calls": calls,
        "emitted_updates": emitted_updates,
        "commit_metadata_at_creation": commit_metadata_at_creation,
        "save_snapshots": save_snapshots,
    }


def _run_handler(
    *,
    workspace,
    package,
    lesson,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    deps: BoardTaskAwaitWriteConfirmationDependencies,
    board_search_evidence: dict[str, object] | None = None,
):
    requirement_history, _ = _histories(lesson)
    lesson.board_task_requirements = board_task
    ready_stamp = board_task_history.record_update(sheet=board_task, status="ready")
    return handle_board_task_await_write_confirmation_terminal(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="Explain the missing concept"),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        route_decision=_route_decision(),
        board_task_stamp=ready_stamp,
        board_search_evidence=board_search_evidence,
        source_interaction_metadata={"source_marker": "unit"},
        deps=deps,
    )


def _awaiting_sheet_from_operations(operations: list[dict[str, Any]]) -> dict[str, Any]:
    awaiting_versions = [
        operation
        for operation in operations
        if operation.get("type") == "insert_board_task_version"
        and operation.get("status") == "awaiting_confirmation"
    ]
    assert awaiting_versions
    return json.loads(awaiting_versions[-1]["sheet_json"])


def test_terminal_sets_absent_write_confirmation_and_records_trace_after_save() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson)
    board_search_evidence = {"status": "content_absent", "source": "unit"}

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            board_task_history=board_task_history,
            deps=deps,
            board_search_evidence=board_search_evidence,
        )

    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert captured["calls"] == ["activate", "emit", "message", "commit", "normalize", "save", "response"]
    assert captured["save_snapshots"][0]["latest_commit_id"] == commit.id
    assert captured["save_snapshots"][0]["latest_label"] == "Board write confirmation"
    assert NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value not in captured["save_snapshots"][0]["trace_at_save"]
    assert collector.steps[-2].commit_id == commit.id

    active_task = response.active_board_task_sheet
    assert active_task is not None
    assert active_task == lesson.board_task_requirements
    assert active_task.requested_action == "write"
    assert active_task.location_status == "content_absent"
    assert active_task.confirmation_status == "awaiting"
    assert active_task.progress == 100
    assert active_task.missing_items == []
    assert active_task.clarification_question == ""
    assert response.board_task_phase == "awaiting_confirmation"

    assert commit.metadata == captured["commit_metadata_at_creation"]
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot_board_task_clarification"
    assert commit.metadata["board_search_evidence"] == board_search_evidence
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
    assert commit.metadata["board_task_cleared"] is False
    assert commit.metadata["board_task_phase"] == "awaiting_confirmation"
    assert commit.metadata["board_task_decision"]["location_status"] == "content_absent"
    assert commit.metadata["board_task_sheet"]["requested_action"] == "write"
    assert commit.metadata["board_task_sheet"]["location_status"] == "content_absent"
    assert commit.metadata["board_task_sheet"]["confirmation_status"] == "awaiting"
    assert commit.metadata["decision_trace"]["role_executed"] == "board_task_route_decider"
    assert commit.metadata["source_marker"] == "unit"

    saved_operations = captured["save_snapshots"][0]["operations"]
    awaiting_sheet = _awaiting_sheet_from_operations(saved_operations)
    assert awaiting_sheet["requested_action"] == "write"
    assert awaiting_sheet["location_status"] == "content_absent"
    assert awaiting_sheet["confirmation_status"] == "awaiting"
    assert any(
        operation.get("type") == "update_board_task_run"
        and operation.get("status") == "awaiting_confirmation"
        for operation in saved_operations
    )
    assert not any(
        operation.get("type") == "update_board_task_run" and operation.get("status") == "consumed"
        for operation in saved_operations
    )
    assert captured["emitted_updates"][-1]["sheet"]["confirmation_status"] == "awaiting"


def test_save_failure_does_not_record_await_or_response_trace() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, fail_save=True)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                board_task_history=board_task_history,
                deps=deps,
            )

    nodes = _node_values(collector)
    assert nodes == [NodeId.BOARD_TASK_COLLECT.value]
    assert captured["calls"] == ["activate", "emit", "message", "commit", "normalize", "save"]
    assert NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    assert captured["save_snapshots"][0]["trace_at_save"] == [NodeId.BOARD_TASK_COLLECT.value]
    assert lesson.history_graph.commits[-1].label == "Board write confirmation"
    assert lesson.board_task_requirements is not None
    assert lesson.board_task_requirements.requested_action == "write"
    assert lesson.board_task_requirements.location_status == "content_absent"
    assert lesson.board_task_requirements.confirmation_status == "awaiting"


def test_response_failure_does_not_record_response_trace_after_durable_await() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)

    def _raise_response(**kwargs):
        raise RuntimeError("response failed")

    deps, captured = _make_deps(lesson=lesson, response_builder=_raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                board_task_history=board_task_history,
                deps=deps,
            )

    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value,
    ]
    assert captured["calls"] == ["activate", "emit", "message", "commit", "normalize", "save", "response"]
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    assert captured["save_snapshots"][0]["latest_commit_id"] == commit.id
    assert NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value not in captured["save_snapshots"][0]["trace_at_save"]
    assert collector.steps[-1].node_id == NodeId.BOARD_AWAIT_WRITE_CONFIRMATION
    assert collector.steps[-1].commit_id == commit.id
    saved_operations = captured["save_snapshots"][0]["operations"]
    assert _awaiting_sheet_from_operations(saved_operations)["confirmation_status"] == "awaiting"
    assert not any(
        operation.get("type") == "update_board_task_run" and operation.get("status") == "consumed"
        for operation in saved_operations
    )
