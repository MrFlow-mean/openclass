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
    OPENAI_DEFAULT_TEXT_MODEL,
    OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL,
    OPENAI_OFFICIAL_BASE_URL,
    OPENAI_IMAGE_MODEL,
    default_text_selection,
)
from app.services.lesson_factory import slugify
from app.services.rich_document import build_document

logger = logging.getLogger(__name__)
_URLLIB_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

REFERENCE_HANDOUT_QUALITY_STANDARD = (
    "Chapter handout quality: polished teaching prose, not pasted notes. Roughly ten coherent H2 sections from framing through summary and checks. "
    "Strip OCR noise, page numbers, broken formulas, and filename or downloader cruft; cite sources without treating metadata as lesson body. "
    "No placeholder headings or deferrals. Match depth to material shape—conceptual, procedural, quantitative, argumentative, case-based, textual—"
    "with real paragraphs under each heading, not outline-only labels. Do not merely summarize the source; add motivation, links, worked intuition, "
    "and classroom checks. Use substantial length in the teaching language for a full chapter."
)


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


def _redact_reference_payload(reference: dict[str, Any] | None) -> dict[str, Any] | None:
    if reference is None:
        return None
    redacted = dict(reference)
    chapter_text = str(redacted.pop("chapter_text", "") or "")
    if chapter_text:
        redacted["chapter_text_redacted"] = f"<omitted {len(chapter_text)} chars>"
    return redacted


def _compact_generation_request(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _contract_has_explicit_append_intent(compact: str) -> bool:
    section_targets = ("页面", "一页", "几页", "多页", "章节", "新章节", "一节", "几节", "整章")
    content_targets = (*section_targets, "内容")
    forward_signals = ("继续写", "续写", "接着写", "再写", "往后写", "继续生成")
    create_signals = ("新增", "追加", "新生成", "再生成")
    if any(signal in compact for signal in forward_signals) and any(target in compact for target in content_targets):
        return True
    if any(signal in compact for signal in create_signals) and any(target in compact for target in content_targets):
        return True
    if "补充" in compact and any(target in compact for target in section_targets):
        return True
    if any(signal in compact for signal in ("加上", "加几", "加一", "加个", "添加")) and any(
        target in compact for target in section_targets
    ):
        return True
    tail_markers = ("在后面补", "在末尾补", "追加到末尾", "接在后面", "放到最后", "另起一节", "另起一章")
    return any(marker in compact for marker in tail_markers)


def _contract_is_in_place_expansion(compact: str) -> bool:
    if _contract_has_explicit_append_intent(compact):
        return False
    expansion_signals = (
        "扩展",
        "扩写",
        "展开",
        "细化",
        "丰富",
        "补全",
        "完善",
        "补充",
        "讲透",
        "更详细",
        "更细致",
        "细致讲解",
        "详细讲解",
        "详细解析",
        "全面",
    )
    current_targets = (
        "板书",
        "版书",
        "讲义",
        "文档",
        "内容",
        "当前",
        "原有",
        "已有",
        "这一节",
        "这节",
        "这一章",
        "这章",
        "小节",
        "段落",
        "例子",
        "案例",
        "知识点",
    )
    return any(signal in compact for signal in expansion_signals) and any(target in compact for target in current_targets)


def _document_edit_generation_contract(
    *,
    request_message: str,
    scope_action: ScopeAction | None,
) -> dict[str, Any]:
    compact = _compact_generation_request(request_message)
    append_signals = (
        "新增",
        "追加",
        "补充",
        "加上",
        "再生成",
        "新生成",
        "继续生成",
        "继续写",
        "续写",
        "接着写",
        "再写",
        "往后写",
    )
    chapter_targets = ("章节", "新章节", "一节", "几节", "整章")
    page_targets = ("页面", "一页", "几页", "多页")
    if scope_action != "append_section" and _contract_is_in_place_expansion(compact):
        return {
            "mode": "expand_existing_board_in_place",
            "html_scope": "return the complete updated board document, not only a new section",
            "required_behavior": "preserve the original heading order and expand the existing paragraphs, examples, and explanations under their relevant headings",
            "forbidden": "do not append a 补充章节/新增章节 unless the user explicitly asks to 新增/追加/续写/新章节/页面/末尾",
        }
    if scope_action == "append_section" and any(target in compact for target in chapter_targets):
        return {
            "mode": "append_full_chapter",
            "append_position": "end_of_current_board",
            "html_scope": "return only the new chapter HTML, not the existing board",
            "required_scale": "comparable to a newly generated board/lesson, not a short addendum",
            "minimum_structure": "one h2 chapter title, about 8-10 h3 subsections, multiple developed paragraphs per subsection, concrete examples, exercises, and reference answers or summary",
            "target_length": "usually 2400-4500 Chinese characters unless the requested topic is intentionally narrow",
            "forbidden": "do not echo the user instruction, do not return only an introduction, and do not ask the user to fill examples later",
        }
    if scope_action == "append_section" and any(signal in compact for signal in append_signals):
        return {
            "mode": "append_new_section",
            "append_position": "end_of_current_board",
            "html_scope": "return only the new HTML to append, not the existing board",
            "required_scale": "large enough to teach directly; avoid one-paragraph placeholders",
        }
    if any(target in compact for target in page_targets) and any(signal in compact for signal in append_signals):
        return {
            "mode": "append_new_page",
            "append_position": "end_of_current_board",
            "html_scope": "return only the new page or section HTML, not the existing board",
        }
    return {
        "mode": "edit_or_generate_document",
        "html_scope": "follow the user's edit scope and selection rules",
    }


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
    api_key: str | None = Field(default_factory=_shared_api_key)
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL") or OPENAI_OFFICIAL_BASE_URL)
    default_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", DEFAULT_TEXT_MODEL))
    image_model: str = Field(default_factory=lambda: os.getenv("OPENAI_IMAGE_MODEL", OPENAI_IMAGE_MODEL))
    pm_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_PM_MODEL"))
    board_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BOARD_MODEL"))
    guide_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_GUIDE_MODEL"))
    teacher_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_TEACHER_MODEL"))
    lesson_model: str | None = Field(default_factory=lambda: os.getenv("OPENAI_LESSON_MODEL"))
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
                "pm": self.config.model_for("pm"),
                "board": self.config.model_for("board"),
                "guide": self.config.model_for("guide"),
                "teacher": self.config.model_for("teacher"),
                "lesson": self.config.model_for("lesson"),
                "image": self.config.image_model,
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
                "The board is now one continuous rich document, not separate blocks."
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
                "Fill learning_need_checklist with 2 to 6 concrete learner needs derived from the user message, selected text, recent conversation, and board outline. "
                "When the learner asks a new question while a numbered board section is being taught, preserve existing checklist items and add the new need as a child marker such as 4.1 or 4.2 when that marker is present in context. "
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
        board_edit_action: str | None,
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
                "Default ask-mode follow-up questions should be no_change: Board AI prepares an internal lecture handout and Teacher AI explains, without changing the visible board. "
                "Use edit_board/append_section/create_new_lesson only when the learner explicitly asks to generate/rewrite/expand board or handout content, or when board_edit_action is confirm. "
                "When board_edit_action is confirm, choose the best write strategy yourself: edit_board for in-place expansion, append_section for an extension chapter, or create_new_lesson for a genuinely separate topic. "
                "Use append_section when the learner explicitly asks for a new page/section/chapter or when confirmed expansion should safely extend the current lesson. "
                "If the confirmed request corresponds to a learning_need_checklist child marker like 2.1, append it as a child subsection rather than rewriting the existing section."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "user_message": request_message,
                    "selection": selection,
                    "interaction_mode": interaction_mode,
                    "scope_action": scope_action,
                    "board_edit_action": board_edit_action,
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
            "generation_contract": _document_edit_generation_contract(
                request_message=request_message,
                scope_action=scope_action,
            ),
        }
        log_payload = dict(prompt_payload)
        log_payload["selected_reference"] = _redact_reference_payload(selected_reference)
        return self._parse(
            "board",
            system_prompt=(
                "You are Board AI editing a Word-like rich teaching document. "
                f"{REFERENCE_HANDOUT_QUALITY_STANDARD} "
                "Return replacement_html containing coherent long-form teaching prose. "
                "If a selection is provided and the user did not explicitly ask to rewrite the whole document, edit only that selection and never rewrite the full document. "
                "For enhancement requests such as 完善/补充/详细解析/全面/展开, keep the selected original wording visible and continue writing from it instead of deleting it. "
                "If no selection is provided and the user asks to 扩展/扩写/展开/细化/丰富/更详细/细致讲解 the current board, handout, content, examples, or knowledge points, edit the existing document in place: preserve the original heading order, expand paragraphs and examples under the relevant headings, and return the complete updated document. Do not append a 补充章节/新增章节 unless they explicitly ask for 新增/追加/续写/新章节/页面/末尾. "
                "If the user explicitly asks to add a new page, add several pages, append at the end, continue writing after the current board, or add a new section/chapter, return only the new HTML section/page content, set replace_whole false, and set target_action to append_section. "
                "For append_section, write the actual expanded new section with concrete explanations, examples, checks, and teaching flow; never echo the user's instruction as board content. "
                "If learning_requirement_sheet.learning_need_checklist contains a child marker such as 2.1/4.2 for the current request, use that marker in the appended heading and write it as a child subsection that connects back to the parent section. "
                "When append_section continues or adds a chapter (续写章节/新章节/一节/整章), append substantive new teaching content whose structure follows the current board, reference material, and learner goal. "
                "Do not force a fixed number of subsections or a preset outline; choose the document shape from the content itself. "
                "If the user asks to generate or rewrite the lesson, return a complete HTML document whose headings and body are derived from the learning requirement sheet, reference material, and requested content form. "
                "If selected_reference.chapter_text is provided, treat it as the full relevant chapter content and ground the handout in that chapter. "
                "Never return a placeholder template, empty section label, or fill-in-later scaffold. "
                "If the request is to teach a chapter, start from the chapter's concrete ideas, terms, or excerpts immediately and make each section detailed enough to teach directly. "
                "If the user asks for dialogue, scenario, practice, data analysis, close reading, or project work, let that requested form drive the body instead of applying a fixed template. "
                "Also return board_teaching_guide in Chinese, permanently bound to this board snapshot. "
                "In board_teaching_guide, explain which board excerpts should be taught first, why they were selected, "
                "which learner needs they correspond to, and what teaching flow Teacher AI should follow. "
                "When the visible board contains quantitative data that should be visualized, keep the data fragment explicit and machine-readable enough for the chart image generator to extract it. "
                "Use this chart selection policy: trend over time -> line chart; size comparison -> bar/horizontal bar; share/composition -> pie/donut; distribution -> histogram/box plot; two-variable relationship -> scatter; three-variable relationship -> bubble; whole over time -> area; multi-dimensional ability -> radar; object relations -> network graph; geographic data -> map; total plus growth rate -> combo chart. "
                "Fill board_teaching_guide.lecture_handout as an internal lecture handout for Teacher AI. It may be richer than the visible board, but it must stay grounded in the document/reference and must not be treated as persisted board content. "
                "Fill board_teaching_guide.section_plans by H2 section. Each section plan should tell Teacher AI the section title, "
                "board summary, core knowledge points, teaching steps, teaching method, example or analogy, common pitfalls, "
                "check question, and transition to the next section. "
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
        learning_clarification: dict[str, Any],
        clarification_questions: list[str],
        reference_prompt: dict[str, Any] | None,
        selected_reference: dict[str, Any] | None,
        teaching_progress: dict[str, Any] | None = None,
    ) -> str | None:
        result = self._parse(
            "pm",
            system_prompt=(
                "You are Teacher AI speaking to the learner in Chinese. "
                "Every visible chat paragraph must be authored by you for this specific turn; never output canned workflow, loading, error, or fallback copy. "
                "Start with the subject matter itself, not workflow status, board status, or what you are about to do. "
                "Sound like a live teacher, not a narrator reading the board. "
                "When the first learner turn is a broad learning goal and the learner's level/background is missing, do not teach a generic orientation; ask a natural diagnostic question about study stage, concrete subtopic, and purpose first. "
                "For any advanced or prerequisite-heavy topic, ask for the learner's stage and prerequisite background in terms that fit that topic, without assuming a subject-specific path. "
                "If clarification is needed, ask at most one very short question and avoid repeating fixed wording about level/scenario. "
                "If the document was updated, do not announce the update unless the learner asked about the document. "
                "Teach only from board_teaching_guide.selected_items, board_teaching_guide.lecture_handout, and board_teaching_guide.teacher_brief. "
                "Do not independently introduce new curriculum content that Board AI did not prepare. "
                "Do not quote, enumerate, or read out the board unless the learner explicitly asks for exact wording. "
                "Prefer this structure: first give the core idea in your own words, then explain why it matters, then offer one analogy, example, or check question. "
                "Keep the answer tight and classroom-like, with minimal transition filler, usually 2 to 4 short paragraphs. "
                "Use short paragraphs separated by blank lines. Never return one dense wall of text. "
                "Never end with a generic prompt like 顺手告诉我, 你可以告诉我, 是为了考试/工作/兴趣; if you need background, ask a domain-specific prerequisite question. "
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
                    "learning_clarification": learning_clarification,
                    "clarification_questions": clarification_questions,
                    "reference_prompt": reference_prompt,
                    "selected_reference": selected_reference,
                    "teaching_progress": teaching_progress,
                }
            ),
            schema=TeacherMessageOutput,
        )
        return result.teacher_message if result else None

    def generate_clarification_message(
        self,
        *,
        lesson_title: str,
        request_message: str,
        requirements: LearningRequirementSheet,
        learning_clarification: dict[str, Any],
        clarification_questions: list[str],
        conversation: list[dict[str, Any]],
    ) -> str | None:
        result = self._parse(
            "teacher",
            system_prompt=(
                "You are PM AI, a learning-needs interviewer for an AI blackboard course workbench, speaking to the learner in Chinese. "
                "Your only job is to help the learner express what they want to learn, why they want it, their current background, and the result they expect. "
                "Generate the next user-facing interview reply yourself; every visible chat sentence must be newly authored for this turn. "
                "Do not copy canned wording, templates, or any provided question verbatim. "
                "Use the learner's latest wording and recent conversation to ask a natural, context-specific follow-up. "
                "When the learner gives only a broad learning category, ask for the concrete learning purpose, current level/study stage, and the first subtopic or problem they want to start from. "
                "Avoid repetitive form-like wording. Prefer one compact question, at most two short sentences. "
                "Do not teach substantive content, write board content, update the learning requirement sheet, or decide whether to edit the board. "
                "If a tiny orientation phrase helps the question feel natural, keep it brief and immediately return to interviewing."
            ),
            user_prompt=_json(
                {
                    "lesson_title": lesson_title,
                    "conversation": conversation,
                    "user_message": request_message,
                    "learning_requirement_sheet": requirements.model_dump(mode="json"),
                    "learning_clarification": learning_clarification,
                    "ai_generated_question_candidates_from_pm": clarification_questions,
                    "instruction": (
                        "The candidate questions, if present, are only semantic hints from PM AI. "
                        "Rewrite naturally and adapt to this learner; never paste them directly."
                    ),
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
                "and provide a concise teacher_brief plus lecture_handout that can drive a live spoken explanation. "
                "When the user asks a new follow-up during section-by-section teaching, prepare a temporary lecture_handout for that new need and show how it connects back to the current board section; do not require the visible board to contain the answer already. "
                "When a selected board excerpt contains quantitative data, note what data fragment should be handed to the chart image generator and follow this chart policy: trend over time -> line chart; comparison -> bar/horizontal bar; composition -> pie/donut; distribution -> histogram/box plot; two variables -> scatter; three variables -> bubble; whole over time -> area; multiple abilities -> radar; object relations -> network graph; geographic data -> map; total plus growth rate -> combo chart. "
                "lecture_handout is internal guidance for Teacher AI only; never rewrite the board itself. "
                "Also fill section_plans by H2 section: each plan must include the section heading, board excerpt, core points, "
                "teaching steps, teaching method, example or analogy, pitfalls, check question, and transition to the next section. "
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
                f"{REFERENCE_HANDOUT_QUALITY_STANDARD} "
                "Return one complete handout-style document, not blocks. Use HTML with h1/h2/h3/p/ol/ul/table when helpful. "
                "The document should be long enough for a real lesson and should follow the learning form requested by the user, "
                "such as explanation, scenario, dialogue, worked example, practice set, source-based analysis, or project walkthrough. "
                "Avoid card-like fragmented notes. "
                "If reference_context.chapter_text is provided, treat it as the full relevant chapter content and ground the new lesson in that chapter. "
                "Never return an empty scaffold or ask the learner to fill in examples later; provide concrete teaching content in this response."
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
