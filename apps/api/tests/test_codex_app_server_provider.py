from __future__ import annotations

import time

import pytest

from app.models import AIModelSelection, CodexProviderStatus
from app.services import codex_app_server
from app.services import openai_course_ai
from app.services.ai_call_budget import AICallBudget, bind_ai_call_budget
from app.services.openai_course_ai import ChatbotReply, OpenAICourseAI, bind_text_model_selection


class _FakeCodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return openai_course_ai.ParsedAIResponse(
            output_parsed=ChatbotReply(chatbot_message="ok"),
            output_text='{"chatbot_message":"ok"}',
        )


@pytest.fixture
def isolated_ai_log(monkeypatch: pytest.MonkeyPatch, tmp_path):
    log_path = tmp_path / "logs" / "ai-usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(openai_course_ai.ai_usage_logger, "path", log_path)
    return log_path


def test_openai_codex_selection_uses_codex_app_server_adapter(monkeypatch, isolated_ai_log) -> None:
    fake_client = _FakeCodexClient()
    monkeypatch.setenv("OPENCLASS_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setattr(
        openai_course_ai,
        "codex_provider_status",
        lambda **_kwargs: CodexProviderStatus(enabled=True, available=True, configured=True),
    )
    monkeypatch.setattr(openai_course_ai, "CodexAppServerTextClient", lambda: fake_client)

    ai = OpenAICourseAI()
    with bind_text_model_selection(AIModelSelection(provider="openai_codex", model="gpt-5.5")):
        result = ai._parse("chatbot", "system", "user", ChatbotReply)

    assert result == ChatbotReply(chatbot_message="ok")
    assert fake_client.calls
    assert fake_client.calls[0]["model"] == "gpt-5.5"
    assert fake_client.calls[0]["system_prompt"] == "system"
    assert fake_client.calls[0]["user_prompt"] == "user"


def test_openai_codex_failure_does_not_fallback_to_server_api_key(monkeypatch, isolated_ai_log) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_APP_SERVER_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "server-key")
    monkeypatch.setattr(
        openai_course_ai,
        "codex_provider_status",
        lambda **_kwargs: CodexProviderStatus(enabled=True, available=True, configured=False, message="not signed in"),
    )

    ai = OpenAICourseAI()
    with bind_text_model_selection(AIModelSelection(provider="openai_codex", model="gpt-5.5")):
        result = ai._parse("chatbot", "system", "user", ChatbotReply)

    assert result is None


def test_managed_codex_session_inherits_active_absolute_deadline(monkeypatch) -> None:
    captured: dict[str, float] = {}

    class _Session:
        def __init__(self, *, timeout_seconds, deadline_monotonic) -> None:
            captured["timeout_seconds"] = timeout_seconds
            captured["deadline_monotonic"] = deadline_monotonic

        def close(self) -> None:
            captured["closed"] = 1

    monkeypatch.setattr(codex_app_server, "CodexAppServerSession", _Session)
    budget = AICallBudget(
        deadline_monotonic=time.monotonic() + 5,
        max_output_tokens=512,
        max_output_chars=4096,
    )

    with bind_ai_call_budget(budget):
        with codex_app_server._managed_session(timeout_seconds=30):
            pass

    assert captured["deadline_monotonic"] == budget.deadline_monotonic
    assert 0 < captured["timeout_seconds"] <= 5
    assert captured["closed"] == 1


def test_structured_turn_does_not_restart_deadline_after_thread_start(monkeypatch) -> None:
    clock = {"now": 100.0}
    writes: list[dict[str, object]] = []

    class _Session:
        deadline_monotonic = 105.0
        _next_id = 1

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert 0 < timeout_seconds <= 5
            clock["now"] += 5.1
            return {"thread": {"id": "thread-id"}}

        def _write(self, payload):
            writes.append(payload)

    monkeypatch.setattr(codex_app_server.time, "monotonic", lambda: clock["now"])

    with pytest.raises(codex_app_server.CodexAppServerError, match="Timed out"):
        codex_app_server._run_structured_turn(
            session=_Session(),
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatbotReply,
        )

    assert writes == []
