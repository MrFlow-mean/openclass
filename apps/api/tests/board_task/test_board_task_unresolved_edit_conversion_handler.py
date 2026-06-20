from __future__ import annotations

import json
from typing import Any

from app.models import (
    BoardDecision,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.paths.board_task_unresolved_edit_conversion import (
    BoardTaskUnresolvedEditConversionDependencies,
    handle_board_task_unresolved_edit_conversion,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_unresolved_edit_conversion"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("未定位编辑转换测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 已有范围\n这里有一段已有内容。\n",
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


def _edit_task(*, failure_count: int, action: str = "edit") -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="缺失主题",
        location_status="missing",
        requested_action=action,
        question_or_topic="缺失主题",
        progress=100,
        missing_items=[],
        failure_count=failure_count,
    )


def _clarify_decision(*, location_status: str = "missing") -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status=location_status,
        reason="没有定位到可编辑的原内容。",
    )


def _resolution(*, status: str = "missing") -> FocusResolution:
    return FocusResolution(
        focus=None,
        candidates=[],
        status=status,
        question="没有定位到可编辑的原内容。",
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


def _event_markers(operations: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (operation["event_type"], operation["run_id"])
        for operation in operations
        if operation.get("type") == "insert_board_task_event"
    ]


def _make_deps(*, lesson) -> tuple[BoardTaskUnresolvedEditConversionDependencies, dict[str, Any]]:
    calls: list[str] = []
    emitted_updates: list[dict[str, Any]] = []
    commit_metadata_at_creation: dict[str, Any] = {}
    save_snapshots: list[dict[str, Any]] = []

    def _activate(target_lesson, sheet):
        calls.append("activate")
        target_lesson.board_task_requirements = sheet

    def _emit(*, sheet, stamp, **kwargs):
        calls.append("emit")
        emitted_updates.append(
            {
                "run_id": stamp.run_id if stamp else None,
                "version_id": stamp.version_id if stamp else None,
                "phase": stamp.phase if stamp else None,
                "sheet": sheet.model_dump(mode="json"),
            }
        )

    def _message(**kwargs):
        calls.append("message")
        board_task = kwargs["board_task"]
        assert board_task.requested_action == "write"
        assert board_task.confirmation_status == "awaiting"
        return "AI生成：板书里没有对应原内容，要先扩写这部分吗？", "chatbot_board_task_clarification"

    def _board_search_evidence(resolution):
        return {
            "board_search_evidence": (
                {"status": resolution.status, "question": resolution.question} if resolution else None
            )
        }

    def _decision_trace(**kwargs):
        route_decision = kwargs["route_decision"]
        return {
            "decision_trace": {
                "route": route_decision.route if route_decision else None,
                "location_status": route_decision.location_status if route_decision else None,
                "role_executed": kwargs["role_executed"],
                "document_changed": kwargs["document_changed"],
                "reason": kwargs["reason"],
            }
        }

    def _board_task_metadata(
        *,
        board_task: BoardTaskRequirementSheet | None,
        stamp: BoardTaskHistoryStamp | None,
        route: str | None = None,
        decision: dict[str, object] | None = None,
        cleared: bool = False,
    ) -> dict[str, object]:
        return {
            "board_task_sheet": board_task.model_dump(mode="json") if board_task else None,
            "board_task_run_id": stamp.run_id if stamp else None,
            "board_task_version_id": stamp.version_id if stamp else None,
            "board_task_phase": stamp.phase if stamp else None,
            "board_task_route": route,
            "board_task_decision": decision,
            "board_task_cleared": cleared,
            "requirement_cleared": True,
            "active_requirement_sheet_after": None,
        }

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
            }
        )

    def _response(**kwargs):
        calls.append("response")
        stamp = kwargs["board_task_stamp"]
        return ChatResponse(
            chatbot_message=kwargs["chatbot_message"],
            learning_requirement_sheet=kwargs["requirements"],
            active_requirement_sheet=None,
            learning_clarification=kwargs["learning_clarification"],
            board_task_sheet=kwargs["lesson"].board_task_requirements,
            active_board_task_sheet=kwargs["lesson"].board_task_requirements,
            board_task_run_id=stamp.run_id if stamp else None,
            board_task_version_id=stamp.version_id if stamp else None,
            board_task_phase=stamp.phase if stamp else None,
            board_decision=kwargs["board_decision"],
            course_package=workspace_state.package_view_for_lesson(
                kwargs["workspace"],
                kwargs["package"],
                kwargs["lesson"].id,
            ),
        )

    deps = BoardTaskUnresolvedEditConversionDependencies(
        activate_board_task_requirements=_activate,
        emit_board_task_update=_emit,
        generate_board_task_clarification_message=_message,
        board_search_evidence_metadata=_board_search_evidence,
        decision_trace_metadata=_decision_trace,
        board_task_metadata=_board_task_metadata,
        commit_operations=_commit,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
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
    lesson,
    package,
    workspace,
    board_task: BoardTaskRequirementSheet,
    deps: BoardTaskUnresolvedEditConversionDependencies,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    decision: BoardTaskRouteDecision | None = None,
    resolution: FocusResolution | None = None,
):
    lesson.board_task_requirements = board_task
    return handle_board_task_unresolved_edit_conversion(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="请改写缺失主题"),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        board_task_history=board_task_history,
        requirement_history=requirement_history,
        decision=decision or _clarify_decision(),
        resolution=resolution or _resolution(),
        source_interaction_metadata={"source_marker": "unit"},
        deps=deps,
    )


def test_unresolved_edit_conversion_archives_old_run_before_new_write_confirmation() -> None:
    workspace, package, lesson = _workspace_context()
    requirement_history, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson)

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_edit_task(failure_count=1),
            deps=deps,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )

    assert response is not None
    commit = lesson.history_graph.commits[-1]
    metadata = commit.metadata
    assert commit.metadata == captured["commit_metadata_at_creation"]
    assert captured["calls"] == ["activate", "emit", "message", "commit", "normalize", "save", "response"]
    assert commit.label == "Board task converted to write confirmation"
    assert metadata["source_marker"] == "unit"
    assert metadata["board_task_route"] == "clarify_location"
    assert metadata["board_task_cleared"] is True
    assert metadata["board_task_phase"] == "not_executed"
    assert metadata["new_board_task_phase"] == "awaiting_confirmation"
    assert metadata["board_task_run_id"] != metadata["new_board_task_run_id"]
    assert metadata["board_task_version_id"] == metadata["old_board_task_ready_version_id"]
    assert metadata["new_board_task_version_id"] == response.board_task_version_id
    assert metadata["new_board_task_run_id"] == response.board_task_run_id
    assert metadata["decision_trace"]["role_executed"] == "focus_resolver"
    assert metadata["decision_trace"]["document_changed"] is False
    assert metadata["board_search_evidence"]["status"] == "missing"

    new_task = metadata["new_board_task"]
    assert new_task["requested_action"] == "write"
    assert new_task["confirmation_status"] == "awaiting"
    assert new_task["location_status"] == "content_absent"
    assert metadata["active_board_task_sheet_after"] == new_task
    assert lesson.board_task_requirements is not None
    assert lesson.board_task_requirements.requested_action == "write"
    assert lesson.board_task_requirements.confirmation_status == "awaiting"
    assert response.active_board_task_sheet == lesson.board_task_requirements
    assert response.board_task_phase == "awaiting_confirmation"
    assert captured["emitted_updates"][0]["run_id"] == response.board_task_run_id
    assert captured["emitted_updates"][0]["phase"] == "awaiting_confirmation"

    saved_operations = captured["save_snapshots"][0]["operations"]
    events = _event_markers(saved_operations)
    old_run_id = metadata["board_task_run_id"]
    new_run_id = metadata["new_board_task_run_id"]
    terminal_markers = [
        marker
        for marker in events
        if marker in {("not_executed", old_run_id), ("awaiting_confirmation", new_run_id)}
    ]
    assert terminal_markers == [("not_executed", old_run_id), ("awaiting_confirmation", new_run_id)]
    assert any(
        operation.get("type") == "update_board_task_run"
        and operation.get("id") == old_run_id
        and operation.get("status") == "not_executed"
        and operation.get("archived_at") is not None
        for operation in saved_operations
    )
    assert any(
        operation.get("type") == "insert_board_task_version"
        and operation.get("run_id") == new_run_id
        and operation.get("status") == "awaiting_confirmation"
        for operation in saved_operations
    )
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TARGET_RESOLVE.value,
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].run_id == old_run_id
    assert collector.steps[1].run_id == old_run_id
    assert collector.steps[2].run_id == new_run_id
    assert collector.steps[2].commit_id == commit.id


def test_unresolved_edit_conversion_waits_for_second_location_failure() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    requirement_history, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson)
    original_task = _edit_task(failure_count=0)

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=original_task,
            deps=deps,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )

    assert response is None
    assert captured["calls"] == []
    assert board_task_history.operations == []
    assert lesson.history_graph.commits[-1].id == initial_commit_id
    assert lesson.board_task_requirements == original_task
    assert _node_values(collector) == []


def test_non_edit_location_clarification_stays_outside_conversion_handler() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    requirement_history, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson)
    explain_task = _edit_task(failure_count=3, action="explain")

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=explain_task,
            deps=deps,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            decision=_clarify_decision(location_status="ambiguous"),
            resolution=_resolution(status="ambiguous"),
        )

    assert response is None
    assert captured["calls"] == []
    assert board_task_history.operations == []
    assert lesson.history_graph.commits[-1].id == initial_commit_id
    assert lesson.board_task_requirements == explain_task
    assert _node_values(collector) == []
