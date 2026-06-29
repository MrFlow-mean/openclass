from __future__ import annotations

from app.models import ChatRequest, ChatResponse
from app.services.chatbot import process_chat_on_lesson as _process_chat_on_lesson


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    return _process_chat_on_lesson(lesson_id, request, user_id=user_id)
