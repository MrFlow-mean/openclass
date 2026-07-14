from __future__ import annotations

import queue
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.models import CodexAccountView
from app.services import codex_app_server
from app.services.ai_call_budget import AICallBudget, bind_ai_call_budget


class _Payload(BaseModel):
    value: str = ""


def test_managed_codex_session_inherits_active_absolute_deadline(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Session:
        def __init__(self, *, user_id, timeout_seconds, deadline_monotonic) -> None:
            captured["user_id"] = user_id
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
        with codex_app_server._managed_session(user_id="user_a", timeout_seconds=30):
            pass

    assert captured["user_id"] == "user_a"
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


def test_codex_home_is_isolated_per_openclass_user(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))

    first = codex_app_server.codex_home_path("user_a")
    second = codex_app_server.codex_home_path("user_b")

    assert first != second
    assert first.parent == second.parent == (tmp_path / "codex" / "accounts")
    assert "user_a" not in str(first)
    assert "user_b" not in str(second)


def test_codex_status_cache_is_isolated_per_openclass_user(monkeypatch) -> None:
    reads: list[str] = []
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "codex_app_server_available", lambda: True)
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            reads.append(user_id)
            or CodexAccountView(
                type="chatgpt",
                email=f"{user_id}@example.test",
            ),
            False,
        ),
    )
    with codex_app_server._status_cache_lock:
        codex_app_server._cached_status.clear()

    first = codex_app_server.codex_provider_status("user_a")
    second = codex_app_server.codex_provider_status("user_b")
    cached_first = codex_app_server.codex_provider_status("user_a")

    assert reads == ["user_a", "user_b"]
    assert first.account is not None
    assert second.account is not None
    assert cached_first.account is not None
    assert first.account.email == cached_first.account.email == "user_a@example.test"
    assert second.account.email == "user_b@example.test"
    with codex_app_server._status_cache_lock:
        codex_app_server._cached_status.clear()


def test_login_completion_with_null_login_id_is_owned_and_refreshes_account(monkeypatch) -> None:
    class _Session:
        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    session._messages.put(
        {
            "method": "account/login/completed",
            "params": {"loginId": None, "success": True},
        }
    )
    session._messages.put({"method": "account/updated", "params": {}})
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            CodexAccountView(type="chatgpt", email=f"{user_id}@example.test"),
            False,
        ),
    )

    codex_app_server._watch_login_attempt(attempt)

    assert attempt.status == "succeeded"
    assert attempt.account is not None
    assert attempt.account.email == "user_a@example.test"
    assert session.closed is True


def test_login_attempt_cannot_be_read_or_cancelled_by_another_user() -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_owned",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=None,
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        with pytest.raises(codex_app_server.CodexAppServerError, match="Unknown"):
            codex_app_server.codex_login_status(attempt.login_id, "user_b")
        with pytest.raises(codex_app_server.CodexAppServerError, match="Unknown"):
            codex_app_server.cancel_codex_login(attempt.login_id, "user_b")
        assert codex_app_server.cancel_codex_login(attempt.login_id, "user_a").status == "cancelled"
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)
