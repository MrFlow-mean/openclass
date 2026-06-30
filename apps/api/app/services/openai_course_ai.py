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
    BoardFocusRef,
    BoardPatchRequest,
    BoardSearchRerankResult,
    BoardTaskAction,
    BoardTaskRequirementSheet,
    BoardTaskRoute,
    InitialLearningGranularity,
    InitialLearningWorkMode,
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
    OPENAI_CODEX_DEFAULT_TEXT_MODEL,
    OPENAI_DEFAULT_TEXT_MODEL,
    OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL,
    OPENAI_OFFICIAL_BASE_URL,
    OPENAI_IMAGE_MODEL,
    default_text_selection,
)
from app.services.codex_app_server import CodexAppServerTextClient, codex_provider_status
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
_board_model_selection: ContextVar[AIModelSelection | None] = ContextVar(
    "board_model_selection", default=None
)
AIStreamObserver = Callable[[dict[str, Any]], None]
_ai_stream_observer: ContextVar[AIStreamObserver | None] = ContextVar("ai_stream_observer", default=None)
CHATBOT_BOARD_DOCUMENT_REDACTION = (
    "已隔离：Chatbot 没有直接读取右侧板书文档的权限。"
    "如果本轮需要讲解板书内容，只能依据板书侧 directive、互动 session 或工具结果中明确提供的目标摘录和指令。"
)
BOARD_EDITOR_CHAT_LOG_REDACTION = (
    "已隔离：板书文档编辑 AI 没有读取用户和 Chatbot 原始聊天记录的权限；"
    "只能依据结构化需求清单、任务清单、定位证据、当前文档和资料摘要执行。"
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
    output_parsed: BaseModel | None
    id: str | None = None
    output_text: str | None = None
    usage: Any = None
    visible_field_value: str = ""
    visible_field_was_streamed: bool = False
    structured_parse_failed: bool = False


@dataclass(frozen=True)
class StreamedChatCompletionResult:
    output_text: str
    visible_field_value: str = ""
    visible_field_was_streamed: bool = False


@dataclass(frozen=True)
class BlankBoardRequirementRefinementResult:
    result: "BlankBoardRequirementRefinement | None"
    visible_chat_buffer: str = ""
    visible_chat_was_streamed: bool = False
    structured_parse_failed: bool = False


class AIStreamOutputError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        output_text: str = "",
        visible_field_value: str = "",
        visible_field_was_streamed: bool = False,
    ) -> None:
        super().__init__(message)
        self.output_text = output_text
        self.visible_field_value = visible_field_value
        self.visible_field_was_streamed = visible_field_was_streamed


class ChatbotReply(BaseModel):
    chatbot_message: str


def _chatbot_reply_from_unstructured_output(exc: Exception) -> ChatbotReply | None:
    if not isinstance(exc, AIOutputParseError):
        return None
    for raw in (exc.output_text, exc.repair_output_text):
        text = _coerce_unstructured_chatbot_text(raw)
        if text:
            return ChatbotReply(chatbot_message=text)
    return None


def _coerce_unstructured_chatbot_text(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    partial_field = _partial_json_string_field_value(text, "chatbot_message").strip()
    if partial_field:
        return partial_field
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|markdown|md)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text or text.startswith(("{", "[")) or '"chatbot_message"' in text:
        return ""
    return text


class BoardExplanationDirective(BaseModel):
    status: Literal["approved", "needs_clarification", "blocked"] = "approved"
    target_summary: str = ""
    target_excerpt: str = ""
    board_feedback: str = ""
    teaching_instruction: str = ""
    constraints: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    reason: str = ""


BoardDocumentEditOperation = Literal["replace_document", "replace_selection", "append_section"]


class BoardDocumentEditResult(BaseModel):
    operation: BoardDocumentEditOperation = "replace_document"
    title: str = ""
    content_text: str = ""
    content_html: str = ""
    summary: str = ""
    chatbot_message: str = ""
    section_titles: list[str] = Field(default_factory=list)

    @field_validator("title", "content_text", "content_html", "summary", "chatbot_message", mode="before")
    @classmethod
    def _coerce_nullable_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)


class BoardDocumentQualityReview(BaseModel):
    status: Literal["pass", "repair_required"] = "pass"
    issues: list[str] = Field(default_factory=list)
    repair_instruction: str = ""
    checked_dimensions: list[str] = Field(default_factory=list)


class BoardTaskRouteDecision(BaseModel):
    route: BoardTaskRoute
    location_status: Literal["found", "missing", "ambiguous", "content_absent"] = "missing"
    target_focus: BoardFocusRef | None = None
    candidate_focuses: list[BoardFocusRef] = Field(default_factory=list)
    reason: str = ""
    write_proposal: str = ""
    target_scope: Literal["focus", "section", "whole_document", "append"] | None = None


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


GuidedRequirementStrategy = Literal[
    "none",
    "starting_point",
    "light_self_report",
    "recent_experience",
    "known_unknown",
    "mode_split",
    "scenario",
    "goal_output",
    "stuck_point",
    "choice_cards",
    "domain_map",
    "recommended_entry",
    "implicit_observation",
]


class GuidedRequirementEntryPoint(BaseModel):
    label: str
    why_it_matters: str = ""
    best_for: str = ""


class BlankBoardRequirementRefinement(BaseModel):
    route: Literal["ordinary_chat", "requirement_refining"] = "ordinary_chat"
    chatbot_message: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    summary: str = ""
    work_mode: InitialLearningWorkMode = "unknown"
    granularity: InitialLearningGranularity = "unclear"
    learning_goal: str = ""
    current_level: str = ""
    target_scenario: str = ""
    known_background: str = ""
    target_depth: str = ""
    output_preference: str = ""
    boundary: str = ""
    board_scope: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    learning_need_checklist: list[str] = Field(default_factory=list)
    key_facts: list[LearningRequirementKeyFact] = Field(default_factory=list)
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    guidance_strategy: GuidedRequirementStrategy = "none"
    learning_map_summary: str = ""
    entry_point_options: list[GuidedRequirementEntryPoint] = Field(default_factory=list)
    recommended_entry_point: str = ""
    reason_for_recommendation: str = ""
    learner_profile_inference: str = ""
    missing_items: list[str] = Field(default_factory=list)
    next_question: str = ""
    recommended_teaching_plan_summary: str = ""
    ready_for_board: bool = False


class InitialLearningWorkModeDecision(BaseModel):
    work_mode: InitialLearningWorkMode = "unknown"
    granularity: InitialLearningGranularity = "unclear"
    topic: str = ""
    reason: str = ""
    next_question: str = ""
    guided_discovery_reply: str = ""


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
def bind_board_model_selection(selection: AIModelSelection | None):
    token = _board_model_selection.set(selection)
    try:
        yield
    finally:
        _board_model_selection.reset(token)


@contextmanager
def bind_ai_output_stream(observer: AIStreamObserver | None) -> Iterator[None]:
    token = _ai_stream_observer.set(observer)
    try:
        yield
    finally:
        _ai_stream_observer.reset(token)


def emit_ai_stream_event(payload: dict[str, Any]) -> None:
    observer = _ai_stream_observer.get()
    if observer:
        observer(payload)


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


class OpenAICodexConfig(BaseModel):
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_MODEL", OPENAI_CODEX_DEFAULT_TEXT_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_GUIDE_MODEL"))
    chatbot_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_CHATBOT_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_LESSON_MODEL"))
    catalog_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_CODEX_CATALOG_MODEL"))

    @property
    def enabled(self) -> bool:
        return codex_provider_status().configured

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
        self.openai_codex_config = OpenAICodexConfig()
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
        self.openai_codex_client: CodexAppServerTextClient | None = None
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

    def _ensure_openai_codex_client(self) -> CodexAppServerTextClient | None:
        if self.openai_codex_client is None and self.openai_codex_config.enabled:
            self.openai_codex_client = CodexAppServerTextClient()
        return self.openai_codex_client

    @property
    def enabled(self) -> bool:
        return any(
            [
                self.client is not None,
                self.deepseek_client is not None,
                self.kimi_client is not None,
                self.minimax_client is not None,
                self.openai_compatible_client is not None,
                self._ensure_openai_codex_client() is not None,
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
                "openai_codex": self._ensure_openai_codex_client() is not None,
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
                "openai_codex": self.openai_codex_config.default_model,
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

    def generate_basic_chat_reply(
        self,
        *,
        board_document_state: dict[str, Any] | None = None,
        conversation_summary: str,
        user_message: str,
    ) -> ChatbotReply | None:
        system_prompt = (
            "你是一个通用 AI 助手，在聊天对话框中和用户进行自然、连续、有帮助的你问我答交流。\n"
            "回答方式像 ChatGPT：直接理解用户当前问题，根据问题本身决定回答长短、结构和语气；"
            "可以解释概念、协助写作、分析问题、生成想法、检查文本、回答代码或学习问题。\n"
            "不要套用课程模板，不要根据具体学科、教材、考试或 demo 文本走特殊规则。\n"
            "你会收到 board_document_state，它只说明右侧板书文档是否为空，不包含板书正文。\n"
            "可以在用户问到当前板书/文档状态时自然说明 empty 或 non_empty 的含义；"
            "不要推测、引用或讲解任何未提供的板书正文。\n"
            "不要声称已经修改本地文件、右侧文档或外部应用；当前能力只是聊天框内的文本回答。"
        )
        user_prompt = _json(
            {
                "board_document_state": board_document_state or {},
                "recent_conversation": conversation_summary or "",
                "user_message": user_message,
                "response_contract": {
                    "chatbot_message": "直接回复用户当前问题；允许根据需要输出完整解释、列表、步骤、示例或代码。",
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

    def generate_blank_board_requirement_refinement(
        self,
        *,
        board_document_state: dict[str, Any] | None = None,
        conversation_summary: str,
        user_message: str,
        existing_requirement_sheet: dict[str, Any] | None = None,
        existing_clarification: dict[str, Any] | None = None,
        quality_repair_context: dict[str, Any] | None = None,
        include_stream_result: bool = False,
    ) -> BlankBoardRequirementRefinement | BlankBoardRequirementRefinementResult | None:
        system_prompt = (
            "你是 OpenClass 的空白板书学习需求收敛器，也是左侧聊天框里的自然对话 AI。\n"
            "运行前提：board_document_state.status 必须是 empty；本阶段只维护 LearningRequirementSheet，"
            "不生成板书、不冻结清单、不调用 Board AI。\n"
            "任务：先判断用户当前轮是 ordinary_chat 还是 requirement_refining。\n"
            "ordinary_chat：用户没有表达学习、练习、资料、板书或教学目的时，像 ChatGPT 一样自然回复；"
            "不要新建或更新清单，不要追问学习需求。\n"
            "requirement_refining：用户表达了学习或练习目的，但还需要收敛到可生成板书的教学目标。"
            "你要自然回复用户，同时输出结构化清单变化。\n"
            "两类终点：\n"
            "1. knowledge_board：用户想学新知识。只有 learning_goal 已经明确到一个具体知识点、概念、方法、步骤或单一问题，"
            "且 granularity=single_knowledge_point 时，ready_for_board 才能为 true；其他场景、考试、工作用途只作为辅助因素。\n"
            "2. practice_artifact：用户想练习已有知识或技能。ready_for_board=true 必须同时具备 learning_goal、"
            "current_level、target_scenario 三个核心因素；如果用户明确没有场景或让系统不按场景定制，target_scenario 可写"
            "“无明确应用场景”。\n"
            "收敛方法：你必须根据上下文灵活选择起点定位法、轻量自述法、最近经历法、已会/未会法、"
            "学习模式分流法、场景定位法、目标产出法、卡点定位法、选择卡片法、领域地图法、推荐入口法或隐性观察法。"
            "这些方法只用于自然语言引导，不要变成固定问卷，也不要要求用户填字段。"
            "你要在聊天中观察、承接、推荐和追问，同时把用户自然透露的信息记录到结构化字段；"
            "不要让用户感觉自己在填写 LearningRequirementSheet。\n"
            "引导策略选择优先级：\n"
            "- 用户只说宽泛领域且起点未知：先用领域地图法打开结构，再用起点定位法或选择卡片法确认起点；"
            "推荐入口只能是暂定入口，主问题优先问当前水平、已会/未会或最近接触情况。\n"
            "- 用户说“不知道、你安排、帮我安排路线”：用选择卡片法降低表达成本，同时给出推荐入口和推荐理由；"
            "如果同时是纯新手委托式入门，则直接落定领域总览型第一课。\n"
            "- 用户自述“已经会/学过/还没学/忘得多”：优先用轻量自述法或已会/未会法，"
            "把已会、未会、忘得多、下一步入口写入 known_background、learner_profile_inference 和 key_facts。\n"
            "- 用户说“最近学到、最近做过、最近卡在”：优先用最近经历法；如果有明显卡点，优先用卡点定位法，"
            "先判断卡在概念、步骤、公式/规则、应用迁移、表达或不知道从哪开始。\n"
            "- 用户说“练习、训练、提高、复习、测验、实战、角色扮演”等：优先归为 practice_artifact，"
            "自然收敛想练的内容、当前水平、面向场景；不要只给领域地图，也不要把练习需求当成新知识点教学。\n"
            "- 练习型需求中，如果用户已经说清想练的内容，但没有说明当前水平，必须优先用选择卡片法探寻水平；"
            "chatbot_message 必须先承接用户的练习目标，再给一个自然标题、一个降低选择压力的副标题，以及 4-6 个 A/B/C 卡片选项。"
            "卡片选项由你根据当前技能自主生成，应该覆盖从纯入门、基础规则/语法、写过基础产物、能写标准组件、想练复杂任务到不确定等通用水平梯度。"
            "entry_point_options 也要记录这些水平卡片。不要默认用户从基础练起，不要先推荐具体练习难度，"
            "也不要在同一轮同时追问面向场景；等当前水平明确后再继续收敛场景。\n"
            "- 用户表达“为了、用来、应对、解决、学完能做到/会做/看懂/写出”：使用场景定位法或目标产出法，"
            "把这些信息作为 target_scenario、target_depth 或 success_criteria 的辅助因素。\n"
            "- 用户表达不清时，可以给 3-6 个 A/B/C 选择卡片，但卡片必须是通用学习状态或内容形态，"
            "不是学科模板；选择卡片后仍只问一个主问题。\n"
            "如果用户是纯新手且想入门某领域，可以用领域地图介绍该领域由哪些通用部分构成、推荐一个入门入口，"
            "并继续把需求收敛到一个可开始的知识点或练习产物。纯新手、零基础、入门、先了解一下、"
            "感兴趣想学，都表示一种明确的入门型学习状态；默认目的可以记录为“入门了解 / 建立领域地图 / 找到第一个学习入口”。"
            "这时不要强制追问考试、面试、工作、赚钱、项目或现实产出场景；如果用户没有主动说场景，"
            "不要把应用场景当作缺失核心因素。\n"
            "宽泛复合领域且用户起点未知时，可以给学习地图和暂定入口，但不要把具体工具、语法、框架或项目实操入口直接判定为最终第一课；"
            "entry_point_options 应优先作为学习者起点/背景的选择卡片，而不是让用户在高级内容路线里做选择；"
            "唯一主问题必须优先询问用户起点、已有背景、已会/未会或最近接触情况。\n"
            "如果用户已经说明自己是纯新手/零基础/纯入门/先了解一下/感兴趣想入门，"
            "即使没有明确说“你安排”，也表示系统应选择最安全的基础入口；"
            "不要再让用户在工具、语法、框架、测试或项目实操等后续模块里选择。"
            "这时要主动落定一个基础总览型第一课，例如“这个领域的基础概念与整体组成 / 核心对象、基本流程和关键术语 / 整体结构是什么”。"
            "此时必须输出 work_mode=knowledge_board、granularity=single_knowledge_point、ready_for_board=true，"
            "learning_goal 写成这个具体基础入口，next_question 为空。\n"
            "如果用户已经说明自己是纯新手/零基础/纯入门，并表达“为我指导、你安排、帮我安排、帮我规划、按你推荐、听你的、直接安排”等委托意图，"
            "表示用户进一步授权你主动选择入口；这时更不要再问“你愿意从 X 开始吗”，而要主动落定一个领域总览型第一课，"
            "例如“这个领域由哪几部分组成 / 整体结构是什么 / 基本工作方式是什么 / 它和普通系统有什么区别”这一类可教学入口。"
            "此时必须输出 work_mode=knowledge_board、granularity=single_knowledge_point、ready_for_board=true，"
            "learning_goal 写成这个具体第一课入口，next_question 为空。\n"
            "当 learning_goal 仍是宽泛主题、granularity=broad_topic 或用户说不知道从哪开始时，"
            "chatbot_message 必须优先呈现“开场承接 + 简短学习地图 + 2-5 个入口选项 + 一个推荐入口 + 推荐理由 + 一个绑定推荐入口的主问题”，"
            "而不是只追问“具体想学什么”；学习地图和入口选项必须真的写进 chatbot_message，不能只写在结构化字段里。\n"
            "宽泛主题下，chatbot_message 不能是一两句话，也不能只是列分支名；它要像老师在聊天框里给用户打开地图，"
            "先降低表达成本，再把用户带向一个可开始的知识点。\n"
            "entry_point_options 只记录通用入口建议，由模型根据当前领域自主生成；不要写固定讲义正文或固定课程模板。"
            "recommended_entry_point 必须从 entry_point_options 或用户已明确内容中选择一个最适合的入口。"
            "learner_profile_inference 只记录可由用户自述、最近经历、已会/未会或卡点直接推出的起点信息。\n"
            "如果你已经给出 recommended_entry_point，但 current_level、known_background 和 learner_profile_inference "
            "都没有可靠依据，那么 chatbot_message 结尾的唯一主问题必须优先询问用户当前水平、已会/未会或最近学到哪里；"
            "不要继续只问用户要不要选择推荐入口。\n"
            "如果用户明确表达“想学某个领域/方向/主题”，即使主题很宽，也应归为 knowledge_board + broad_topic；"
            "只有连学习还是练习、或主题对象都无法判断时，work_mode 才保持 unknown。\n"
            "规则：\n"
            "1. 不写任何学科、教材、考试、语言名、旅游场景或 demo 专属规则；只根据用户意图形态和内容产物形态判断。\n"
            "2. 不脑补核心因素；核心因素不全时 ready_for_board=false，但要通过引导、推荐和选择卡片降低表达成本，"
            "最后只问一个最关键问题。\n"
            "3. 辅助因素可以记录 known_background、target_depth、output_preference、board_scope、"
            "learning_need_checklist、success_criteria，但不能替代核心因素。\n"
            "如果是纯新手入门型宽泛主题，target_depth 可写“入门了解 / 建立领域地图”，"
            "success_criteria 可写“理解领域组成，并确定第一个可学习入口”；如果纯新手委托你安排入口并已落定第一课，"
            "success_criteria 可写“理解领域组成，并确定后续学习入口”。\n"
            "4. key_facts 只记录用户已经透露或你从当前对话可直接归纳的事实，优先使用标签："
            "用户想学的内容、当前水平、面向场景。\n"
            "5. chatbot_message 面向用户自然表达；必须综合使用 learning_map_summary、entry_point_options、"
            "recommended_entry_point 和 reason_for_recommendation 中的有用信息，但不要输出 JSON、字段名、内部状态名或右侧板书正文。"
            "不要说“请填写学习内容/当前水平/面向场景”，不要暴露 learning_goal、current_level、target_scenario、"
            "missing_items、ready_for_board 等内部字段名；如果需要信息，用自然聊天的一句话询问。"
        )
        if quality_repair_context:
            system_prompt += (
                "\n质量修复模式：上一轮结构化结果已经被后端判定为宽泛主题引导不够丰富。"
                "通常只能修复 chatbot_message、guidance_strategy、learning_map_summary、entry_point_options、"
                "recommended_entry_point、reason_for_recommendation、learner_profile_inference 和 next_question；"
                "不得改变用户核心学习事实，不得生成板书，不得把固定模板或学科硬编码写进核心逻辑。"
                "如果 repair_reason 提到练习型、已会/未会、最近经历、卡点、场景定位或目标产出，"
                "可以修复 work_mode、granularity、learning_goal、current_level、target_scenario、known_background、"
                "target_depth、success_criteria、key_facts、checklist、missing_items 和 ready_for_board，但只能基于用户已说内容，不能脑补。"
                "如果 repair_reason 提到字段泄露、填表感或一次问多个问题，必须改成自然对话表达，并只保留一个主问题。"
                "如果 repair_reason 提到练习型水平选择卡片，必须把开放式水平追问改成 choice_cards："
                "chatbot_message 写成标题、副标题和 4-6 个 A/B/C 水平卡片，entry_point_options 记录这些水平选项，"
                "本轮只问水平，不默认练习难度。"
                "如果 repair_reason 提到委托式入门，你可以并且应该同时修复 granularity、learning_goal、ready_for_board、"
                "progress、success_criteria 和 missing_items：把宽泛主题落定为领域总览型第一课，"
                "ready_for_board=true，next_question 为空。"
                "如果 repair_reason 提到新手基础入口，你也必须按同样方式修复："
                "不要让入门新手选择高级路线，直接落定基础总览型第一课。"
            )
        user_prompt = _json(
            {
                "board_document_state": board_document_state or {},
                "recent_conversation": conversation_summary or "",
                "current_user_message": user_message,
                "existing_requirement_sheet": existing_requirement_sheet or None,
                "existing_clarification": existing_clarification or None,
                "quality_repair_context": quality_repair_context or None,
                "response_contract": {
                    "route": "ordinary_chat 或 requirement_refining。",
                    "chatbot_message": (
                        "直接给用户看的自然回复；如果是宽泛主题，必须包含开场承接、学习地图、"
                        "2-5 个入口选项、一个推荐入口、推荐理由和一个关键问题；每次最多追问一个主问题；"
                        "不得输出内部字段名、JSON、表单格式或让用户填写清单。"
                    ),
                    "progress": "0-100 的清单完整度；ready_for_board=true 时必须为 100。",
                    "summary": "当前学习需求的一句话摘要；普通聊天可为空。",
                    "work_mode": "knowledge_board、practice_artifact 或 unknown；本阶段不使用其他新值。",
                    "granularity": "single_knowledge_point、practice_artifact、broad_topic 或 unclear。",
                    "learning_goal": "核心因素。新知识点教学写用户具体想学的知识点；练习型写用户具体想练的内容。",
                    "current_level": "练习型核心因素。用户当前水平、已有基础或最近状态；新知识点教学可作为辅助。",
                    "target_scenario": "练习型核心因素。用户面向的任务、应用、输出或“无明确应用场景”。",
                    "known_background": "可选辅助因素：已会、未会、最近经历、卡点或背景。",
                    "target_depth": "可选辅助因素：希望理解、会做、能应用、能讲给别人等深度。",
                    "output_preference": "可选辅助因素：希望板书或教学呈现的形态偏好。",
                    "boundary": "可选辅助因素：范围边界或不要展开的部分。",
                    "board_scope": "可选辅助因素：未来板书可覆盖的通用模块清单，不写固定讲义正文。",
                    "success_criteria": "可选辅助因素：用户希望学完能做到什么，练习场景可写在这里。",
                    "learning_need_checklist": "当前清单的简短条目，用用户已表达事实和缺项组织。",
                    "key_facts": "0-5 条事实，每项包含 label、value、evidence、category。",
                    "checklist": "2-5 个动态检查项，每项包含 title、is_clear、evidence。",
                    "guidance_strategy": (
                        "本轮采用的通用引导策略。只能使用 none、starting_point、light_self_report、"
                        "recent_experience、known_unknown、mode_split、scenario、goal_output、stuck_point、"
                        "choice_cards、domain_map、recommended_entry、implicit_observation。必须和用户当前表达形态匹配："
                        "宽泛领域用 domain_map/starting_point/choice_cards；自述已会未会用 known_unknown/light_self_report；"
                        "最近经历用 recent_experience；卡点用 stuck_point；练习需求用 mode_split/starting_point/scenario/goal_output；"
                        "练习需求缺当前水平时优先用 choice_cards；"
                        "不知道你安排用 choice_cards/recommended_entry。"
                    ),
                    "learning_map_summary": "给用户看的简短学习地图摘要；宽泛主题时应填写，不写固定讲义正文。",
                    "entry_point_options": (
                        "2-6 个候选入口或水平卡片，每项包含 label、why_it_matters、best_for；"
                        "练习型缺当前水平时这里必须是当前技能水平卡片，而不是练习任务清单。没有必要时可为空。"
                    ),
                    "recommended_entry_point": "AI 推荐的一个入口，优先来自 entry_point_options。",
                    "reason_for_recommendation": "推荐理由，必须基于用户已说信息或通用入门原则。",
                    "learner_profile_inference": "从用户自述、最近经历、已会/未会或卡点推断出的起点信息。",
                    "missing_items": "仍缺少的核心因素或重要辅助因素；核心因素不全必须列出。",
                    "next_question": (
                        "清单未完整时下一轮最有价值的一个问题；如果已推荐入口但不了解用户水平，"
                        "优先询问当前水平、已会/未会或最近学到哪里；如果用户已说明纯新手入门，"
                        "必须直接落定基础总览型第一课，next_question 为空；ready_for_board=true 时可为空。"
                    ),
                    "recommended_teaching_plan_summary": "可选：给用户看的教学方案摘要，不是板书正文。",
                    "ready_for_board": "只表示清单核心因素齐全，可以进入未来板书生成；本阶段不会实际生成。",
                },
            }
        )
        if include_stream_result:
            response = self._parse_response(
                "pm",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=BlankBoardRequirementRefinement,
                visible_stream_field="chatbot_message",
                disable_stream_repair=True,
            )
            if response is None:
                return BlankBoardRequirementRefinementResult(result=None)
            parsed = response.output_parsed
            result = parsed if isinstance(parsed, BlankBoardRequirementRefinement) else None
            visible_chat_buffer = (response.visible_field_value or "").strip()
            if result is not None and visible_chat_buffer:
                result = result.model_copy(update={"chatbot_message": visible_chat_buffer})
            return BlankBoardRequirementRefinementResult(
                result=result,
                visible_chat_buffer=visible_chat_buffer,
                visible_chat_was_streamed=response.visible_field_was_streamed,
                structured_parse_failed=response.structured_parse_failed or result is None,
            )

        result = self._parse(
            "pm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BlankBoardRequirementRefinement,
        )
        return result if isinstance(result, BlankBoardRequirementRefinement) else None

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
            "1. 只根据用户问题、当前课程上下文、板书侧 directive/互动 session 明确给你的目标摘录、"
            "资料摘要和最近对话回答；Chatbot 没有直接读取右侧板书文档全文或摘要的权限。\n"
            "2. Chatbot 不生成整篇文档、板书、讲义、课文、练习、试题、对话稿等长篇产物；"
            "这类内容只能写入右侧文档区。用户要求生成可写入文档的内容时，"
            "只做简短承接、确认或引导，不要把正文铺在聊天框里。\n"
            "3. 不要假装已经修改讲义；如果用户要求改文档，只先给出可执行的修改建议或确认问题。\n"
            "4. 回答要直接、清楚、可继续追问；必要时用短列表、步骤或检查问题；"
            "除非是在讲解既有文档内容，否则回复保持短小。\n"
            "5. 如果学习需求还不清楚，先说明澄清是为了匹配讲解深度、材料组织和练习方式，"
            "再从具体想学什么、当前水平、学习目的/使用场景中选择最缺的一项追问。\n"
            "6. 主动与被动动作边界必须分清：当用户只是聊天、泛泛表达兴趣或需求清单还不完整时，"
            "Chatbot 不主动展开讲解，只继续完善需求；当用户已经明确要求写、改、讲或按规则互动时，"
            "可承接该动作，但仍必须服从后端清单、定位和板书侧授权门禁。\n"
            "7. Chatbot 不能自行进入讲解动作。只有 interaction_context 或 user_message 明确包含"
            "板书侧给出的讲解指令、讲解依据和目标片段时，才可以围绕该依据讲解；否则即使用户说"
            "“直接讲、开始讲、从零开始、不要再问”，也只能继续澄清学习需求或询问是否先生成/定位板书。\n"
            "8. 每次最多追问一个主问题；可以给 2-3 个可选回答方向，但不要像机械问卷或客服套话。\n"
            "9. 如果 interaction_context 存在，说明系统正在执行用户指定的通用互动规则；"
            "回复必须同时参考互动规则、原文内容、互动进度和用户当前输入，但不要输出系统字段名。\n"
            "10. 当 interaction_context.turn_mode == 'ordinary_chat' 时，本轮已由后端判定为普通聊天；"
            "自然回应当前话题，不主动追问学习需求、生成板书或引导进入课程，除非用户当前轮明确提出学习、"
            "练习、资料、板书或文档操作任务。\n"
            "11. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "learning_goal": learning_goal,
                "board_summary": CHATBOT_BOARD_DOCUMENT_REDACTION,
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

    def generate_board_explanation_directive(
        self,
        *,
        lesson_title: str,
        learning_goal: str,
        board_summary: str,
        target_excerpt: str,
        user_message: str,
        action_type: str,
        resource_summary: str,
        interaction_context: dict[str, Any] | None = None,
    ) -> BoardExplanationDirective | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的板书理解与讲解指令 AI。你不直接面向学习者聊天，也不写入文档；"
            "你的职责是先阅读当前板书、目标片段和用户请求，判断 Chatbot 是否可以进行讲解，"
            "并给 Chatbot 提供必须遵守的讲解依据和指令。\n"
            "规则：\n"
            "1. 只有当前板书或目标片段足以支撑用户要的讲解时，status 才能是 approved。\n"
            "2. approved 时，board_feedback 和 teaching_instruction 必须给出 Chatbot 可依照的内容依据、"
            "讲解边界、先后顺序和注意点；不要让 Chatbot 自由发挥板书外知识。\n"
            "3. 如果目标不清楚、板书依据不足或用户请求脱离板书，status 使用 needs_clarification 或 blocked，"
            "并给出 clarification_question 或 reason；此时 Chatbot 只能追问或说明需要先定位/补充板书，不能讲解。\n"
            "4. 主动/被动边界：如果用户没有明确要求讲解，而需求或任务仍不完整，不允许主动展开讲解；"
            "如果用户已经明确要求讲解，且传入的任务清单、定位裁决和目标摘录足以支撑本轮讲解，"
            "不要仅因为还可以收集更多背景而拒绝授权。\n"
            "5. 对“都讲、逐个、按顺序”等多目标讲解，本轮只授权 target_excerpt 中标明的当前目标；"
            "后续候选只能作为顺序上下文，不得让 Chatbot 越界把未授权目标一起讲完。\n"
            "6. 不输出最终给学习者看的讲解正文，不写固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "learning_goal": learning_goal,
                "board_summary": board_summary,
                "target_excerpt": target_excerpt,
                "user_message": user_message,
                "action_type": action_type,
                "resource_summary": resource_summary,
                "interaction_context": interaction_context or None,
                "response_contract": {
                    "status": "approved、needs_clarification 或 blocked。",
                    "target_summary": "被允许讲解的板书对象摘要。",
                    "target_excerpt": "Chatbot 必须依据的板书片段；可压缩，但不能编造。",
                    "board_feedback": "给 Chatbot 的板书依据反馈。",
                    "teaching_instruction": "给 Chatbot 的讲解指令；说明顺序、边界和侧重点。",
                    "constraints": "Chatbot 讲解时必须遵守的限制。",
                    "clarification_question": "不能讲解时，给 Chatbot 的追问方向。",
                    "reason": "判断理由。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardExplanationDirective,
        )
        return result if isinstance(result, BoardExplanationDirective) else None

    def generate_board_task_requirement_sheet(
        self,
        *,
        lesson_title: str,
        existing_task: dict[str, Any] | None,
        board_summary: str,
        resource_summary: str,
        conversation_summary: str,
        user_message: str,
        selection_excerpt: str | None = None,
    ) -> BoardTaskRequirementSheet | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的已有板书任务清单 AI。当前右侧板书已经有内容，"
            "你的职责不是生成或讲解，而是从用户话语中维护一张四字段任务清单。\n"
            "四字段：目标位置、动作类型、问题/主题内容、是否有练习或特殊互动规则。\n"
            "规则：\n"
            "1. requested_action 只能是 write、edit、explain、chat；chat 只有用户明确要求按规则互动、练习、问答、"
            "角色或轮次交流时才使用。\n"
            "2. target_hint 只记录用户给出的定位线索、选区摘要、标题、编号或前后文；不要编造段落 ID。\n"
            "3. question_or_topic 记录用户想处理的问题或主题内容；不能把系统追问写成用户需求。\n"
            "4. interaction_rule_draft 只在用户提出特殊互动方式时填写，否则留空；"
            "如果填写，expected_user_behavior 必须说明什么样的用户输入算合规，assistant_behavior 必须说明 AI 如何按规则回应。\n"
            "5. progress 按四项清晰度估算，每项 25 分；非 chat 任务的互动规则项可视为已明确为无特殊规则。\n"
            "6. 用户没有明确要求行动时，清单应尽量完善，缺项就追问；用户已经明确要求写、改、讲或聊时，"
            "只要四字段达到可执行最低条件，就不要为了追求更完整背景而阻止行动。\n"
            "7. 用户在多候选澄清后说“都讲、全部讲、逐个、按顺序”等，表示目标位置是这些候选的顺序集合；"
            "不得继续把目标位置判为空缺。\n"
            "8. missing_items 只写还缺的字段；clarification_question 只问最关键的一个缺项。\n"
            "9. 不写任何学科、教材、考试或样例专属规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "existing_task": existing_task,
                "board_summary": board_summary,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "selection_excerpt": selection_excerpt or "",
                "current_user_message": user_message,
                "response_contract": {
                    "target_hint": "用户给出的目标位置线索；有选区就概括选区。",
                    "location_status": "missing、selected、resolved、ambiguous 或 content_absent；清单阶段通常是 missing/selected。",
                    "requested_action": "write、edit、explain、chat 或 null。",
                    "question_or_topic": "用户要处理的问题或主题内容。",
                    "interaction_rule_draft": "用户明确要求特殊互动时填写，否则 null。",
                    "missing_items": "仍缺少的四字段名称。",
                    "progress": "四项清晰度百分比，每项 25。",
                    "confirmation_status": "none、awaiting、confirmed 或 declined。",
                    "clarification_question": "未完整时只问一个最关键问题。",
                    "failure_count": "保留既有值，除非输入明确解决了定位失败。",
                },
            }
        )
        result = self._parse(
            "pm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardTaskRequirementSheet,
        )
        return result if isinstance(result, BoardTaskRequirementSheet) else None

    def generate_board_search_rerank(
        self,
        *,
        board_task: dict[str, Any] | None,
        query_plan: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> BoardSearchRerankResult | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的板书侧目标内容检索重排 AI。你只根据结构化 board task、查询计划和候选位置证据排序，"
            "不得凭空补充板书内容，不得读取用户和 Chatbot 的原始聊天记录。\n"
            "规则：\n"
            "1. 只能在候选 match_id 中排序和打分，不能创造新位置。\n"
            "2. 优先选择最符合目标位置、动作类型、问题/主题和互动规则的候选。\n"
            "3. 分数接近时保留多个高分候选，交给后续流程澄清。\n"
            "4. 不输出面向学习者的话术，不写学科、教材、考试或样例专属规则。"
        )
        user_prompt = _json(
            {
                "board_task": board_task,
                "query_plan": query_plan,
                "candidates": candidates,
                "response_contract": {
                    "ranked": "按相关性排序的 match_id 列表，每项包含 match_id、score、reason。",
                    "reason": "整体重排依据。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardSearchRerankResult,
        )
        return result if isinstance(result, BoardSearchRerankResult) else None

    def generate_board_task_route_decision(
        self,
        *,
        lesson_title: str,
        board_task: BoardTaskRequirementSheet,
        location_evidence: dict[str, Any],
        resource_summary: str,
    ) -> BoardTaskRouteDecision | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的已有板书任务裁决 AI。你只根据四字段任务清单和板书侧定位证据，"
            "决定本轮走写、改、讲、聊，或继续澄清位置/等待扩写确认。\n"
            "规则：\n"
            "1. 定位 found 且动作是 write/edit/explain/chat 时，分别 route=write/edit/explain/chat；"
            "其中 found+write 表示在目标位置扩写特定内容。\n"
            "2. 定位 ambiguous 时 route=clarify_location，不执行任何写改讲聊；但 explain 任务中，"
            "如果用户已经明确说“都讲、全部讲、逐个、按顺序”，则可以把多个候选视为顺序讲解目标，"
            "route=explain，并把本轮应先讲的 target_focus 填为第一个候选。\n"
            "3. 用户要问/学/讲的内容在全文没有相关位置时，route=await_write_confirmation，"
            "location_status=content_absent，并给 write_proposal。\n"
            "4. 用户要编辑但目标位置缺失时，route=clarify_location；不要擅自变成写。"
            "只有用户明确说全文、整篇、整个板书、全部内容等，才允许 target_scope=whole_document。\n"
            "5. 如果任务已经 confirmation_status=confirmed 且是无目标 write，route=write。\n"
            "6. 不读取原始用户聊天记录，不直接搜索整篇板书；定位只能来自 location_evidence。\n"
            "7. 不输出面向学习者的最终回复，不写学科、教材、考试或样例专属规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "board_task": board_task.model_dump(mode="json"),
                "location_evidence": location_evidence,
                "resource_summary": resource_summary,
                "response_contract": {
                    "route": "write、edit、explain、chat、clarify_location 或 await_write_confirmation。",
                    "location_status": "found、missing、ambiguous 或 content_absent。",
                    "target_focus": "route 需要位置且已找到时填写。",
                    "candidate_focuses": "ambiguous 时填写候选。",
                    "reason": "裁决理由。",
                    "write_proposal": "需要扩写时，给板书编辑 AI 的扩写意图摘要。",
                    "target_scope": "focus、section、whole_document 或 append；非明确全文任务不得输出 whole_document。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardTaskRouteDecision,
        )
        return result if isinstance(result, BoardTaskRouteDecision) else None

    def generate_post_board_generation_reply(
        self,
        *,
        lesson_title: str,
        learning_goal: str,
        board_summary: str,
        resource_summary: str,
        requirement_context: dict[str, Any],
        editor_summary: str,
        section_titles: list[str],
    ) -> ChatbotReply | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的 Chatbot，负责左侧聊天框里的自然学习对话。\n"
            "当前右侧文档已经从空白状态生成了第一版板书。你的任务是给学习者一个短回复："
            "确认板书已就绪，并询问是否要按照板书从开头开始讲解。\n"
            "规则：\n"
            "1. 不要输出板书正文、讲义正文、练习正文或长篇教学内容；右侧文档已经由板书文档编辑 AI 完成。\n"
            "2. 不要开始正式讲解，只提出下一步教学邀请，等待用户确认。\n"
            "3. 语气自然，结合学习需求和板书结构表达，不要套用固定格式。\n"
            "4. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "learning_goal": learning_goal,
                "board_summary": CHATBOT_BOARD_DOCUMENT_REDACTION,
                "resource_summary": resource_summary,
                "requirement_context": requirement_context,
                "board_editor_summary": editor_summary,
                "section_titles": section_titles,
                "response_contract": {
                    "chatbot_message": "面向学习者的自然语言短回复；确认板书已就绪，并询问是否要从开头开始讲解。",
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

    def generate_initial_learning_work_mode(
        self,
        *,
        lesson_title: str,
        resource_summary: str,
        conversation_summary: str,
        user_message: str,
    ) -> InitialLearningWorkModeDecision | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的初始学习入口分类 AI，只做通用学习工作模式判断，不直接回复用户，"
            "也不生成板书。\n"
            "任务：在右侧板书为空时，判断用户本轮学习意图应进入哪种通用链路。\n"
            "分类：\n"
            "1. knowledge_board：用户想学新知识，且目标已经小到一个可生成聚焦板书的知识点、概念、方法、步骤或单一问题。"
            "系统后续会用最小清单生成相关知识板书。\n"
            "2. narrow_topic：用户想学新知识，但范围太宽，尚不能聚焦到一个知识点或清晰起点；只需要追问一个缩小问题。\n"
            "3. practice_artifact：用户想练习，或要求生成可操练学习材料、任务、题目、测验、案例、情景材料、对话材料、角色任务等。"
            "系统后续会维护完整需求清单，再生成练习板书。\n"
            "4. unknown：无法可靠判断。此时必须根据已知上下文生成 guided_discovery_reply，"
            "给出 2-3 个可选学习内容方向或学习产物方向，并只问一个选择/缩小问题。\n"
            "规则：\n"
            "1. 不写任何学科、教材、考试、语法点、场景或样例专属分支。\n"
            "2. 根据用户意图形态和产物形态判断，不根据具体主题名特殊处理。\n"
            "3. topic 只抽取用户已经表达的学习主题或产物目标，不脑补。\n"
            "4. narrow_topic 必须给出自然的 next_question；unknown 必须给出 next_question 和 guided_discovery_reply。\n"
            "5. 如果用户要求生成可操练材料，即使材料里包含知识点，也归为 practice_artifact。\n"
            "6. guided_discovery_reply 必须像 Chatbot 在左侧聊天框自然说话：基于 lesson_title、resource_summary、"
            "recent_conversation 和 current_user_message 中已经明确的信息给建议；上下文不足时，只建议通用学习切入方式，"
            "不要编造具体事实或固定主题。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "resource_summary": resource_summary,
                "recent_conversation": conversation_summary,
                "current_user_message": user_message,
                "response_contract": {
                    "work_mode": "knowledge_board、narrow_topic、practice_artifact 或 unknown。",
                    "granularity": "single_knowledge_point、broad_topic、practice_artifact 或 unclear。",
                    "topic": "用户已表达的学习主题、知识点或练习产物目标。",
                    "reason": "简短说明通用判断依据，不引用任何专属规则。",
                    "next_question": "narrow_topic 和 unknown 必填，只问一个缩小主题或选择方向的问题。",
                    "guided_discovery_reply": "仅 unknown 必填。基于已知上下文给 2-3 个学习内容建议，并让用户选择或修正。",
                },
            }
        )
        result = self._parse(
            "pm",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=InitialLearningWorkModeDecision,
        )
        return result if isinstance(result, InitialLearningWorkModeDecision) else None

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
        system_prompt = (
            "你是 OpenClass Chatbot 的隐藏强推理工具，只提供解题材料，不直接面向学习者发言。\n"
            "规则：\n"
            "1. 只解决用户问题本身，不修改板书、不生成整篇文档、不扮演新的 AI 角色。\n"
            "2. 根据课程标题、目标片段、资料摘要和最近对话进行严谨分析；"
            "不得把右侧板书全文或摘要作为 Chatbot 的间接读取通道。\n"
            "3. 输出要便于 Chatbot 直接转述：给出结论、关键依据、必要步骤和不确定性。\n"
            "4. 不写任何学科、教材、考试或样例专属分支；换成任意主题后规则仍成立。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "question": question,
                "target_excerpt": target_excerpt or "无",
                "board_summary": CHATBOT_BOARD_DOCUMENT_REDACTION,
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
            "1. route 优先只能是 continue_rule、rule_violation、exit_rule、new_task；"
            "side_learning_request 和 resume_rule 仅为旧数据兼容，不作为默认选择。\n"
            "2. continue_rule 表示用户输入符合当前互动规则，应继续按规则互动。\n"
            "3. rule_violation 表示用户仍在当前互动任务内，但输入格式、顺序或内容不符合规则，应让 Chatbot 在规则内纠错。\n"
            "4. new_task 表示用户开启了新的生成、编辑、定位、讲解或学习任务，或问题已经脱离当前互动规则。\n"
            "5. exit_rule 表示用户明确结束当前互动规则。\n"
            "6. 不要把规则外的新讲解/编辑/写作需求判成 side_learning_request；这类输入必须判成 new_task，回到四字段任务清单。\n"
            "7. progress_note 只记录通用进度，不写固定场景模板或样例内容。"
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
                    "route": "continue_rule、rule_violation、exit_rule 或 new_task；旧兼容可返回 side_learning_request/resume_rule。",
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

    def generate_board_patch_plan(
        self,
        *,
        lesson_title: str,
        learning_requirement_context: dict[str, Any],
        board_snapshot: dict[str, Any],
        resource_summary: str,
        selection_excerpt: str | None = None,
        target_scope: str | None = None,
        user_instruction: str | None = None,
        allow_delete: bool = False,
        allow_whole_document: bool = False,
    ) -> BoardPatchRequest | None:
        if not self.enabled:
            return None
        system_prompt = (
            "你是 OpenClass 的板书 patch planning AI，只负责把已有板书写入/编辑任务转成结构化 BoardPatchRequest，"
            "不直接输出整篇新版板书，也不扮演 Chatbot。\n"
            "规则：\n"
            "1. 只能根据结构化学习/任务上下文、board_snapshot、定位摘录和资料摘要规划修改；"
            "不得读取或依赖原始聊天记录。\n"
            "2. V1 只允许 operations 使用 insert_block、update_block_content、delete_block。"
            "默认优先 insert_block 或 update_block_content；除非 allow_delete=true，否则不要 delete_block。\n"
            "3. 每个修改既要填写 block_id 或 node_path，也要填写 expected_text 或 expected_text_hash。"
            "insert_block 应使用 after_block_id 锚定插入位置；无锚点时只可用于 target_scope=append。\n"
            "4. content 必须是 Markdown 或普通文本，不得包含 HTML 标签、style、class，不得把普通文字包成公式。\n"
            "5. source_commit_id 与 source_document_hash 必须从 board_snapshot 原样复制；target_scope 使用输入值。\n"
            "6. risk_level：纯插入为 low，局部改写为 medium，删除或全文范围为 high。"
            "whole_document 只有 allow_whole_document=true 时才允许。\n"
            "7. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_prompt = _json(
            {
                "lesson_title": lesson_title,
                "learning_requirement_context": learning_requirement_context,
                "board_snapshot": board_snapshot,
                "resource_summary": resource_summary,
                "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "",
                "target_scope": target_scope or "",
                "user_instruction": user_instruction or "",
                "allow_delete": allow_delete,
                "allow_whole_document": allow_whole_document,
                "response_contract": {
                    "source_commit_id": "从 board_snapshot.source_commit_id 复制。",
                    "source_document_hash": "从 board_snapshot.source_document_hash 复制。",
                    "target_scope": "focus、section、append 或 whole_document。",
                    "operations": (
                        "PatchOperation 数组；V1 只用 insert_block、update_block_content、delete_block。"
                        "更新/删除必须带 block_id 或 node_path，并带 expected_text 或 expected_text_hash。"
                    ),
                    "summary": "一句话说明这组 patch 会做什么。",
                    "risk_level": "low、medium 或 high。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardPatchRequest,
        )
        return result if isinstance(result, BoardPatchRequest) else None

    def generate_board_document_edit(
        self,
        *,
        intent: str,
        lesson_title: str,
        learning_requirement_context: dict[str, Any],
        current_document_title: str,
        current_document_text: str,
        resource_summary: str,
        conversation_summary: str | None = None,
        user_instruction: str | None = None,
        selection_excerpt: str | None = None,
        target_scope: str | None = None,
        allow_replace_document: bool = False,
    ) -> BoardDocumentEditResult | None:
        is_initial_generation = intent == "generate_from_requirements"
        system_prompt = (
            "你是 OpenClass 的板书文档编辑 AI，只负责生成或编辑板书文档，不负责学习需求澄清，"
            "也不扮演 Chatbot。\n"
            "规则：\n"
            "1. 生成空白板书的第一版时，只根据已冻结学习需求清单和资料摘要写入文档；"
            "编辑已有板书时，只根据结构化需求/任务清单、当前板书、目标选区/定位摘录和资料摘要写入，"
            "不得读取用户和 Chatbot 的原始聊天记录。\n"
            "2. intent=generate_from_requirements 时，输出一份完整板书，operation 使用 replace_document，"
            "content_text 必须包含清晰章节标题；默认按一节可直接教学的完整文档篇幅生成，"
            "优先组织多个相互衔接的 H2 小节，篇幅要足以支撑一节课直接教学，"
            "除非用户明确要求短版、速览或只要大纲。\n"
            "3. intent=edit_existing_document 时，有选区就优先 replace_selection；需要新增内容时用 append_section；"
            "只有 target_scope=whole_document 且 allow_replace_document=true 时才允许 replace_document，"
            "否则不要整体覆盖已有文档。\n"
            "4. content_text 是可直接进入文档的正文；必须像 ChatGPT 正常回答一样使用 Markdown 或普通文本，"
            "用 Markdown 表达标题、列表、加粗和表格。除真正公式的 LaTeX 定界符外，"
            "不得在 content_text 或 content_html 中输出 HTML 标签，例如 <h1>、<p>、<strong>、<table>。"
            "全文重写、缩短或精简时也不能把原有层级压成普通段落。不要用代码块包裹全文。"
            "content_html 必须为空字符串；后端会把 content_text 规范化为可编辑富文本。\n"
            "5. 如果 learning_requirement_context.document_quality_repair 存在，说明上一版输出已被后端质量门禁拒绝；"
            "必须根据 failure_reason 重写不合格内容，直到满足格式、结构和操作范围要求，"
            "不得重复返回被拒绝的 HTML、扁平段落或越界替换。\n"
            "6. 完整生成时，每个主要 H2 小节都要有可讲解密度：核心解释、必要步骤或推理、"
            "至少一个例子或类比、常见误区/注意点、一个检查问题。不要只写目录式提纲。\n"
            "7. section_titles 写入本次文档的主要 H2 章节标题，用于后续分节讲解。\n"
            "8. 格式约束：语言例句、语法说明、纠错箭头、对话台词和普通解释一律输出普通文字；"
            "只有真正公式才使用 LaTeX 定界符或公式排版。不要为了强调普通文字而使用 $...$、\\(...\\)、\\[...\\] 或 $$...$$。\n"
            "9. 不写任何固定主题模板，不根据主题名、资料名或样例走特殊规则。"
        )
        user_payload: dict[str, Any] = {
            "intent": intent,
            "learning_requirement_context": learning_requirement_context,
            "resource_summary": resource_summary,
            "response_contract": {
                "operation": "replace_document、replace_selection 或 append_section。",
                "title": "文档标题；局部编辑时可沿用当前标题。",
                "content_text": (
                    "完整生成时是整份板书，默认按一节可直接教学的较完整篇幅展开；"
                    "局部替换时是替换片段；追加时是追加片段。必须像 ChatGPT 正常回答一样使用 Markdown/普通文本，"
                    "用 Markdown 保留标题、列表、加粗、表格等文档结构；不得输出 HTML 标签；"
                    "普通语言文本不得包进公式定界符，只有真正公式才使用 LaTeX 定界符。"
                ),
                "content_html": "必须为空字符串；不要输出 HTML。后端内部会从 content_text 生成编辑器 HTML。",
                "summary": "一句话说明本次生成或编辑了什么。",
                "chatbot_message": "可直接展示给学习者的自然语言短回复，说明本次动作结果，不要套用固定格式。",
                "section_titles": "主要章节标题数组，用于分节讲解。",
            },
        }
        if is_initial_generation:
            user_payload["generation_source"] = "frozen_learning_requirement"
        else:
            user_payload.update(
                {
                    "lesson_title": lesson_title,
                    "current_document_title": current_document_title,
                    "current_document_text": current_document_text,
                    "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "无选中引用",
                    "target_scope": target_scope or "",
                    "allow_replace_document": allow_replace_document,
                    "input_isolation": BOARD_EDITOR_CHAT_LOG_REDACTION,
                }
            )
        user_prompt = _json(user_payload)
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardDocumentEditResult,
        )
        return result if isinstance(result, BoardDocumentEditResult) else None

    def generate_board_document_quality_review(
        self,
        *,
        intent: str,
        lesson_title: str,
        learning_requirement_context: dict[str, Any],
        operation: str,
        candidate_title: str,
        candidate_content_text: str,
        resource_summary: str,
        current_document_title: str = "",
        target_scope: str | None = None,
        selection_excerpt: str | None = None,
        section_titles: list[str] | None = None,
    ) -> BoardDocumentQualityReview | None:
        system_prompt = (
            "你是 OpenClass 的板书文档质量审查 AI，只负责审查候选板书是否能安全写入，"
            "不负责和用户聊天，也不生成最终板书。\n"
            "规则：\n"
            "1. 只依据结构化需求/任务清单、候选板书正文、资料摘要和板书侧上下文审查；"
            "不得要求或引用用户与 Chatbot 的原始聊天记录。\n"
            "2. 只做通用质量审查，不写任何主题、学科、教材、考试或样例专属规则。\n"
            "3. 必须检查候选文档内部一致性：标题、术语、定义、解释、例子、练习、答案、"
            "输出范围、用户约束和章节结构之间不能互相矛盾。\n"
            "4. 如果发现候选内容自相矛盾、范围错位、练习答案与说明冲突、术语前后不一致，"
            "status 必须为 repair_required，并给出可交给 BoardEditor 重写的通用修复指令。\n"
            "5. 如果只是需要更华丽的表达但没有一致性或安全问题，status 使用 pass。"
        )
        user_prompt = _json(
            {
                "intent": intent,
                "lesson_title": lesson_title,
                "learning_requirement_context": learning_requirement_context,
                "operation": operation,
                "candidate_title": candidate_title,
                "candidate_content_text": candidate_content_text,
                "resource_summary": resource_summary,
                "current_document_title": current_document_title,
                "target_scope": target_scope or "",
                "selection_excerpt": selection_excerpt.strip() if selection_excerpt else "",
                "section_titles": section_titles or [],
                "input_isolation": BOARD_EDITOR_CHAT_LOG_REDACTION,
                "response_contract": {
                    "status": "pass 或 repair_required。",
                    "issues": "候选文档存在的通用一致性问题；没有问题则为空数组。",
                    "repair_instruction": "status=repair_required 时，给 BoardEditor 的自然语言重写指令。",
                    "checked_dimensions": "实际检查过的维度，如 title_terms、definitions、examples、exercises、answers、scope、structure。",
                },
            }
        )
        result = self._parse(
            "board",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=BoardDocumentQualityReview,
        )
        return result if isinstance(result, BoardDocumentQualityReview) else None

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
            "任务：从最近对话和课程上下文中动态判断用户当前学习产物或练习需求是否足够清晰。\n"
            "规则：\n"
            "1. key_facts 只写用户已经透露的关键信息，每项包含 category、label、value、evidence；"
            "category 必须是 learning、level、vocabulary、scenario、output、other 之一，"
            "label 只做中文展示短标签，value 保留用户透露的具体内容；"
            "不要使用 preferred_output、output_preference 等内部字段名，也不要记录输出形式偏好；"
            "不要把缺失信息、Chatbot 追问、系统默认选项或推测写进去；没有就返回空数组。\n"
            "2. checklist 必须是 3 到 5 个当前最关键的动态需求项，不使用固定栏目模板。\n"
            "3. 优先判断三类通用信息是否足够：用户要生成或练习的内容形态、用户当前水平/已有基础、"
            "用户要面对什么任务或使用场景；若对话中已有其他更关键约束，可以动态替换或合并。\n"
            "4. checklist 每个已明确项必须有来自对话或上下文的简短 evidence；不确定就 is_clear=false。\n"
            "5. 不要猜用户没有透露的信息；缺失内容写入 missing_items 或 next_question。\n"
            "6. next_question 只问下一轮最有价值的一个问题，语言自然，避免机械套话。\n"
            "7. ready_for_board 仅在这些动态需求足以支撑后续生成有用板书时为 true；"
            "单个新知识点的知识板书由初始学习模式分类链路处理，不要求补齐整篇课程需求。\n"
            "8. 如果用户表达的是对现有板书局部内容的动作，额外填写 action_type、action_instruction、target_hint："
            "action_type 只能是 generate_board、explain_target、rewrite_target、expand_target、simplify_target；"
            "target_hint 只写用户给出的定位线索，不猜具体段落 ID。\n"
            "9. 如果用户表达的是希望系统按照某种规则进行连续对话或学习互动，"
            "填写 interaction_rule_draft；它只描述用户给出的通用互动规则、目标、目标线索、用户应如何输入、"
            "Chatbot 应如何输出，不写任何具体主题或样例专属分支。\n"
            "10. 不写任何主题、资料或样例专属规则。"
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
        visible_stream_field: str | None = None,
        disable_stream_repair: bool = False,
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
        if provider == "openai_codex":
            client = self._ensure_openai_codex_client()
            if not client:
                raise RuntimeError("OpenAI Codex app-server is not configured")
            return client.parse(
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
                visible_stream_field=visible_stream_field,
                disable_stream_repair=disable_stream_repair,
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
                visible_stream_field=visible_stream_field,
                disable_stream_repair=disable_stream_repair,
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
                visible_stream_field=visible_stream_field,
                disable_stream_repair=disable_stream_repair,
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
                visible_stream_field=visible_stream_field,
                disable_stream_repair=disable_stream_repair,
            )
        return self._call_openai_parse(
            role=role,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            client=self.client,
            config=self.config,
            visible_stream_field=visible_stream_field,
            disable_stream_repair=disable_stream_repair,
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
        visible_stream_field: str | None = None,
        disable_stream_repair: bool = False,
    ) -> ParsedAIResponse | Any:
        client = client or self.client
        config = config or self.config
        assert client is not None
        compat_mode = config.compat_api.strip().lower()
        observer = _ai_stream_observer.get()
        stream_field = visible_stream_field or (
            "chatbot_message" if role == "chatbot" else "content_text" if role == "board" else None
        )
        if observer and stream_field and compat_mode not in {"chat", "chat_completions", "chat-completions"}:
            try:
                return self._call_openai_chat_parse(
                    role=role,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    client=client,
                    visible_stream_field=stream_field,
                    disable_stream_repair=disable_stream_repair,
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
                visible_stream_field=stream_field,
                disable_stream_repair=disable_stream_repair,
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
                visible_stream_field=stream_field,
                disable_stream_repair=disable_stream_repair,
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
        visible_stream_field: str | None = None,
        disable_stream_repair: bool = False,
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
        stream_field = visible_stream_field or (
            "chatbot_message" if role == "chatbot" else "content_text" if role == "board" else None
        )
        if observer and stream_field:
            try:
                streamed = self._stream_openai_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=schema,
                    schema_payload=schema_payload,
                    role=role,
                    field_name=stream_field,
                    use_response_format=True,
                )
            except AIStreamOutputError as exc:
                if disable_stream_repair and exc.visible_field_value:
                    return ParsedAIResponse(
                        output_parsed=None,
                        output_text=exc.output_text,
                        visible_field_value=exc.visible_field_value,
                        visible_field_was_streamed=exc.visible_field_was_streamed,
                        structured_parse_failed=True,
                    )
                if not self._should_retry_openai_chat_without_schema(exc):
                    raise
                streamed = self._stream_openai_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=schema,
                    schema_payload=schema_payload,
                    role=role,
                    field_name=stream_field,
                    use_response_format=False,
                )
            except Exception as exc:
                if not self._should_retry_openai_chat_without_schema(exc):
                    raise
                streamed = self._stream_openai_chat_completion(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=schema,
                    schema_payload=schema_payload,
                    role=role,
                    field_name=stream_field,
                    use_response_format=False,
                )
            output_text = streamed.output_text
            try:
                output_parsed = schema.model_validate(_extract_json_object(output_text))
            except Exception as exc:
                if disable_stream_repair and streamed.visible_field_value:
                    return ParsedAIResponse(
                        output_parsed=None,
                        output_text=output_text,
                        visible_field_value=streamed.visible_field_value,
                        visible_field_was_streamed=streamed.visible_field_was_streamed,
                        structured_parse_failed=True,
                    )
                repair_prompt = (
                    "The previous streamed response could not be parsed as valid JSON. "
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
                    visible_field_value=streamed.visible_field_value,
                    visible_field_was_streamed=streamed.visible_field_was_streamed,
                )
            return ParsedAIResponse(
                output_parsed=output_parsed,
                output_text=output_text,
                visible_field_value=streamed.visible_field_value,
                visible_field_was_streamed=streamed.visible_field_was_streamed,
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
    ) -> StreamedChatCompletionResult:
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
        output_parts: list[str] = []
        last_visible_value = ""
        try:
            stream = client.chat.completions.create(**kwargs)
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
        except Exception as exc:
            raise AIStreamOutputError(
                str(exc),
                output_text="".join(output_parts),
                visible_field_value=last_visible_value,
                visible_field_was_streamed=bool(last_visible_value),
            ) from exc
        return StreamedChatCompletionResult(
            output_text="".join(output_parts),
            visible_field_value=last_visible_value,
            visible_field_was_streamed=bool(last_visible_value),
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

        selection = _board_model_selection.get() if role == "board" else None
        if selection is None:
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
        if provider == "openai_codex":
            return self.openai_codex_config.model_for(role)
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
        if provider == "openai_codex":
            return self._ensure_openai_codex_client() is not None
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
        if failed_provider == "openai_codex":
            return []
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

    def _parse_response(
        self,
        role: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        *,
        log_user_prompt: str | None = None,
        visible_stream_field: str | None = None,
        disable_stream_repair: bool = False,
    ) -> ParsedAIResponse | None:
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
            fallback = self._try_provider_fallback(
                role=role,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                call_details=call_details,
                failed_provider=provider,
                failed_model=requested_model,
                error=RuntimeError("client_disabled"),
            )
            return ParsedAIResponse(output_parsed=fallback) if fallback is not None else None

        started_at = time.perf_counter()
        try:
            response = self._call_parse(
                role=role,
                provider=provider,
                model=requested_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                visible_stream_field=visible_stream_field,
                disable_stream_repair=disable_stream_repair,
            )
            ai_usage_logger.log_event(
                self._log_event_name(provider, ""),
                **call_details,
                duration_ms=_elapsed_ms(started_at),
                response_id=getattr(response, "id", None),
                output_text=getattr(response, "output_text", None),
                usage=getattr(response, "usage", None),
                parsed_output=response.output_parsed,
                visible_field_value=getattr(response, "visible_field_value", ""),
                visible_field_was_streamed=getattr(response, "visible_field_was_streamed", False),
                structured_parse_failed=getattr(response, "structured_parse_failed", False),
            )
            return response
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            duration_ms = _elapsed_ms(started_at)
            if disable_stream_repair and isinstance(exc, AIStreamOutputError) and exc.visible_field_value:
                response = ParsedAIResponse(
                    output_parsed=None,
                    output_text=exc.output_text,
                    visible_field_value=exc.visible_field_value,
                    visible_field_was_streamed=exc.visible_field_was_streamed,
                    structured_parse_failed=True,
                )
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_structured_parse_failed"),
                    **call_details,
                    duration_ms=duration_ms,
                    error=str(exc),
                    output_text=response.output_text,
                    visible_field_value=response.visible_field_value,
                    visible_field_was_streamed=response.visible_field_was_streamed,
                    structured_parse_failed=True,
                )
                return response

            fallback_model = self._fallback_model_for(provider, exc, requested_model)
            if fallback_model:
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_retry"),
                    **call_details,
                    duration_ms=duration_ms,
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
                        visible_stream_field=visible_stream_field,
                        disable_stream_repair=disable_stream_repair,
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
                        visible_field_value=getattr(response, "visible_field_value", ""),
                        visible_field_was_streamed=getattr(response, "visible_field_was_streamed", False),
                        structured_parse_failed=getattr(response, "structured_parse_failed", False),
                    )
                    return response
                except Exception as retry_exc:  # pragma: no cover - network/runtime dependent
                    if disable_stream_repair and isinstance(retry_exc, AIStreamOutputError) and retry_exc.visible_field_value:
                        response = ParsedAIResponse(
                            output_parsed=None,
                            output_text=retry_exc.output_text,
                            visible_field_value=retry_exc.visible_field_value,
                            visible_field_was_streamed=retry_exc.visible_field_was_streamed,
                            structured_parse_failed=True,
                        )
                        ai_usage_logger.log_event(
                            self._log_event_name(provider, "_structured_parse_failed"),
                            **{**call_details, "model": fallback_model},
                            fallback_from_model=requested_model,
                            duration_ms=_elapsed_ms(retry_started_at),
                            error=str(retry_exc),
                            output_text=response.output_text,
                            visible_field_value=response.visible_field_value,
                            visible_field_was_streamed=response.visible_field_was_streamed,
                            structured_parse_failed=True,
                        )
                        return response
                    exc = retry_exc

            if self._should_retry_provider_fallback(exc):
                fallback = self._try_provider_fallback(
                    role=role,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    call_details=call_details,
                    failed_provider=provider,
                    failed_model=requested_model,
                    error=exc,
                )
                if fallback is not None:
                    return ParsedAIResponse(output_parsed=fallback)
            ai_usage_logger.log_event(
                self._log_event_name(provider, "_error"),
                **call_details,
                duration_ms=duration_ms,
                error=str(exc),
                output_text=getattr(exc, "output_text", None),
                repair_output_text=getattr(exc, "repair_output_text", None),
            )
            logger.warning("%s %s call failed, falling back to heuristic flow: %s", provider, role, exc)
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
            if schema is ChatbotReply and isinstance(exc, AIOutputParseError):
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_retry"),
                    **call_details,
                    duration_ms=primary_duration_ms,
                    retry_model=requested_model,
                    retry_reason="chatbot_reply_parse",
                    error=str(exc),
                    output_text=getattr(exc, "output_text", None),
                    repair_output_text=getattr(exc, "repair_output_text", None),
                )
                retry_started_at = time.perf_counter()
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
                        retry_reason="chatbot_reply_parse",
                        duration_ms=_elapsed_ms(retry_started_at),
                        response_id=getattr(response, "id", None),
                        output_text=getattr(response, "output_text", None),
                        usage=getattr(response, "usage", None),
                        parsed_output=response.output_parsed,
                    )
                    return response.output_parsed
                except Exception as retry_exc:  # pragma: no cover - network/runtime dependent
                    recovered_chatbot_reply = _chatbot_reply_from_unstructured_output(retry_exc)
                    if recovered_chatbot_reply is not None:
                        ai_usage_logger.log_event(
                            self._log_event_name(provider, "_recovered"),
                            **call_details,
                            duration_ms=_elapsed_ms(retry_started_at),
                            error=str(retry_exc),
                            output_text=getattr(retry_exc, "output_text", None),
                            repair_output_text=getattr(retry_exc, "repair_output_text", None),
                            parsed_output=recovered_chatbot_reply,
                            recovered_after_retry=True,
                        )
                        return recovered_chatbot_reply
            recovered_chatbot_reply = _chatbot_reply_from_unstructured_output(exc) if schema is ChatbotReply else None
            if recovered_chatbot_reply is not None:
                ai_usage_logger.log_event(
                    self._log_event_name(provider, "_recovered"),
                    **call_details,
                    duration_ms=primary_duration_ms,
                    error=str(exc),
                    output_text=getattr(exc, "output_text", None),
                    repair_output_text=getattr(exc, "repair_output_text", None),
                    parsed_output=recovered_chatbot_reply,
                )
                return recovered_chatbot_reply
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
                    recovered_chatbot_reply = (
                        _chatbot_reply_from_unstructured_output(retry_exc) if schema is ChatbotReply else None
                    )
                    if recovered_chatbot_reply is not None:
                        ai_usage_logger.log_event(
                            self._log_event_name(provider, "_recovered"),
                            **{**call_details, "model": fallback_model},
                            fallback_from_model=requested_model,
                            duration_ms=_elapsed_ms(retry_started_at),
                            error=str(retry_exc),
                            output_text=getattr(retry_exc, "output_text", None),
                            repair_output_text=getattr(retry_exc, "repair_output_text", None),
                            parsed_output=recovered_chatbot_reply,
                        )
                        return recovered_chatbot_reply
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
