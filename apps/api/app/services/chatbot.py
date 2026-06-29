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


BASIC_CHAT_METADATA_KIND = "basic_chat"


def _conversation_summary(conversation: list[ConversationTurn], *, limit: int = 1600) -> str:
    lines = [f"{turn.role}: {turn.content.strip()}" for turn in conversation if turn.content.strip()]
    summary = "\n".join(lines[-8:])
    return summary[-limit:] if len(summary) > limit else summary


def _reset_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="basic_chat",
        reason="当前聊天框只执行基础你问我答，不进入文档工作流。",
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


def _run_basic_chat_turn(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    ai_reply = openai_course_ai.generate_basic_chat_reply(
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    _clear_legacy_runtime_state(lesson)
    board_decision = BoardDecision(
        action="no_change",
        reason="基础聊天回合不修改右侧文档。",
    )
    commit_operations(
        lesson,
        [],
        label="Basic chat",
        message="Recorded a basic chatbot conversation turn",
        new_document=lesson.board_document,
        metadata={
            "kind": BASIC_CHAT_METADATA_KIND,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot" if chatbot_message else "chatbot_empty",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": True,
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
                return _run_basic_chat_turn(lesson_id, request, user_id=user_id)


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
