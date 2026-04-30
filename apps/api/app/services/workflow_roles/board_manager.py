from __future__ import annotations

from app.models import BoardDecision
from app.services.ai_workflow import (
    WorkflowState,
    _board_generation_confirmation_prompt,
    _build_reference_prompt,
    _build_scope_options,
    _fallback_board_decision,
    _has_reference_intent,
    _is_board_generation_confirmation_response,
    _is_board_generation_request,
    _is_explanation_request,
    _is_forced_start_request,
    _resource_query_text,
    _selected_reference_context,
    _should_auto_attach_reference_for_direct_teaching,
    _should_use_fast_board_path,
    match_resources,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import extract_reference_context
from app.services.rich_document import is_document_empty


def run_board_manager(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    matches = match_resources(state["course_package"], lesson, request, requirements)

    if request.interaction_mode == "direct_edit":
        return {
            "board_decision": BoardDecision(action="edit_board", reason=""),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    if state.get("needs_clarification"):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason=state.get("pm_reason", "")),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    if request.board_edit_action == "confirm" or _is_board_generation_confirmation_response(request):
        return {
            "board_decision": BoardDecision(action="edit_board", reason=""),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": _selected_reference_context(state["course_package"], lesson, request, requirements),
        }

    if request.board_edit_action == "skip":
        return {
            "board_decision": BoardDecision(action="no_change", reason=""),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    if is_document_empty(lesson.board_document) and not (
        _is_board_generation_request(request.message)
        or _is_explanation_request(request.message)
        or _is_forced_start_request(request.message)
    ):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason=""),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
            "board_edit_prompt": _board_generation_confirmation_prompt(requirements),
        }

    if _should_use_fast_board_path(lesson=lesson, request=request, requirements=requirements):
        decision = _fallback_board_decision(lesson, request, requirements, matches)
    else:
        ai_decision = openai_course_ai.generate_board_decision(
            lesson_title=lesson.title,
            request_message=request.message,
            selection=request.selection.model_dump(mode="json") if request.selection else None,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            requirements=requirements,
            document=lesson.board_document,
            resource_matches=[match.model_dump(mode="json") for match in matches],
        )
        decision = ai_decision or _fallback_board_decision(lesson, request, requirements, matches)

    if is_document_empty(lesson.board_document) and (
        _is_board_generation_request(request.message)
        or _is_explanation_request(request.message)
        or _is_forced_start_request(request.message)
    ):
        decision = BoardDecision(action="edit_board", reason="")
    elif decision.action == "no_change" and _is_board_generation_request(request.message):
        decision = BoardDecision(action="edit_board", reason="")

    if decision.action == "await_scope_choice":
        return {
            "board_decision": decision,
            "scope_options": _build_scope_options(matches),
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    top_match = matches[0] if matches else None
    second_match = matches[1] if len(matches) > 1 else None
    ambiguous_reference = (
        top_match is not None
        and second_match is not None
        and top_match.is_high_overlap
        and abs(top_match.score - second_match.score) <= 0.06
    )
    reference_intent = _has_reference_intent(request)
    if (
        request.resource_reference_action is None
        and reference_intent
        and decision.action in {"edit_board", "append_section", "create_new_lesson", "no_change"}
    ):
        if ambiguous_reference and top_match is not None:
            return {
                "board_decision": BoardDecision(
                    action="await_reference_choice",
                    reason="",
                ),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": _build_reference_prompt(top_match),
                "selected_reference": None,
            }
        if top_match is not None and _should_auto_attach_reference_for_direct_teaching(
            request=request,
            decision=decision,
            top_match=top_match,
        ):
            return {
                "board_decision": decision,
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "selected_reference": extract_reference_context(
                    next(
                        resource
                        for resource in state["course_package"].resources
                        if resource.id == top_match.resource_id
                    ),
                    top_match.chapter_id,
                    user_query=_resource_query_text(lesson, request, requirements),
                ),
            }

    return {
        "board_decision": decision,
        "scope_options": [],
        "resource_matches": matches,
        "reference_prompt": None,
        "selected_reference": _selected_reference_context(state["course_package"], lesson, request, requirements),
    }
