from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import (
    AIModelSelection,
    AIProvider,
    BoardDecision,
    BoardDocument,
    BoardTeachingGuide,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    ScopeAction,
    ScopeOption,
    TeachingGuide,
    new_id,
    now_iso,
)
from app.services.ai_logging import ai_usage_logger
from app.services.ai_model_catalog import (
    ANTHROPIC_DEFAULT_TEXT_MODEL,
    ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL,
    DEEPSEEK_DEFAULT_TEXT_MODEL,
    GOOGLE_DEFAULT_TEXT_MODEL,
    KIMI_DEFAULT_TEXT_MODEL,
    MINIMAX_DEFAULT_TEXT_MODEL,
    OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL,
    default_text_selection,
)
from app.services.lesson_factory import slugify
from app.services.rich_document import build_document

logger = logging.getLogger(__name__)


def _load_root_dotenv() -> None:
    root_env = Path(__file__).resolve().parents[4] / ".env"
    if root_env.exists():
        load_dotenv(root_env)
        return
    load_dotenv()


_load_root_dotenv()
DEFAULT_TEXT_MODEL = "gpt-5-mini"
_text_model_selection: ContextVar[AIModelSelection | None] = ContextVar(
    "text_model_selection", default=None
)


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _normalize_optional_api_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith("你的_") or normalized.startswith("your_"):
        return None
    return normalized


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _extract_json_object(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty model response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError("Model response did not contain a JSON object")


def _redact_reference_payload(reference: dict[str, Any] | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    redacted = dict(reference)
    chapter_text = str(redacted.pop("chapter_text", "") or "")
    if chapter_text:
        redacted["chapter_text_redacted"] = f"<omitted {len(chapter_text)} chars>"
    return redacted


class TeacherMessageOutput(BaseModel):
    teacher_message: str


class PMAssessmentOutput(BaseModel):
    ready: bool
    reason: str
    clarification_questions: list[str] = Field(default_factory=list)
    learning_requirement_sheet: LearningRequirementSheet


class DocumentEditOutput(BaseModel):
    rationale: str
    commit_label: str = "AI document edit"
    replacement_html: str
    replacement_text: str = ""
    teacher_talk_track: str = ""
    board_teaching_guide: BoardTeachingGuide | None = None
    replace_whole: bool = False
    target_action: ScopeAction = "patch_current_lesson"
    suggested_title: str | None = None


class GeneratedLessonDocument(BaseModel):
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    content_html: str
    content_text: str = ""
    content_json: dict[str, Any] = Field(default_factory=lambda: {"type": "doc", "content": [{"type": "paragraph"}]})


@dataclass
class ParsedAIResponse:
    output_parsed: BaseModel
    id: str | None = None
    output_text: str | None = None
    usage: Any = None


@contextmanager
def bind_text_model_selection(selection: AIModelSelection | None):
    token = _text_model_selection.set(selection)
    try:
        yield
    finally:
        _text_model_selection.reset(token)


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", DEFAULT_TEXT_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_LESSON_MODEL"))
    fallback_model: str = Field(default_factory=lambda: os.getenv("OPENAI_FALLBACK_MODEL", DEFAULT_TEXT_MODEL))
    compat_api: str = Field(default_factory=lambda: os.getenv("OPENAI_COMPAT_API", "responses"))

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class DeepSeekConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY"))
    base_url: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    default_model: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_TEXT_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_LESSON_MODEL"))
    fallback_model: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_FALLBACK_MODEL", "deepseek-chat"))
    compat_api: str = Field(default_factory=lambda: os.getenv("DEEPSEEK_COMPAT_API", "chat_completions"))

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class KimiConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: _env_any("KIMI_API_KEY", "MOONSHOT_API_KEY"))
    base_url: str = Field(default_factory=lambda: _env_any("KIMI_BASE_URL", "MOONSHOT_BASE_URL") or "https://api.moonshot.ai/v1")
    default_model: str = Field(default_factory=lambda: _env_any("KIMI_MODEL", "MOONSHOT_MODEL") or KIMI_DEFAULT_TEXT_MODEL)
    pm_model: str | None = Field(default_factory=lambda: _env_any("KIMI_PM_MODEL", "MOONSHOT_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: _env_any("KIMI_BOARD_MODEL", "MOONSHOT_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: _env_any("KIMI_GUIDE_MODEL", "MOONSHOT_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: _env_any("KIMI_TEACHER_MODEL", "MOONSHOT_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: _env_any("KIMI_LESSON_MODEL", "MOONSHOT_LESSON_MODEL"))
    fallback_model: str = Field(default_factory=lambda: _env_any("KIMI_FALLBACK_MODEL", "MOONSHOT_FALLBACK_MODEL") or KIMI_DEFAULT_TEXT_MODEL)
    compat_api: str = Field(default_factory=lambda: _env_any("KIMI_COMPAT_API", "MOONSHOT_COMPAT_API") or "chat_completions")

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class MiniMaxConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_API_KEY"))
    base_url: str = Field(default_factory=lambda: os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"))
    default_model: str = Field(default_factory=lambda: os.getenv("MINIMAX_MODEL", MINIMAX_DEFAULT_TEXT_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_LESSON_MODEL"))
    fallback_model: str = Field(default_factory=lambda: os.getenv("MINIMAX_FALLBACK_MODEL", "MiniMax-M2"))
    compat_api: str = Field(default_factory=lambda: os.getenv("MINIMAX_COMPAT_API", "chat_completions"))

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class OpenAICompatibleConfig(BaseModel):
    api_key: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_API_KEY", "CUSTOM_OPENAI_API_KEY", "AI_OPENAI_COMPAT_API_KEY")
    )
    base_url: str | None = Field(
        default_factory=lambda: _env_any(
            "OPENAI_COMPATIBLE_BASE_URL",
            "CUSTOM_OPENAI_BASE_URL",
            "AI_OPENAI_COMPAT_BASE_URL",
        )
    )
    default_model: str = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_MODEL", "CUSTOM_OPENAI_MODEL", "AI_OPENAI_COMPAT_MODEL")
        or OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL
    )
    pm_model: str | None = Field(default_factory=lambda: _env_any("OPENAI_COMPATIBLE_PM_MODEL", "CUSTOM_OPENAI_PM_MODEL"))
    board_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_BOARD_MODEL", "CUSTOM_OPENAI_BOARD_MODEL")
    )
    guide_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_GUIDE_MODEL", "CUSTOM_OPENAI_GUIDE_MODEL")
    )
    teacher_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_TEACHER_MODEL", "CUSTOM_OPENAI_TEACHER_MODEL")
    )
    lesson_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_LESSON_MODEL", "CUSTOM_OPENAI_LESSON_MODEL")
    )
    fallback_model: str = Field(
        default_factory=lambda: _env_any(
            "OPENAI_COMPATIBLE_FALLBACK_MODEL",
            "CUSTOM_OPENAI_FALLBACK_MODEL",
            "AI_OPENAI_COMPAT_FALLBACK_MODEL",
        )
        or OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL
    )
    compat_api: str = Field(
        default_factory=lambda: _env_any(
            "OPENAI_COMPATIBLE_COMPAT_API",
            "OPENAI_COMPATIBLE_API_MODE",
            "CUSTOM_OPENAI_COMPAT_API",
            "AI_OPENAI_COMPAT_API",
        )
        or "chat_completions"
    )

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key) and self.base_url)

    def model_for(self, role: str) -> str:
        value = getattr(self, f"{role}_model", None)
        return value or self.default_model


class AnthropicConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    base_url: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    default_model: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_TEXT_MODEL))
    api_version: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_VERSION", "2023-06-01"))
    max_tokens: int = Field(default_factory=lambda: int(os.getenv("ANTHROPIC_MAX_TOKENS", "12000")))

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))


class AnthropicCompatibleConfig(BaseModel):
    api_key: str | None = Field(
        default_factory=lambda: _env_any(
            "ANTHROPIC_COMPATIBLE_API_KEY",
            "CUSTOM_ANTHROPIC_API_KEY",
            "AI_ANTHROPIC_COMPAT_API_KEY",
        )
    )
    base_url: str | None = Field(
        default_factory=lambda: _env_any(
            "ANTHROPIC_COMPATIBLE_BASE_URL",
            "CUSTOM_ANTHROPIC_BASE_URL",
            "AI_ANTHROPIC_COMPAT_BASE_URL",
        )
    )
    default_model: str = Field(
        default_factory=lambda: _env_any(
            "ANTHROPIC_COMPATIBLE_MODEL",
            "CUSTOM_ANTHROPIC_MODEL",
            "AI_ANTHROPIC_COMPAT_MODEL",
        )
        or ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL
    )
    api_version: str = Field(
        default_factory=lambda: _env_any(
            "ANTHROPIC_COMPATIBLE_VERSION",
            "CUSTOM_ANTHROPIC_VERSION",
            "AI_ANTHROPIC_COMPAT_VERSION",
        )
        or "2023-06-01"
    )
    max_tokens: int = Field(
        default_factory=lambda: _env_int("ANTHROPIC_COMPATIBLE_MAX_TOKENS", 12000)
    )

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key) and self.base_url)


class GoogleTextConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    base_url: str = Field(
        default_factory=lambda: os.getenv(
            "GOOGLE_GENERATIVE_LANGUAGE_BASE_URL",
            "https://generativelanguage.googleapis.com",
        )
    )
    default_model: str = Field(default_factory=lambda: os.getenv("GOOGLE_TEXT_MODEL", GOOGLE_DEFAULT_TEXT_MODEL))

    @property
    def enabled(self) -> bool:
        return bool(_normalize_optional_api_key(self.api_key))


class AnthropicTextClient:
    def __init__(self, config: AnthropicConfig) -> None:
        self.config = config

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> ParsedAIResponse:
        if not self.config.api_key:
            raise RuntimeError("Anthropic is not configured")

        tool_schema = schema.model_json_schema()
        payload = {
            "model": model,
            "max_tokens": self.config.max_tokens,
            "system": (
                f"{system_prompt}\n\n"
                "Return the final answer by calling the return_result tool. "
                "Do not put the JSON in normal text."
            ),
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [
                {
                    "name": "return_result",
                    "description": f"Return the {schema.__name__} object.",
                    "input_schema": tool_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "return_result"},
        }
        raw = self._post_json("/v1/messages", payload)
        parsed_payload: Any | None = None
        output_text_parts: list[str] = []
        for block in raw.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "return_result":
                parsed_payload = block.get("input")
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                output_text_parts.append(block["text"])
        output_text = "\n".join(output_text_parts)
        if parsed_payload is None:
            parsed_payload = _extract_json_object(output_text)
        return ParsedAIResponse(
            output_parsed=schema.model_validate(parsed_payload),
            id=raw.get("id"),
            output_text=output_text or _compact_json(parsed_payload),
            usage=raw.get("usage"),
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}{path}",
            data=data,
            headers={
                "content-type": "application/json",
                "anthropic-version": self.config.api_version,
                "x-api-key": self.config.api_key or "",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API error {exc.code}: {body}") from exc


class GoogleTextClient:
    def __init__(self, config: GoogleTextConfig) -> None:
        self.config = config

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> ParsedAIResponse:
        if not self.config.api_key:
            raise RuntimeError("Google Gemini is not configured")

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
            },
        }
        normalized_model = model.removeprefix("models/")
        path_model = urllib.parse.quote(normalized_model, safe="")
        raw = self._post_json(f"/v1beta/models/{path_model}:generateContent", payload)
        candidates = raw.get("candidates") or []
        first_candidate = candidates[0] if candidates else {}
        content = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        output_text = "\n".join(
            part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        parsed_payload = _extract_json_object(output_text)
        return ParsedAIResponse(
            output_parsed=schema.model_validate(parsed_payload),
            id=raw.get("responseId"),
            output_text=output_text,
            usage=raw.get("usageMetadata"),
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        separator = "&" if "?" in path else "?"
        url = f"{self.config.base_url.rstrip('/')}{path}{separator}key={urllib.parse.quote(self.config.api_key or '')}"
        request = urllib.request.Request(
            url,
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Google Gemini API error {exc.code}: {body}") from exc


class OpenAICourseAI:
    def __init__(self) -> None:
        self.config = OpenAIConfig()
        self.deepseek_config = DeepSeekConfig()
        self.kimi_config = KimiConfig()
        self.minimax_config = MiniMaxConfig()
        self.openai_compatible_config = OpenAICompatibleConfig()
        self.anthropic_config = AnthropicConfig()
        self.anthropic_compatible_config = AnthropicCompatibleConfig()
        self.google_config = GoogleTextConfig()
        self.client = (
            OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
            if self.config.enabled
            else None
        )
        self.deepseek_client = (
            OpenAI(api_key=self.deepseek_config.api_key, base_url=self.deepseek_config.base_url)
            if self.deepseek_config.enabled
            else None
        )
        self.kimi_client = (
            OpenAI(api_key=self.kimi_config.api_key, base_url=self.kimi_config.base_url)
            if self.kimi_config.enabled
            else None
        )
        self.minimax_client = (
            OpenAI(api_key=self.minimax_config.api_key, base_url=self.minimax_config.base_url)
            if self.minimax_config.enabled
            else None
        )
        self.openai_compatible_client = (
            OpenAI(
                api_key=self.openai_compatible_config.api_key,
                base_url=self.openai_compatible_config.base_url,
            )
            if self.openai_compatible_config.enabled
            else None
        )
        self.anthropic_client = (
            AnthropicTextClient(self.anthropic_config) if self.anthropic_config.enabled else None
        )
        self.anthropic_compatible_client = (
            AnthropicTextClient(self.anthropic_compatible_config)
            if self.anthropic_compatible_config.enabled
            else None
        )
        self.google_client = GoogleTextClient(self.google_config) if self.google_config.enabled else None

    @property
    def enabled(self) -> bool:
        return any(
            [
                self.client is not None,
                self.deepseek_client is not None,
                self.kimi_client is not None,
                self.minimax_client is not None,
                self.openai_compatible_client is not None,
                self.anthropic_client is not None,
                self.anthropic_compatible_client is not None,
                self.google_client is not None,
            ]
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "providers": {
                "openai": self.client is not None,
                "anthropic": self.anthropic_client is not None,
                "google": self.google_client is not None,
                "deepseek": self.deepseek_client is not None,
                "kimi": self.kimi_client is not None,
                "minimax": self.minimax_client is not None,
                "openai_compatible": self.openai_compatible_client is not None,
                "anthropic_compatible": self.anthropic_compatible_client is not None,
            },
            "models": {
                "pm": self.config.model_for("pm"),
                "board": self.config.model_for("board"),
                "guide": self.config.model_for("guide"),
                "teacher": self.config.model_for("teacher"),
                "lesson": self.config.model_for("lesson"),
                "deepseek": self.deepseek_config.default_model,
                "kimi": self.kimi_config.default_model,
                "minimax": self.minimax_config.default_model,
                "openai_compatible": self.openai_compatible_config.default_model,
                "anthropic_compatible": self.anthropic_compatible_config.default_model,
            },
        }

    def _call_parse(
        self,
        *,
        provider: AIProvider,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ):
        if provider == "anthropic":
            if not self.anthropic_client:
                raise RuntimeError("Anthropic is not configured")
            return self.anthropic_client.parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
        if provider == "google":
            if not self.google_client:
                raise RuntimeError("Google Gemini is not configured")
            return self.google_client.parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
        if provider == "anthropic_compatible":
            if not self.anthropic_compatible_client:
                raise RuntimeError("Anthropic-compatible API is not configured")
            return self.anthropic_compatible_client.parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
        if provider == "deepseek":
            return self._call_openai_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.deepseek_client,
                config=self.deepseek_config,
            )
        if provider == "kimi":
            return self._call_openai_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.kimi_client,
                config=self.kimi_config,
            )
        if provider == "minimax":
            return self._call_openai_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.minimax_client,
                config=self.minimax_config,
            )
        if provider == "openai_compatible":
            return self._call_openai_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.openai_compatible_client,
                config=self.openai_compatible_config,
            )
        return self._call_openai_parse(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            client=self.client,
            config=self.config,
        )

    def _call_openai_parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        client: Any | None = None,
        config: Any | None = None,
    ) -> ParsedAIResponse | Any:
        client = client or self.client
        config = config or self.config
        assert client is not None
        if config.compat_api.strip().lower() in {"chat", "chat_completions", "chat-completions"}:
            return self._call_openai_chat_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=client,
            )
        try:
            return client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text_format=schema,
            )
        except Exception as exc:
            if not self._should_retry_openai_chat_parse(exc):
                raise
            return self._call_openai_chat_parse(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=client,
            )

    def _should_retry_openai_chat_parse(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        message = str(exc).lower()
        if status_code in {404, 405} and "model_not_found" not in message:
            return True
        return "responses" in message and any(
            marker in message
            for marker in (
                "not found",
                "not supported",
                "unsupported",
                "unknown endpoint",
                "invalid url",
            )
        )

    def _call_openai_chat_parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        client: Any | None = None,
    ) -> ParsedAIResponse:
        client = client or self.client
        assert client is not None
        schema_payload = schema.model_json_schema()
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "Return only valid JSON that matches this JSON schema:\n"
                    f"{_compact_json(schema_payload)}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema_payload,
                        "strict": True,
                    },
                },
            )
        except Exception as exc:
            if not self._should_retry_openai_chat_without_schema(exc):
                raise
            response = client.chat.completions.create(model=model, messages=messages)

        output_text = self._chat_completion_text(response)
        return ParsedAIResponse(
            output_parsed=schema.model_validate(_extract_json_object(output_text)),
            id=getattr(response, "id", None),
            output_text=output_text,
            usage=getattr(response, "usage", None),
        )

    def _should_retry_openai_chat_without_schema(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return "response_format" in message or "json_schema" in message or "schema" in message

    def _chat_completion_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("Chat completion response did not include choices")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                else:
                    text = getattr(part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        raise ValueError("Chat completion response did not include text content")

    def _model_for(self, role: str) -> tuple[AIProvider, str]:
        selection = _text_model_selection.get()
        if selection:
            return selection.provider, selection.model

        default_selection = default_text_selection()
        if default_selection.provider == "anthropic":
            return "anthropic", default_selection.model or self.anthropic_config.default_model
        if default_selection.provider == "google":
            return "google", default_selection.model or self.google_config.default_model
        if default_selection.provider == "deepseek":
            return "deepseek", default_selection.model or self.deepseek_config.default_model
        if default_selection.provider == "kimi":
            return "kimi", default_selection.model or self.kimi_config.default_model
        if default_selection.provider == "minimax":
            return "minimax", default_selection.model or self.minimax_config.default_model
        if default_selection.provider == "openai_compatible":
            return "openai_compatible", default_selection.model or self.openai_compatible_config.default_model
        if default_selection.provider == "anthropic_compatible":
            return "anthropic_compatible", default_selection.model or self.anthropic_compatible_config.default_model
        return "openai", self.config.model_for(role)

    def _log_event_name(self, provider: AIProvider, suffix: str) -> str:
        return f"{provider}_text_call{suffix}"

    def _provider_available(self, provider: AIProvider) -> bool:
        if provider == "anthropic":
            return self.anthropic_client is not None
        if provider == "google":
            return self.google_client is not None
        if provider == "deepseek":
            return self.deepseek_client is not None
        if provider == "kimi":
            return self.kimi_client is not None
        if provider == "minimax":
            return self.minimax_client is not None
        if provider == "openai_compatible":
            return self.openai_compatible_client is not None
        if provider == "anthropic_compatible":
            return self.anthropic_compatible_client is not None
        return self.client is not None

    def _fallback_model_for(self, provider: AIProvider, exc: Exception, attempted_model: str) -> str | None:
        if provider == "openai_compatible":
            fallback_model = self.openai_compatible_config.fallback_model.strip()
        elif provider == "deepseek":
            fallback_model = self.deepseek_config.fallback_model.strip()
        elif provider == "kimi":
            fallback_model = self.kimi_config.fallback_model.strip()
        elif provider == "minimax":
            fallback_model = self.minimax_config.fallback_model.strip()
        elif provider == "openai":
            fallback_model = self.config.fallback_model.strip()
        else:
            return None
        if not fallback_model or fallback_model == attempted_model:
            return None

        error_code = getattr(exc, "code", None)
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                error_code = error.get("code") or error_code

        message = str(exc).lower()
        if error_code == "model_not_found" or "model_not_found" in message or "does not exist" in message:
            return fallback_model
        return None

    def _parse(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        *,
        log_user_prompt: str | None = None,
    ):
        provider, requested_model = self._model_for(role)
        call_details = {
            "provider": provider,
            "role": role,
            "model": requested_model,
            "schema": schema.__name__,
            "system_prompt": system_prompt,
            "user_prompt": log_user_prompt or user_prompt,
        }
        if not self._provider_available(provider):
            ai_usage_logger.log_event(
                self._log_event_name(provider, "_skipped"),
                **call_details,
                reason="client_disabled",
            )
            return None

        try:
            response = self._call_parse(
                provider=provider,
                model=requested_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
            ai_usage_logger.log_event(
                self._log_event_name(provider, ""),
                **call_details,
                response_id=getattr(response, "id", None),
                output_text=getattr(response, "output_text", None),
                usage=getattr(response, "usage", None),
                parsed_output=response.output_parsed,
            )
            return response.output_parsed
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            fallback_model = self._fallback_model_for(provider, exc, requested_model)
            if fallback_model:
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_retry"),
                    **call_details,
                    retry_model=fallback_model,
                    error=str(exc),
                )
                try:
                    response = self._call_parse(
                        provider=provider,
                        model=fallback_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        schema=schema,
                    )
                    ai_usage_logger.log_event(
                        self._log_event_name(provider, ""),
                        **{**call_details, "model": fallback_model},
                        fallback_from_model=requested_model,
                        response_id=getattr(response, "id", None),
                        output_text=getattr(response, "output_text", None),
                        usage=getattr(response, "usage", None),
                        parsed_output=response.output_parsed,
                    )
                    return response.output_parsed
                except Exception as retry_exc:  # pragma: no cover - network/runtime dependent
                    ai_usage_logger.log_event(
                        self._log_event_name(provider, "_error"),
                        **{**call_details, "model": fallback_model},
                        fallback_from_model=requested_model,
                        error=str(retry_exc),
                    )
                    logger.warning(
                        "OpenAI %s fallback model call failed after %s was unavailable: %s",
                        role,
                        requested_model,
                        retry_exc,
                    )
                    return None
            ai_usage_logger.log_event(
                self._log_event_name(provider, "_error"),
                **call_details,
                error=str(exc),
            )
            logger.warning("%s %s call failed, falling back to heuristic flow: %s", provider, role, exc)
            return None

    def generate_learning_requirements(
        self,
        *,
        lesson_title: str,
        lesson_summary: str,
        lesson_tags: list[str],
        document_outline: list[str] | None = None,
        block_titles: list[str] | None = None,
        user_message: str,
        selection_excerpt: str | None,
    ) -> LearningRequirementSheet | None:
        outline = document_outline or block_titles or []
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI Word-like teaching document product. "
                "Return a LearningRequirementSheet in Chinese. Infer the learner's goal, level, desired depth, "
                "output preference, document scope, and success criteria from the current rich document and request. "
                "The board is now one continuous rich document, not separate blocks. "
                "Use learning_need_catalog as a mini table of contents for the generated board: top-level items mirror "
                "major board sections, and related follow-up questions should become child items such as 7.1 or 7.2 "
                "instead of starting a separate requirement sheet."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "document_outline": outline,
                    "user_message": user_message,
                    "selection_excerpt": selection_excerpt,
                }
            ),
            schema=LearningRequirementSheet,
        )

    def assess_learning_requirements(
        self,
        *,
        lesson_title: str,
        lesson_summary: str,
        lesson_tags: list[str],
        document_outline: list[str] | None = None,
        block_titles: list[str] | None = None,
        user_message: str,
        selection_excerpt: str | None,
        conversation: list[dict[str, Any]],
    ) -> PMAssessmentOutput | None:
        outline = document_outline or block_titles or []
        return self._parse(
            "pm",
            system_prompt=(
                "You are PM AI for an AI teaching workbench. Decide whether the learner's request is clear enough. "
                "If not, set ready=false and ask 1 to 3 concise clarification questions in Chinese. "
                "If ready, set ready=true. Always provide the best current LearningRequirementSheet. "
                "Maintain exactly one cumulative learning_need_catalog for this lesson. Treat it like a mini table "
                "of contents for the board: append related new needs under the most relevant existing section "
                "using section_path values like 7.1 or 7.2; only mark clearly off-topic requests as new_topic or deferred. "
                "The visible board is a single Word-like rich document."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "lesson_summary": lesson_summary,
                    "lesson_tags": lesson_tags,
                    "document_outline": outline,
                    "conversation": conversation,
                    "user_message": user_message,
                    "selection_excerpt": selection_excerpt,
                }
            ),
            schema=PMAssessmentOutput,
        )

    def generate_board_decision(
        self,
        *,
        lesson_title: str,
        request_message: str,
        selection: dict[str, Any] | None,
        interaction_mode: str,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        resource_matches: list[dict[str, Any]],
    ) -> BoardDecision | None:
        return self._parse(
            "board",
            system_prompt=(
                "You are Board Manager AI for a Word-like teaching document. Choose one action. "
                "clarify_request asks PM follow-up questions; no_change only answers; edit_board edits the current document; "
                "append_section appends a section; create_new_lesson creates a separate lesson; await_scope_choice asks the learner to choose. "
                "Because the board is now a full rich document, prefer edit_board for requests that ask to generate or rewrite teaching material."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "selection": selection,
                    "interaction_mode": interaction_mode,
                    "scope_action": scope_action,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                    "resource_matches": resource_matches,
                }
            ),
            schema=BoardDecision,
        )

    def generate_document_edit(
        self,
        *,
        lesson_id: str,
        lesson_title: str,
        current_branch: str,
        request_message: str,
        selection: dict[str, Any] | None,
        interaction_mode: str,
        scope_action: ScopeAction | None,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
        selected_reference: dict[str, Any] | None,
    ) -> DocumentEditOutput | None:
        prompt_payload = {
            "lesson_id": lesson_id,
            "lesson_title": lesson_title,
            "current_branch": current_branch,
            "user_message": request_message,
            "selection": selection,
            "interaction_mode": interaction_mode,
            "requested_scope_action": scope_action,
            "learning_requirement_sheet": requirements.model_dump(mode="json"),
            "board_document": document.model_dump(mode="json"),
            "selected_reference": selected_reference,
        }
        log_payload = dict(prompt_payload)
        log_payload["selected_reference"] = _redact_reference_payload(selected_reference)
        return self._parse(
            "board",
            system_prompt=(
                "You are Board AI editing a Word-like rich teaching document. "
                "Return replacement_html containing coherent long-form teaching prose. "
                "If a selection is provided and the user did not explicitly ask to rewrite the whole document, edit only that selection and never rewrite the full document. "
                "For enhancement requests such as 完善/补充/详细解析/全面/展开, keep the selected original wording visible and continue writing from it instead of deleting it. "
                "If the user asks to generate or rewrite the lesson, return a complete handout-style HTML document with headings, long dialogue/body content, "
                "explanations, examples, exercises, and answers. Do not split content into blocks or cards. "
                "If selected_reference.chapter_text is provided, treat it as the full relevant chapter content and ground the handout in that chapter. "
                "Do not reuse any canned sample lesson, hard-coded topic template, or fixed example. Derive the structure and examples from the user's current request. "
                "Also return board_teaching_guide in Chinese, permanently bound to this board snapshot. "
                "In board_teaching_guide, explain which board excerpts should be taught first, why they were selected, "
                "which learner needs they correspond to, and what teaching flow Teacher AI should follow. "
                "Also return teacher_talk_track in Chinese: a short classroom-style explanation using your own words, "
                "not a recap of the document wording. It should sound like a real teacher introducing the main idea, "
                "the why behind it, and one way to understand or apply it."
            ),
            user_prompt=_json(prompt_payload),
            log_user_prompt=_json(log_payload),
            schema=DocumentEditOutput,
        )

    def generate_teaching_guide(
        self,
        *,
        lesson_id: str,
        lesson_title: str,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
    ) -> TeachingGuide | None:
        return self._parse(
            "guide",
            system_prompt=(
                "You are generating an internal teaching guide for a continuous Word-like board document. "
                "Return a TeachingGuide in Chinese. Mappings may use synthetic ids such as section_1. "
                "Explain which document sections support the goal, how to teach them, and what check questions to ask."
            ),
            user_prompt=_json(
                {
                    "lesson_id": lesson_id,
                    "lesson_title": lesson_title,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                }
            ),
            schema=TeachingGuide,
        )

    def generate_teacher_message(
        self,
        *,
        lesson_title: str,
        request_message: str,
        requirements: LearningRequirementSheet,
        board_teaching_guide: BoardTeachingGuide,
        board_decision: BoardDecision,
        document_updated: bool,
        scope_options: list[ScopeOption],
        resource_matches: list[dict[str, Any]],
        clarification_questions: list[str],
        reference_prompt: dict[str, Any] | None,
        selected_reference: dict[str, Any] | None,
    ) -> str | None:
        result = self._parse(
            "teacher",
            system_prompt=(
                "You are Teacher AI speaking to the learner in Chinese. "
                "Sound like a live teacher, not a narrator reading the board. "
                "If clarification is needed, ask at most one very short question and only about current level or learning purpose/application. "
                "If the document was updated, mention that the right-side Word-like board has been updated in one short clause only. "
                "Teach mainly from board_teaching_guide.selected_items and board_teaching_guide.teacher_brief. "
                "Do not quote, enumerate, or read out the board unless the learner explicitly asks for exact wording. "
                "Prefer this structure: first give the core idea in your own words, then explain why it matters, then offer one analogy, example, or check question. "
                "Keep the answer tight and classroom-like, with minimal transition filler. "
                "Use short paragraphs separated by blank lines. Never return one dense wall of text. "
                "Do not mention internal schemas."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_teaching_guide": board_teaching_guide.model_dump(mode="json"),
                    "board_decision": board_decision.model_dump(mode="json"),
                    "document_updated": document_updated,
                    "scope_options": [option.model_dump(mode="json") for option in scope_options],
                    "resource_matches": resource_matches,
                    "clarification_questions": clarification_questions,
                    "reference_prompt": reference_prompt,
                    "selected_reference": selected_reference,
                }
            ),
            schema=TeacherMessageOutput,
        )
        return result.teacher_message if result else None

    def generate_board_teaching_guide(
        self,
        *,
        lesson_title: str,
        request_message: str,
        requirements: LearningRequirementSheet,
        document: BoardDocument,
    ) -> BoardTeachingGuide | None:
        return self._parse(
            "board",
            system_prompt=(
                "You are Board AI preparing a teaching guide permanently bound to the current Word-like board snapshot. "
                "Return BoardTeachingGuide in Chinese. "
                "Select the most important excerpts from the board, explain why they matter, map them to the learner's needs, "
                "and provide a concise teacher_brief that can drive a live spoken explanation. "
                "Use learning_requirement_sheet.learning_need_catalog as the learner-facing mini table of contents; "
                "need_mappings and section_plans should preserve its section_path order where possible. "
                "Do not rewrite the board itself."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "board_document": document.model_dump(mode="json"),
                }
            ),
            schema=BoardTeachingGuide,
        )

    def generate_lesson_document(
        self,
        *,
        topic: str,
        reference_context: dict[str, Any] | None = None,
    ) -> GeneratedLessonDocument | None:
        prompt_payload = {"topic": topic, "reference_context": reference_context}
        log_payload = {
            "topic": topic,
            "reference_context": _redact_reference_payload(reference_context),
        }
        return self._parse(
            "lesson",
            system_prompt=(
                "You are Board AI creating a brand-new Word-like rich teaching document. "
                "Return one complete handout-style document, not blocks. Use HTML with h1/h2/h3/p/ol/ul/table when helpful. "
                "The document should be long enough for a real lesson. Do not reuse any canned sample lesson, hard-coded topic template, or fixed example. "
                "Derive the structure, examples, exercises, and pacing from the user's current topic and any reference context. Avoid card-like fragmented notes. "
                "If reference_context.chapter_text is provided, treat it as the full relevant chapter content and ground the new lesson in that chapter."
            ),
            user_prompt=_json(prompt_payload),
            log_user_prompt=_json(log_payload),
            schema=GeneratedLessonDocument,
        )

    def build_lesson_from_generated(
        self,
        *,
        topic: str,
        generated: GeneratedLessonDocument,
        requirements: LearningRequirementSheet,
        guide: TeachingGuide,
    ) -> Lesson:
        lesson_id = guide.lesson_id
        document = build_document(
            title=generated.title,
            content_html=generated.content_html,
            content_text=generated.content_text,
            content_json=generated.content_json,
        )
        commit = CommitRecord(
            label="Initial document draft",
            message=f"Generated starter rich document for {topic} via OpenAI",
            branch_name="main",
            snapshot=document,
        )
        history = LessonHistoryGraph(
            branches={
                "main": BranchRef(
                    name="main",
                    head_commit_id=commit.id,
                    base_commit_id=commit.id,
                )
            },
            commits=[commit],
            current_branch="main",
        )
        return Lesson(
            id=lesson_id,
            title=generated.title,
            slug=slugify(generated.title),
            summary=generated.summary,
            tags=generated.tags,
            board_document=document,
            learning_requirements=requirements,
            teaching_guide=guide,
            history_graph=history,
            created_at=now_iso(),
            updated_at=now_iso(),
        )


def build_generated_lesson(
    *,
    topic: str,
    generated: GeneratedLessonDocument,
    requirements: LearningRequirementSheet,
    guide_template: TeachingGuide,
) -> Lesson:
    lesson_id = new_id("lesson")
    guide = guide_template.model_copy(update={"lesson_id": lesson_id})
    document = build_document(
        title=generated.title,
        content_html=generated.content_html,
        content_text=generated.content_text,
        content_json=generated.content_json,
    )
    commit = CommitRecord(
        label="Initial document draft",
        message=f"Generated starter rich document for {topic} via OpenAI",
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main",
                head_commit_id=commit.id,
                base_commit_id=commit.id,
            )
        },
        commits=[commit],
        current_branch="main",
    )
    return Lesson(
        id=lesson_id,
        title=generated.title,
        slug=slugify(generated.title),
        summary=generated.summary or requirements.learning_goal,
        tags=generated.tags or [topic],
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


openai_course_ai = OpenAICourseAI()
