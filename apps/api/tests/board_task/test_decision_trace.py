from __future__ import annotations

import json

from app.services import turn_intent
from app.services.board_task_decider import decide_board_task_action
from app.services.decision_trace import decision_trace_metadata
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.sequence_planner import SequencePlan


def _action_decision(
    message: str,
    *,
    has_selection: bool = False,
    document_empty: bool = False,
):
    signals = turn_intent.extract_intent_signals(message)
    return decide_board_task_action(
        message=message,
        signals=signals,
        has_selection=has_selection,
        document_empty=document_empty,
        interaction_mode="ask",
        board_generation_action=None,
        has_explicit_resource_reference=signals.wants_explicit_resource,
    )


def _trace(
    message: str,
    *,
    route: str | None = None,
    location_status: str = "found",
    role_executed: str,
    document_changed: bool,
    sequence_plan: SequencePlan | None = None,
    has_selection: bool = False,
):
    route_decision = (
        BoardTaskRouteDecision(
            route=route,
            location_status=location_status,
            reason=f"{route} route",
            target_scope="focus" if route in {"explain", "edit"} else "append",
        )
        if route
        else None
    )
    metadata = decision_trace_metadata(
        message=message,
        board_action_decision=_action_decision(message, has_selection=has_selection),
        route_decision=route_decision,
        sequence_plan=sequence_plan,
        role_executed=role_executed,
        document_changed=document_changed,
        reason=route_decision.reason if route_decision else "resource path",
    )
    return metadata["decision_trace"]


def test_explain_trace_marks_document_unchanged() -> None:
    trace = _trace(
        "解释这里是什么意思",
        route="explain",
        role_executed="chatbot_board_directed",
        document_changed=False,
        has_selection=True,
    )

    assert trace["selected_board_action"] == "explain_target"
    assert trace["route_decision"]["route"] == "explain"
    assert trace["role_executed"] == "chatbot_board_directed"
    assert trace["document_changed"] is False
    assert trace["intent_signals"]["wants_explain"] is True
    assert "explain" in trace["matched_rules"]


def test_append_and_edit_traces_mark_document_changed() -> None:
    append_trace = _trace(
        "继续写下一节",
        route="write",
        role_executed="board_editor",
        document_changed=True,
    )
    edit_trace = _trace(
        "润色这一段",
        route="edit",
        role_executed="board_editor",
        document_changed=True,
        has_selection=True,
    )

    assert append_trace["selected_board_action"] == "append_section"
    assert append_trace["document_changed"] is True
    assert edit_trace["selected_board_action"] == "rewrite_target"
    assert edit_trace["document_changed"] is True


def test_resource_reference_trace_stays_out_of_board_write() -> None:
    trace = _trace(
        "根据上传资料回答这个问题",
        role_executed="resource_resolver",
        document_changed=False,
    )

    assert trace["selected_board_action"] is None
    assert trace["board_action_decision"]["requires_resource_resolution"] is True
    assert trace["board_action_decision"]["write_allowed"] is False
    assert trace["role_executed"] == "resource_resolver"
    assert trace["document_changed"] is False


def test_collection_sequence_trace_includes_sequence_mode() -> None:
    plan = SequencePlan(
        mode="atomic_explanation",
        items=[],
        start_index=0,
        scope_label="练习题",
        reason="explicit_collection_explanation",
        planner_name="sequence_planner",
    )

    trace = _trace(
        "为我讲解练习题",
        route="explain",
        role_executed="chatbot_board_directed",
        document_changed=False,
        sequence_plan=plan,
    )

    assert trace["intent_signals"]["wants_collection"] is True
    assert trace["sequence_mode"] == "atomic_explanation"
    assert trace["sequence_planner"] == "sequence_planner"
    assert trace["target_scope"] == "练习题"


def test_single_target_explain_trace_has_no_collection_sequence() -> None:
    trace = _trace(
        "讲解第 2 题",
        route="explain",
        role_executed="chatbot_board_directed",
        document_changed=False,
    )

    assert trace["intent_signals"]["has_single_target"] is True
    assert trace["intent_signals"]["wants_collection"] is False
    assert trace["sequence_mode"] is None
    assert trace["sequence_planner"] is None


def test_trace_contains_no_subject_or_demo_special_cases() -> None:
    trace = _trace(
        "为我讲解练习题",
        route="explain",
        role_executed="chatbot_board_directed",
        document_changed=False,
    )
    serialized = json.dumps(trace, ensure_ascii=False)

    for banned in ["法语", "数学", "CSAPP", "高考", "demo"]:
        assert banned not in serialized
