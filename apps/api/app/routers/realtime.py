from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

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
from app.services.ai_model_catalog import OPENAI_DEFAULT_REALTIME_MODEL, OPENAI_OFFICIAL_BASE_URL, default_realtime_selection
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import find_lesson_package, load_workspace_for_user

router = APIRouter()

REALTIME_CONNECT_DETAIL = "OpenAI GPT Realtime 已接入；Google realtime 连接层等待按新工作流重新接入。"


def _openai_realtime_api_key() -> str | None:
    key = (os.getenv("OPENAI_REALTIME_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return key or None


def _multipart_form(parts: dict[str, tuple[str, str]]) -> tuple[bytes, str]:
    boundary = f"----openclass-realtime-{uuid4().hex}"
    body_parts: list[bytes] = []
    for name, (content_type, value) in parts.items():
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(
            (
                f'Content-Disposition: form-data; name="{name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body_parts.append(value.encode("utf-8"))
        body_parts.append(b"\r\n")
    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(body_parts), boundary


def _create_openai_realtime_call(
    *,
    offer_sdp: str,
    model: str,
    lesson_title: str,
    latest_assistant_message: str | None,
) -> str:
    api_key = _openai_realtime_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="OpenAI realtime API key is not configured")

    voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")
    session = {
        "type": "realtime",
        "model": model,
        "instructions": (
            "你是 OpenClass 的交互 AI。你负责通过语音理解用户的学习需求，"
            "把用户意图保持在当前课程工作台语境中，并用自然语言回应。"
            "不要使用固定课程模板，不要假设具体学科；后续板书和资料处理交给统一工作流。"
            f"当前 lesson：{lesson_title}。"
            + (f" 最近一次讲师回复：{latest_assistant_message[:600]}" if latest_assistant_message else "")
        ),
        "audio": {
            "input": {
                "transcription": {
                    "model": os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
                }
            },
            "output": {
                "voice": voice,
            },
        },
    }
    body, boundary = _multipart_form(
        {
            "sdp": ("application/sdp", offer_sdp),
            "session": ("application/json", json.dumps(session, ensure_ascii=False)),
        }
    )
    base_url = os.getenv("OPENAI_REALTIME_BASE_URL") or os.getenv("OPENAI_BASE_URL") or OPENAI_OFFICIAL_BASE_URL
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/realtime/calls",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"OpenAI realtime session failed: {detail}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI realtime session failed: {exc}") from exc


@router.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    user: UserView = Depends(current_user),
) -> RealtimeConnectResponse:
    workspace = load_workspace_for_user(user.id)
    _package, lesson = find_lesson_package(workspace, lesson_id)
    selection = request.realtime_model or default_realtime_selection()
    if selection.provider != "openai":
        raise HTTPException(status_code=501, detail="Only OpenAI GPT Realtime is connected in the new workflow")

    model = selection.model or os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/connect",
        lesson=lesson,
        trace_prefix="realtime_connect",
        realtime_model=model,
        client_session_id=request.client_session_id,
    ):
        answer_sdp = _create_openai_realtime_call(
            offer_sdp=request.offer_sdp,
            model=model,
            lesson_title=lesson.title,
            latest_assistant_message=request.latest_assistant_message,
        )
    return RealtimeConnectResponse(
        answer_sdp=answer_sdp,
        provider="openai",
        model=model,
        voice=os.getenv("OPENAI_REALTIME_VOICE", "marin"),
    )


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
