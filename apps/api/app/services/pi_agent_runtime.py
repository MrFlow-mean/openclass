from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from app.models import AgentActivityEvent, new_id
from app.services.ai_logging import ai_usage_logger
from app.services.config import DATA_DIR, load_root_dotenv
from app.services.structured_output import (
    json_object,
    validation_issues,
    validation_repair_prompt,
)


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
PiProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PiStructuredResponse:
    output_parsed: BaseModel
    activity: list[AgentActivityEvent]


def pi_runtime_available() -> bool:
    return shutil.which("pi") is not None


def pi_credentials_available(
    *, owner_user_id: str, runtime_root: Path | None = None
) -> bool:
    """Report whether the selected Pi account directory has usable auth state."""
    configured_agent_dir = (os.getenv("OPENCLASS_PI_AGENT_DIR") or "").strip()
    if configured_agent_dir:
        agent_dir = Path(configured_agent_dir).expanduser().resolve()
    else:
        root = runtime_root or DATA_DIR / "pi-runtime"
        owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]
        agent_dir = root / "agents" / owner_key
    auth_path = agent_dir / "auth.json"
    try:
        return auth_path.is_file() and auth_path.stat().st_size > 2
    except OSError:
        return False


def pi_agent_directory(*, owner_user_id: str, runtime_root: Path) -> Path:
    owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]
    configured_agent_dir = (os.getenv("OPENCLASS_PI_AGENT_DIR") or "").strip()
    if configured_agent_dir:
        agent_dir = Path(configured_agent_dir).expanduser().resolve()
        if not agent_dir.is_dir():
            raise RuntimeError("The configured Pi agent directory does not exist")
        return agent_dir
    agent_dir = runtime_root / "agents" / owner_key
    agent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return agent_dir


def _pi_provider(provider: str) -> str:
    return "openai-codex" if provider == "openai_codex" else provider.replace("_", "-")


def _assistant_text(stdout: str) -> str:
    final_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        error_message = message.get("errorMessage")
        if isinstance(error_message, str) and error_message.strip():
            raise RuntimeError(f"Pi model request failed: {error_message.strip()}")
        content = message.get("content")
        if isinstance(content, str):
            final_text = content.strip()
            continue
        if not isinstance(content, list):
            continue
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if text_parts:
            final_text = "".join(text_parts).strip()
    if not final_text:
        raise RuntimeError("Pi completed without an assistant response")
    return final_text


class PiTextClient:
    """Tool-free Pi runtime used behind OpenClass workflow validation."""

    def __init__(
        self,
        *,
        owner_user_id: str,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        binary: str | None = None,
        runtime_root: Path | None = None,
        process_runner: PiProcessRunner | None = None,
    ) -> None:
        resolved_binary = binary or shutil.which("pi")
        if not resolved_binary:
            raise RuntimeError("Pi is not installed on this server")
        self.owner_user_id = owner_user_id
        self.provider = provider
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.binary = resolved_binary
        self.runtime_root = runtime_root or DATA_DIR / "pi-runtime"
        self._process_runner = process_runner or subprocess.run

    def _command(self, *, system_prompt: str) -> list[str]:
        command = [
            self.binary,
            "--provider",
            _pi_provider(self.provider),
            "--model",
            self.model,
            "--mode",
            "json",
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--system-prompt",
            system_prompt,
        ]
        if self.reasoning_effort:
            command.extend(["--thinking", self.reasoning_effort])
        return command

    def _run(self, *, system_prompt: str, user_prompt: str) -> str:
        load_root_dotenv()
        agent_dir = pi_agent_directory(
            owner_user_id=self.owner_user_id,
            runtime_root=self.runtime_root,
        )
        workspace_root = self.runtime_root / "workspaces"
        workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = os.environ.copy()
        environment.update(
            {
                "PI_CODING_AGENT_DIR": str(agent_dir),
                "PI_OFFLINE": "1",
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_TELEMETRY": "0",
            }
        )
        with tempfile.TemporaryDirectory(prefix="turn-", dir=workspace_root) as temporary:
            try:
                result = self._process_runner(
                    self._command(system_prompt=system_prompt),
                    input=user_prompt,
                    text=True,
                    capture_output=True,
                    cwd=temporary,
                    env=environment,
                    timeout=180,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Pi model request timed out") from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[-600:]
            raise RuntimeError(
                "Pi model request failed"
                + (f": {detail}" if detail else f" with exit code {result.returncode}")
            )
        return _assistant_text(result.stdout)

    def parse(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[StructuredModel],
        image_inputs: list[str] | None = None,
    ) -> PiStructuredResponse:
        if image_inputs:
            raise RuntimeError("The selected Pi runtime does not accept image inputs yet")
        turn_id = new_id("piturn")
        schema_text = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        structured_system_prompt = (
            f"{system_prompt}\n\n"
            "Return one valid JSON object matching this JSON schema. "
            "Do not wrap it in Markdown and do not add prose outside the JSON.\n"
            f"JSON schema: {schema_text}"
        )
        output_text = self._run(
            system_prompt=structured_system_prompt,
            user_prompt=user_prompt,
        )
        try:
            parsed = schema.model_validate(json_object(output_text))
        except Exception as first_error:
            initial_issues = validation_issues(first_error)
            repaired_text = self._run(
                system_prompt=structured_system_prompt,
                user_prompt=(
                    f"{user_prompt}\n\nPrevious response:\n{output_text}\n\n"
                    f"{validation_repair_prompt(first_error)}"
                ),
            )
            try:
                parsed = schema.model_validate(json_object(repaired_text))
            except Exception as repair_error:
                ai_usage_logger.log_event(
                    "pi_structured_response_failed",
                    provider=self.provider,
                    model=self.model,
                    turn_id=turn_id,
                    initial_validation_issues=initial_issues,
                    repair_validation_issues=validation_issues(repair_error),
                )
                raise RuntimeError("Pi returned an invalid structured response") from repair_error
            output_text = repaired_text
            ai_usage_logger.log_event(
                "pi_structured_response_repaired",
                provider=self.provider,
                model=self.model,
                turn_id=turn_id,
                initial_validation_issues=initial_issues,
            )
        ai_usage_logger.log_event(
            "pi_request_completed",
            provider=self.provider,
            model=self.model,
            turn_id=turn_id,
            output_character_count=len(output_text),
        )
        activity = [
            AgentActivityEvent(
                turn_id=turn_id,
                stage="execute_role",
                label="Pi completed the model request",
                status="completed",
                role="pi",
                metadata={
                    "agent_backend": "pi",
                    "provider": self.provider,
                    "model": self.model,
                },
            )
        ]
        return PiStructuredResponse(output_parsed=parsed, activity=activity)
