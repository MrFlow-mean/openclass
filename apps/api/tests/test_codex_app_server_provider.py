from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, Field

from app.models import CodexAccountView
from app.services import codex_app_server
from app.services.ai_call_budget import AICallBudget, bind_ai_call_budget


class _Payload(BaseModel):
    value: str = ""


class _NestedPayload(BaseModel):
    note: str = ""


class _StructuredPayload(BaseModel):
    value: str = ""
    nested: _NestedPayload = Field(default_factory=_NestedPayload)


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
            return {
                "thread": {"id": "thread-id"},
                "activePermissionProfile": {"id": "openclass_chat"},
                "sandbox": {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            }

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


def test_structured_turn_sends_provider_strict_output_schema() -> None:
    class _Session:
        deadline_monotonic = time.monotonic() + 5
        _next_id = 1

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.writes: list[dict[str, object]] = []
            self.thread_params: dict[str, object] = {}
            self._messages.put(
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "agentMessage",
                            "text": '{"value":"ok","nested":{"note":""}}',
                        }
                    },
                }
            )
            self._messages.put(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"status": "completed"}},
                }
            )

        def request(self, method, params, *, timeout_seconds):
            assert method == "thread/start"
            assert timeout_seconds > 0
            self.thread_params = params
            return {
                "thread": {"id": "thread-id"},
                "activePermissionProfile": {"id": "openclass_chat"},
                "sandbox": {
                    "type": "readOnly",
                    "networkAccess": False,
                },
            }

        def _write(self, payload):
            self.writes.append(payload)

        def _answer_server_request(self, message):
            raise AssertionError(message)

    session = _Session()

    codex_app_server._run_structured_turn(
        session=session,  # type: ignore[arg-type]
        model="gpt-5.5",
        system_prompt="system",
        user_prompt="user",
        schema=_StructuredPayload,
        allow_live_web_search=True,
    )

    output_schema = session.writes[0]["params"]["outputSchema"]
    assert output_schema["additionalProperties"] is False
    assert output_schema["required"] == ["value", "nested"]
    nested_schema = output_schema["$defs"]["_NestedPayload"]
    assert nested_schema["additionalProperties"] is False
    assert nested_schema["required"] == ["note"]
    turn_params = session.writes[0]["params"]
    assert "sandboxPolicy" not in turn_params
    assert turn_params["input"] == [{"type": "text", "text": "user"}]
    assert session.thread_params["config"] == {
        "default_permissions": "openclass_chat",
        "web_search": "live",
    }
    assert "built-in web search" in session.thread_params["developerInstructions"]
    assert "Role instructions:\nsystem" in session.thread_params["developerInstructions"]


def test_codex_home_is_isolated_per_openclass_user(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))

    first = codex_app_server.codex_home_path("user_a")
    second = codex_app_server.codex_home_path("user_b")

    assert first != second
    assert first.parent == second.parent == (tmp_path / "codex" / "accounts")
    assert "user_a" not in str(first)
    assert "user_b" not in str(second)


def test_copy_codex_auth_preserves_existing_target_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    source_home = codex_app_server.codex_home_path("guest_a")
    target_home = codex_app_server.codex_home_path("user_a")
    source_home.mkdir(parents=True)
    target_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"verified": true}', encoding="utf-8")
    (target_home / "state_5.sqlite").write_text("existing runtime", encoding="utf-8")

    codex_app_server.copy_codex_auth("guest_a", "user_a")

    assert (target_home / "auth.json").read_text(encoding="utf-8") == '{"verified": true}'
    assert (target_home / "state_5.sqlite").read_text(encoding="utf-8") == "existing runtime"
    assert (target_home / "auth.json").stat().st_mode & 0o777 == 0o600


def test_remove_codex_auth_preserves_source_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_HOME", str(tmp_path / "codex"))
    source_home = codex_app_server.codex_home_path("guest_a")
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"refresh": "credential"}', encoding="utf-8")
    (source_home / "state_5.sqlite").write_text("guest runtime", encoding="utf-8")

    codex_app_server.remove_codex_auth("guest_a")

    assert (source_home / "auth.json").exists() is False
    assert (source_home / "state_5.sqlite").read_text(encoding="utf-8") == "guest runtime"


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


def test_login_watcher_does_not_read_account_before_login_completion(monkeypatch) -> None:
    class _Messages:
        def __init__(self) -> None:
            self.items: list[dict] = []

        def get(self, timeout: float) -> dict:
            if self.items:
                return self.items.pop(0)
            raise queue.Empty

    class _Session:
        def __init__(self) -> None:
            self._messages = _Messages()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(codex_app_server, "CODEX_LOGIN_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda *_args, **_kwargs: pytest.fail("account/read ran before account/updated"),
    )

    codex_app_server._watch_login_attempt(attempt)

    assert attempt.status == "expired"
    assert attempt.error == "Codex login timed out"
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


def test_cancelled_login_cannot_be_overwritten_by_late_success(monkeypatch) -> None:
    account_read_started = threading.Event()
    release_account_read = threading.Event()

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
            "params": {"loginId": "login_cancel_race", "success": True},
        }
    )
    session._messages.put(
        {"method": "account/updated", "params": {"authMode": "chatgpt"}}
    )
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_cancel_race",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=session,  # type: ignore[arg-type]
    )

    def delayed_account_read(*_args, **_kwargs):
        account_read_started.set()
        assert release_account_read.wait(timeout=1)
        return CodexAccountView(type="chatgpt", email="user_a@example.test"), False

    monkeypatch.setattr(codex_app_server, "_read_account", delayed_account_read)
    watcher = threading.Thread(
        target=codex_app_server._watch_login_attempt,
        args=(attempt,),
        daemon=True,
    )
    attempt.thread = watcher
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        watcher.start()
        assert account_read_started.wait(timeout=1)
        cancelled = codex_app_server.cancel_codex_login(attempt.login_id, "user_a")
        release_account_read.set()
        watcher.join(timeout=1)

        assert cancelled.status == "cancelled"
        assert attempt.status == "cancelled"
        assert attempt.account is None
        assert watcher.is_alive() is False
    finally:
        release_account_read.set()
        watcher.join(timeout=1)
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_login_start_rejects_a_second_pending_attempt_for_same_user(monkeypatch) -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_pending",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        session=None,
    )
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()
    try:
        with pytest.raises(codex_app_server.CodexLoginRateLimitError, match="already in progress"):
            codex_app_server.start_codex_device_login("user_a")
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)
            codex_app_server._login_start_events.clear()
            codex_app_server._login_starting_users.clear()


def test_platform_login_claim_rejects_superseded_account(monkeypatch) -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="account-a@example.test"),
        completed_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (
            CodexAccountView(type="chatgpt", email="account-b@example.test"),
            False,
        ),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        with pytest.raises(codex_app_server.CodexAppServerError, match="no longer matches"):
            codex_app_server.claim_completed_codex_platform_login(attempt.login_id, "guest_a")

        assert attempt.status == "failed"
        assert attempt.completion_state == "consumed"
        assert "superseded" in (attempt.error or "")
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_platform_login_claim_is_consumed_after_matching_account(monkeypatch) -> None:
    account = CodexAccountView(type="chatgpt", email="account-a@example.test")
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=account,
        completed_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_read_account",
        lambda user_id, refresh_token=False: (account, False),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
    try:
        claimed = codex_app_server.claim_completed_codex_platform_login(attempt.login_id, "guest_a")
        codex_app_server.complete_codex_platform_login_claim(attempt.login_id, "guest_a")

        assert claimed.email == "account-a@example.test"
        assert attempt.completion_state == "consumed"
    finally:
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)


def test_unconsumed_platform_login_blocks_replacement_until_consumed() -> None:
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="guest_a",
        login_id="login_a",
        verification_url="https://example.test/device",
        user_code="ABCD-EFGH",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        purpose="platform",
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="account-a@example.test"),
        completed_at=datetime.now(timezone.utc),
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()
    try:
        with pytest.raises(codex_app_server.CodexLoginRateLimitError, match="already in progress"):
            codex_app_server._reserve_login_start("guest_a", "platform")

        attempt.completion_state = "consumed"
        codex_app_server._reserve_login_start("guest_a", "platform")
        assert "guest_a" in codex_app_server._login_starting_users
    finally:
        codex_app_server._release_login_start("guest_a")
        with codex_app_server._login_lock:
            codex_app_server._login_attempts.pop(attempt.login_id, None)
            codex_app_server._login_start_events.clear()
            codex_app_server._login_starting_users.clear()


def test_login_start_cleans_up_when_watcher_thread_cannot_start(monkeypatch) -> None:
    class _Session:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        def request(self, method: str, _params: dict, **_kwargs) -> dict:
            assert method == "account/login/start"
            return {
                "loginId": "login_thread_failure",
                "verificationUrl": "https://example.test/device",
                "userCode": "ABCD-EFGH",
            }

        def close(self) -> None:
            self.closed = True

    class _Thread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread unavailable")

    session = _Session()
    monkeypatch.setattr(codex_app_server, "codex_app_server_runtime_enabled", lambda: True)
    monkeypatch.setattr(codex_app_server, "CodexAppServerSession", lambda **_kwargs: session)
    monkeypatch.setattr(codex_app_server.threading, "Thread", _Thread)
    with codex_app_server._login_lock:
        codex_app_server._login_attempts.clear()
        codex_app_server._login_start_events.clear()
        codex_app_server._login_starting_users.clear()

    with pytest.raises(RuntimeError, match="thread unavailable"):
        codex_app_server.start_codex_device_login("user_a")

    with codex_app_server._login_lock:
        assert "login_thread_failure" not in codex_app_server._login_attempts
        assert "user_a" not in codex_app_server._login_starting_users
        codex_app_server._login_start_events.clear()
    assert session.closed is True


def test_prune_login_state_removes_old_terminal_attempt_data() -> None:
    completed_at = datetime.now(timezone.utc) - timedelta(
        seconds=codex_app_server.CODEX_LOGIN_ATTEMPT_RETENTION_SECONDS + 1
    )
    attempt = codex_app_server._LoginAttempt(
        owner_user_id="user_a",
        login_id="login_old",
        verification_url="https://example.test/device",
        user_code="SENSITIVE-CODE",
        expires_at=completed_at,
        status="succeeded",
        account=CodexAccountView(type="chatgpt", email="user_a@example.test"),
        completed_at=completed_at,
    )
    with codex_app_server._login_lock:
        codex_app_server._login_attempts[attempt.login_id] = attempt
        codex_app_server._prune_login_state_locked(
            now_monotonic=time.monotonic(),
            now_utc=datetime.now(timezone.utc),
        )
        retained = attempt.login_id in codex_app_server._login_attempts

    assert retained is False
