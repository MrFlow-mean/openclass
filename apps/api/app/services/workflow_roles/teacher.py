from __future__ import annotations

from app.services.ai_workflow import (
    WorkflowState,
    _fallback_teacher_message,
    _format_teacher_message,
    _reference_payload,
    _teacher_message_from_talk_track,
)
from app.services.openai_course_ai import openai_course_ai


def run_teacher(state: WorkflowState) -> WorkflowState:
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    reference_prompt = state.get("reference_prompt")
    selected_reference = state.get("selected_reference")
    teacher_talk_track = (state.get("teacher_talk_track") or "").strip()
    board_teaching_guide = state.get("board_teaching_guide")

    if decision.action == "clarify_request":
        pm_dialogue_message = (state.get("pm_dialogue_message") or "").strip()
        if pm_dialogue_message:
            return {"teacher_message": _format_teacher_message(pm_dialogue_message)}
        ai_message = openai_course_ai.generate_clarification_message(
            lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
            request_message=request.message,
            conversation=[turn.model_dump(mode="json") for turn in request.conversation],
            requirements=requirements,
            learning_clarification=state["learning_clarification"].model_dump(mode="json"),
            clarification_questions=state.get("clarification_questions", []),
        )
        return {"teacher_message": _format_teacher_message(ai_message or _fallback_teacher_message(state))}
    if decision.action in {"await_scope_choice", "await_reference_choice"}:
        return {"teacher_message": _format_teacher_message(_fallback_teacher_message(state))}
    if teacher_talk_track and decision.action in {"edit_board", "append_section"}:
        return {"teacher_message": _format_teacher_message(_teacher_message_from_talk_track(state, teacher_talk_track))}
    if decision.action == "create_new_lesson":
        return {"teacher_message": _format_teacher_message(_fallback_teacher_message(state))}
    if board_teaching_guide is None:
        return {"teacher_message": _format_teacher_message(_fallback_teacher_message(state))}

    ai_message = openai_course_ai.generate_teacher_message(
        lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
        request_message=request.message,
        requirements=requirements,
        board_teaching_guide=board_teaching_guide,
        board_decision=decision,
        document_updated=state.get("document_updated", False),
        scope_options=state.get("scope_options", []),
        resource_matches=[match.model_dump(mode="json") for match in state.get("resource_matches", [])],
        clarification_questions=state.get("clarification_questions", []),
        reference_prompt=reference_prompt.model_dump(mode="json") if reference_prompt else None,
        selected_reference=_reference_payload(selected_reference, include_full_text=False),
    )
    return {"teacher_message": _format_teacher_message(ai_message or _fallback_teacher_message(state))}
