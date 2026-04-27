from __future__ import annotations

import asyncio
import re
import ssl

import certifi
import websockets
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.models import (
    AIModelSelection,
    GoogleRealtimeSessionRequest,
    GoogleRealtimeSessionResponse,
    RealtimeConnectRequest,
    RealtimeConnectResponse,
    RealtimeTranscriptLogRequest,
)
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_model_catalog import default_realtime_selection
from app.services.openai_realtime import google_realtime_teacher, openai_realtime_teacher
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import find_lesson_package, load_workspace

router = APIRouter()

_GOOGLE_API_KEY_PATTERN = re.compile(r"([?&]key=)[^&\s)]+")


def _sdp_log_summary(value: str) -> dict[str, object]:
    return {
        "present": bool(value.strip()),
        "length": len(value),
    }


def _redact_google_api_key(value: str) -> str:
    return _GOOGLE_API_KEY_PATTERN.sub(r"\1[redacted]", value)


def _google_proxy_error_payload(error: Exception) -> tuple[str, dict[str, object]]:
    sanitized = _redact_google_api_key(str(error))
    normalized = sanitized.lower()
    if "api key not valid" in normalized or "invalid api key" in normalized:
        return sanitized, {
            "code": 401,
            "status": "UNAUTHENTICATED",
            "message": "Google Gemini Live API key is invalid",
        }
    if "permission denied" in normalized or "permission_denied" in normalized or "403" in normalized:
        return sanitized, {
            "code": 403,
            "status": "PERMISSION_DENIED",
            "message": "Google Gemini Live permission denied",
        }
    if "unauthenticated" in normalized or "401" in normalized:
        return sanitized, {
            "code": 401,
            "status": "UNAUTHENTICATED",
            "message": "Google Gemini Live authentication failed",
        }
    return sanitized, {
        "code": 502,
        "status": "BAD_GATEWAY",
        "message": "Google Gemini Live proxy failed",
    }


async def _send_google_realtime_error(websocket: WebSocket, error: dict[str, object]) -> None:
    try:
        await websocket.send_json({"error": error})
    except RuntimeError:
        pass


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str, request: RealtimeConnectRequest
) -> RealtimeConnectResponse:
    realtime_model = request.realtime_model or default_realtime_selection()
    if realtime_model.provider != "openai":
        raise HTTPException(
            status_code=400,
            detail="This realtime endpoint only supports OpenAI WebRTC. Use the Google Live endpoint for Google models.",
        )
    if not openai_realtime_teacher.enabled:
        raise HTTPException(status_code=503, detail="OpenAI Realtime is not configured")

    workspace = load_workspace()
    _, lesson = find_lesson_package(workspace, lesson_id)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/connect",
        lesson=lesson,
        trace_prefix="realtime",
        trace_id=request.client_session_id,
    ):
        ai_usage_logger.log_event(
            "realtime_connect_request",
            offer_sdp=_sdp_log_summary(request.offer_sdp),
            latest_assistant_message=request.latest_assistant_message,
        )
        try:
            answer_sdp = openai_realtime_teacher.create_call(
                lesson=lesson,
                offer_sdp=request.offer_sdp,
                latest_assistant_message=request.latest_assistant_message,
                model_selection=realtime_model,
            )
        except RuntimeError as exc:
            ai_usage_logger.log_event("realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            ai_usage_logger.log_event("realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=502, detail=f"Realtime connect failed: {exc}") from exc

        response = RealtimeConnectResponse(
            answer_sdp=answer_sdp,
            provider="openai",
            model=realtime_model.model,
            voice=openai_realtime_teacher.config.voice,
        )
        ai_usage_logger.log_event(
            "realtime_connect_response",
            answer_sdp=_sdp_log_summary(response.answer_sdp),
            model=response.model,
            voice=response.voice,
        )
        return response


@router.post("/api/lessons/{lesson_id}/realtime/google/session", response_model=GoogleRealtimeSessionResponse)
def create_google_realtime_session(
    lesson_id: str, request: GoogleRealtimeSessionRequest
) -> GoogleRealtimeSessionResponse:
    realtime_model = request.realtime_model or AIModelSelection(
        provider="google",
        model=google_realtime_teacher.config.model,
    )
    if realtime_model.provider != "google":
        raise HTTPException(status_code=400, detail="This endpoint only supports Google Gemini Live models")
    if not google_realtime_teacher.enabled:
        raise HTTPException(status_code=503, detail="Google Gemini Live is not configured")

    workspace = load_workspace()
    _, lesson = find_lesson_package(workspace, lesson_id)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/google/session",
        lesson=lesson,
        trace_prefix="realtime",
        trace_id=request.client_session_id,
    ):
        ai_usage_logger.log_event(
            "google_realtime_connect_request",
            latest_assistant_message=request.latest_assistant_message,
            realtime_model=realtime_model,
        )
        try:
            session = google_realtime_teacher.create_live_session(
                lesson=lesson,
                latest_assistant_message=request.latest_assistant_message,
                model_selection=realtime_model,
            )
        except RuntimeError as exc:
            ai_usage_logger.log_event("google_realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            ai_usage_logger.log_event("google_realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=502, detail=f"Google realtime connect failed: {exc}") from exc

        response = GoogleRealtimeSessionResponse(
            websocket_url=f"/api/lessons/{lesson_id}/realtime/google/ws",
            setup=session["setup"],
            provider="google",
            model=str(session["model"]),
            voice=str(session["voice"]),
        )
        ai_usage_logger.log_event(
            "google_realtime_connect_response",
            model=response.model,
            voice=response.voice,
        )
        return response


@router.websocket("/api/lessons/{lesson_id}/realtime/google/ws")
async def proxy_google_realtime_session(websocket: WebSocket, lesson_id: str) -> None:
    await websocket.accept()
    if not google_realtime_teacher.enabled:
        await _send_google_realtime_error(
            websocket,
            {
                "code": 503,
                "status": "UNCONFIGURED",
                "message": "Google Gemini Live is not configured",
            },
        )
        await websocket.close(code=1011, reason="Google Gemini Live is not configured")
        return

    try:
        google_url = google_realtime_teacher.websocket_url()
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(google_url, max_size=None, ssl=ssl_context) as google_socket:
            ai_usage_logger.log_event("google_realtime_proxy_open", lesson_id=lesson_id)

            async def forward_browser_to_google() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        await google_socket.close()
                        return
                    if message.get("text") is not None:
                        await google_socket.send(message["text"])
                    elif message.get("bytes") is not None:
                        await google_socket.send(message["bytes"])

            async def forward_google_to_browser() -> None:
                async for message in google_socket:
                    if isinstance(message, bytes):
                        try:
                            await websocket.send_text(message.decode("utf-8"))
                        except UnicodeDecodeError:
                            await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(forward_browser_to_google()),
                asyncio.create_task(forward_google_to_browser()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log_error, client_error = _google_proxy_error_payload(exc)
        ai_usage_logger.log_event("google_realtime_proxy_error", lesson_id=lesson_id, error=log_error)
        await _send_google_realtime_error(websocket, client_error)
        try:
            await websocket.close(code=1011, reason="Google Gemini Live proxy failed")
        except RuntimeError:
            pass


@router.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(lesson_id: str, request: RealtimeTranscriptLogRequest) -> dict[str, str]:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/events",
        trace_prefix="realtime",
        trace_id=request.client_session_id,
        lesson_id=lesson_id,
        lesson_title=request.lesson_title,
    ):
        ai_usage_logger.log_event(
            "realtime_transcript",
            role=request.role,
            transport_event_type=request.transport_event_type,
            transcript=request.transcript,
        )
        log_ai_interaction_message(
            channel="voice",
            direction="input" if request.role == "user" else "output",
            role=request.role,
            transport=request.transport_event_type,
            content=request.transcript,
            metadata={"lesson_title": request.lesson_title},
        )
    return {"status": "ok"}
