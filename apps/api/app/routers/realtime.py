from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket

from app.models import (
    GoogleRealtimeSessionRequest,
    GoogleRealtimeSessionResponse,
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers.auth import current_user
from app.services.ai_logging import log_ai_interaction_message
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import find_lesson_package, load_workspace_for_user

router = APIRouter()

REALTIME_CONNECT_DETAIL = "实时语音连接层等待按新工作流重新接入；文字和转写主链路已可用。"


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    raise HTTPException(status_code=501, detail=REALTIME_CONNECT_DETAIL)


@router.post("/api/lessons/{lesson_id}/realtime/google/session", response_model=GoogleRealtimeSessionResponse)
def create_google_realtime_session(
    lesson_id: str,
    request: GoogleRealtimeSessionRequest,
    user: UserView = Depends(current_user),
) -> GoogleRealtimeSessionResponse:
    raise HTTPException(status_code=501, detail=REALTIME_CONNECT_DETAIL)


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "error": {
                "code": 501,
                "status": "REALTIME_CONNECT_PENDING",
                "message": REALTIME_CONNECT_DETAIL,
            }
        }
    )
    await websocket.close(code=1011, reason="Realtime connect pending")


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> dict[str, str]:
    workspace = load_workspace_for_user(user.id)
    _package, lesson = find_lesson_package(workspace, lesson_id)
    direction = "input" if request.role == "user" else "output"
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/events",
        lesson=lesson,
        trace_prefix="realtime",
        client_session_id=request.client_session_id,
    ):
        log_ai_interaction_message(
            channel="realtime",
            direction=direction,
            role=request.role,
            transport=request.transport_event_type,
            content=request.transcript,
            metadata={
                "lesson_title": request.lesson_title,
                "client_session_id": request.client_session_id,
            },
        )
    return {"status": "logged"}
