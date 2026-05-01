from __future__ import annotations

import asyncio
from typing import Any

import websockets
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.models import (
    GoogleRealtimeSessionRequest,
    GoogleRealtimeSessionResponse,
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers.auth import current_user, current_websocket_user
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_model_catalog import default_realtime_selection
from app.services.openai_realtime import google_realtime_teacher, openai_realtime_teacher
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import find_lesson_package, load_workspace_for_user

router = APIRouter()


def _lesson_for_user(lesson_id: str, user_id: str):
    workspace = load_workspace_for_user(user_id)
    return find_lesson_package(workspace, lesson_id)


def _provider_error(provider: str) -> HTTPException:
    return HTTPException(status_code=400, detail=f"当前接口不支持 {provider} 实时语音模型")


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    _, lesson = _lesson_for_user(lesson_id, user.id)
    selection = request.realtime_model or default_realtime_selection()
    if selection.provider != "openai":
        raise _provider_error(selection.provider)
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
    _, lesson = _lesson_for_user(lesson_id, user.id)
    selection = request.realtime_model or default_realtime_selection()
    if selection.provider != "google":
        raise _provider_error(selection.provider)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/google/session",
        lesson=lesson,
        trace_prefix="realtime_google",
        client_session_id=request.client_session_id,
        realtime_provider=selection.provider,
        realtime_model=selection.model,
    ):
        try:
            session = google_realtime_teacher.create_live_session(
                lesson=lesson,
                latest_assistant_message=request.latest_assistant_message,
                model_selection=selection,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return GoogleRealtimeSessionResponse(
        websocket_url=f"/api/lessons/{lesson_id}/realtime/google/ws",
        setup=session["setup"],
        provider="google",
        model=str(session["model"]),
        voice=str(session["voice"]),
    )


async def _send_ws_error(websocket: WebSocket, *, code: int, status: str, message: str) -> None:
    await websocket.send_json({"error": {"code": code, "status": status, "message": message}})


async def _forward_client_to_google(websocket: WebSocket, upstream: Any) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                await upstream.close()
                return
            if "text" in message and message["text"] is not None:
                await upstream.send(message["text"])
            elif "bytes" in message and message["bytes"] is not None:
                await upstream.send(message["bytes"])
    except WebSocketDisconnect:
        await upstream.close()


async def _forward_google_to_client(websocket: WebSocket, upstream: Any) -> None:
    async for message in upstream:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
        else:
            await websocket.send_text(str(message))


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    await websocket.accept()
    try:
        user = current_websocket_user(websocket)
        _, lesson = _lesson_for_user(lesson_id, user.id)
    except Exception as exc:
        await _send_ws_error(websocket, code=401, status="UNAUTHORIZED", message=str(exc))
        await websocket.close(code=1008, reason="Unauthorized")
        return

    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/google/ws",
        lesson=lesson,
        trace_prefix="realtime_google_ws",
    ):
        try:
            upstream_url = google_realtime_teacher.websocket_url()
            async with websockets.connect(upstream_url, max_size=16 * 1024 * 1024) as upstream:
                ai_usage_logger.log_event("google_realtime_proxy_connected", lesson_id=lesson_id)
                tasks = {
                    asyncio.create_task(_forward_client_to_google(websocket, upstream)),
                    asyncio.create_task(_forward_google_to_client(websocket, upstream)),
                }
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
        except Exception as exc:
            ai_usage_logger.log_event("google_realtime_proxy_error", lesson_id=lesson_id, error=str(exc))
            try:
                await _send_ws_error(websocket, code=503, status="UPSTREAM_ERROR", message=str(exc))
                await websocket.close(code=1011, reason="Google realtime proxy error")
            except RuntimeError:
                return


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(
    lesson_id: str,
    request: RealtimeTranscriptLogRequest,
    user: UserView = Depends(current_user),
) -> dict[str, str]:
    _, lesson = _lesson_for_user(lesson_id, user.id)
    direction = "input" if request.role == "user" else "output"
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
            transcript=request.transcript,
        )
        log_ai_interaction_message(
            channel="voice",
            direction=direction,
            role=request.role,
            transport="realtime_voice",
            content=request.transcript,
            metadata={
                "client_session_id": request.client_session_id,
                "lesson_title": request.lesson_title,
                "transport_event_type": request.transport_event_type,
            },
        )
    return {"status": "ok"}
