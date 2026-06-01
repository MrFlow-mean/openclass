from __future__ import annotations

from app.models import ChatRequest, ChatResponse, ConversationTurn
from app.services.openai_course_ai import bind_text_model_selection
from app.services.route_context import bind_ai_request_context
from app.services.chatbot_flow import _chat_response
from app.services.chatbot_support import _resource_resolution_query

__all__ = ["_resource_resolution_query", "document_ai_edit_request", "process_chat_on_lesson"]


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        trace_prefix="chat",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        with bind_text_model_selection(request.text_model):
            return _chat_response(lesson_id=lesson_id, request=request, user_id=user_id)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/ai-edit",
        trace_prefix="document_ai_edit",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        request = ChatRequest(
            message=instruction,
            interaction_mode="direct_edit",
            conversation=conversation,
        )
        return _chat_response(
            lesson_id=lesson_id,
            request=request,
            user_id=user_id,
            selection_text=selection_text,
        )
