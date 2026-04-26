import json

import pytest

from app.services.lesson_factory import create_lesson
from app.services.ai_logging import ai_log_context, ai_usage_logger
from app.services.openai_realtime import OpenAIRealtimeTeacher


class _FakeRealtimeCalls:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def create(self, **kwargs: object):
        self.payload = kwargs
        return type("FakeResponse", (), {"text": "v=0\r\no=- 0 0 IN IP4 127.0.0.1"})()


class _FakeRealtime:
    def __init__(self) -> None:
        self.calls = _FakeRealtimeCalls()


class _FakeClient:
    def __init__(self) -> None:
        self.realtime = _FakeRealtime()


def _read_log_entries(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def isolated_ai_log(monkeypatch: pytest.MonkeyPatch, tmp_path):
    log_path = tmp_path / "logs" / "ai-usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ai_usage_logger, "path", log_path)
    return log_path


def test_realtime_call_uses_requested_model_and_lesson_context(isolated_ai_log) -> None:
    teacher = OpenAIRealtimeTeacher()
    teacher.client = _FakeClient()
    teacher.config.model = "gpt-realtime-1.5"
    teacher.config.voice = "marin"
    lesson = create_lesson("勾股定理")

    with ai_log_context(trace_id="realtime_test", route="unit_test"):
        answer = teacher.create_call(
            lesson=lesson,
            offer_sdp="offer-sdp",
            latest_assistant_message="先用直角三角形来理解这个公式。",
        )

    assert answer.startswith("v=0")
    assert teacher.client.realtime.calls.payload is not None
    session = teacher.client.realtime.calls.payload["session"]
    assert isinstance(session, dict)
    assert session["model"] == "gpt-realtime-1.5"
    assert session["audio"]["output"]["voice"] == "marin"
    assert "勾股定理" in session["instructions"]
    entries = _read_log_entries(isolated_ai_log)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event_type"] == "openai_realtime_session"
    assert entry["context"]["trace_id"] == "realtime_test"
    assert entry["payload"]["answer_sdp"].startswith("v=0")
