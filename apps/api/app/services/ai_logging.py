from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from app.models import new_id, now_iso

_ai_log_context: ContextVar[dict[str, Any] | None] = ContextVar("ai_log_context", default=None)


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def current_ai_log_context() -> dict[str, Any]:
    return dict(_ai_log_context.get() or {})


@contextmanager
def ai_log_context(**context: Any) -> Iterator[dict[str, Any]]:
    next_context = current_ai_log_context()
    next_context.update({key: _json_safe(value) for key, value in context.items() if value is not None})
    token = _ai_log_context.set(next_context)
    try:
        yield next_context
    finally:
        _ai_log_context.reset(token)


def new_trace_id(prefix: str = "trace") -> str:
    return new_id(prefix)


class AIUsageLogger:
    def __init__(self, path: Path | None = None) -> None:
        configured_path = os.getenv("AI_USAGE_LOG_PATH")
        self.path = path or (
            Path(configured_path)
            if configured_path
            else Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ai-usage.jsonl"
        )
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(self, event_type: str, **payload: Any) -> dict[str, Any]:
        event = {
            "id": new_id("ai_log"),
            "occurred_at": now_iso(),
            "event_type": event_type,
            "context": _json_safe(current_ai_log_context()),
            "payload": _json_safe(payload),
        }
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(f"{line}\n")
        return event


ai_usage_logger = AIUsageLogger()


def log_ai_interaction_message(
    *,
    channel: str,
    direction: str,
    role: str,
    transport: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized = content.strip()
    if not normalized:
        return None

    payload: dict[str, Any] = {
        "message_id": new_id("ai_message"),
        "channel": channel,
        "direction": direction,
        "role": role,
        "transport": transport,
        "content": normalized,
        "content_length": len(normalized),
    }
    if metadata:
        payload["metadata"] = _json_safe(metadata)
    return ai_usage_logger.log_event("ai_interaction_message", **payload)
