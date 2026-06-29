from __future__ import annotations

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
)
from app.services import workspace_state
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations
from app.services.openai_course_ai import (
    bind_board_model_selection,
    bind_text_model_selection,
    openai_course_ai,
)
from app.services.route_context import bind_ai_request_context


RESET_METADATA_KIND = "product_workflow_reset_chat"


def _conversation_summary(conversation: list[ConversationTurn], *, limit: int = 1600) -> str:
    lines = [f"{turn.role}: {turn.content.strip()}" for turn in conversation if turn.content.strip()]
    summary = "\n".join(lines[-8:])
    return summary[-limit:] if len(summary) > limit else summary


def _resource_summary(package, *, lesson_id: str, limit: int = 1200) -> str:
    visible = workspace_state.resources_visible_to_lesson(
        package,
        lesson_id=lesson_id,
        isolate_lesson_resources=False,
    )
    parts: list[str] = []
    for resource in visible[:6]:
        fragments = [resource.name]
        if resource.summary:
            fragments.append(resource.summary)
        if resource.chapters:
            fragments.append(" / ".join(chapter.title for chapter in resource.chapters[:4]))
        parts.append("：".join(item for item in fragments if item))
    text = "\n".join(parts)
    return text[:limit]


def _reset_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="workflow_reset",
        reason="旧产品工作链路运行时已移除，等待新的工作链路接入。",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode="unknown",
        granularity="unclear",
    )


def _clear_legacy_runtime_state(lesson) -> None:
    lesson.learning_requirements = None
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None


def _build_response(
    *,
    workspace,
    package,
    lesson,
    chatbot_message: str,
    board_decision: BoardDecision,
) -> ChatResponse:
    requirements = effective_requirements(lesson)
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=None,
        active_interaction_session=None,
        interaction_decision=None,
        learning_clarification=_reset_clarification(),
        board_task_sheet=None,
        active_board_task_sheet=None,
        board_task_questions=[],
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        resource_matches=[],
        focus_candidates=[],
        requirement_cleared=True,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _run_reset_chat_turn(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    requirements = effective_requirements(lesson)
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary="",
        resource_summary=_resource_summary(package, lesson_id=lesson.id),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=request.selection.excerpt if request.selection else None,
        interaction_mode=request.interaction_mode,
        interaction_context={
            "turn_mode": "product_workflow_reset",
            "document_write_enabled": False,
            "board_workflow_enabled": False,
        },
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    _clear_legacy_runtime_state(lesson)
    board_decision = BoardDecision(
        action="no_change",
        reason="旧产品工作链路运行时已移除；本轮只记录聊天，不修改右侧文档。",
    )
    commit_operations(
        lesson,
        [],
        label="Workflow reset chat",
        message="Recorded a chat turn after removing the previous product workflow runtime",
        new_document=lesson.board_document,
        metadata={
            "kind": RESET_METADATA_KIND,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot" if chatbot_message else "chatbot_empty",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "legacy_workflow_state_cleared": True,
            "document_changed": False,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_for_user(user_id, workspace)
    return _build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        board_decision=board_decision,
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        trace_prefix="chat",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        with bind_text_model_selection(request.text_model):
            with bind_board_model_selection(request.board_model):
                return _run_reset_chat_turn(lesson_id, request, user_id=user_id)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    request = ChatRequest(
        message=instruction,
        interaction_mode="direct_edit",
        conversation=conversation,
    )
    return process_chat_on_lesson(lesson_id, request, user_id=user_id)
