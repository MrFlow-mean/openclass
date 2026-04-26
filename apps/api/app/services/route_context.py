from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.models import Lesson
from app.services.ai_logging import ai_log_context, new_trace_id


@contextmanager
def bind_ai_request_context(
    route_name: str,
    *,
    lesson: Lesson | None = None,
    trace_prefix: str = "trace",
    trace_id: str | None = None,
    **extra: object,
) -> Iterator[dict[str, object]]:
    context: dict[str, object] = {
        "trace_id": trace_id or new_trace_id(trace_prefix),
        "route": route_name,
    }
    if lesson is not None:
        context["lesson_id"] = lesson.id
        context["lesson_title"] = lesson.title
    context.update({key: value for key, value in extra.items() if value is not None})
    with ai_log_context(**context):
        yield context
