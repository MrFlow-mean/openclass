from __future__ import annotations

import re

from app.models import ConversationTurn, Lesson, LearningRequirementKeyFact, LearningRequirementSheet, ResourceLibraryItem
from app.services.chat.context import compact_text


MAX_RECOMMENDATION_SOURCES = 2
MAX_FACT_SOURCES = 4
PLACEHOLDER_PATTERNS = (
    "待确认",
    "尚未明确",
    "先澄清",
    "根据用户",
    "动态决定",
    "暂无",
)


def requirement_recommendation_context(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    user_message: str,
) -> str:
    """Build a small hidden context for optional inline learning recommendations."""
    fact_lines = _fact_lines(lesson=lesson, requirements=requirements)
    resource_lines = _resource_lines(resources)
    current_line = _current_message_line(user_message=user_message, conversation=conversation)
    if not fact_lines and not resource_lines and not current_line:
        return ""

    sections: list[str] = [
        "如果学习需求仍不清楚，可以基于以下已知来源，在追问后顺带给出最多两个学习内容入口推荐；来源不足时不要硬凑推荐。"
    ]
    if fact_lines:
        sections.append("已知需求线索：\n" + "\n".join(fact_lines[:MAX_FACT_SOURCES]))
    if resource_lines:
        sections.append("可参考资料入口：\n" + "\n".join(resource_lines[:MAX_RECOMMENDATION_SOURCES]))
    if current_line:
        sections.append(f"当前用户表达：{current_line}")
    sections.append("推荐必须是聊天里的自然短句，不得写成讲义正文，不得声称已生成或写入板书。")
    return "\n".join(sections)


def _fact_lines(*, lesson: Lesson, requirements: LearningRequirementSheet) -> list[str]:
    lines: list[str] = []
    lines.extend(_requirement_lines(requirements))
    for fact in _historical_key_facts(lesson):
        line = _line_from_fact(fact)
        if line:
            lines.append(line)
    return _dedupe(lines)[:MAX_FACT_SOURCES]


def _requirement_lines(requirements: LearningRequirementSheet) -> list[str]:
    candidates = [
        ("学习目标", requirements.learning_goal),
        ("当前水平", requirements.level),
        ("已有背景", requirements.known_background),
        ("输出偏好", requirements.output_preference),
    ]
    lines: list[str] = []
    for label, value in candidates:
        text = _usable_text(value, limit=120)
        if text:
            lines.append(f"- {label}: {text}")
    return lines


def _historical_key_facts(lesson: Lesson) -> list[LearningRequirementKeyFact]:
    facts: list[LearningRequirementKeyFact] = []
    for commit in reversed(lesson.history_graph.commits):
        raw = commit.metadata.get("learning_clarification") if isinstance(commit.metadata, dict) else None
        raw_facts = raw.get("key_facts") if isinstance(raw, dict) else None
        if not isinstance(raw_facts, list):
            continue
        for raw_fact in raw_facts:
            if not isinstance(raw_fact, dict):
                continue
            try:
                facts.append(LearningRequirementKeyFact.model_validate(raw_fact))
            except Exception:
                continue
        if len(facts) >= MAX_FACT_SOURCES:
            break
    return facts


def _line_from_fact(fact: LearningRequirementKeyFact) -> str:
    value = _usable_text(fact.value, limit=120)
    if not value:
        return ""
    label = _usable_text(fact.label, limit=40) or "历史线索"
    return f"- {label}: {value}"


def _resource_lines(resources: list[ResourceLibraryItem]) -> list[str]:
    lines: list[str] = []
    for resource in resources:
        resource_name = _usable_text(resource.name, limit=60)
        if not resource_name:
            continue
        for title, summary in _resource_entry_candidates(resource):
            title_text = _usable_text(title, limit=80)
            if not title_text:
                continue
            summary_text = _usable_text(summary, limit=120)
            if summary_text:
                lines.append(f"- {resource_name} / {title_text}: {summary_text}")
            else:
                lines.append(f"- {resource_name} / {title_text}")
            if len(lines) >= MAX_RECOMMENDATION_SOURCES:
                return lines
        if not resource.outline and not resource.chapter_shards:
            lines.append(f"- {resource_name}")
            if len(lines) >= MAX_RECOMMENDATION_SOURCES:
                return lines
    return lines[:MAX_RECOMMENDATION_SOURCES]


def _resource_entry_candidates(resource: ResourceLibraryItem) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for chapter in resource.outline[:2]:
        candidates.append((chapter.title, chapter.summary))
    if len(candidates) < MAX_RECOMMENDATION_SOURCES:
        for shard in resource.chapter_shards[: MAX_RECOMMENDATION_SOURCES - len(candidates)]:
            candidates.append((shard.title, shard.summary))
    return candidates


def _current_message_line(*, user_message: str, conversation: list[ConversationTurn]) -> str:
    latest = compact_text(user_message, limit=120)
    if _is_vague_only(latest):
        for turn in reversed(conversation[-4:]):
            if turn.role != "user":
                continue
            latest = compact_text(turn.content, limit=120)
            if latest and not _is_vague_only(latest):
                break
    if _is_vague_only(latest):
        return ""
    return latest


def _usable_text(value: str | None, *, limit: int) -> str:
    text = compact_text(value, limit=limit)
    if not text:
        return ""
    if any(pattern in text for pattern in PLACEHOLDER_PATTERNS):
        return ""
    return text


def _is_vague_only(value: str) -> bool:
    compact = re.sub(r"[\s，,。！？!?；;：:]+", "", value or "")
    return compact in {"", "不知道", "没想法", "没有想法", "随便", "你安排", "都可以", "推荐一下"}


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = re.sub(r"\s+", "", line)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result
