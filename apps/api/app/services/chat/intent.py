from __future__ import annotations

from app.models import BoardTaskAction, ChatRequest, LearningRequirementSheet
from app.services.board_task_decider import decide_board_task_action
from app.services.learning_requirement_manager import is_generation_control_request
from app.services.turn_intent import (
    MAX_CONTEXT_CHARS,
    compact_text,
    extract_intent_signals,
    has_explicit_resource_reference,
    is_followup_execution_request,
    should_force_explain_task,
    wants_append,
    wants_document_artifact_generation,
    wants_explain,
    wants_learning_start,
    wants_resource_reference,
)

EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    return compact_text(value, limit=limit)


def _requests_explanation(text: str) -> bool:
    return wants_explain(text)


def _requests_append_section(text: str) -> bool:
    return wants_append(text)


def _is_followup_execution_request(text: str) -> bool:
    return is_followup_execution_request(text)


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
    return _requests_append_section(action_text)


def _has_explicit_resource_reference(text: str) -> bool:
    return has_explicit_resource_reference(text)


def _should_force_explain_task(message: str) -> bool:
    return should_force_explain_task(message)


def _infer_board_task_action(request: ChatRequest, *, has_selection: bool, document_empty: bool) -> BoardTaskAction | None:
    message = _compact_text(request.message, limit=280)
    signals = extract_intent_signals(message)
    decision = decide_board_task_action(
        message=message,
        signals=signals,
        has_selection=has_selection,
        document_empty=document_empty,
        interaction_mode=request.interaction_mode,
        board_generation_action=request.board_generation_action,
        has_explicit_resource_reference=_has_explicit_resource_reference(message),
    )
    return decision.board_action


def _prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
    *,
    request_message: str,
    requirements: LearningRequirementSheet,
) -> BoardTaskAction | None:
    if inferred is None and _is_followup_execution_request(request_message) and _requirements_imply_append(requirements):
        return "append_section"
    if requirement_action == "append_section":
        return requirement_action
    if requirement_action in EDIT_ACTIONS:
        return requirement_action
    if requirement_action == "explain_target" and inferred is None:
        return requirement_action
    return inferred


def _requests_document_artifact_generation(text: str) -> bool:
    return wants_document_artifact_generation(text)


def _requests_resource_backed_answer(text: str) -> bool:
    return wants_resource_reference(text)


def _requests_learning_start(text: str) -> bool:
    return wants_learning_start(text)


def _should_prompt_resource_reference(text: str) -> bool:
    return (
        _requests_resource_backed_answer(text)
        or _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )
