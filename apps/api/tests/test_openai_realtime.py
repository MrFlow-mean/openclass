import json

import pytest

from app.models import BoardTeachingGuide, BoardTeachingProgress, ReadingCompanionRule, ReadingCompanionTurn
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
    assert session["type"] == "transcription"
    assert session["model"] == "gpt-realtime-1.5"
    assert session["audio"]["input"]["transcription"]["language"] == "zh"
    assert "勾股定理" in session["audio"]["input"]["transcription"]["prompt"]
    assert session["audio"]["input"]["turn_detection"]["create_response"] is False
    assert "output" not in session["audio"]
    entries = _read_log_entries(isolated_ai_log)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event_type"] == "openai_realtime_session"
    assert entry["context"]["trace_id"] == "realtime_test"
    assert entry["payload"]["answer_sdp"].startswith("v=0")


def test_realtime_instructions_switch_to_reading_companion_mode(isolated_ai_log) -> None:
    teacher = OpenAIRealtimeTeacher()
    teacher.client = _FakeClient()
    lesson = create_lesson("酒店英语对话")
    lesson.board_teaching_guide = BoardTeachingGuide(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash="reading-hash",
        board_title="酒店英语对话",
        reading_companion=True,
        reading_rule=ReadingCompanionRule(
            user_role="客人",
            assistant_role="服务员",
            rule_text="我是客人你是服务员，我们轮流读。",
            matching_policy="允许轻微口误，但必须对应当前台词。",
            focus_excerpt="客人：你好，我想订一间房。\n服务员：您好，请问您想订哪一天的房间？",
            valid_user_inputs=["你好，我想订一间房。"],
            turns=[
                ReadingCompanionTurn(order_index=0, speaker="客人", text="你好，我想订一间房。", role="user"),
                ReadingCompanionTurn(
                    order_index=1,
                    speaker="服务员",
                    text="您好，请问您想订哪一天的房间？",
                    role="assistant",
                ),
            ],
        ),
    )
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash="reading-hash",
        current_section_index=0,
    )

    teacher.create_call(
        lesson=lesson,
        offer_sdp="offer-sdp",
        latest_assistant_message="已进入陪读模式。",
    )

    assert teacher.client.realtime.calls.payload is not None
    session = teacher.client.realtime.calls.payload["session"]
    prompt = session["audio"]["input"]["transcription"]["prompt"]
    assert "陪读/轮读/角色扮演朗读" in prompt
    assert "valid_user_inputs" in prompt
    assert "这句话是什么意思" not in prompt
    assert "客人" in prompt
    assert "服务员" in prompt


def test_realtime_base_url_defaults_to_gateway_not_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SINGLE_API_KEY_MODE", "true")
    monkeypatch.setenv("AI_API_KEY", "shared-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)

    teacher = OpenAIRealtimeTeacher()

    assert teacher.config.base_url == "https://api.bupt8.com/v1"
    assert teacher.client is None
