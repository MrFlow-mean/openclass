from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import (
    BoardDecision,
    BoardEditPrompt,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ScopeOption,
    SectionTeachingProgressView,
)

_SPACE_RE = re.compile(r"\s+")
_LOW_SUBSTANCE_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "继续",
    "继续讲",
    "继续下一节",
    "下一节",
}


@dataclass
class WorkflowResult:
    teacher_message: str
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = field(default_factory=list)
    patch_proposal: None = None
    scope_options: list[ScopeOption] = field(default_factory=list)
    resource_matches: list[ResourceMatch] = field(default_factory=list)
    reference_prompt: ResourceReferencePrompt | None = None
    board_edit_prompt: BoardEditPrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    teaching_progress: SectionTeachingProgressView | None = None
    document_changed: bool = False
    commit_label: str | None = None
    commit_message: str | None = None
    commit_metadata: dict[str, object] = field(default_factory=dict)


def compact(value: str, *, limit: int = 160) -> str:
    text = _SPACE_RE.sub(" ", value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def dedupe(items: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        compact_item = compact(item, limit=220)
        key = compact_item.lower()
        if not compact_item or key in seen:
            continue
        seen.add(key)
        result.append(compact_item)
        if len(result) >= limit:
            break
    return result


def message_topic(lesson: Lesson, request: ChatRequest) -> str:
    message = compact(request.message, limit=80)
    if message:
        return message
    return lesson.title


def is_low_substance_message(message: str) -> bool:
    normalized = _SPACE_RE.sub("", (message or "").strip().lower())
    if not normalized:
        return True
    return normalized in _LOW_SUBSTANCE_MESSAGES

