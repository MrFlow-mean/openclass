from __future__ import annotations

from app.models import ChatRequest, ChatResponse, ConversationTurn
from app.services.chatbot import document_ai_edit_request as _document_ai_edit_request
from app.services.chatbot import process_chat_on_lesson as _process_chat_on_lesson
from app.services.history import bind_commit_metadata


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    # 聊天 service 是 router 和核心编排之间的薄层，顺手把“编辑已有聊天”的来源写进 commit metadata。
    with bind_commit_metadata(_chat_edit_metadata(request)):
        return _process_chat_on_lesson(lesson_id, request, user_id=user_id)


def _chat_edit_metadata(request: ChatRequest) -> dict[str, object]:
    if not request.chat_edit_source_commit_id:
        return {}
    return {
        "chat_edit_source_commit_id": request.chat_edit_source_commit_id,
        "chat_edit_base_commit_id": request.chat_edit_base_commit_id,
        "chat_edit_original_message": request.chat_edit_original_message or "",
    }


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    # 文档 AI 编辑入口也复用同一条 chat 编排链，避免出现另一套绕过历史/定位的写入路径。
    return _document_ai_edit_request(
        lesson_id,
        instruction,
        selection_text,
        conversation,
        user_id=user_id,
    )
