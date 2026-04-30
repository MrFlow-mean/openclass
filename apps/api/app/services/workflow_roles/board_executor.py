from __future__ import annotations

from app.services.ai_workflow import (
    WorkflowState,
    _bound_board_teaching_guide,
    _extract_focus_terms,
    _fallback_document_update,
    _interactive_teaching_guide,
    _is_full_rewrite_request,
    _merge_selection_edit,
    _reference_payload,
    _resolve_board_teaching_guide,
)
from app.services.course_runtime import build_lesson_for_topic
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import (
    append_html_section,
    build_document,
    document_changed,
    html_to_text,
    replace_selection_in_document,
)


def run_board_executor(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    selected_reference = state.get("selected_reference")

    if decision.action in {"clarify_request", "await_scope_choice", "await_reference_choice"}:
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": None,
        }

    if decision.action == "no_change":
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": _resolve_board_teaching_guide(
                lesson=lesson,
                request=request,
                requirements=requirements,
                document=lesson.board_document,
                prefer_existing=True,
                selected_reference=selected_reference,
            ),
        }

    if decision.action == "create_new_lesson":
        topic = _extract_focus_terms(request.message)[0] if _extract_focus_terms(request.message) else request.message
        generated_lesson = build_lesson_for_topic(
            topic,
            requirements=requirements,
            reference_context=selected_reference,
        )
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=generated_lesson,
            request=request,
            requirements=requirements,
            document=generated_lesson.board_document,
            prefer_existing=True,
            selected_reference=selected_reference,
        )
        generated_lesson.board_teaching_guide = board_teaching_guide
        if generated_lesson.history_graph.commits:
            generated_lesson.history_graph.commits[-1].metadata["board_teaching_guide"] = board_teaching_guide.model_dump(mode="json")
        return {
            "teaching_guide": generated_lesson.teaching_guide,
            "teacher_document": generated_lesson.board_document,
            "document_updated": True,
            "generated_lesson": generated_lesson,
            "teacher_talk_track": None,
            "board_teaching_guide": board_teaching_guide,
        }

    ai_edit = openai_course_ai.generate_document_edit(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=lesson.history_graph.current_branch,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        interaction_mode=request.interaction_mode,
        scope_action=request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=_reference_payload(selected_reference, include_full_text=True),
    )

    if ai_edit is not None:
        replacement_doc = build_document(
            title=ai_edit.suggested_title or lesson.board_document.title,
            content_html=ai_edit.replacement_html,
            content_text=ai_edit.replacement_text or None,
            document_id=lesson.board_document.id,
        )
        if (
            request.selection
            and request.interaction_mode == "direct_edit"
            and not _is_full_rewrite_request(request.message)
        ):
            replacement_text = _merge_selection_edit(
                selection_text=request.selection.excerpt,
                generated_text=replacement_doc.content_text or html_to_text(ai_edit.replacement_html),
                request_message=request.message,
            )
            next_document = replace_selection_in_document(
                lesson.board_document,
                selection_text=request.selection.excerpt,
                replacement_text=replacement_text,
            )
        elif decision.action == "append_section" and not ai_edit.replace_whole:
            next_document = append_html_section(lesson.board_document, replacement_doc.content_html)
        else:
            next_document = replacement_doc
        teacher_talk_track = ai_edit.teacher_talk_track.strip() or None
        board_teaching_guide = _bound_board_teaching_guide(
            guidance=ai_edit.board_teaching_guide,
            document=next_document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    else:
        next_document = _fallback_document_update(
            lesson=lesson,
            request=request,
            decision=decision,
            requirements=requirements,
            selected_reference=selected_reference,
        )
        teacher_talk_track = None
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=lesson,
            request=request,
            requirements=requirements,
            document=next_document,
            prefer_existing=False,
            selected_reference=selected_reference,
        )

    guide = _interactive_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=next_document,
        requirements=requirements,
    )
    return {
        "teaching_guide": guide,
        "teacher_document": next_document,
        "document_updated": document_changed(lesson.board_document, next_document),
        "generated_lesson": None,
        "teacher_talk_track": teacher_talk_track,
        "board_teaching_guide": board_teaching_guide,
    }
