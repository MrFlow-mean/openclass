from __future__ import annotations

from app.models import ChatRequest, ChatResponse, ConversationTurn, SelectionRef, now_iso
from app.services.ai_workflow import WorkflowResult, course_workflow
from app.services.openai_course_ai import bind_text_model_selection
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import (
    commit_document_snapshot,
    find_lesson_package,
    load_workspace_for_user,
    package_context_for_lesson,
    package_view_for_lesson,
    save_workspace_for_user,
)


def _response_from_result(result: WorkflowResult, course_package) -> ChatResponse:
    return ChatResponse(
        teacher_message=result.teacher_message,
        learning_requirement_sheet=result.learning_requirement_sheet,
        learning_clarification=result.learning_clarification,
        board_decision=result.board_decision,
        needs_clarification=result.needs_clarification,
        clarification_questions=result.clarification_questions,
        patch_proposal=result.patch_proposal,
        scope_options=result.scope_options,
        resource_matches=result.resource_matches,
        reference_prompt=result.reference_prompt,
        board_edit_prompt=result.board_edit_prompt,
        selected_reference=result.selected_reference,
        created_lesson=None,
        teaching_progress=result.teaching_progress,
        course_package=course_package,
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = load_workspace_for_user(user_id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    workspace.active_package_id = package.id
    visible_package = package_context_for_lesson(workspace, package, lesson.id)

    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        lesson=lesson,
        trace_prefix="chat",
    ), bind_text_model_selection(request.text_model):
        result = course_workflow.invoke(
            {
                "lesson": lesson,
                "request": request,
                "resources": visible_package.resources,
            }
        )
        if result.document_changed:
            commit_document_snapshot(
                lesson,
                label=result.commit_label or "Workflow board update",
                message=result.commit_message or "Updated board from the integrated workflow",
                metadata=result.commit_metadata or {"kind": "workflow_board_update"},
            )
        else:
            lesson.updated_at = now_iso()
        save_workspace_for_user(user_id, workspace)

    return _response_from_result(result, package_view_for_lesson(workspace, package, lesson.id))


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    selection = (
        SelectionRef(kind="board", excerpt=selection_text, lesson_id=lesson_id)
        if selection_text and selection_text.strip()
        else None
    )
    request = ChatRequest(
        message=instruction,
        selection=selection,
        interaction_mode="direct_edit",
        conversation=conversation,
    )
    return process_chat_on_lesson(lesson_id, request, user_id=user_id)
