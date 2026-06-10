from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BoardGenerationAction, BoardTaskAction, BoardTaskRequestedAction, ChatInteractionMode
from app.services.turn_intent import (
    IntentSignals,
    compact_text,
    extract_intent_signals,
    has_explicit_resource_reference as detect_explicit_resource_reference,
    infer_board_task_requested_action,
    should_force_explain_task,
)


EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}


@dataclass(frozen=True)
class BoardTaskActionDecision:
    board_action: BoardTaskAction | None
    reason: str
    write_allowed: bool
    requires_resource_resolution: bool
    requires_target_resolution: bool
    decision_notes: list[str] = field(default_factory=list)


def _decision(
    board_action: BoardTaskAction | None,
    *,
    reason: str,
    requires_resource_resolution: bool = False,
    requires_target_resolution: bool = False,
    decision_notes: list[str] | None = None,
) -> BoardTaskActionDecision:
    return BoardTaskActionDecision(
        board_action=board_action,
        reason=reason,
        write_allowed=board_action in DOCUMENT_WRITE_ACTIONS or board_action == "generate_board",
        requires_resource_resolution=requires_resource_resolution,
        requires_target_resolution=requires_target_resolution,
        decision_notes=decision_notes or [],
    )


def decide_board_task_action(
    *,
    message: str,
    signals: IntentSignals | None,
    has_selection: bool,
    document_empty: bool,
    interaction_mode: ChatInteractionMode,
    board_generation_action: BoardGenerationAction | None,
    has_explicit_resource_reference: bool | None,
) -> BoardTaskActionDecision:
    compact_message = compact_text(message, limit=280)
    resolved_signals = signals or extract_intent_signals(compact_message)
    explicit_resource_reference = (
        detect_explicit_resource_reference(compact_message)
        if has_explicit_resource_reference is None
        else has_explicit_resource_reference
    )
    notes: list[str] = []

    if board_generation_action == "start":
        return _decision(
            "generate_board",
            reason="board_generation_action=start",
            decision_notes=["explicit_board_generation_action"],
        )

    if interaction_mode == "direct_edit":
        notes.append("direct_edit_priority")
        if resolved_signals.wants_append:
            return _decision(
                "append_section",
                reason="direct_edit append intent",
                decision_notes=notes,
            )
        if document_empty:
            return _decision(
                None,
                reason="direct_edit cannot edit an empty board document",
                decision_notes=[*notes, "document_empty"],
            )
        if resolved_signals.wants_simplify:
            return _decision(
                "simplify_target",
                reason="direct_edit simplify intent",
                requires_target_resolution=True,
                decision_notes=notes,
            )
        if resolved_signals.wants_expand:
            return _decision(
                "expand_target",
                reason="direct_edit expand intent",
                requires_target_resolution=True,
                decision_notes=notes,
            )
        return _decision(
            "rewrite_target",
            reason="direct_edit default rewrite",
            requires_target_resolution=True,
            decision_notes=notes,
        )

    if not has_selection and explicit_resource_reference:
        return _decision(
            None,
            reason="explicit resource reference without board selection",
            requires_resource_resolution=True,
            decision_notes=["resource_reference_before_board_task"],
        )

    if document_empty:
        return _decision(
            None,
            reason="empty board document has no existing-board target",
            decision_notes=["document_empty"],
        )

    if should_force_explain_task(compact_message):
        return _decision(
            "explain_target",
            reason="strong explain intent on existing board",
            requires_target_resolution=not has_selection,
            decision_notes=["force_explain"],
        )

    if resolved_signals.wants_append:
        return _decision(
            "append_section",
            reason="append intent on existing board",
            decision_notes=["append"],
        )

    if resolved_signals.wants_simplify:
        return _decision(
            "simplify_target",
            reason="simplify intent on existing board",
            requires_target_resolution=not has_selection,
            decision_notes=["simplify"],
        )

    if resolved_signals.wants_expand:
        return _decision(
            "expand_target",
            reason="expand intent on existing board",
            requires_target_resolution=not has_selection,
            decision_notes=["expand"],
        )

    if resolved_signals.wants_rewrite:
        if resolved_signals.wants_simplify:
            return _decision(
                "simplify_target",
                reason="rewrite intent refined to simplify",
                requires_target_resolution=not has_selection,
                decision_notes=["rewrite", "simplify"],
            )
        if resolved_signals.wants_expand:
            return _decision(
                "expand_target",
                reason="rewrite intent refined to expand",
                requires_target_resolution=not has_selection,
                decision_notes=["rewrite", "expand"],
            )
        return _decision(
            "rewrite_target",
            reason="rewrite intent on existing board",
            requires_target_resolution=not has_selection,
            decision_notes=["rewrite"],
        )

    if has_selection:
        return _decision(
            "explain_target",
            reason="board selection defaults to explain",
            requires_target_resolution=False,
            decision_notes=["selection_default_explain"],
        )

    if resolved_signals.wants_resource:
        return _decision(
            None,
            reason="resource hint without selection waits for resource resolution",
            requires_resource_resolution=True,
            decision_notes=["resource_hint_before_board_task"],
        )

    return _decision(
        None,
        reason="no board task action signal",
        decision_notes=["no_action"],
    )


def decide_board_task_requested_action(
    *,
    message: str,
    decision: BoardTaskActionDecision,
) -> BoardTaskRequestedAction | None:
    requested_action = infer_board_task_requested_action(message)
    if requested_action is not None:
        return requested_action
    if decision.board_action == "append_section":
        return "write"
    if decision.board_action in {"rewrite_target", "simplify_target"}:
        return "edit"
    if decision.board_action == "expand_target":
        return "write"
    if decision.board_action == "explain_target" and "selection_default_explain" not in decision.decision_notes:
        return "explain"
    return None
