from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from pydantic import BaseModel

from app.services.pi_agent_runtime import PiTextClient
from app.models import AIModelSelection
from app.services import ai_execution_adapter


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


def test_pi_client_runs_without_tools_or_discovered_resources(tmp_path) -> None:
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
    assert str(kwargs["env"]["PI_CODING_AGENT_DIR"]).startswith(str(tmp_path))


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


def test_pi_backend_builds_pi_adapter_independently_of_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePiTextClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def parse(self, **kwargs):
            return SimpleNamespace(
                output_parsed=kwargs["schema"](answer="through pi"),
                activity=[],
            )

    monkeypatch.setattr(ai_execution_adapter, "PiTextClient", FakePiTextClient)
    adapter = ai_execution_adapter.build_ai_execution_adapter(
        AIModelSelection(
            agent_backend="pi",
            provider="deepseek",
            model="deepseek-v4-flash",
        ),
        owner_user_id="user_test",
    )

    result = adapter.parse_structured(
        system_prompt="Answer.",
        user_prompt="Question",
        schema=_Answer,
    )

    assert captured == {
        "owner_user_id": "user_test",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "reasoning_effort": None,
    }
    assert result.output_parsed.answer == "through pi"
