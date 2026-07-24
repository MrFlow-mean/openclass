from __future__ import annotations

import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.services.pi_agent_runtime import PiTextClient
from app.models import AIModelSelection
from app.services import ai_execution_adapter, pi_agent_runtime


class _Answer(BaseModel):
    answer: str


def _pi_stdout(content: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def _pi_error_stdout(message: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "errorMessage": message,
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def _pi_stdout_with_live_reasoning(content: str) -> str:
    return "\n".join(
        [
            json.dumps({"type": "agent_start"}),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_start",
                        "contentIndex": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 0,
                        "delta": "private reasoning that must not be persisted",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_end",
                        "contentIndex": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_start",
                        "contentIndex": 1,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 1,
                        "delta": content,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 1,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}],
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )


def test_pi_client_runs_without_tools_or_discovered_resources(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENCLASS_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_agent_runtime, "load_root_dotenv", lambda: None)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _pi_stdout('{"answer":"ok"}'), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="deepseek",
        model="deepseek-v4-flash",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(
        system_prompt="Answer from verified context.",
        user_prompt="Question",
        schema=_Answer,
    )

    command, kwargs = calls[0]
    assert response.output_parsed == _Answer(answer="ok")
    assert command[:5] == [
        "/test/pi",
        "--provider",
        "deepseek",
        "--model",
        "deepseek-v4-flash",
    ]
    assert "--no-tools" in command
    assert "--no-extensions" in command
    assert "--no-context-files" in command
    assert kwargs["input"] == "Question"
    assert kwargs["env"]["PI_TELEMETRY"] == "0"
    assert kwargs["timeout"] == 600
    assert str(kwargs["env"]["PI_CODING_AGENT_DIR"]).startswith(str(tmp_path))


def test_pi_client_converts_live_json_events_into_public_activity(tmp_path) -> None:
    observed = []

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            _pi_stdout_with_live_reasoning('{"answer":"ok"}'),
            "",
        )

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=observed.append,
    )

    assert response.output_parsed.answer == "ok"
    assert any(event.label == "OpenClass 正在推理" for event in observed)
    assert any(event.label == "OpenClass 已完成推理" for event in observed)
    assert any(event.label == "OpenClass 正在生成结果" for event in observed)
    assert any(event.label == "OpenClass 已校验模型结果" for event in observed)
    assert all("private reasoning" not in str(event.metadata) for event in observed)
    assert all("private reasoning" not in str(event.metadata) for event in response.activity)


def test_pi_client_publishes_activity_before_the_process_finishes(tmp_path) -> None:
    fake_pi = tmp_path / "fake-pi"
    fake_pi.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time

sys.stdin.read()
print(json.dumps({"type": "agent_start"}), flush=True)
time.sleep(0.2)
print(json.dumps({
    "type": "message_update",
    "assistantMessageEvent": {"type": "text_start", "contentIndex": 0},
}), flush=True)
time.sleep(0.2)
print(json.dumps({
    "type": "message_end",
    "message": {"role": "assistant", "content": [{"type": "text", "text": "{\\\"answer\\\":\\\"ok\\\"}"}]},
}), flush=True)
print(json.dumps({"type": "agent_end", "messages": []}), flush=True)
""",
        encoding="utf-8",
    )
    os.chmod(fake_pi, 0o700)
    observed: list[tuple[float, object]] = []

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary=str(fake_pi),
        runtime_root=tmp_path / "runtime",
    ).parse(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=lambda event: observed.append((time.monotonic(), event)),
    )
    finished_at = time.monotonic()

    assert response.output_parsed.answer == "ok"
    assert observed
    assert observed[0][0] < finished_at - 0.25


def test_pi_client_accepts_a_bounded_request_timeout(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS", "420")

    def run(_command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess([], 0, _pi_stdout('{"answer":"ok"}'), "")

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert calls[0]["timeout"] == 420


def test_pi_client_retries_one_transient_websocket_failure(tmp_path) -> None:
    outputs = iter(
        [
            _pi_error_stdout("WebSocket error"),
            _pi_stdout('{"answer":"recovered"}'),
        ]
    )
    calls = 0

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, next(outputs), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert response.output_parsed.answer == "recovered"
    assert calls == 2


def test_pi_client_does_not_retry_a_non_transient_failure(tmp_path) -> None:
    calls = 0

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0,
            _pi_error_stdout("Invalid authentication"),
            "",
        )

    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    )

    try:
        client.parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)
    except RuntimeError as error:
        assert str(error) == "Pi model request failed: Invalid authentication"
    else:  # pragma: no cover - guards retry classification
        raise AssertionError("non-transient Pi failure was accepted")

    assert calls == 1


def test_pi_client_rejects_an_invalid_request_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS", "unbounded")

    client = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=lambda *_args, **_kwargs: None,
    )

    try:
        client.parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)
    except RuntimeError as exc:
        assert str(exc) == "OPENCLASS_PI_REQUEST_TIMEOUT_SECONDS must be an integer"
    else:  # pragma: no cover - guards configuration validation
        raise AssertionError("invalid Pi request timeout was accepted")


def test_pi_client_uses_an_explicit_operator_agent_directory(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[dict[str, object]] = []
    configured_agent_dir = tmp_path / "configured-agent"
    configured_agent_dir.mkdir()
    monkeypatch.setenv("OPENCLASS_PI_AGENT_DIR", str(configured_agent_dir))

    def run(_command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            [],
            0,
            _pi_stdout('{"answer":"ok"}'),
            "",
        )

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.6-sol",
        binary="/test/pi",
        runtime_root=tmp_path / "runtime",
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert calls[0]["env"]["PI_CODING_AGENT_DIR"] == str(configured_agent_dir)


def test_pi_client_maps_codex_provider_and_repairs_invalid_json(tmp_path) -> None:
    outputs = iter([_pi_stdout("not json"), _pi_stdout('{"answer":"fixed"}')])
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, next(outputs), "")

    response = PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        reasoning_effort="high",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    assert response.output_parsed.answer == "fixed"
    assert commands[0][2] == "openai-codex"
    assert commands[0][-2:] == ["--thinking", "high"]


def test_pi_client_applies_a_supported_service_tier(tmp_path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, _pi_stdout('{"answer":"ok"}'), "")

    PiTextClient(
        owner_user_id="user_test",
        provider="openai_codex",
        model="gpt-5.5",
        service_tier="priority",
        binary="/test/pi",
        runtime_root=tmp_path,
        process_runner=run,
    ).parse(system_prompt="Answer.", user_prompt="Question", schema=_Answer)

    command, kwargs = calls[0]
    assert command[command.index("--extension") + 1].endswith(
        "pi_runtime_settings_extension.ts"
    )
    assert kwargs["env"]["OPENCLASS_PI_SERVICE_TIER"] == "priority"


@pytest.mark.parametrize(
    ("provider", "service_tier"),
    [("deepseek", "priority"), ("openai_codex", "unsupported")],
)
def test_pi_client_rejects_an_unsupported_service_tier(
    provider: str,
    service_tier: str,
    tmp_path,
) -> None:
    with pytest.raises(RuntimeError, match="does not support this service tier"):
        PiTextClient(
            owner_user_id="user_test",
            provider=provider,
            model="test-model",
            service_tier=service_tier,
            binary="/test/pi",
            runtime_root=tmp_path,
        )


def test_server_forces_pi_adapter_for_a_legacy_codex_backend_selection(monkeypatch) -> None:
    captured: dict[str, object] = {}
    observed_activity = []

    class FakePiTextClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def parse(self, **kwargs):
            assert observed_activity[0].status == "running"
            return SimpleNamespace(
                output_parsed=kwargs["schema"](answer="through pi"),
                activity=[],
            )

    monkeypatch.setattr(ai_execution_adapter, "PiTextClient", FakePiTextClient)
    adapter = ai_execution_adapter.build_ai_execution_adapter(
        AIModelSelection(
            agent_backend="codex",
            provider="deepseek",
            model="deepseek-v4-flash",
        ),
        owner_user_id="user_test",
    )

    result = adapter.parse_structured(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
        on_activity=observed_activity.append,
    )

    assert captured == {
        "owner_user_id": "user_test",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "reasoning_effort": None,
        "service_tier": None,
    }
    assert result.output_parsed.answer == "through pi"
    assert [event.status for event in observed_activity] == ["running", "completed"]
    assert observed_activity[0].id == observed_activity[1].id
    assert result.activity == [observed_activity[1]]
