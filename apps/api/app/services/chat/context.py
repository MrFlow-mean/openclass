from __future__ import annotations

import re

from app.models import (
    ChatRequest,
    ConversationTurn,
    Lesson,
    ResourceLibraryItem,
    ResourceReferenceContext,
    SelectionRef,
)


MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8


def compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def board_summary(lesson: Lesson) -> str:
    document = lesson.board_document
    content = compact_text(document.content_text, limit=MAX_CONTEXT_CHARS)
    if content:
        return content
    return document.title or lesson.title


def resource_summary(resources: list[ResourceLibraryItem]) -> str:
    lines: list[str] = []
    for resource in resources[:6]:
        chapter_titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        if chapter_titles:
            lines.append(f"{resource.name}: {' / '.join(chapter_titles)}")
        else:
            lines.append(resource.name)
    return "\n".join(lines) or "暂无已上传资料摘要"


def resource_context_excerpt(reference: ResourceReferenceContext | None) -> str | None:
    if reference is None:
        return None
    lines = [
        f"参考资料：{reference.resource_name} / {reference.chapter_title}",
        f"资料摘要：{reference.summary}",
    ]
    if reference.teaching_points:
        lines.append("讲解要点：" + "；".join(reference.teaching_points[:4]))
    for chunk in reference.chunks[:4]:
        lines.append(f"{chunk.title}：{compact_text(chunk.excerpt, limit=520)}")
    return "\n".join(line for line in lines if line.strip())


def resource_summary_with_reference(
    resources: list[ResourceLibraryItem],
    reference: ResourceReferenceContext | None,
) -> str:
    parts = [resource_summary(resources)]
    reference_excerpt = resource_context_excerpt(reference)
    if reference_excerpt:
        parts.append(reference_excerpt)
    return "\n\n".join(parts)


def conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-MAX_CONVERSATION_TURNS:]
    return "\n".join(f"{turn.role}: {compact_text(turn.content, limit=500)}" for turn in turns if turn.content.strip())


def selection_excerpt(selection: SelectionRef | None, fallback: str | None = None) -> str | None:
    excerpt = selection.excerpt if selection else fallback
    compact = compact_text(excerpt, limit=1200)
    return compact or None


def chatbot_visible_selection_excerpt(request: ChatRequest, excerpt: str | None) -> str | None:
    if request.selection and request.selection.kind == "board":
        return None
    return excerpt


def merge_selection_and_reference(
    selection_excerpt: str | None,
    reference: ResourceReferenceContext | None,
) -> str | None:
    reference_excerpt = resource_context_excerpt(reference)
    return "\n\n".join(part for part in [selection_excerpt, reference_excerpt] if part)
