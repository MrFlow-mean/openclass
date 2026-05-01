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

router = APIRouter()

REALTIME_REMOVED_DETAIL = "旧版实时语音课堂主链路已删除，等待新的课堂 AI 架构接入。"


def _realtime_removed() -> HTTPException:
    return HTTPException(status_code=410, detail=REALTIME_REMOVED_DETAIL)


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    _ = lesson_id, request, user
    raise _realtime_removed()


@router.post("/api/lessons/{lesson_id}/realtime/google/session", response_model=GoogleRealtimeSessionResponse)
def create_google_realtime_session(
    lesson_id: str,
    request: GoogleRealtimeSessionRequest,
    user: UserView = Depends(current_user),
) -> GoogleRealtimeSessionResponse:
    _ = lesson_id, request, user
    raise _realtime_removed()


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    _ = lesson_id
    await websocket.accept()
    await websocket.send_json(
        {
            "error": {
                "code": 410,
                "status": "REMOVED",
                "message": REALTIME_REMOVED_DETAIL,
            }
        }
    )
    await websocket.close(code=1012, reason="Realtime workflow removed")


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> dict[str, str]:
    _ = lesson_id, request, user
    raise _realtime_removed()
