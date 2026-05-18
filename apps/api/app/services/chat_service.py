from __future__ import annotations

from app.models import ChatRequest, ChatResponse, ConversationTurn
from app.services.chatbot import document_ai_edit_request as _document_ai_edit_request
from app.services.chatbot import process_chat_on_lesson as _process_chat_on_lesson


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    return _process_chat_on_lesson(lesson_id, request, user_id=user_id)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    return _document_ai_edit_request(
        lesson_id,
        instruction,
        selection_text,
        conversation,
        user_id=user_id,
    )
