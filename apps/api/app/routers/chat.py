from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, UserView
from app.routers.auth import current_user
from app.services.chat_service import process_chat_on_lesson
from app.services.openai_course_ai import bind_ai_output_stream

router = APIRouter()


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chat_stream_events(lesson_id: str, request: ChatRequest, *, user_id: str) -> Iterator[str]:
    events: queue.Queue[tuple[str, object] | None] = queue.Queue()
    phase_labels = {
        "chatbot": "正在回复",
        "pm": "正在整理学习需求",
        "board": "正在生成右侧文档",
    }

    def emit(event: str, data: object) -> None:
        events.put((event, data))

    def observer(payload: dict[str, object]) -> None:
        if payload.get("type") == "board_task_update":
            data = payload.get("payload")
            if isinstance(data, dict):
                emit("board_task_update", data)
            return
        if payload.get("type") == "requirement_update":
            data = payload.get("payload")
            if isinstance(data, dict):
                emit("requirement_update", data)
            return
        if payload.get("type") == "role_start":
            role = str(payload.get("role") or "")
            label = phase_labels.get(role)
            if label:
                emit("phase", {"label": label, "role": role})
            return
        if payload.get("type") != "field_delta":
            return
        role = str(payload.get("role") or "")
        field = str(payload.get("field") or "")
        delta = str(payload.get("delta") or "")
        if not delta:
            return
        if role == "chatbot" and field == "chatbot_message":
            for char in delta:
                emit("chat_delta", {"delta": char})
        elif role == "board" and field == "content_text":
            for char in delta:
                emit("document_delta", {"delta": char})

    def run() -> None:
        try:
            emit("phase", {"label": "正在准备回复", "role": "request"})
            with bind_ai_output_stream(observer):
                response = process_chat_on_lesson(lesson_id, request, user_id=user_id)
            emit("final", response.model_dump(mode="json"))
        except Exception as exc:  # pragma: no cover - route safety net
            emit("error", {"message": str(exc)})
        finally:
            events.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    while True:
        item = events.get()
        if item is None:
            break
        event, data = item
        yield _sse_event(event, data)


@router.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    user: UserView = Depends(current_user),
) -> ChatResponse:
    return process_chat_on_lesson(lesson_id, request, user_id=user.id)


@router.post("/api/lessons/{lesson_id}/chat/stream")
def stream_chat_on_lesson(
    lesson_id: str,
    request: ChatRequest,
    user: UserView = Depends(current_user),
) -> StreamingResponse:
    return StreamingResponse(
        _chat_stream_events(lesson_id, request, user_id=user.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
