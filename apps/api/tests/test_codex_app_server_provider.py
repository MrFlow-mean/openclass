from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from app.services import codex_app_server
from app.services.ai_call_budget import AICallBudget, bind_ai_call_budget


class _Payload(BaseModel):
    value: str = ""


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
            schema=_Payload,
        )

    assert writes == []
