from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Callable

from pydantic import BaseModel

from app.models import CodexAccountView, CodexLoginStartResponse, CodexLoginStatusResponse, CodexProviderStatus
from app.services.ai_call_budget import AICallBudgetExceeded, current_ai_call_budget


CODEX_DEFAULT_MODELS: tuple[tuple[str, str], ...] = (
    ("gpt-5.5", "OpenAI Codex GPT-5.5"),
    ("gpt-5.4", "OpenAI Codex GPT-5.4"),
    ("gpt-5.4-mini", "OpenAI Codex GPT-5.4 Mini"),
)
CODEX_APP_SERVER_TIMEOUT_SECONDS = 180
CODEX_LOGIN_TIMEOUT_SECONDS = 15 * 60
CODEX_BOARD_PERMISSION_PROFILE = "openclass_board"
CODEX_MIN_PERMISSION_PROFILE_VERSION = (0, 138, 0)
_STATUS_CACHE_TTL_SECONDS = 10


class CodexAppServerError(RuntimeError):
    pass


class CodexTurnCancelledError(CodexAppServerError):
    pass


def _deadline_for(
    timeout_seconds: float,
    *,
    deadline_monotonic: float | None = None,
) -> float:
    deadline = (
        deadline_monotonic
        if deadline_monotonic is not None
        else time.monotonic() + timeout_seconds
    )
    budget = current_ai_call_budget()
    if budget is not None:
        deadline = min(deadline, budget.deadline_monotonic)
    return deadline


def _remaining_before(deadline_monotonic: float, *, cap: float | None = None) -> float:
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        budget = current_ai_call_budget()
        if budget is not None:
            budget.checkpoint()
        raise CodexAppServerError("Timed out waiting for Codex app-server")
    return min(remaining, cap) if cap is not None else remaining


@dataclass
class CodexParsedResponse:
    output_parsed: BaseModel
    id: str | None = None
    output_text: str | None = None
    usage: Any = None


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def codex_app_server_runtime_enabled() -> bool:
    configured = os.getenv("OPENCLASS_CODEX_APP_SERVER_ENABLED")
    return True if configured is None else _env_truthy("OPENCLASS_CODEX_APP_SERVER_ENABLED")


def codex_home_path() -> Path:
    configured = (os.getenv("OPENCLASS_CODEX_HOME") or "").strip()
    path = Path(configured).expanduser() if configured else Path.home() / ".openclass" / "codex"
    return path.resolve()


def _codex_process_env() -> dict[str, str]:
    home = codex_home_path()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    return {**os.environ, "CODEX_HOME": str(home)}


def _codex_permission_config_args() -> list[str]:
    return [
        "-c",
        f'default_permissions="{CODEX_BOARD_PERMISSION_PROFILE}"',
        "-c",
        'approval_policy="never"',
        "-c",
        'web_search="disabled"',
        "-c",
        (
            f'permissions.{CODEX_BOARD_PERMISSION_PROFILE}.filesystem='
            '{":minimal"="read",":workspace_roots"={"board.md"="write"}}'
        ),
        "-c",
        f"permissions.{CODEX_BOARD_PERMISSION_PROFILE}.network.enabled=false",
        "-c",
        'shell_environment_policy.inherit="none"',
        "-c",
        (
            'shell_environment_policy.set={PATH="/usr/bin:/bin:/usr/sbin:/sbin",'
            'LANG="en_US.UTF-8",SHELL="/bin/zsh"}'
        ),
    ]


def _codex_app_server_command(binary: str) -> list[str]:
    return [
        binary,
        "app-server",
        "--strict-config",
        *_codex_permission_config_args(),
    ]


@lru_cache(maxsize=8)
def _validate_codex_cli_version(binary: str) -> None:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=_codex_process_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexAppServerError("Unable to verify the Codex CLI version") from exc
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", completed.stdout or completed.stderr)
    if completed.returncode != 0 or match is None:
        raise CodexAppServerError("Unable to verify the Codex CLI version")
    version = tuple(int(part) for part in match.groups())
    if version < CODEX_MIN_PERMISSION_PROFILE_VERSION:
        minimum = ".".join(str(part) for part in CODEX_MIN_PERMISSION_PROFILE_VERSION)
        raise CodexAppServerError(
            f"Codex CLI {minimum} or newer is required for exact board-file permissions"
        )


def _validate_effective_permission_config(result: dict[str, Any]) -> None:
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    profile_map = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
    profile = (
        profile_map.get(CODEX_BOARD_PERMISSION_PROFILE)
        if isinstance(profile_map.get(CODEX_BOARD_PERMISSION_PROFILE), dict)
        else {}
    )
    filesystem = profile.get("filesystem") if isinstance(profile.get("filesystem"), dict) else {}
    active_filesystem = {key: value for key, value in filesystem.items() if value is not None}
    network = profile.get("network") if isinstance(profile.get("network"), dict) else {}
    shell_policy = (
        config.get("shell_environment_policy")
        if isinstance(config.get("shell_environment_policy"), dict)
        else {}
    )
    if (
        config.get("sandbox_mode") is not None
        or config.get("default_permissions") != CODEX_BOARD_PERMISSION_PROFILE
        or config.get("approval_policy") != "never"
        or config.get("web_search") != "disabled"
        or active_filesystem
        != {":minimal": "read", ":workspace_roots": {"board.md": "write"}}
        or network.get("enabled") is not False
        or any(value is not None for key, value in network.items() if key != "enabled")
        or shell_policy.get("inherit") != "none"
    ):
        raise CodexAppServerError(
            "Codex effective permissions do not enforce the exact board.md-only profile"
        )


def _validate_thread_permission_response(result: dict[str, Any], *, cwd: Path) -> None:
    active_profile = (
        result.get("activePermissionProfile")
        if isinstance(result.get("activePermissionProfile"), dict)
        else {}
    )
    sandbox = result.get("sandbox") if isinstance(result.get("sandbox"), dict) else {}
    writable_roots = sandbox.get("writableRoots")
    expected_board = (cwd / "board.md").resolve()
    resolved_roots: list[Path] = []
    if isinstance(writable_roots, list):
        try:
            resolved_roots = [Path(str(value)).resolve() for value in writable_roots]
        except (OSError, RuntimeError):
            resolved_roots = []
    if (
        active_profile.get("id") != CODEX_BOARD_PERMISSION_PROFILE
        or sandbox.get("type") != "workspaceWrite"
        or resolved_roots != [expected_board]
        or sandbox.get("networkAccess") is not False
        or sandbox.get("excludeTmpdirEnvVar") is not True
        or sandbox.get("excludeSlashTmp") is not True
    ):
        raise CodexAppServerError(
            "Codex thread did not activate the exact board.md-only sandbox"
        )


def codex_binary_path() -> str | None:
    configured = (os.getenv("OPENCLASS_CODEX_CLI_PATH") or "").strip()
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which("codex")


def codex_app_server_available() -> bool:
    return codex_binary_path() is not None


def _normalize_account(raw: dict[str, Any] | None) -> CodexAccountView | None:
    if not isinstance(raw, dict):
        return None
    return CodexAccountView(
        type=str(raw.get("type") or "") or None,
        email=str(raw.get("email") or "") or None,
        plan_type=str(raw.get("planType") or raw.get("plan_type") or "") or None,
    )


def _json_response_error(message: dict[str, Any]) -> CodexAppServerError:
    error = message.get("error")
    if isinstance(error, dict):
        return CodexAppServerError(str(error.get("message") or error))
    return CodexAppServerError(str(error or message))


class CodexAppServerSession:
    def __init__(
        self,
        *,
        timeout_seconds: float = CODEX_APP_SERVER_TIMEOUT_SECONDS,
        deadline_monotonic: float | None = None,
    ) -> None:
        binary = codex_binary_path()
        if not binary:
            raise CodexAppServerError("Codex CLI is not installed or OPENCLASS_CODEX_CLI_PATH is invalid")
        _validate_codex_cli_version(binary)
        self.timeout_seconds = timeout_seconds
        self.deadline_monotonic = _deadline_for(
            timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )
        self.process = subprocess.Popen(
            _codex_app_server_command(binary),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(codex_home_path()),
            env=_codex_process_env(),
        )
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr: queue.Queue[str] = queue.Queue()
        self._next_id = 0
        self.notifications: list[dict[str, Any]] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "openclass",
                        "title": "OpenClass",
                        "version": "0.1.0",
                    }
                },
                timeout_seconds=_remaining_before(self.deadline_monotonic, cap=20),
            )
            self.notify("initialized", {})
            self.validate_board_permission_config(codex_home_path())
        except Exception:
            self.close()
            raise

    def validate_board_permission_config(self, cwd: Path) -> None:
        result = self.request(
            "config/read",
            {
                "cwd": str(cwd),
                "includeLayers": True,
            },
            timeout_seconds=_remaining_before(self.deadline_monotonic, cap=20),
        )
        _validate_effective_permission_config(result)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(
                timeout=max(0, min(3, self.deadline_monotonic - time.monotonic()))
            )
        except subprocess.TimeoutExpired:
            self.process.kill()

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                self._stderr.put(text)
                continue
            if isinstance(parsed, dict):
                self._messages.put(parsed)

    def _read_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        for line in stream:
            text = line.strip()
            if text:
                self._stderr.put(text)

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise CodexAppServerError("Codex app-server is not running")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _answer_server_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is None or not method:
            return
        self._write(
            {
                "id": request_id,
                "error": {
                    "code": -32000,
                    "message": "OpenClass Codex adapter does not grant interactive tool requests.",
                },
            }
        )

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": method, "params": params or {}})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"method": method, "id": request_id, "params": params or {}})
        return self.wait_for_response(request_id, timeout_seconds=timeout_seconds)

    def wait_for_response(self, request_id: int, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        deadline = min(
            self.deadline_monotonic,
            time.monotonic() + (timeout_seconds or self.timeout_seconds),
        )
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = self._collect_stderr()
                raise CodexAppServerError(f"Codex app-server exited unexpectedly{': ' + stderr if stderr else ''}")
            try:
                message = self._messages.get(timeout=min(0.2, _remaining_before(deadline)))
            except queue.Empty:
                continue
            _remaining_before(deadline)
            if message.get("id") == request_id:
                if "error" in message:
                    raise _json_response_error(message)
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            if "method" in message and "id" in message and "result" not in message and "error" not in message:
                self._answer_server_request(message)
            else:
                self.notifications.append(message)
        _remaining_before(deadline)
        raise CodexAppServerError(f"Timed out waiting for Codex app-server response to {request_id}")

    def _collect_stderr(self) -> str:
        lines: list[str] = []
        while not self._stderr.empty():
            lines.append(self._stderr.get())
        return "\n".join(lines[-10:])


def _read_account(refresh_token: bool = False) -> tuple[CodexAccountView | None, bool]:
    with _managed_session(timeout_seconds=30) as session:
        result = session.request("account/read", {"refreshToken": refresh_token}, timeout_seconds=30)
    return _normalize_account(result.get("account")), bool(result.get("requiresOpenaiAuth"))


@dataclass
class _ManagedSession:
    timeout_seconds: float
    deadline_monotonic: float
    session: CodexAppServerSession | None = None

    def __enter__(self) -> CodexAppServerSession:
        self.session = CodexAppServerSession(
            timeout_seconds=self.timeout_seconds,
            deadline_monotonic=self.deadline_monotonic,
        )
        return self.session

    def __exit__(self, *_exc: object) -> None:
        if self.session:
            self.session.close()


def _managed_session(
    *,
    timeout_seconds: float,
    deadline_monotonic: float | None = None,
) -> _ManagedSession:
    deadline = _deadline_for(
        timeout_seconds,
        deadline_monotonic=deadline_monotonic,
    )
    return _ManagedSession(
        timeout_seconds=_remaining_before(deadline),
        deadline_monotonic=deadline,
    )


_cached_status: tuple[float, CodexProviderStatus] | None = None


def codex_provider_status(*, refresh: bool = False, include_rate_limits: bool = False) -> CodexProviderStatus:
    global _cached_status
    enabled = codex_app_server_runtime_enabled()
    available = codex_app_server_available()
    now = time.monotonic()
    if not refresh and not include_rate_limits and _cached_status and now - _cached_status[0] < _STATUS_CACHE_TTL_SECONDS:
        cached = _cached_status[1]
        if cached.enabled == enabled and cached.available == available:
            return cached
    if not enabled:
        status = CodexProviderStatus(
            enabled=False,
            available=available,
            configured=False,
            message="Set OPENCLASS_CODEX_APP_SERVER_ENABLED=true to enable the ChatGPT/Codex provider.",
        )
        _cached_status = (time.monotonic(), status)
        return status
    if not available:
        status = CodexProviderStatus(
            enabled=True,
            available=False,
            configured=False,
            message="Codex CLI is not installed or OPENCLASS_CODEX_CLI_PATH is invalid.",
        )
        _cached_status = (time.monotonic(), status)
        return status
    try:
        account, _requires_openai_auth = _read_account(refresh_token=refresh)
        rate_limits: dict[str, Any] | None = None
        if include_rate_limits and account and account.type == "chatgpt":
            with _managed_session(timeout_seconds=30) as session:
                rate_limits = session.request("account/rateLimits/read", {}, timeout_seconds=30)
        status = CodexProviderStatus(
            enabled=True,
            available=True,
            configured=bool(account and account.type == "chatgpt"),
            account=account,
            rate_limits=rate_limits,
            message="" if account and account.type == "chatgpt" else "Sign in with ChatGPT/Codex to use subscription models.",
        )
        _cached_status = (time.monotonic(), status)
        return status
    except AICallBudgetExceeded:
        raise
    except Exception as exc:
        status = CodexProviderStatus(
            enabled=True,
            available=True,
            configured=False,
            message=str(exc),
        )
        _cached_status = (time.monotonic(), status)
        return status


def list_codex_models() -> list[dict[str, Any]]:
    if not codex_app_server_runtime_enabled() or not codex_app_server_available():
        return []
    with _managed_session(timeout_seconds=30) as session:
        result = session.request("model/list", {"limit": 20, "includeHidden": False}, timeout_seconds=30)
    data = result.get("data")
    return data if isinstance(data, list) else []


@dataclass
class _LoginAttempt:
    login_id: str
    verification_url: str
    user_code: str
    expires_at: datetime
    status: str = "pending"
    error: str | None = None
    account: CodexAccountView | None = None
    session: CodexAppServerSession | None = None
    thread: threading.Thread | None = None
    completed_at: datetime | None = None
    notifications: list[dict[str, Any]] = field(default_factory=list)


_login_lock = threading.Lock()
_login_attempts: dict[str, _LoginAttempt] = {}


def start_codex_device_login() -> CodexLoginStartResponse:
    if not codex_app_server_runtime_enabled():
        raise CodexAppServerError("OPENCLASS_CODEX_APP_SERVER_ENABLED is not enabled")
    session = CodexAppServerSession(timeout_seconds=CODEX_LOGIN_TIMEOUT_SECONDS)
    try:
        result = session.request(
            "account/login/start",
            {"type": "chatgptDeviceCode"},
            timeout_seconds=30,
        )
        login_id = str(result.get("loginId") or "")
        verification_url = str(result.get("verificationUrl") or "")
        user_code = str(result.get("userCode") or "")
        if not login_id or not verification_url or not user_code:
            raise CodexAppServerError(f"Invalid Codex device login response: {result}")
        attempt = _LoginAttempt(
            login_id=login_id,
            verification_url=verification_url,
            user_code=user_code,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=CODEX_LOGIN_TIMEOUT_SECONDS),
            session=session,
        )
        thread = threading.Thread(target=_watch_login_attempt, args=(attempt,), daemon=True)
        attempt.thread = thread
        with _login_lock:
            _login_attempts[login_id] = attempt
        thread.start()
        return CodexLoginStartResponse(
            login_id=login_id,
            verification_url=verification_url,
            user_code=user_code,
            expires_at=attempt.expires_at.isoformat(),
        )
    except Exception:
        session.close()
        raise


def _watch_login_attempt(attempt: _LoginAttempt) -> None:
    assert attempt.session is not None
    try:
        deadline = time.monotonic() + CODEX_LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                message = attempt.session._messages.get(timeout=0.5)
            except queue.Empty:
                continue
            attempt.notifications.append(message)
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "account/login/completed" and params.get("loginId") == attempt.login_id:
                success = bool(params.get("success"))
                attempt.status = "succeeded" if success else "failed"
                attempt.error = None if success else str(params.get("error") or "Codex login failed")
                attempt.completed_at = datetime.now(timezone.utc)
                continue
            if method == "account/updated" and attempt.status == "succeeded":
                account, _requires = _read_account(refresh_token=False)
                attempt.account = account
                break
        if attempt.status == "pending":
            attempt.status = "expired"
            attempt.error = "Codex login timed out"
    except Exception as exc:
        attempt.status = "failed"
        attempt.error = str(exc)
    finally:
        attempt.completed_at = attempt.completed_at or datetime.now(timezone.utc)
        attempt.session.close()
        attempt.session = None
        global _cached_status
        _cached_status = None


def codex_login_status(login_id: str) -> CodexLoginStatusResponse:
    with _login_lock:
        attempt = _login_attempts.get(login_id)
    if not attempt:
        raise CodexAppServerError("Unknown Codex login id")
    return CodexLoginStatusResponse(
        login_id=attempt.login_id,
        status=attempt.status,  # type: ignore[arg-type]
        error=attempt.error,
        account=attempt.account,
    )


def cancel_codex_login(login_id: str) -> CodexLoginStatusResponse:
    with _login_lock:
        attempt = _login_attempts.get(login_id)
    if not attempt:
        raise CodexAppServerError("Unknown Codex login id")
    if attempt.status == "pending":
        attempt.status = "cancelled"
        attempt.error = "Login cancelled"
        if attempt.session:
            try:
                attempt.session.request("account/login/cancel", {"loginId": login_id}, timeout_seconds=10)
            except Exception:
                pass
            attempt.session.close()
            attempt.session = None
    return codex_login_status(login_id)


def logout_codex() -> None:
    if not codex_app_server_runtime_enabled() or not codex_app_server_available():
        return
    with _managed_session(timeout_seconds=30) as session:
        session.request("account/logout", {}, timeout_seconds=30)
    global _cached_status
    _cached_status = None


class CodexAppServerTextClient:
    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> CodexParsedResponse:
        budget = current_ai_call_budget()
        status = codex_provider_status(refresh=False)
        if not status.configured:
            raise CodexAppServerError(status.message or "ChatGPT/Codex provider is not signed in")
        deadline_monotonic = (
            budget.deadline_monotonic
            if budget is not None
            else time.monotonic() + CODEX_APP_SERVER_TIMEOUT_SECONDS
        )
        timeout_seconds = _remaining_before(deadline_monotonic)
        with _managed_session(
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        ) as session:
            output_text, usage = _run_structured_turn(
                session=session,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                deadline_monotonic=deadline_monotonic,
            )
        if budget is not None:
            budget.validate_output(output_text)
        parsed = schema.model_validate(_extract_json(output_text))
        return CodexParsedResponse(output_parsed=parsed, output_text=output_text, usage=usage)


@dataclass(frozen=True)
class CodexTurnResult:
    thread_id: str
    turn_id: str | None
    final_response: str
    usage: Any = None
    parent_thread_id: str | None = None
    replaced_stale_thread_id: str | None = None


def _missing_codex_thread(error: CodexAppServerError) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "no rollout found for thread id",
            "thread not found",
            "no thread found",
            "unknown thread id",
        )
    )


def _discard_thread(
    session: CodexAppServerSession,
    thread_id: str,
    *,
    deadline_monotonic: float,
) -> None:
    try:
        session.request(
            "thread/delete",
            {"threadId": thread_id},
            timeout_seconds=_remaining_before(deadline_monotonic, cap=10),
        )
    except Exception:
        pass


def delete_codex_thread(thread_id: str) -> None:
    if not thread_id or not codex_app_server_available():
        return
    with _managed_session(timeout_seconds=30) as session:
        session.request("thread/delete", {"threadId": thread_id}, timeout_seconds=20)


def run_codex_thread_turn(
    *,
    model: str,
    cwd: Path,
    user_prompt: str,
    developer_instructions: str,
    thread_id: str | None = None,
    last_turn_id: str | None = None,
    fallback_user_prompt: str | None = None,
    image_urls: list[str] | None = None,
    timeout_seconds: float = CODEX_APP_SERVER_TIMEOUT_SECONDS,
    on_delta: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> CodexTurnResult:
    status = codex_provider_status(refresh=False)
    if not status.configured:
        raise CodexAppServerError(status.message or "ChatGPT/Codex provider is not signed in")
    deadline = _deadline_for(timeout_seconds)
    with _managed_session(
        timeout_seconds=_remaining_before(deadline),
        deadline_monotonic=deadline,
    ) as session:
        session.validate_board_permission_config(cwd)
        base_params: dict[str, Any] = {
            "model": model,
            "cwd": str(cwd),
            "approvalPolicy": "never",
            "developerInstructions": developer_instructions,
        }
        replaced_stale_thread_id: str | None = None
        if thread_id:
            method = "thread/fork"
            fork_params = {
                **base_params,
                "threadId": thread_id,
                "ephemeral": False,
            }
            if last_turn_id:
                fork_params["lastTurnId"] = last_turn_id
            try:
                thread_result = session.request(
                    method,
                    fork_params,
                    timeout_seconds=_remaining_before(deadline, cap=30),
                )
            except CodexAppServerError as exc:
                if not _missing_codex_thread(exc):
                    raise
                replaced_stale_thread_id = thread_id
                method = "thread/start"
                thread_result = session.request(
                    method,
                    {
                        **base_params,
                        "ephemeral": False,
                        "serviceName": "openclass_codex_chat",
                    },
                    timeout_seconds=_remaining_before(deadline, cap=30),
                )
        else:
            method = "thread/start"
            thread_result = session.request(
                method,
                {
                    **base_params,
                    "ephemeral": False,
                    "serviceName": "openclass_codex_chat",
                },
                timeout_seconds=_remaining_before(deadline, cap=30),
            )
        thread = thread_result.get("thread") if isinstance(thread_result.get("thread"), dict) else {}
        active_thread_id = str(thread.get("id") or "")
        if not active_thread_id:
            raise CodexAppServerError(f"Codex {method} did not return a thread id: {thread_result}")
        try:
            _validate_thread_permission_response(thread_result, cwd=cwd)
            turn_result = _run_conversation_turn(
                session=session,
                thread_id=active_thread_id,
                model=model,
                cwd=cwd,
                user_prompt=(
                    fallback_user_prompt
                    if replaced_stale_thread_id and fallback_user_prompt is not None
                    else user_prompt
                ),
                image_urls=image_urls,
                deadline_monotonic=deadline,
                on_delta=on_delta,
                is_cancelled=is_cancelled,
            )
        except Exception:
            _discard_thread(session, active_thread_id, deadline_monotonic=deadline)
            raise
        return CodexTurnResult(
            thread_id=turn_result.thread_id,
            turn_id=turn_result.turn_id,
            final_response=turn_result.final_response,
            usage=turn_result.usage,
            parent_thread_id=thread_id,
            replaced_stale_thread_id=replaced_stale_thread_id,
        )


def _run_conversation_turn(
    *,
    session: CodexAppServerSession,
    thread_id: str,
    model: str,
    cwd: Path,
    user_prompt: str,
    image_urls: list[str] | None,
    deadline_monotonic: float,
    on_delta: Callable[[str], None] | None,
    is_cancelled: Callable[[], bool] | None,
) -> CodexTurnResult:
    request_id = session._next_id
    session._next_id += 1
    turn_input: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    turn_input.extend(
        {"type": "image", "url": url, "detail": "original"}
        for url in image_urls or []
    )
    session._write(
        {
            "method": "turn/start",
            "id": request_id,
            "params": {
                "threadId": thread_id,
                "input": turn_input,
                "model": model,
                "cwd": str(cwd),
                "approvalPolicy": "never",
            },
        }
    )
    final_text = ""
    delta_text = ""
    turn_id: str | None = None
    usage: Any = None
    while time.monotonic() < deadline_monotonic:
        if is_cancelled is not None and is_cancelled():
            if turn_id:
                try:
                    session.request(
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": turn_id},
                        timeout_seconds=_remaining_before(deadline_monotonic, cap=10),
                    )
                except Exception:
                    pass
            raise CodexTurnCancelledError("Codex turn was cancelled")
        try:
            message = session._messages.get(
                timeout=min(0.25, _remaining_before(deadline_monotonic))
            )
        except queue.Empty:
            continue
        _remaining_before(deadline_monotonic)
        if message.get("id") == request_id:
            if "error" in message:
                raise _json_response_error(message)
            result = message.get("result") if isinstance(message.get("result"), dict) else {}
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or turn_id or "") or None
            continue
        if "method" in message and "id" in message and "result" not in message and "error" not in message:
            session._answer_server_request(message)
            continue
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or turn_id or "") or None
        elif method == "thread/tokenUsage/updated":
            usage = params
        elif method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                delta_text += delta
                if on_delta is not None:
                    on_delta(delta)
        elif method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                final_text = item["text"]
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            turn_id = str(turn.get("id") or turn_id or "") or None
            turn_status = str(turn.get("status") or "")
            if turn_status != "completed":
                error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                raise CodexAppServerError(
                    str(error.get("message") or error or f"Codex turn ended with status {turn_status or 'unknown'}")
                )
            response = (final_text or delta_text).strip()
            if not response:
                raise CodexAppServerError("Codex turn completed without an agent message")
            return CodexTurnResult(
                thread_id=thread_id,
                turn_id=turn_id,
                final_response=response,
                usage=usage,
            )
    _remaining_before(deadline_monotonic)
    raise CodexAppServerError("Timed out waiting for Codex turn completion")


def _run_structured_turn(
    *,
    session: CodexAppServerSession,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[BaseModel],
    deadline_monotonic: float | None = None,
) -> tuple[str, Any]:
    deadline = _deadline_for(
        CODEX_APP_SERVER_TIMEOUT_SECONDS,
        deadline_monotonic=(
            deadline_monotonic
            if deadline_monotonic is not None
            else session.deadline_monotonic
        ),
    )
    with tempfile.TemporaryDirectory(prefix="openclass-codex-") as cwd:
        thread_result = session.request(
            "thread/start",
            {
                "model": model,
                "cwd": cwd,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "serviceName": "openclass_codex_provider",
            },
            timeout_seconds=_remaining_before(deadline, cap=30),
        )
        _remaining_before(deadline)
        thread = thread_result.get("thread") if isinstance(thread_result.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise CodexAppServerError(f"Codex thread/start did not return a thread id: {thread_result}")
        request_id = session._next_id
        session._next_id += 1
        prompt = (
            "You are the model provider adapter for OpenClass. Follow the system instructions below, "
            "then answer the user request. Return only a JSON object matching the provided output schema. "
            "Do not inspect files, run shell commands, call tools, or modify anything.\n\n"
            f"System instructions:\n{system_prompt}\n\n"
            f"User request:\n{user_prompt}"
        )
        session._write(
            {
                "method": "turn/start",
                "id": request_id,
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "model": model,
                    "cwd": cwd,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly"},
                    "outputSchema": schema.model_json_schema(),
                },
            }
        )
        final_text = ""
        usage: Any = None
        while time.monotonic() < deadline:
            try:
                message = session._messages.get(timeout=min(0.5, _remaining_before(deadline)))
            except queue.Empty:
                continue
            _remaining_before(deadline)
            if message.get("id") == request_id and "error" in message:
                raise _json_response_error(message)
            if "method" in message and "id" in message and "result" not in message and "error" not in message:
                session._answer_server_request(message)
                continue
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "thread/tokenUsage/updated":
                usage = params
            if method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    final_text = item["text"]
            if method == "turn/completed":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                if turn.get("status") == "failed":
                    error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                    raise CodexAppServerError(str(error.get("message") or error or "Codex turn failed"))
                if final_text:
                    return final_text, usage
                raise CodexAppServerError("Codex turn completed without an agent message")
        _remaining_before(deadline)
        raise CodexAppServerError("Timed out waiting for Codex turn completion")


def _extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        raise CodexAppServerError("Codex returned an empty response")
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            stripped = parts[1]
            if stripped.lstrip().lower().startswith("json"):
                stripped = stripped.lstrip()[4:]
            stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise
