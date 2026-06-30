from __future__ import annotations

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
)
from app.services import workspace_state
from app.services.board_document_sensor import read_board_document_sensor
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations
from app.services.learning_requirement_history import RequirementHistoryStamp
from app.services.learning_requirement_refiner import refine_blank_board_requirement
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import (
    bind_board_model_selection,
    bind_text_model_selection,
    openai_course_ai,
)
from app.services.route_context import bind_ai_request_context


BASIC_CHAT_METADATA_KIND = "basic_chat"
LEARNING_REQUIREMENT_REFINEMENT_METADATA_KIND = "learning_requirement_refinement"


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


def _clear_board_task_runtime_state(lesson) -> None:
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None


def _is_default_learning_requirement_sheet(lesson) -> bool:
    if lesson.learning_requirements is None:
        return False
    default_requirements = build_requirements(lesson.title)
    return (
        lesson.learning_requirements.model_dump(mode="json")
        == default_requirements.model_dump(mode="json")
    )


def _build_response(
    *,
    workspace,
    package,
    lesson,
    chatbot_message: str,
    board_decision: BoardDecision,
    active_requirement_sheet=None,
    learning_clarification: LearningClarificationStatus | None = None,
    requirement_stamp: RequirementHistoryStamp | None = None,
    requirement_cleared: bool = True,
) -> ChatResponse:
    requirements = effective_requirements(lesson)
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=active_requirement_sheet,
        active_interaction_session=None,
        interaction_decision=None,
        learning_clarification=learning_clarification or _reset_clarification(),
        requirement_run_id=requirement_stamp.run_id if requirement_stamp else None,
        requirement_version_id=requirement_stamp.version_id if requirement_stamp else None,
        requirement_phase=requirement_stamp.phase if requirement_stamp else None,
        board_task_sheet=None,
        active_board_task_sheet=None,
        board_task_questions=[],
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        resource_matches=[],
        focus_candidates=[],
        requirement_cleared=requirement_cleared,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _run_basic_chat_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
    clear_learning_requirements: bool,
) -> ChatResponse:
    ai_reply = openai_course_ai.generate_basic_chat_reply(
        board_document_state=board_document_state.model_context(),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    if clear_learning_requirements:
        _clear_legacy_runtime_state(lesson)
        active_requirement_sheet = None
    else:
        if _is_default_learning_requirement_sheet(lesson):
            lesson.learning_requirements = None
        _clear_board_task_runtime_state(lesson)
        active_requirement_sheet = lesson.learning_requirements
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
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": True,
            "document_changed": False,
            "active_requirement_sheet_after": (
                active_requirement_sheet.model_dump(mode="json")
                if active_requirement_sheet is not None
                else None
            ),
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
        active_requirement_sheet=active_requirement_sheet,
        requirement_cleared=active_requirement_sheet is None,
    )


def _run_requirement_refinement_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
) -> ChatResponse:
    history_state = workspace_state.load_learning_requirement_history_state_for_user(user_id, lesson.id)
    outcome = refine_blank_board_requirement(
        owner_user_id=user_id,
        lesson=lesson,
        board_document_state=board_document_state,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        history_state=history_state,
    )
    if outcome is None:
        return _run_basic_chat_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
            clear_learning_requirements=history_state is None,
        )

    if outcome.active_requirement_sheet is None:
        lesson.learning_requirements = None
    else:
        lesson.learning_requirements = outcome.active_requirement_sheet
    _clear_board_task_runtime_state(lesson)
    board_decision = BoardDecision(
        action="no_change",
        reason="空白板书学习需求收敛只维护清单，不修改右侧文档。",
    )
    metadata_kind = (
        LEARNING_REQUIREMENT_REFINEMENT_METADATA_KIND
        if outcome.route == "requirement_refining"
        else BASIC_CHAT_METADATA_KIND
    )
    commit_operations(
        lesson,
        [],
        label="Learning requirement refinement" if outcome.route == "requirement_refining" else "Basic chat",
        message=(
            "Recorded a blank-board learning requirement refinement turn"
            if outcome.route == "requirement_refining"
            else "Recorded a basic chatbot conversation turn"
        ),
        new_document=lesson.board_document,
        metadata={
            "kind": metadata_kind,
            "refinement_route": outcome.route,
            "user_message": request.message,
            "assistant_message": outcome.chatbot_message,
            "assistant_message_source": "chatbot" if outcome.chatbot_message else "chatbot_empty",
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": outcome.route == "ordinary_chat",
            "document_changed": False,
            "active_requirement_sheet_after": (
                outcome.active_requirement_sheet.model_dump(mode="json")
                if outcome.active_requirement_sheet is not None
                else None
            ),
            "learning_clarification_after": outcome.learning_clarification.model_dump(mode="json"),
            "guided_requirement_discovery": outcome.guidance_metadata,
            "requirement_run_id": outcome.history_stamp.run_id,
            "requirement_version_id": outcome.history_stamp.version_id,
            "requirement_phase": outcome.history_stamp.phase,
            "requirement_history_changed": outcome.changed,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_and_learning_requirement_history_for_user(
        user_id,
        workspace,
        learning_requirement_history_operations=outcome.history_operations,
    )
    return _build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=outcome.chatbot_message,
        board_decision=board_decision,
        active_requirement_sheet=outcome.active_requirement_sheet,
        learning_clarification=outcome.learning_clarification,
        requirement_stamp=outcome.history_stamp,
        requirement_cleared=outcome.active_requirement_sheet is None,
    )


def _run_chat_turn(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    board_document_state = read_board_document_sensor(lesson.board_document)
    if board_document_state.status == "empty":
        return _run_requirement_refinement_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
        )
    return _run_basic_chat_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        request=request,
        user_id=user_id,
        board_document_state=board_document_state,
        clear_learning_requirements=False,
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
                return _run_chat_turn(lesson_id, request, user_id=user_id)


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
