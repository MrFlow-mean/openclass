from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, UserView, new_id, now_iso
from app.routers.auth import current_user
from app.services.ai_logging import ai_log_context, ai_usage_logger
from app.services.chat_service import process_chat_on_lesson
from app.services.openai_course_ai import bind_ai_output_stream

router = APIRouter()
CHAT_STREAM_HEARTBEAT_SECONDS = 10.0
CHAT_STREAM_CHAT_DELTA_DELAY_SECONDS = 0.004
CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS = 0.0015


@dataclass
class ChatStreamState:
    trace_id: str
    lesson_id: str
    user_id: str
    user_message_excerpt: str
    last_phase: str = "request"
    final_enqueued: bool = False
    final_yielded: bool = False
    error_enqueued: bool = False
    produced_commit_id: str | None = None


def _message_excerpt(message: str, limit: int = 180) -> str:
    compact = " ".join(message.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _head_commit_id(response: ChatResponse, lesson_id: str) -> str | None:
    lesson = next((item for item in response.course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return None
    branch = lesson.history_graph.branches.get(lesson.history_graph.current_branch)
    if branch is not None:
        return branch.head_commit_id
    if lesson.history_graph.commits:
        return lesson.history_graph.commits[-1].id
    return None


def _lesson_document_text(response: ChatResponse, lesson_id: str) -> str:
    lesson = next((item for item in response.course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return ""
    return lesson.board_document.content_text or ""


def _log_stream_lifecycle(state: ChatStreamState, event: str, **payload: object) -> None:
    ai_usage_logger.log_event(
        "chat_stream_lifecycle",
        stream_event=event,
        trace_id=state.trace_id,
        lesson_id=state.lesson_id,
        user_id=state.user_id,
        user_message_excerpt=state.user_message_excerpt,
        last_phase=state.last_phase,
        final_enqueued=state.final_enqueued,
        final_yielded=state.final_yielded,
        error_enqueued=state.error_enqueued,
        produced_commit_id=state.produced_commit_id,
        **payload,
    )


def _sse_event(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _visible_delta_delay_seconds(event: str) -> float:
    if event == "chat_delta":
        return CHAT_STREAM_CHAT_DELTA_DELAY_SECONDS
    if event == "document_delta":
        return CHAT_STREAM_DOCUMENT_DELTA_DELAY_SECONDS
    return 0.0


def _chat_stream_events(lesson_id: str, request: ChatRequest, *, user_id: str) -> Iterator[str]:
    events: queue.Queue[tuple[str, object] | None] = queue.Queue()
    state = ChatStreamState(
        trace_id=new_id("chat"),
        lesson_id=lesson_id,
        user_id=user_id,
        user_message_excerpt=_message_excerpt(request.message),
    )
    phase_labels = {
        "chatbot": "正在回复",
        "pm": "正在整理学习需求",
        "board": "正在生成右侧文档",
    }
    chat_delta_emitted = False
    document_delta_emitted = False

    def emit(event: str, data: object) -> None:
        events.put((event, data))

    def observer(payload: dict[str, object]) -> None:
        nonlocal chat_delta_emitted, document_delta_emitted
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
            if role:
                state.last_phase = role
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
        if role in {"chatbot", "pm"} and field == "chatbot_message":
            for char in delta:
                emit("chat_delta", {"delta": char})
            chat_delta_emitted = True
        elif role == "board" and field == "content_text":
            for char in delta:
                emit("document_delta", {"delta": char})
            document_delta_emitted = True

    def emit_missing_visible_deltas(response: ChatResponse) -> None:
        nonlocal chat_delta_emitted, document_delta_emitted
        if not chat_delta_emitted and response.chatbot_message:
            for char in response.chatbot_message:
                emit("chat_delta", {"delta": char})
            chat_delta_emitted = True
        if (
            not document_delta_emitted
            and response.board_document_operation_status == "succeeded"
        ):
            document_text = _lesson_document_text(response, lesson_id)
            if document_text:
                for char in document_text:
                    emit("document_delta", {"delta": char})
                document_delta_emitted = True

    def run() -> None:
        with ai_log_context(
            trace_id=state.trace_id,
            route="/api/lessons/{lesson_id}/chat/stream",
            lesson_id=lesson_id,
            user_id=user_id,
        ):
            _log_stream_lifecycle(state, "stream_started")
            try:
                emit("phase", {"label": "正在准备回复", "role": "request"})
                with bind_ai_output_stream(observer):
                    response = process_chat_on_lesson(lesson_id, request, user_id=user_id)
                state.produced_commit_id = _head_commit_id(response, lesson_id)
                emit_missing_visible_deltas(response)
                state.final_enqueued = True
                emit("final", response.model_dump(mode="json"))
                _log_stream_lifecycle(state, "stream_final_sent")
            except Exception as exc:  # pragma: no cover - route safety net
                state.error_enqueued = True
                emit("error", {"message": str(exc), "trace_id": state.trace_id})
                _log_stream_lifecycle(state, "stream_error", error_message=str(exc))
            finally:
                events.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        while True:
            try:
                item = events.get(timeout=CHAT_STREAM_HEARTBEAT_SECONDS)
            except queue.Empty:
                yield _sse_event("heartbeat", {"trace_id": state.trace_id, "ts": now_iso()})
                continue
            if item is None:
                break
            event, data = item
            if event == "final":
                state.final_yielded = True
            yield _sse_event(event, data)
            delay = _visible_delta_delay_seconds(event)
            if delay > 0:
                time.sleep(delay)
    finally:
        if not state.final_yielded and not state.error_enqueued:
            _log_stream_lifecycle(state, "stream_disconnected_or_no_final")


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
