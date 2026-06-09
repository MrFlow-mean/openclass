from __future__ import annotations

from typing import Any

from app.services.board_task_decider import BoardTaskActionDecision
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.sequence_planner import SequencePlan
from app.services.turn_intent import IntentSignals, extract_intent_signals


def decision_trace_metadata(
    *,
    message: str,
    signals: IntentSignals | None = None,
    board_action_decision: BoardTaskActionDecision | None = None,
    route_decision: BoardTaskRouteDecision | None = None,
    sequence_plan: SequencePlan | None = None,
    role_executed: str,
    document_changed: bool,
    reason: str = "",
    target_scope: str | None = None,
) -> dict[str, object]:
    resolved_signals = signals or extract_intent_signals(message)
    trace = {
        "intent_signals": _intent_signals_payload(resolved_signals),
        "matched_rules": sorted(resolved_signals.raw_matches),
        "raw_matches": {key: list(value) for key, value in sorted(resolved_signals.raw_matches.items())},
        "selected_board_action": board_action_decision.board_action if board_action_decision else None,
        "board_action_reason": board_action_decision.reason if board_action_decision else "",
        "board_action_decision": _board_action_decision_payload(board_action_decision),
        "route_decision": _route_decision_payload(route_decision),
        "target_scope": _target_scope(
            explicit_scope=target_scope,
            route_decision=route_decision,
            sequence_plan=sequence_plan,
        ),
        "sequence_mode": sequence_plan.mode if sequence_plan else None,
        "sequence_planner": sequence_plan.planner_name if sequence_plan else None,
        "role_executed": role_executed,
        "document_changed": document_changed,
        "reason": reason
        or (sequence_plan.reason if sequence_plan else "")
        or (route_decision.reason if route_decision else "")
        or (board_action_decision.reason if board_action_decision else ""),
    }
    return {"decision_trace": trace}


def _intent_signals_payload(signals: IntentSignals) -> dict[str, bool]:
    return {
        "wants_write": signals.wants_write,
        "wants_edit": signals.wants_edit,
        "wants_explain": signals.wants_explain,
        "wants_append": signals.wants_append,
        "wants_expand": signals.wants_expand,
        "wants_simplify": signals.wants_simplify,
        "wants_rewrite": signals.wants_rewrite,
        "wants_resource": signals.wants_resource,
        "wants_sequence": signals.wants_sequence,
        "wants_collection": signals.wants_collection,
        "wants_whole_document": signals.wants_whole_document,
        "has_single_target": signals.has_single_target,
        "has_target_hint": signals.has_target_hint,
        "wants_chat": signals.wants_chat,
        "wants_strong_explain": signals.wants_strong_explain,
        "wants_explicit_resource": signals.wants_explicit_resource,
        "wants_learning_start": signals.wants_learning_start,
        "wants_document_artifact": signals.wants_document_artifact,
    }


def _board_action_decision_payload(decision: BoardTaskActionDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "board_action": decision.board_action,
        "reason": decision.reason,
        "write_allowed": decision.write_allowed,
        "requires_resource_resolution": decision.requires_resource_resolution,
        "requires_target_resolution": decision.requires_target_resolution,
        "decision_notes": list(decision.decision_notes),
    }


def _route_decision_payload(decision: BoardTaskRouteDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "route": decision.route,
        "location_status": decision.location_status,
        "reason": decision.reason,
        "target_scope": decision.target_scope,
        "has_target_focus": decision.target_focus is not None,
        "candidate_count": len(decision.candidate_focuses),
        "has_write_proposal": bool(decision.write_proposal.strip()),
    }


def _target_scope(
    *,
    explicit_scope: str | None,
    route_decision: BoardTaskRouteDecision | None,
    sequence_plan: SequencePlan | None,
) -> str | None:
    if explicit_scope:
        return explicit_scope
    if sequence_plan is not None:
        return sequence_plan.scope_label
    if route_decision is None:
        return None
    if route_decision.target_scope:
        return route_decision.target_scope
    if route_decision.target_focus is not None:
        return "focus"
    if route_decision.candidate_focuses:
        return "candidate_set"
    return None
