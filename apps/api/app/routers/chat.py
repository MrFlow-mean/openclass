from __future__ import annotations

from fastapi import APIRouter

from app.models import ChatRequest, ChatResponse
from app.services.chat_service import process_chat_on_lesson

router = APIRouter()


@router.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(lesson_id: str, request: ChatRequest) -> ChatResponse:
    return process_chat_on_lesson(lesson_id, request)
