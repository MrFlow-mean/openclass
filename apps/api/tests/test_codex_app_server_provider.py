from __future__ import annotations

import pytest

from app.models import AIModelSelection, CodexProviderStatus
from app.services import openai_course_ai
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
