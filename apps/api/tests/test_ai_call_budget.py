from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.services.ai_call_budget import (
    AICallBudget,
    AICallBudgetExceeded,
    AIOutputBudgetExceeded,
    bind_ai_call_budget,
)
from app.services.openai_course_ai import OpenAICourseAI


class _Payload(BaseModel):
    value: str


class _DefaultedPayload(BaseModel):
    value: str = ""
    tags: list[str] = Field(default_factory=list)


class _FakeCompletions:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.output_text))],
            id="completion-id",
            usage=None,
        )


class _FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=self.output_text,
            output_parsed=_Payload(value="ok"),
        )


class _FakeClient:
    def __init__(self, output_text: str = '{"value":"ok"}') -> None:
        self.completions = _FakeCompletions(output_text)
        self.responses = _FakeResponses(output_text)
        self.chat = SimpleNamespace(completions=self.completions)
        self.with_options_calls: list[dict[str, Any]] = []

    def with_options(self, **kwargs: Any) -> "_FakeClient":
        self.with_options_calls.append(kwargs)
        return self


def _ai() -> OpenAICourseAI:
    return object.__new__(OpenAICourseAI)


def _budget(*, seconds: float = 5, tokens: int = 123, chars: int = 1000) -> AICallBudget:
    return AICallBudget(
        deadline_monotonic=time.monotonic() + seconds,
        max_output_tokens=tokens,
        max_output_chars=chars,
    )


@pytest.mark.parametrize(
    ("model", "expected_token_key", "unexpected_token_key"),
    [
        ("gpt-5.5", "max_completion_tokens", "max_tokens"),
        ("deepseek-chat", "max_tokens", "max_completion_tokens"),
    ],
)
def test_chat_completion_uses_model_appropriate_token_budget(
    model: str,
    expected_token_key: str,
    unexpected_token_key: str,
) -> None:
    client = _FakeClient()

    with bind_ai_call_budget(_budget(tokens=321)):
        response = _ai()._call_openai_chat_parse(
            role="pm",
            model=model,
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
            client=client,
        )

    assert response.output_parsed == _Payload(value="ok")
    assert len(client.completions.calls) == 1
    request = client.completions.calls[0]
    assert request[expected_token_key] == 321
    assert unexpected_token_key not in request
    assert 0 < request["timeout"] <= 5
    assert len(client.with_options_calls) == 1
    assert client.with_options_calls[0]["max_retries"] == 0
    assert 0 < client.with_options_calls[0]["timeout"] <= 5


def test_chat_completion_sends_openai_strict_json_schema() -> None:
    client = _FakeClient('{"value":"ok","tags":[]}')

    with bind_ai_call_budget(_budget()):
        response = _ai()._call_openai_chat_parse(
            role="pm",
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_DefaultedPayload,
            client=client,
        )

    assert response.output_parsed == _DefaultedPayload(value="ok", tags=[])
    schema = client.completions.calls[0]["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_openai_compatible_gpt_name_uses_legacy_max_tokens() -> None:
    client = _FakeClient()
    ai = _ai()
    ai.openai_compatible_client = client
    ai.openai_compatible_config = SimpleNamespace(compat_api="chat_completions")

    with bind_ai_call_budget(_budget(tokens=222)):
        response = ai._call_parse(
            role="pm",
            provider="openai_compatible",
            model="gpt-compatible-alias",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
        )

    assert response.output_parsed == _Payload(value="ok")
    request = client.completions.calls[0]
    assert request["max_tokens"] == 222
    assert "max_completion_tokens" not in request


def test_responses_api_receives_timeout_and_max_output_tokens() -> None:
    client = _FakeClient()

    with bind_ai_call_budget(_budget(tokens=456)):
        response = _ai()._call_openai_parse(
            role="pm",
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
            client=client,
            config=SimpleNamespace(compat_api="responses"),
        )

    assert response.output_parsed == _Payload(value="ok")
    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["max_output_tokens"] == 456
    assert 0 < request["timeout"] <= 5
    assert client.with_options_calls[0]["max_retries"] == 0


def test_output_character_budget_is_enforced_before_parsing() -> None:
    client = _FakeClient('{"value":"too long"}')

    with bind_ai_call_budget(_budget(chars=8)):
        with pytest.raises(AIOutputBudgetExceeded, match="8-character budget"):
            _ai()._call_openai_chat_parse(
                role="pm",
                model="gpt-5.5",
                system_prompt="system",
                user_prompt="user",
                schema=_Payload,
                client=client,
            )

    assert len(client.completions.calls) == 1


def test_expired_deadline_prevents_network_call() -> None:
    client = _FakeClient()
    expired = AICallBudget(
        deadline_monotonic=time.monotonic() - 1,
        max_output_tokens=123,
        max_output_chars=1000,
    )

    with bind_ai_call_budget(expired):
        with pytest.raises(AICallBudgetExceeded, match="deadline exceeded"):
            _ai()._call_openai_chat_parse(
                role="pm",
                model="gpt-5.5",
                system_prompt="system",
                user_prompt="user",
                schema=_Payload,
                client=client,
            )

    assert client.completions.calls == []
    assert client.with_options_calls == []


def test_disable_stream_repair_returns_failure_without_repair_request() -> None:
    client = _FakeClient("not valid json")

    with bind_ai_call_budget(_budget()):
        response = _ai()._call_openai_chat_parse(
            role="pm",
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=_Payload,
            client=client,
            disable_stream_repair=True,
        )

    assert response.output_parsed is None
    assert response.output_text == "not valid json"
    assert response.structured_parse_failed is True
    assert len(client.completions.calls) == 1
