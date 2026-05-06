from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket

from app.models import (
    GoogleRealtimeSessionRequest,
    GoogleRealtimeSessionResponse,
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeTranscriptLogRequest,
    RealtimeTranscriptLogResponse,
    RealtimeTranscriptTurn,
    UserView,
)
from app.routers.auth import current_user
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_model_catalog import default_realtime_selection
from app.services.openai_course_ai import openai_course_ai
from app.services.openai_realtime import openai_realtime_teacher
from app.services.route_context import bind_ai_request_context
from app.services.rich_document import is_document_empty
from app.services.workspace_state import find_lesson_package, load_workspace_for_user, package_view_for_lesson, save_workspace_for_user

router = APIRouter()


def _lesson_for_user(lesson_id: str, user_id: str):
    workspace = load_workspace_for_user(user_id)
    return find_lesson_package(workspace, lesson_id)


def _conversation_from_realtime_transcript(lesson) -> list[dict[str, str]]:
    return [
        {"role": turn.role, "content": turn.content}
        for turn in lesson.realtime_transcript[-24:]
        if turn.content.strip()
    ]


def _last_user_message(conversation: list[dict[str, str]]) -> str:
    for turn in reversed(conversation):
        if turn["role"] == "user" and turn["content"].strip():
            return turn["content"].strip()
    return ""


def _openai_only_realtime_error() -> HTTPException:
    return HTTPException(status_code=400, detail="当前仅支持 OpenAI 实时语音模型")


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    _, lesson = _lesson_for_user(lesson_id, user.id)
    selection = default_realtime_selection()
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/connect",
        lesson=lesson,
        trace_prefix="realtime",
        client_session_id=request.client_session_id,
        realtime_provider=selection.provider,
        realtime_model=selection.model,
    ):
        try:
            answer_sdp = openai_realtime_teacher.create_call(
                lesson=lesson,
                offer_sdp=request.offer_sdp,
                latest_assistant_message=request.latest_assistant_message,
                model_selection=selection,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RealtimeConnectResponse(
        answer_sdp=answer_sdp,
        provider="openai",
        model=selection.model,
        voice=openai_realtime_teacher.config.voice,
    )


@router.post("/api/lessons/{lesson_id}/realtime/google/session", response_model=GoogleRealtimeSessionResponse)
def create_google_realtime_session(
    lesson_id: str,
    request: GoogleRealtimeSessionRequest,
    user: UserView = Depends(current_user),
) -> GoogleRealtimeSessionResponse:
    _ = lesson_id, request, user
    raise _openai_only_realtime_error()


async def _send_ws_error(websocket: WebSocket, *, code: int, status: str, message: str) -> None:
    await websocket.send_json({"error": {"code": code, "status": status, "message": message}})


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    await websocket.accept()
    _ = lesson_id
    await _send_ws_error(
        websocket,
        code=400,
        status="UNSUPPORTED_REALTIME_PROVIDER",
        message="当前仅支持 OpenAI 实时语音模型",
    )
    await websocket.close(code=1008, reason="Only OpenAI realtime is supported")


@router.post("/api/lessons/{lesson_id}/realtime/events", response_model=RealtimeTranscriptLogResponse)
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> RealtimeTranscriptLogResponse:
    workspace = load_workspace_for_user(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    direction = "input" if request.role == "user" else "output"
    normalized_transcript = request.transcript.strip()
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/events",
        lesson=lesson,
        trace_prefix="realtime_event",
        client_session_id=request.client_session_id,
        transport_event_type=request.transport_event_type,
    ):
        ai_usage_logger.log_event(
            "realtime_transcript",
            client_session_id=request.client_session_id,
            lesson_title=request.lesson_title,
            role=request.role,
            transport_event_type=request.transport_event_type,
            transcript=normalized_transcript,
        )
        log_ai_interaction_message(
            channel="voice",
            direction=direction,
            role=request.role,
            transport="realtime_voice",
            content=normalized_transcript,
            metadata={
                "client_session_id": request.client_session_id,
                "lesson_title": request.lesson_title,
                "transport_event_type": request.transport_event_type,
            },
        )
        if normalized_transcript:
            lesson.realtime_transcript.append(
                RealtimeTranscriptTurn(
                    role=request.role,
                    content=normalized_transcript,
                    client_session_id=request.client_session_id,
                    transport_event_type=request.transport_event_type,
                )
            )
            lesson.realtime_transcript = lesson.realtime_transcript[-80:]

        conversation = _conversation_from_realtime_transcript(lesson)
        latest_user_message = _last_user_message(conversation)
        assessment = None
        if latest_user_message:
            assessment = openai_course_ai.assess_learning_requirements(
                lesson_title=lesson.title,
                lesson_summary=lesson.summary,
                lesson_tags=lesson.tags,
                document_outline=None,
                board_is_empty=is_document_empty(lesson.board_document),
                user_message=latest_user_message,
                selection_excerpt=None,
                conversation=conversation,
            )
        ready_for_next_step = False
        if assessment is not None:
            lesson.learning_requirements = assessment.learning_requirement_sheet
            lesson.summary = assessment.learning_requirement_sheet.learning_goal
            ready_for_next_step = assessment.ready
            ai_usage_logger.log_event(
                "realtime_pm_requirements_sync",
                client_session_id=request.client_session_id,
                ready=assessment.ready,
                reason=assessment.reason,
                learning_requirement_sheet=assessment.learning_requirement_sheet,
            )
        save_workspace_for_user(user.id, workspace)

    return RealtimeTranscriptLogResponse(
        status="ok",
        learning_requirement_sheet=lesson.learning_requirements,
        ready_for_next_step=ready_for_next_step,
        course_package=package_view_for_lesson(workspace, package, lesson.id),
    )
