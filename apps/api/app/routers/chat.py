from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models import ChatRequest, ChatResponse, UserView
from app.routers.auth import current_user
from app.services.chat_service import process_chat_on_lesson

router = APIRouter()


@router.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    user: UserView = Depends(current_user),
) -> ChatResponse:
    return process_chat_on_lesson(lesson_id, request, user_id=user.id)
