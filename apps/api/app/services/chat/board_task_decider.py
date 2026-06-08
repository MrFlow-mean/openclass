from __future__ import annotations

from app.models import BoardTaskAction, ChatRequest, LearningRequirementSheet
from app.services.chat.turn_intent import extract_intent_signals


EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}


def requests_append_section(text: str) -> bool:
    return extract_intent_signals(text).wants_append


def is_followup_execution_request(text: str) -> bool:
    return extract_intent_signals(text).is_followup_execution


def has_explicit_resource_reference(text: str) -> bool:
    return extract_intent_signals(text).has_explicit_resource_reference


def should_force_explain_task(message: str) -> bool:
    signals = extract_intent_signals(message)
    if not signals.wants_explanation:
        return False
    has_write_intent = signals.wants_append or signals.wants_expand
    if has_write_intent and not signals.wants_strong_explanation:
        return False
    return True


def infer_board_task_action(request: ChatRequest, *, has_selection: bool, document_empty: bool) -> BoardTaskAction | None:
    if request.board_generation_action == "start":
        return "generate_board"
    signals = extract_intent_signals(request.message)
    message = signals.compact
    if request.interaction_mode == "direct_edit":
        if signals.wants_append:
            return "append_section"
        if signals.wants_simplify:
            return "simplify_target"
        if signals.wants_expand:
            return "expand_target"
        return "rewrite_target"
    if not has_selection and signals.has_explicit_resource_reference:
        return None
    if not document_empty and should_force_explain_task(message):
        return "explain_target"
    if signals.wants_append and not document_empty:
        return "append_section"
    if not document_empty and signals.wants_simplify:
        return "simplify_target"
    if not document_empty and signals.wants_expand:
        return "expand_target"
    if signals.wants_rewrite:
        if signals.wants_simplify:
            return "simplify_target"
        if signals.wants_expand:
            return "expand_target"
        return "rewrite_target"
    if has_selection and not document_empty:
        if signals.wants_simplify:
            return "simplify_target"
        if signals.wants_expand:
            return "expand_target"
    if should_force_explain_task(message) and (has_selection or signals.has_target_location_hint):
        return "explain_target"
    if not has_selection and signals.has_resource_reference_hint:
        return None
    if has_selection and not document_empty:
        return "explain_target"
    return None


def _requirements_imply_append(requirements: LearningRequirementSheet) -> bool:
    if requirements.action_type == "append_section":
        return True
    action_text = " ".join(
        part
        for part in [
            requirements.action_instruction,
            requirements.learning_goal,
            *requirements.learning_need_checklist,
        ]
        if part
    )
    return requests_append_section(action_text)


def prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
    *,
    request_message: str,
    requirements: LearningRequirementSheet,
) -> BoardTaskAction | None:
    if inferred is None and is_followup_execution_request(request_message) and _requirements_imply_append(requirements):
        return "append_section"
    if requirement_action == "append_section":
        return requirement_action
    if requirement_action in EDIT_ACTIONS:
        return requirement_action
    if requirement_action == "explain_target" and inferred is None:
        return requirement_action
    return inferred
