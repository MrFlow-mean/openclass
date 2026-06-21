from __future__ import annotations

import json
from typing import Any, NoReturn

import pytest

from app.models import (
    BoardFocusRef,
    BoardSearchEvidence,
    BoardTaskRequirementSheet,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    SelectionRef,
)
from app.services import chatbot as chatbot_module
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_location_clarification import (
    BoardTaskLocationClarificationDependencies,
    handle_board_task_location_clarification,
    is_normal_location_clarification,
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


TEST_USER_ID = "user_board_task_location_clarification_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("位置澄清测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 第一处\n第一处内容。\n\n## 第二处\n第二处内容。\n",
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


def _board_task(*, action: str = "explain", failure_count: int = 0) -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="多个候选位置",
        location_status="missing",
        requested_action=action,
        question_or_topic="解释多个候选位置",
        progress=100,
        missing_items=[],
        failure_count=failure_count,
    )


def _focus(lesson, *, label: str, excerpt: str, segment_id: str) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=segment_id,
        kind="paragraph",
        heading_path=["已有板书", label],
        excerpt=excerpt,
        confidence=0.78,
        reason=f"{label} 是候选位置。",
        display_label=label,
    )


def _selection() -> SelectionRef:
    return SelectionRef(kind="chat", excerpt="解释多个候选位置")


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


def _ambiguous_decision(candidates: list[BoardFocusRef]) -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous",
        candidate_focuses=candidates,
        reason="找到了多个候选位置，请确认其中一个。",
    )


def _missing_decision() -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="missing",
        reason="没有定位到明确目标位置，请补充位置。",
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _make_deps(*, response_builder=None, save_failure: Exception | None = None):
    calls: list[str] = []
    captured: dict[str, Any] = {
        "calls": calls,
        "commit_metadata_at_creation": {},
        "generated_resolutions": [],
        "emitted_updates": [],
        "saved_operations": [],
    }

    def _generate_focus_candidate_message(**kwargs):
        calls.append("generate")
        captured["generated_resolutions"].append(kwargs["resolution"])
        return "AI生成：你想让我处理哪一段？", "chatbot_board_task_clarification"

    def _emit_board_task_update(**kwargs):
        calls.append("emit")
        captured["emitted_updates"].append(
            {
                "sheet": kwargs["sheet"],
                "stamp": kwargs["stamp"],
            }
        )

    def _commit(*args, **kwargs):
        calls.append("commit")
        result = commit_operations(*args, **kwargs)
        captured["commit_metadata_at_creation"].clear()
        captured["commit_metadata_at_creation"].update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    def _normalize(package):
        calls.append("normalize")
        chatbot_module.workspace_state.normalize_package_state(package)

    def _save(**kwargs):
        calls.append("save")
        if save_failure is not None:
            raise save_failure
        board_task_history = kwargs["board_task_history"]
        captured["saved_operations"].extend([dict(operation) for operation in board_task_history.operations])

    def _response(**kwargs):
        calls.append("response")
        if response_builder is not None:
            return response_builder(**kwargs)
        return chatbot_module._response(**kwargs)

    return BoardTaskLocationClarificationDependencies(
        requirements_from_board_task=chatbot_module._requirements_from_board_task,
        generate_focus_candidate_message=_generate_focus_candidate_message,
        decision_trace_metadata=chatbot_module.decision_trace_metadata,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        emit_board_task_update=_emit_board_task_update,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
        commit_operations=_commit,
        build_response=_response,
    ), captured


def _run_handler(
    *,
    lesson,
    workspace,
    package,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    route_decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    deps: BoardTaskLocationClarificationDependencies,
):
    requirement_history, _ = _histories(lesson)
    lesson.board_task_requirements = board_task
    initial_stamp = board_task_history.record_update(sheet=board_task)
    return handle_board_task_location_clarification(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="解释多个候选位置", selection=_selection()),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        board_action="explain_target",
        board_task_history=board_task_history,
        board_task_stamp=initial_stamp,
        action_decision=None,
        route_decision=route_decision,
        resolution=resolution,
        requirement_history=requirement_history,
        source_interaction_metadata={"source_marker": "unit"},
        deps=deps,
    )


def test_ambiguous_location_success_keeps_active_task_metadata_and_trace_order() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    first_focus = _focus(lesson, label="第一处", excerpt="第一处内容。", segment_id="seg-one")
    second_focus = _focus(lesson, label="第二处", excerpt="第二处内容。", segment_id="seg-two")
    resolution = FocusResolution(
        focus=None,
        candidates=[first_focus, second_focus],
        status="ambiguous",
        question="找到了多个候选位置，请确认其中一个。",
        evidence=BoardSearchEvidence(status="ambiguous", reason="多个候选位置。"),
    )
    decision = _ambiguous_decision([first_focus, second_focus])
    deps, captured = _make_deps()

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            board_task_history=board_task_history,
            route_decision=decision,
            resolution=resolution,
            deps=deps,
        )

    commit = lesson.history_graph.commits[-1]
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TARGET_RESOLVE.value,
        NodeId.BOARD_ROUTE_CLARIFY_LOCATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert captured["calls"] == ["emit", "generate", "commit", "normalize", "save", "response"]
    assert response.chatbot_message == "AI生成：你想让我处理哪一段？"
    assert response.board_decision.action == "await_focus_choice"
    assert response.active_board_task_sheet is lesson.board_task_requirements
    assert response.active_board_task_sheet.location_status == "ambiguous"
    assert response.board_task_phase == "ready"
    assert len(response.focus_candidates) == 2
    assert lesson.learning_requirements is None
    assert commit.label == "Board task location clarification"
    assert commit.metadata == captured["commit_metadata_at_creation"]
    assert commit.metadata["source_marker"] == "unit"
    assert commit.metadata["board_task_route"] == "clarify_location"
    assert commit.metadata["board_task_cleared"] is False
    assert commit.metadata["board_task_sheet"]["location_status"] == "ambiguous"
    assert commit.metadata["board_task_decision"]["location_status"] == "ambiguous"
    assert commit.metadata["board_search_evidence"]["status"] == "ambiguous"
    assert commit.metadata["decision_trace"]["role_executed"] == "focus_resolver"
    assert commit.metadata["decision_trace"]["document_changed"] is False
    assert len(commit.metadata["focus_candidates"]) == 2
    assert collector.steps[0].version_id != collector.steps[2].version_id
    assert collector.steps[1].decision == "ambiguous"
    assert collector.steps[1].reason == resolution.question
    assert collector.steps[2].decision == "ambiguous"
    assert collector.steps[2].commit_id == commit.id
    assert captured["emitted_updates"][0]["sheet"].location_status == "ambiguous"
    assert any(
        operation.get("type") == "insert_board_task_event" and operation.get("event_type") == "ready"
        for operation in captured["saved_operations"]
    )


def test_missing_location_success_uses_fallback_resolution_without_candidates() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps()
    decision = _missing_decision()

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            board_task_history=board_task_history,
            route_decision=decision,
            resolution=None,
            deps=deps,
        )

    generated_resolution = captured["generated_resolutions"][0]
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TARGET_RESOLVE.value,
        NodeId.BOARD_ROUTE_CLARIFY_LOCATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert generated_resolution.status == "missing"
    assert generated_resolution.question == decision.reason
    assert generated_resolution.candidates == []
    assert response.active_board_task_sheet.location_status == "missing"
    assert response.focus_candidates == []
    assert lesson.history_graph.commits[-1].metadata["board_search_evidence"] is None
    assert collector.steps[1].decision == "missing"
    assert collector.steps[2].decision == "missing"


def test_unresolved_edit_conversion_remains_delegated_to_separate_handler() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    _, board_task_history = _histories(lesson)
    board_task = _board_task(action="edit", failure_count=1)
    decision = _missing_decision()
    deps, captured = _make_deps()

    assert is_normal_location_clarification(board_task=_board_task(action="edit"), route_decision=decision) is True
    assert is_normal_location_clarification(board_task=board_task, route_decision=decision) is False

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="unresolved edit conversion is delegated"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=board_task,
                board_task_history=board_task_history,
                route_decision=decision,
                resolution=None,
                deps=deps,
            )

    assert _node_values(collector) == []
    assert captured["calls"] == []
    assert lesson.history_graph.commits[-1].id == initial_commit_id


def test_save_failure_stops_before_clarification_trace_or_response() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)

    def _raise_response(**kwargs) -> NoReturn:
        raise AssertionError("response should not be built after save failure")

    deps, captured = _make_deps(response_builder=_raise_response, save_failure=RuntimeError("save failed"))

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                board_task_history=board_task_history,
                route_decision=_missing_decision(),
                resolution=None,
                deps=deps,
            )

    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TARGET_RESOLVE.value,
    ]
    assert captured["calls"] == ["emit", "generate", "commit", "normalize", "save"]
    assert NodeId.BOARD_ROUTE_CLARIFY_LOCATION.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
