from __future__ import annotations

from app.models import BoardTaskAction, ChatRequest, LearningRequirementSheet
from app.services.chat.board_task_decider import (
    DOCUMENT_WRITE_ACTIONS,
    EDIT_ACTIONS,
    has_explicit_resource_reference,
    infer_board_task_action,
    is_followup_execution_request,
    prefer_requirement_action,
    requests_append_section,
    should_force_explain_task,
)
from app.services.learning_requirement_manager import is_generation_control_request
from app.services.chat.turn_intent import MAX_CONTEXT_CHARS, compact_text, extract_intent_signals


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    return compact_text(value, limit=limit)


def _requests_explanation(text: str) -> bool:
    return extract_intent_signals(text).wants_explanation


def _requests_append_section(text: str) -> bool:
    return requests_append_section(text)


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
    return infer_board_task_action(request, has_selection=has_selection, document_empty=document_empty)


def _prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
    *,
    request_message: str,
    requirements: LearningRequirementSheet,
) -> BoardTaskAction | None:
    return prefer_requirement_action(
        inferred,
        requirement_action,
        request_message=request_message,
        requirements=requirements,
    )


def _requests_document_artifact_generation(text: str) -> bool:
    return extract_intent_signals(text).wants_document_artifact_generation


def _requests_resource_backed_answer(text: str) -> bool:
    return extract_intent_signals(text).has_resource_reference_hint


def _requests_learning_start(text: str) -> bool:
    return extract_intent_signals(text).wants_learning_start


def _should_prompt_resource_reference(text: str) -> bool:
    return (
        _requests_resource_backed_answer(text)
        or _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )
