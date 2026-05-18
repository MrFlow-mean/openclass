from __future__ import annotations

import json
import logging
import os
import re
import ssl
import base64
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import (
    AIModelSelection,
    AIProvider,
    LearningRequirementChecklistItem,
)
from app.services.ai_logging import ai_usage_logger
from app.services.ai_model_catalog import (
    ANTHROPIC_DEFAULT_TEXT_MODEL,
    ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL,
    DEEPSEEK_DEFAULT_TEXT_MODEL,
    GOOGLE_DEFAULT_TEXT_MODEL,
    KIMI_DEFAULT_TEXT_MODEL,
    MINIMAX_DEFAULT_TEXT_MODEL,
    OPENAI_DEFAULT_CATALOG_MODEL,
    OPENAI_DEFAULT_TEXT_MODEL,
    OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL,
    OPENAI_OFFICIAL_BASE_URL,
    OPENAI_IMAGE_MODEL,
    default_text_selection,
)

logger = logging.getLogger(__name__)
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _load_root_dotenv() -> None:
    root_env = Path(__file__).resolve().parents[4] / ".env"
    if root_env.exists():
        load_dotenv(root_env)
        return
    load_dotenv()


_load_root_dotenv()
DEFAULT_TEXT_MODEL = OPENAI_DEFAULT_TEXT_MODEL
_text_model_selection: ContextVar[AIModelSelection | None] = ContextVar(
    "text_model_selection", default=None
)


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _shared_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")


def _google_api_key() -> str | None:
    return _env_any("GOOGLE_API_KEY", "GEMINI_API_KEY")


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


@dataclass
class ParsedAIResponse:
    output_parsed: BaseModel
    id: str | None = None
    output_text: str | None = None
    usage: Any = None


class CourseChatReply(BaseModel):
    teacher_message: str


class LearningRequirementUpdate(BaseModel):
    progress: int = Field(ge=0, le=100)
    summary: str
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    next_question: str = ""
    ready_for_board: bool = False


@contextmanager
def bind_text_model_selection(selection: AIModelSelection | None):
    token = _text_model_selection.set(selection)
    try:
        yield
    finally:
        _text_model_selection.reset(token)


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=_shared_api_key)
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL") or OPENAI_OFFICIAL_BASE_URL)
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", DEFAULT_TEXT_MODEL))
    image_model: str = Field(default_factory=lambda: os.getenv("OPENAI_IMAGE_MODEL", OPENAI_IMAGE_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_LESSON_MODEL"))
    catalog_model: str | None = Field(
        default_factory=lambda: os.getenv("OPENAI_CATALOG_MODEL", OPENAI_DEFAULT_CATALOG_MODEL)
    )
    fallback_model: str = Field(default_factory=lambda: os.getenv("OPENAI_FALLBACK_MODEL", DEFAULT_TEXT_MODEL))
    compat_api: str = Field(default_factory=lambda: os.getenv("OPENAI_COMPAT_API", "chat_completions"))

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
    catalog_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_CATALOG_MODEL"))
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
    catalog_model: str | None = Field(default_factory=lambda: _env_any("KIMI_CATALOG_MODEL", "MOONSHOT_CATALOG_MODEL"))
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
    catalog_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_CATALOG_MODEL"))
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
    catalog_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_CATALOG_MODEL", "CUSTOM_OPENAI_CATALOG_MODEL")
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
    api_key: str | None = Field(default_factory=_google_api_key)
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
            with urllib.request.urlopen(request, timeout=120, context=_URLLIB_SSL_CONTEXT) as response:
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
            with urllib.request.urlopen(request, timeout=120, context=_URLLIB_SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Google Gemini API error {exc.code}: {body}") from exc


class GeneratedCatalogChapter(BaseModel):
    title: str
    summary: str
    keywords: list[str] = Field(default_factory=list)
    level: int = Field(default=1, ge=1, le=4)


class GeneratedResourceCatalog(BaseModel):
    chapters: list[GeneratedCatalogChapter] = Field(default_factory=list)


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
        self.google_client = (
            GoogleTextClient(self.google_config)
            if self.google_config.enabled
            else None
        )

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
                "text": self.config.default_model,
                "catalog": self.config.catalog_model or self.config.default_model,
                "fallback": self.config.fallback_model,
                "image": self.config.image_model,
                "deepseek": self.deepseek_config.default_model,
                "kimi": self.kimi_config.default_model,
                "minimax": self.minimax_config.default_model,
                "openai_compatible": self.openai_compatible_config.default_model,
                "anthropic_compatible": self.anthropic_compatible_config.default_model,
            },
        }

    def generate_resource_outline(
        self,
        *,
        resource_name: str,
        extracted_text: str,
        max_chapters: int = 8,
    ) -> GeneratedResourceCatalog | None:
        compact_text = re.sub(r"\s+", " ", extracted_text or "").strip()
        if len(compact_text) < 80:
            return None
        source_excerpt = compact_text[:12000]
        system_prompt = (
            "You are the Directory AI for a general AI course workbench. "
            "Build a compact, domain-neutral table of contents for uploaded learning materials. "
            "Use only the supplied material. Do not add subject templates, fixed course content, or examples not grounded in the text."
        )
        user_prompt = _json(
            {
                "resource_name": resource_name,
                "max_chapters": max_chapters,
                "requirements": [
                    "Return 1 to max_chapters chapter entries.",
                    "Prefer real section titles when the text implies them.",
                    "If the material has no clear sections, group it by content shape and learning flow.",
                    "Keep summaries concise and useful for later retrieval.",
                    "Keywords should come from the material text, not from external knowledge.",
                ],
                "material_excerpt": source_excerpt,
            }
        )
        result = self._parse(
            "catalog",
            system_prompt,
            user_prompt,
            GeneratedResourceCatalog,
            log_user_prompt=_json(
                {
                    "resource_name": resource_name,
                    "max_chapters": max_chapters,
                    "material_excerpt_length": len(source_excerpt),
                }
            ),
        )
        if not isinstance(result, GeneratedResourceCatalog):
            return None
        result.chapters = result.chapters[:max_chapters]
        return result

    def generate_teacher_chat(
        self,
        *,
        lesson_title: str,
        learning_goal: str,
        board_summary: str,
        resource_summary: str,
        conversation_summary: str,
        user_message: str,
        selection_excerpt: str | None = None,
        interaction_mode: str = "ask",
    ) -> CourseChatReply | None:
        system_prompt = (
            "你是 OpenClass 的通用 AI 课程讲师。你的任务是像成熟聊天机器人一样进行自然、连续、"
            "有帮助的你问我答交流。\n"
            "规则：\n"
            "1. 只根据用户问题、当前课程上下文、讲义摘要、引用选区、资料摘要和最近对话回答。\n"
            "2. 不要假装已经修改讲义；如果用户要求改文档，只先给出可执行的修改建议或确认问题。\n"
            "3. 回答要直接、清楚、可继续追问；必要时用短列表、步骤或检查问题。\n"
            "4. 如果上下文不足，先给出当前可回答部分，再问一个最关键的澄清问题。\n"
            "5. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "learning_goal": learning_goal,
                "board_summary": board_summary,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "无选中引用",
                "interaction_mode": interaction_mode,
                "user_message": user_message,
                "response_contract": {
                    "teacher_message": "面向学习者的自然语言回复，支持 Markdown 风格的短段落和列表。",
                },
            }
        )
        result = self._parse(
            "teacher",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=CourseChatReply,
        )
        return result if isinstance(result, CourseChatReply) else None

    def generate_learning_requirement_update(
        self,
        *,
        lesson_title: str,
        existing_summary: str,
        existing_checklist: list[str],
        board_summary: str,
        resource_summary: str,
        conversation_summary: str,
        user_message: str,
        teacher_message: str,
    ) -> LearningRequirementUpdate | None:
        system_prompt = (
            "你是 OpenClass 的学习需求清单管理 AI，只在后端更新结构化状态，不直接和用户聊天，"
            "也不生成板书。\n"
            "任务：从最近对话和课程上下文中动态判断用户当前学习需求是否足够清晰。\n"
            "规则：\n"
            "1. checklist 必须是 3 到 5 个当前最关键的动态需求项，不使用固定栏目模板。\n"
            "2. 每个已明确项必须有来自对话或上下文的简短 evidence；不确定就 is_clear=false。\n"
            "3. 不要猜用户没有透露的信息；缺失内容写入 missing_items 或 next_question。\n"
            "4. ready_for_board 仅在这些动态需求足以支撑后续生成有用板书时为 true。\n"
            "5. 不写任何主题、资料或样例专属规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "existing_summary": existing_summary,
                "existing_checklist": existing_checklist,
                "board_summary": board_summary,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "current_user_message": user_message,
                "current_teacher_message": teacher_message,
                "response_contract": {
                    "progress": "0-100 的整体清晰度；ready_for_board=true 时必须为 100。",
                    "summary": "用户当前学习目的的一句话摘要；不清楚时说明仍需澄清。",
                    "checklist": "3-5 个动态需求项，每项包含 title、is_clear、evidence。",
                    "missing_items": "仍缺少的信息，不能脑补。",
                    "next_question": "未清晰时建议下一轮只追问一个最有价值的问题。",
                    "ready_for_board": "是否足够进入后续板书生成阶段。",
                },
            }
        )
        result = self._parse(
            "pm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=LearningRequirementUpdate,
        )
        return result if isinstance(result, LearningRequirementUpdate) else None

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

    def generate_chart_image(
        self,
        *,
        prompt: str,
        chart_type: str,
        source_excerpt: str,
    ) -> str | None:
        if self.client is None:
            return None
        model = self.config.image_model
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
        }
        image_size = os.getenv("OPENAI_IMAGE_SIZE")
        image_quality = os.getenv("OPENAI_IMAGE_QUALITY")
        if image_size:
            payload["size"] = image_size
        if image_quality:
            payload["quality"] = image_quality
        try:
            result = self.client.images.generate(**payload)
            image_items = getattr(result, "data", None) or []
            if not image_items:
                return None
            first = image_items[0]
            image_base64 = getattr(first, "b64_json", None)
            image_url = getattr(first, "url", None)
            if not image_base64 and isinstance(first, dict):
                image_base64 = first.get("b64_json")
                image_url = first.get("url")
            if not image_base64 and image_url:
                with urllib.request.urlopen(image_url, timeout=120, context=_URLLIB_SSL_CONTEXT) as response:
                    image_base64 = base64.b64encode(response.read()).decode("ascii")
            if not image_base64:
                return None
            ai_usage_logger.log_event(
                "chart_image_generated",
                model=model,
                chart_type=chart_type,
                source_excerpt=source_excerpt[:800],
            )
            return f"data:image/png;base64,{image_base64}"
        except Exception as exc:
            ai_usage_logger.log_event(
                "chart_image_error",
                model=model,
                chart_type=chart_type,
                error=str(exc),
            )
            logger.warning("OpenAI image generation failed, skipping chart image: %s", exc)
            return None

    def _model_for(self, role: str) -> tuple[AIProvider, str]:
        if role == "catalog":
            return "openai", self.config.model_for(role)

        selection = _text_model_selection.get()
        if selection:
            return selection.provider, selection.model

        default_selection = default_text_selection()
        if default_selection.provider == "openai":
            return "openai", self.config.model_for(role)
        return default_selection.provider, self._model_for_provider(default_selection.provider, role, default_selection.model)

    def _model_for_provider(self, provider: AIProvider, role: str, requested_model: str | None = None) -> str:
        if requested_model:
            return requested_model
        if provider == "anthropic":
            return self.anthropic_config.default_model
        if provider == "google":
            return self.google_config.default_model
        if provider == "deepseek":
            return self.deepseek_config.model_for(role)
        if provider == "kimi":
            return self.kimi_config.model_for(role)
        if provider == "minimax":
            return self.minimax_config.model_for(role)
        if provider == "openai_compatible":
            return self.openai_compatible_config.model_for(role)
        if provider == "anthropic_compatible":
            return self.anthropic_compatible_config.default_model
        return self.config.model_for(role)

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

    def _fallback_provider_candidates(self, failed_provider: AIProvider, role: str) -> list[tuple[AIProvider, str]]:
        ordered_providers: tuple[AIProvider, ...] = (
            "google",
            "deepseek",
            "kimi",
            "minimax",
            "openai_compatible",
            "anthropic",
            "anthropic_compatible",
            "openai",
        )
        candidates: list[tuple[AIProvider, str]] = []
        for provider in ordered_providers:
            if provider == failed_provider or not self._provider_available(provider):
                continue
            candidates.append((provider, self._model_for_provider(provider, role)))
        return candidates

    def _should_retry_provider_fallback(self, exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status_code in {401, 403, 404, 429, 500, 502, 503, 504}:
            return True
        message = str(exc).lower()
        retry_markers = (
            "incorrect api key",
            "invalid_api_key",
            "unauthorized",
            "unauthenticated",
            "permission denied",
            "permission_denied",
            "quota",
            "rate limit",
            "model_not_found",
            "does not exist",
            "not configured",
            "certificate_verify_failed",
            "connection error",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "bad gateway",
            "service unavailable",
        )
        return any(marker in message for marker in retry_markers)

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

    def _try_provider_fallback(
        self,
        *,
        role: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        call_details: dict[str, Any],
        failed_provider: AIProvider,
        failed_model: str,
        error: Exception,
    ):
        for fallback_provider, fallback_model in self._fallback_provider_candidates(failed_provider, role):
            ai_usage_logger.log_event(
                self._log_event_name(failed_provider, "_provider_retry"),
                **call_details,
                retry_provider=fallback_provider,
                retry_model=fallback_model,
                error=str(error),
            )
            fallback_details = {
                **call_details,
                "provider": fallback_provider,
                "model": fallback_model,
            }
            try:
                response = self._call_parse(
                    provider=fallback_provider,
                    model=fallback_model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                )
                ai_usage_logger.log_event(
                    self._log_event_name(fallback_provider, ""),
                    **fallback_details,
                    fallback_from_provider=failed_provider,
                    fallback_from_model=failed_model,
                    response_id=getattr(response, "id", None),
                    output_text=getattr(response, "output_text", None),
                    usage=getattr(response, "usage", None),
                    parsed_output=response.output_parsed,
                )
                return response.output_parsed
            except Exception as fallback_exc:  # pragma: no cover - network/runtime dependent
                ai_usage_logger.log_event(
                    self._log_event_name(fallback_provider, "_error"),
                    **fallback_details,
                    fallback_from_provider=failed_provider,
                    fallback_from_model=failed_model,
                    error=str(fallback_exc),
                )
                logger.warning(
                    "%s %s fallback provider call failed after %s/%s failed: %s",
                    fallback_provider,
                    role,
                    failed_provider,
                    failed_model,
                    fallback_exc,
                )
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
            provider_fallback = self._try_provider_fallback(
                role=role,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                call_details=call_details,
                failed_provider=provider,
                failed_model=requested_model,
                error=RuntimeError("client_disabled"),
            )
            if provider_fallback is not None:
                return provider_fallback
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
                    if self._should_retry_provider_fallback(retry_exc):
                        provider_fallback = self._try_provider_fallback(
                            role=role,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            schema=schema,
                            call_details={**call_details, "model": fallback_model},
                            failed_provider=provider,
                            failed_model=fallback_model,
                            error=retry_exc,
                        )
                        if provider_fallback is not None:
                            return provider_fallback
                    return None
            if self._should_retry_provider_fallback(exc):
                provider_fallback = self._try_provider_fallback(
                    role=role,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    call_details=call_details,
                    failed_provider=provider,
                    failed_model=requested_model,
                    error=exc,
                )
                if provider_fallback is not None:
                    return provider_fallback
            ai_usage_logger.log_event(
                self._log_event_name(provider, "_error"),
                **call_details,
                error=str(exc),
            )
            logger.warning("%s %s call failed, falling back to heuristic flow: %s", provider, role, exc)
            return None

openai_course_ai = OpenAICourseAI()
