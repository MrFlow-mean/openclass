from __future__ import annotations

import json
import logging
import os
import re
import ssl
import base64
import ast
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Callable, Iterator
from typing import Any, Literal

import certifi
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from app.models import (
    AIModelSelection,
    AIProvider,
    BoardTaskAction,
    InteractionRuleDraft,
    InteractionSession,
    InteractionTurnDecision,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
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
from app.services.config import load_root_dotenv

logger = logging.getLogger(__name__)
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _load_root_dotenv() -> None:
    load_root_dotenv()


_load_root_dotenv()
DEFAULT_TEXT_MODEL = OPENAI_DEFAULT_TEXT_MODEL
_text_model_selection: ContextVar[AIModelSelection | None] = ContextVar(
    "text_model_selection", default=None
)
AIStreamObserver = Callable[[dict[str, Any]], None]
_ai_stream_observer: ContextVar[AIStreamObserver | None] = ContextVar("ai_stream_observer", default=None)


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


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _decode_partial_json_string(raw: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char != "\\":
            decoded.append(char)
            index += 1
            continue
        if index + 1 >= len(raw):
            break
        escaped = raw[index + 1]
        if escaped == "n":
            decoded.append("\n")
            index += 2
            continue
        if escaped == "r":
            decoded.append("\r")
            index += 2
            continue
        if escaped == "t":
            decoded.append("\t")
            index += 2
            continue
        if escaped in {'"', "\\", "/"}:
            decoded.append(escaped)
            index += 2
            continue
        if escaped == "u":
            hex_value = raw[index + 2 : index + 6]
            if len(hex_value) < 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", hex_value):
                break
            decoded.append(chr(int(hex_value, 16)))
            index += 6
            continue
        decoded.append(escaped)
        index += 2
    return "".join(decoded)


def _partial_json_string_field_value(text: str, field_name: str) -> str:
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"', text)
    if not match:
        return ""
    index = match.end()
    raw_chars: list[str] = []
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            raw_chars.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            raw_chars.append(char)
            escaped = True
            index += 1
            continue
        if char == '"':
            break
        raw_chars.append(char)
        index += 1
    return _decode_partial_json_string("".join(raw_chars))


def _json_loads_lenient(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        first_error = exc

    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, dict | list):
            return parsed
    except (SyntaxError, ValueError):
        pass

    quoted_keys = re.sub(
        r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:",
        lambda match: f'{match.group(1)}"{match.group(2)}":',
        value,
    )
    if quoted_keys != value:
        try:
            return json.loads(quoted_keys)
        except json.JSONDecodeError:
            pass
        try:
            parsed = ast.literal_eval(quoted_keys)
            if isinstance(parsed, dict | list):
                return parsed
        except (SyntaxError, ValueError):
            pass

    raise first_error


def _extract_json_object(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty model response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return _json_loads_lenient(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return _json_loads_lenient(stripped[start : end + 1])
    raise ValueError("Model response did not contain a JSON object")


class AIOutputParseError(ValueError):
    def __init__(self, message: str, *, output_text: str | None = None, repair_output_text: str | None = None) -> None:
        super().__init__(message)
        self.output_text = output_text
        self.repair_output_text = repair_output_text


@dataclass
class ParsedAIResponse:
    output_parsed: BaseModel
    id: str | None = None
    output_text: str | None = None
    usage: Any = None


class ChatbotReply(BaseModel):
    chatbot_message: str


BoardDocumentEditOperation = Literal["replace_document", "replace_selection", "append_section"]


class BoardDocumentEditResult(BaseModel):
    operation: BoardDocumentEditOperation = "replace_document"
    title: str = ""
    content_text: str = ""
    content_html: str = ""
    summary: str = ""
    chatbot_message: str = ""
    section_titles: list[str] = Field(default_factory=list)


class LearningRequirementUpdate(BaseModel):
    progress: int = Field(ge=0, le=100)
    summary: str
    key_facts: list[LearningRequirementKeyFact] = Field(default_factory=list)
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    next_question: str = ""
    ready_for_board: bool = False
    action_type: BoardTaskAction | None = None
    action_instruction: str = ""
    target_hint: str = ""
    interaction_rule_draft: InteractionRuleDraft | None = None

    @field_validator("next_question", "action_instruction", "target_hint", mode="before")
    @classmethod
    def _empty_string_for_null_text(cls, value: Any) -> Any:
        return "" if value is None else value


class ComplexProblemSolution(BaseModel):
    summary: str = ""
    answer: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"
    limits: str = ""
    model: str = ""
    reasoning_effort: str = ""


@contextmanager
def bind_text_model_selection(selection: AIModelSelection | None):
    token = _text_model_selection.set(selection)
    try:
        yield
    finally:
        _text_model_selection.reset(token)


@contextmanager
def bind_ai_output_stream(observer: AIStreamObserver | None) -> Iterator[None]:
    token = _ai_stream_observer.set(observer)
    try:
        yield
    finally:
        _ai_stream_observer.reset(token)


class OpenAIConfig(BaseModel):
    api_key: str | None = Field(default_factory=_shared_api_key)
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL") or OPENAI_OFFICIAL_BASE_URL)
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", DEFAULT_TEXT_MODEL))
    image_model: str = Field(default_factory=lambda: os.getenv("OPENAI_IMAGE_MODEL", OPENAI_IMAGE_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    chatbot_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CHATBOT_MODEL"))
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
    chatbot_model: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_CHATBOT_MODEL"))
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
    chatbot_model: str | None = Field(default_factory=lambda: _env_any("KIMI_CHATBOT_MODEL", "MOONSHOT_CHATBOT_MODEL"))
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
    chatbot_model: str | None = Field(default_factory=lambda: os.getenv("MINIMAX_CHATBOT_MODEL"))
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
    chatbot_model: str | None = Field(
        default_factory=lambda: _env_any("OPENAI_COMPATIBLE_CHATBOT_MODEL", "CUSTOM_OPENAI_CHATBOT_MODEL")
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
                "strong_reasoning": os.getenv("OPENAI_STRONG_REASONING_MODEL", "gpt-5.5"),
                "pro_reasoning": os.getenv("OPENAI_PRO_REASONING_MODEL", "gpt-5.5-pro"),
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

    def generate_chatbot_reply(
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
        interaction_context: dict[str, Any] | None = None,
    ) -> ChatbotReply | None:
        system_prompt = (
            "你是 OpenClass 的 Chatbot，负责左侧聊天框里的自然、连续、有帮助的你问我答交流。\n"
            "规则：\n"
            "1. 只根据用户问题、当前课程上下文、讲义摘要、引用选区、资料摘要和最近对话回答。\n"
            "2. Chatbot 不生成整篇文档、板书、讲义、课文、练习、试题、对话稿等长篇产物；"
            "这类内容只能写入右侧文档区。用户要求生成可写入文档的内容时，"
            "只做简短承接、确认或引导，不要把正文铺在聊天框里。\n"
            "3. 不要假装已经修改讲义；如果用户要求改文档，只先给出可执行的修改建议或确认问题。\n"
            "4. 回答要直接、清楚、可继续追问；必要时用短列表、步骤或检查问题；"
            "除非是在讲解既有文档内容，否则回复保持短小。\n"
            "5. 如果学习需求还不清楚，先说明澄清是为了匹配讲解深度、材料组织和练习方式，"
            "再从具体想学什么、当前水平、学习目的/使用场景中选择最缺的一项追问。\n"
            "6. 如果用户明确说“直接讲、开始讲、从零开始、当我是零基础、不要再问”等教学启动意图，"
            "并且已经有可识别的学习主题，就先讲一个最基础的小节；结尾只用一个理解检查或继续提示，"
            "不要再把子知识点偏好当成开始前的必答条件。\n"
            "7. 每次最多追问一个主问题；可以给 2-3 个可选回答方向，但不要像机械问卷或客服套话。\n"
            "8. 如果 interaction_context 存在，说明系统正在执行用户指定的通用互动规则；"
            "回复必须同时参考互动规则、原文内容、互动进度和用户当前输入，但不要输出系统字段名。\n"
            "9. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
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
                "interaction_context": interaction_context or None,
                "user_message": user_message,
                "response_contract": {
                    "chatbot_message": "面向学习者的自然语言短回复；不要输出整篇可写入文档区的正文。",
                },
            }
        )
        result = self._parse(
            "chatbot",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=ChatbotReply,
        )
        return result if isinstance(result, ChatbotReply) else None

    def solve_complex_problem(
        self,
        *,
        lesson_title: str,
        question: str,
        target_excerpt: str = "",
        board_summary: str = "",
        resource_summary: str = "",
        conversation_summary: str = "",
        desired_output: str = "",
        high_value: bool = False,
    ) -> ComplexProblemSolution | None:
        if not self.client:
            ai_usage_logger.log_event(
                "openai_strong_reasoning_skipped",
                model=os.getenv("OPENAI_STRONG_REASONING_MODEL", "gpt-5.5"),
                reason="client_disabled",
            )
            return None
        model = os.getenv("OPENAI_STRONG_REASONING_MODEL", "gpt-5.5")
        if high_value and (os.getenv("OPENCLASS_STRONG_REASONING_ALLOW_PRO") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            model = os.getenv("OPENAI_PRO_REASONING_MODEL", "gpt-5.5-pro")
        reasoning_effort = os.getenv("OPENAI_STRONG_REASONING_EFFORT", "high")
        observer = _ai_stream_observer.get()
        if observer:
            observer(
                {
                    "type": "role_start",
                    "role": "strong_reasoning",
                    "provider": "openai",
                    "model": model,
                }
            )
        system_prompt = (
            "你是 OpenClass Chatbot 的隐藏强推理工具，只提供解题材料，不直接面向学习者发言。\n"
            "规则：\n"
            "1. 只解决用户问题本身，不修改板书、不生成整篇文档、不扮演新的 AI 角色。\n"
            "2. 根据课程标题、目标片段、板书摘要、资料摘要和最近对话进行严谨分析。\n"
            "3. 输出要便于 Chatbot 直接转述：给出结论、关键依据、必要步骤和不确定性。\n"
            "4. 不写任何学科、教材、考试或样例专属分支；换成任意主题后规则仍成立。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "question": question,
                "target_excerpt": target_excerpt or "无",
                "board_summary": board_summary or "无",
                "resource_summary": resource_summary or "无",
                "recent_conversation": conversation_summary or "无",
                "desired_output": desired_output or "由 Chatbot 用适合学习者的方式讲解。",
                "response_contract": {
                    "summary": "一句话概括强推理结果。",
                    "answer": "给 Chatbot 的可转述答案材料；不要泄露内部推理链。",
                    "confidence": "low、medium 或 high。",
                    "limits": "必要的不确定性或前提；没有则留空。",
                    "model": "实际使用的强推理模型。",
                    "reasoning_effort": "实际 reasoning effort。",
                },
            }
        )
        started_at = time.perf_counter()
        try:
            response = self.client.responses.parse(
                model=model,
                reasoning={"effort": reasoning_effort},
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text_format=ComplexProblemSolution,
            )
            parsed = response.output_parsed
            if not isinstance(parsed, ComplexProblemSolution):
                ai_usage_logger.log_event(
                    "openai_strong_reasoning_empty",
                    provider="openai",
                    role="strong_reasoning",
                    model=model,
                    reasoning_effort=reasoning_effort,
                    duration_ms=_elapsed_ms(started_at),
                    response_id=getattr(response, "id", None),
                    usage=getattr(response, "usage", None),
                )
                return None
            parsed.model = parsed.model or model
            parsed.reasoning_effort = parsed.reasoning_effort or reasoning_effort
            ai_usage_logger.log_event(
                "openai_strong_reasoning_call",
                provider="openai",
                role="strong_reasoning",
                model=model,
                reasoning_effort=reasoning_effort,
                duration_ms=_elapsed_ms(started_at),
                response_id=getattr(response, "id", None),
                usage=getattr(response, "usage", None),
                parsed_output=parsed,
            )
            return parsed
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            ai_usage_logger.log_event(
                "openai_strong_reasoning_error",
                provider="openai",
                role="strong_reasoning",
                model=model,
                reasoning_effort=reasoning_effort,
                duration_ms=_elapsed_ms(started_at),
                error=str(exc),
            )
            logger.warning("OpenAI strong reasoning call failed: %s", exc)
            return None

    def generate_interaction_turn_decision(
        self,
        *,
        lesson_title: str,
        session: InteractionSession,
        board_summary: str,
        resource_summary: str,
        conversation_summary: str,
        user_message: str,
        selection_excerpt: str | None = None,
    ) -> InteractionTurnDecision | None:
        system_prompt = (
            "你是 OpenClass 的互动规则路由 AI，只做结构化判断，不直接和用户聊天。\n"
            "任务：根据当前互动会话、原文上下文、最近对话和用户输入，判断本轮应该如何路由。\n"
            "规则：\n"
            "1. route 只能是 continue_rule、rule_violation、side_learning_request、resume_rule、exit_rule、new_task。\n"
            "2. continue_rule 表示用户输入符合当前互动规则，应继续按规则互动。\n"
            "3. rule_violation 表示用户仍在当前互动里，但输入不符合规则，应让 Chatbot 在规则内纠错。\n"
            "4. side_learning_request 表示用户临时询问原文、词句、概念、步骤或原因，应暂停互动并讲解。\n"
            "5. resume_rule 表示用户想恢复 paused 状态的互动。\n"
            "6. exit_rule 表示用户明确结束当前互动规则。\n"
            "7. new_task 表示用户开启了新的生成、编辑、定位或学习任务。\n"
            "8. progress_note 只记录通用进度，不写固定场景模板或样例内容。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "interaction_session": session.model_dump(mode="json"),
                "board_summary": board_summary,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "无选中引用",
                "user_message": user_message,
                "response_contract": {
                    "route": "continue_rule、rule_violation、side_learning_request、resume_rule、exit_rule 或 new_task。",
                    "reason": "本轮路由理由；用于内部记录，不面向用户。",
                    "progress_note": "本轮后应保存的互动进度摘要；没有变化可沿用原摘要。",
                    "user_intent": "对用户本轮意图的简短结构化描述。",
                },
            }
        )
        result = self._parse(
            "pm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=InteractionTurnDecision,
        )
        return result if isinstance(result, InteractionTurnDecision) else None

    def generate_board_document_edit(
        self,
        *,
        intent: str,
        lesson_title: str,
        learning_requirement_context: dict[str, Any],
        current_document_title: str,
        current_document_text: str,
        resource_summary: str,
        conversation_summary: str,
        user_instruction: str,
        selection_excerpt: str | None = None,
    ) -> BoardDocumentEditResult | None:
        system_prompt = (
            "你是 OpenClass 的板书文档编辑 AI，只负责生成或编辑板书文档，不负责学习需求澄清，"
            "也不扮演 Chatbot。\n"
            "规则：\n"
            "1. 只根据学习需求清单、用户指令、当前板书、选区、资料摘要和最近对话写入文档内容。\n"
            "2. intent=generate_from_requirements 时，输出一份完整板书，operation 使用 replace_document，"
            "content_text 必须包含清晰章节标题；默认按一节可直接教学的完整文档篇幅生成，"
            "优先组织多个相互衔接的 H2 小节，篇幅要足以支撑一节课直接教学，"
            "除非用户明确要求短版、速览或只要大纲。\n"
            "3. intent=edit_existing_document 时，有选区就优先 replace_selection；需要新增内容时用 append_section；"
            "不要擅自整体覆盖已有文档。\n"
            "4. content_text 是可直接进入文档的正文；可用 Markdown 表达标题、粗体、列表和表格，"
            "不要用代码块包裹全文。content_html 通常留空；后端会把 content_text 规范化为可编辑富文本。\n"
            "5. 如果 intent=edit_existing_document 且用户要求翻译、语言转换或表达转换整篇当前文档，"
            "operation 使用 replace_document，content_text 返回转换后的整篇文档，不要只返回说明。\n"
            "6. 完整生成时，每个主要 H2 小节都要有可讲解密度：核心解释、必要步骤或推理、"
            "至少一个例子或类比、常见误区/注意点、一个检查问题。不要只写目录式提纲。\n"
            "7. section_titles 写入本次文档的主要 H2 章节标题，用于后续分节讲解。\n"
            "8. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "intent": intent,
                "lesson_title": lesson_title,
                "learning_requirement_context": learning_requirement_context,
                "current_document_title": current_document_title,
                "current_document_text": current_document_text,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "无选中引用",
                "user_instruction": user_instruction,
                "response_contract": {
                    "operation": "replace_document、replace_selection 或 append_section。",
                    "title": "文档标题；局部编辑时可沿用当前标题。",
                    "content_text": (
                        "完整生成时是整份板书，默认按一节可直接教学的较完整篇幅展开；"
                        "局部替换时是替换片段；追加时是追加片段。"
                    ),
                    "content_html": "可选 HTML，与 content_text 表达同一内容。",
                    "summary": "一句话说明本次生成或编辑了什么。",
                    "chatbot_message": "可直接展示给学习者的自然语言短回复，说明本次动作结果，不要套用固定格式。",
                    "section_titles": "主要章节标题数组，用于分节讲解。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardDocumentEditResult,
        )
        return result if isinstance(result, BoardDocumentEditResult) else None

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
        chatbot_message: str,
    ) -> LearningRequirementUpdate | None:
        system_prompt = (
            "你是 OpenClass 的学习需求清单管理 AI，只在后端更新结构化状态，不直接和用户聊天，"
            "也不生成板书。\n"
            "任务：从最近对话和课程上下文中动态判断用户当前学习需求是否足够清晰。\n"
            "规则：\n"
            "1. key_facts 只写用户已经透露的关键信息，每项包含 category、label、value、evidence；"
            "category 必须是 learning、level、vocabulary、scenario、output、other 之一，"
            "label 只做中文展示短标签，value 保留用户透露的具体内容；"
            "不要使用 preferred_output、output_preference 等内部字段名，也不要记录输出形式偏好；"
            "不要把缺失信息、Chatbot 追问、系统默认选项或推测写进去；没有就返回空数组。\n"
            "2. checklist 必须是 3 到 5 个当前最关键的动态需求项，不使用固定栏目模板。\n"
            "3. 优先判断三类通用信息是否足够：用户具体想学什么或解决什么问题、用户当前水平/已有基础、"
            "用户为什么学以及要面对什么任务或使用场景；若对话中已有其他更关键约束，可以动态替换或合并。\n"
            "4. 如果用户是在已有文档、资料或上一轮结构总结基础上要求继续讲解、展开讲解、讲透一点，"
            "这不是新的初始需求澄清；不要把缺少水平、学习目的或使用场景写成阻塞项，"
            "可以记录 action_type/action_instruction，但 next_question 应为空，除非当前讲解确实无法继续。\n"
            "5. checklist 每个已明确项必须有来自对话或上下文的简短 evidence；不确定就 is_clear=false。\n"
            "6. 不要猜用户没有透露的信息；缺失内容写入 missing_items 或 next_question。\n"
            "7. next_question 只问下一轮最有价值的一个问题，语言自然，避免机械套话。\n"
            "8. ready_for_board 仅在这些动态需求足以支撑后续生成有用板书时为 true。\n"
            "9. 如果用户表达的是对现有板书局部内容的动作，额外填写 action_type、action_instruction、target_hint："
            "action_type 只能是 generate_board、explain_target、rewrite_target、expand_target、simplify_target；"
            "target_hint 只写用户给出的定位线索，不猜具体段落 ID。\n"
            "10. 如果用户表达的是翻译、语言转换或表达转换已有板书/文档内容，这是 rewrite_target，"
            "不是 generate_board；target_hint 写用户给出的选区、全文、整篇或当前文档等通用定位线索。\n"
            "11. 如果用户表达的是希望系统按照某种规则进行连续对话或学习互动，"
            "填写 interaction_rule_draft；它只描述用户给出的通用互动规则、目标、目标线索、用户应如何输入、"
            "Chatbot 应如何输出，不写任何具体主题或样例专属分支。\n"
            "12. 不写任何主题、资料或样例专属规则。"
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
                "current_chatbot_message": chatbot_message,
                "response_contract": {
                    "progress": "0-100 的整体清晰度；ready_for_board=true 时必须为 100。",
                    "summary": "用户当前学习目的的一句话摘要；不清楚时说明仍需澄清。",
                    "key_facts": "用户已经透露的 0-5 条关键信息，每项包含 category、label、value、evidence，只能来自用户原话或上下文。",
                    "checklist": "3-5 个动态需求项，每项包含 title、is_clear、evidence。",
                    "missing_items": "仍缺少的信息，不能脑补。",
                    "next_question": "未清晰时建议下一轮只追问一个最有价值的问题。",
                    "ready_for_board": "是否足够进入后续板书生成阶段。",
                    "action_type": "可选。本轮任务动作类型：generate_board、explain_target、rewrite_target、expand_target、simplify_target。",
                    "action_instruction": "可选。本轮要如何讲解或如何编写，必须来自用户表达。",
                    "target_hint": "可选。用户给出的目标位置描述、标题、前后文或选区摘要。",
                    "interaction_rule_draft": (
                        "可选。用户要求按规则互动时填写 should_start=true、rule_text、interaction_goal、"
                        "target_hint、expected_user_behavior、assistant_behavior、reference_instruction。"
                    ),
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
        role: str,
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
                role=role,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.deepseek_client,
                config=self.deepseek_config,
            )
        if provider == "kimi":
            return self._call_openai_parse(
                role=role,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.kimi_client,
                config=self.kimi_config,
            )
        if provider == "minimax":
            return self._call_openai_parse(
                role=role,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.minimax_client,
                config=self.minimax_config,
            )
        if provider == "openai_compatible":
            return self._call_openai_parse(
                role=role,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                client=self.openai_compatible_client,
                config=self.openai_compatible_config,
            )
        return self._call_openai_parse(
            role=role,
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
        role: str,
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
        compat_mode = config.compat_api.strip().lower()
        observer = _ai_stream_observer.get()
        stream_field = "chatbot_message" if role == "chatbot" else "content_text" if role == "board" else None
        if observer and stream_field and compat_mode not in {"chat", "chat_completions", "chat-completions"}:
            try:
                return self._call_openai_chat_parse(
                    role=role,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    client=client,
                )
            except Exception:
                pass
        if compat_mode in {"chat", "chat_completions", "chat-completions"}:
            return self._call_openai_chat_parse(
                role=role,
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
                role=role,
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
        role: str,
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
        observer = _ai_stream_observer.get()
        stream_field = "chatbot_message" if role == "chatbot" else "content_text" if role == "board" else None
        if observer and stream_field:
            try:
                output_text = self._stream_openai_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=schema,
                    schema_payload=schema_payload,
                    role=role,
                    field_name=stream_field,
                    use_response_format=True,
                )
            except Exception as exc:
                if not self._should_retry_openai_chat_without_schema(exc):
                    raise
                output_text = self._stream_openai_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=schema,
                    schema_payload=schema_payload,
                    role=role,
                    field_name=stream_field,
                    use_response_format=False,
                )
            return ParsedAIResponse(
                output_parsed=schema.model_validate(_extract_json_object(output_text)),
                output_text=output_text,
            )
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
        try:
            output_parsed = schema.model_validate(_extract_json_object(output_text))
        except Exception as exc:
            repair_prompt = (
                "The previous response could not be parsed as valid JSON. "
                "Reformat the same answer as valid JSON that matches this JSON schema. "
                "Do not add new content. Return only JSON:\n"
                f"{_compact_json(schema_payload)}"
            )
            try:
                repair_response = client.chat.completions.create(
                    model=model,
                    messages=[
                        *messages,
                        {"role": "assistant", "content": output_text},
                        {"role": "user", "content": repair_prompt},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "schema": schema_payload,
                            "strict": True,
                        },
                    },
                )
            except Exception as repair_request_exc:
                raise AIOutputParseError(str(exc), output_text=output_text) from repair_request_exc

            repair_output_text = self._chat_completion_text(repair_response)
            try:
                output_parsed = schema.model_validate(_extract_json_object(repair_output_text))
            except Exception as repair_parse_exc:
                raise AIOutputParseError(
                    str(repair_parse_exc),
                    output_text=output_text,
                    repair_output_text=repair_output_text,
                ) from repair_parse_exc
            return ParsedAIResponse(
                output_parsed=output_parsed,
                id=getattr(repair_response, "id", None),
                output_text=repair_output_text,
                usage=getattr(repair_response, "usage", None),
            )

        return ParsedAIResponse(
            output_parsed=output_parsed,
            id=getattr(response, "id", None),
            output_text=output_text,
            usage=getattr(response, "usage", None),
        )

    def _stream_openai_chat_completion(
        self,
        *,
        client: Any,
        model: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        schema_payload: dict[str, Any],
        role: str,
        field_name: str,
        use_response_format: bool,
    ) -> str:
        observer = _ai_stream_observer.get()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if use_response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema_payload,
                    "strict": True,
                },
            }
        stream = client.chat.completions.create(**kwargs)
        output_parts: list[str] = []
        last_visible_value = ""
        for chunk in stream:
            delta_text = self._chat_completion_stream_delta(chunk)
            if not delta_text:
                continue
            output_parts.append(delta_text)
            output_text = "".join(output_parts)
            visible_value = _partial_json_string_field_value(output_text, field_name)
            if observer and visible_value.startswith(last_visible_value):
                visible_delta = visible_value[len(last_visible_value) :]
                if visible_delta:
                    observer(
                        {
                            "type": "field_delta",
                            "role": role,
                            "field": field_name,
                            "delta": visible_delta,
                            "value": visible_value,
                        }
                    )
                    last_visible_value = visible_value
        return "".join(output_parts)

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

    def _chat_completion_stream_delta(self, chunk: Any) -> str:
        choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
        if not choices:
            return ""
        first_choice = choices[0]
        delta = first_choice.get("delta") if isinstance(first_choice, dict) else getattr(first_choice, "delta", None)
        content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
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
        return ""

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
            fallback_started_at = time.perf_counter()
            try:
                response = self._call_parse(
                    role=role,
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
                    duration_ms=_elapsed_ms(fallback_started_at),
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
                    duration_ms=_elapsed_ms(fallback_started_at),
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
        observer = _ai_stream_observer.get()
        if observer:
            observer(
                {
                    "type": "role_start",
                    "role": role,
                    "provider": provider,
                    "model": requested_model,
                }
            )
        if not self._provider_available(provider):
            ai_usage_logger.log_event(
                self._log_event_name(provider, "_skipped"),
                **call_details,
                duration_ms=0,
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

        primary_started_at = time.perf_counter()
        try:
            response = self._call_parse(
                role=role,
                provider=provider,
                model=requested_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
            )
            ai_usage_logger.log_event(
                self._log_event_name(provider, ""),
                **call_details,
                duration_ms=_elapsed_ms(primary_started_at),
                response_id=getattr(response, "id", None),
                output_text=getattr(response, "output_text", None),
                usage=getattr(response, "usage", None),
                parsed_output=response.output_parsed,
            )
            return response.output_parsed
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            primary_duration_ms = _elapsed_ms(primary_started_at)
            fallback_model = self._fallback_model_for(provider, exc, requested_model)
            if fallback_model:
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_retry"),
                    **call_details,
                    duration_ms=primary_duration_ms,
                    retry_model=fallback_model,
                    error=str(exc),
                )
                retry_started_at = time.perf_counter()
                try:
                    response = self._call_parse(
                        role=role,
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
                        duration_ms=_elapsed_ms(retry_started_at),
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
                        duration_ms=_elapsed_ms(retry_started_at),
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
                duration_ms=primary_duration_ms,
                error=str(exc),
                output_text=getattr(exc, "output_text", None),
                repair_output_text=getattr(exc, "repair_output_text", None),
            )
            logger.warning("%s %s call failed, falling back to heuristic flow: %s", provider, role, exc)
            return None

openai_course_ai = OpenAICourseAI()
