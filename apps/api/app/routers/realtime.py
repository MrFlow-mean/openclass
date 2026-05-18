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

REALTIME_REMOVED_DETAIL = "实时语音后端运行路径已移除；当前课程对话入口是 /api/lessons/{lesson_id}/chat。"


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    raise HTTPException(status_code=410, detail=REALTIME_REMOVED_DETAIL)


@router.post("/api/lessons/{lesson_id}/realtime/google/session", response_model=GoogleRealtimeSessionResponse)
def create_google_realtime_session(
    lesson_id: str,
    request: GoogleRealtimeSessionRequest,
    user: UserView = Depends(current_user),
) -> GoogleRealtimeSessionResponse:
    raise HTTPException(status_code=410, detail=REALTIME_REMOVED_DETAIL)


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "error": {
                "code": 410,
                "status": "REALTIME_REMOVED",
                "message": REALTIME_REMOVED_DETAIL,
            }
        }
    )
    await websocket.close(code=1011, reason="Realtime removed")


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> dict[str, str]:
    raise HTTPException(status_code=410, detail=REALTIME_REMOVED_DETAIL)
