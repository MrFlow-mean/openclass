from __future__ import annotations

from fastapi import HTTPException

from app.models import ChatRequest, ChatResponse, ConversationTurn
from app.services.ai_workflow import WORKFLOW_REMOVED_DETAIL


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    raise HTTPException(status_code=410, detail=WORKFLOW_REMOVED_DETAIL)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    raise HTTPException(status_code=410, detail=WORKFLOW_REMOVED_DETAIL)
