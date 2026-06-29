from __future__ import annotations

from app.models import BoardDecision, ChatRequest, ChatResponse, ConversationTurn, LearningClarificationStatus, LearningRequirementSheet
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.openai_course_ai import (
    bind_board_model_selection,
    bind_text_model_selection,
    openai_course_ai,
)


RESET_METADATA_KIND = "product_workflow_reset_chat"


def _conversation_summary(conversation: list[ConversationTurn], *, limit: int = 1600) -> str:
    lines = [f"{turn.role}: {turn.content.strip()}" for turn in conversation if turn.content.strip()]
    summary = "\n".join(lines[-8:])
    return summary[-limit:] if len(summary) > limit else summary


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


def _compat_requirement_sheet(lesson) -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme=lesson.title,
        learning_goal="旧学习需求链路已清空；当前聊天框只记录基础对话。",
        level="",
        known_background="",
        current_questions=[],
        learning_need_checklist=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _board_document_state(lesson) -> dict[str, object]:
    has_content = bool(
        (lesson.board_document.content_text or "").strip()
        or (lesson.board_document.content_html or "").strip()
    )
    return {
        "status": "non_empty" if has_content else "empty",
        "is_empty": not has_content,
        "content_visibility": "status_only",
    }


def _build_response(
    *,
    workspace,
    package,
    lesson,
    chatbot_message: str,
    board_decision: BoardDecision,
) -> ChatResponse:
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=_compat_requirement_sheet(lesson),
        learning_clarification=_reset_clarification(),
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        requirement_cleared=True,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _run_reset_chat_turn(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal="",
        board_summary="",
        resource_summary="",
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
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip() or "我现在可以先做基础对话；旧产品工作链路已经清空，新的链路还没有接入。"
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
            "assistant_message_source": "chatbot",
            "board_document_state": _board_document_state(lesson),
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
    with bind_text_model_selection(request.text_model):
        with bind_board_model_selection(request.board_model):
            return _run_reset_chat_turn(lesson_id, request, user_id=user_id)
