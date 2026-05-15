from __future__ import annotations

import re

from app.models import (
    ChatRequest,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
)
from app.services.resource_library import extract_reference_context
from app.services.workflow_roles.shared import compact

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


def _tokens(text: str) -> set[str]:
    lowered = (text or "").lower()
    tokens = {match.group(0) for match in _WORD_RE.finditer(lowered)}
    for match in _CJK_RE.finditer(lowered):
        run = match.group(0)
        if 1 < len(run) <= 12:
            tokens.add(run)
        for size in (2, 3):
            for index in range(0, max(0, len(run) - size + 1)):
                tokens.add(run[index : index + size])
    return {token for token in tokens if len(token.strip()) > 1}


def text_score(query: str, candidate: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    candidate_tokens = _tokens(candidate)
    if not candidate_tokens:
        return 0.0
    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0
    query_coverage = len(overlap) / len(query_tokens)
    candidate_coverage = len(overlap) / max(3, len(candidate_tokens))
    compact_query = compact(query, limit=80).lower()
    compact_candidate = compact(candidate, limit=500).lower()
    exact_boost = 0.18 if compact_query and compact_query in compact_candidate else 0.0
    return min(1.0, query_coverage * 0.78 + candidate_coverage * 0.22 + exact_boost)


def _resource_haystack(resource: ResourceLibraryItem, chapter_index: int) -> str:
    chapter = resource.outline[chapter_index]
    return "\n".join(
        [
            resource.name,
            chapter.title,
            chapter.summary,
            " ".join(chapter.path),
            " ".join(chapter.keywords),
            chapter.locator_hint or "",
        ]
    )


def match_resource_chapters(
    resources: list[ResourceLibraryItem],
    query: str,
    *,
    limit: int = 3,
) -> list[ResourceMatch]:
    matches: list[ResourceMatch] = []
    for resource in resources:
        for index, chapter in enumerate(resource.outline):
            score = text_score(query, _resource_haystack(resource, index))
            if score <= 0:
                continue
            matches.append(
                ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    reason=f"目录标题、摘要或关键词与当前学习请求有重合：{compact(query, limit=80)}",
                    score=round(score, 3),
                    is_high_overlap=score >= 0.42,
                )
            )
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches[:limit]


def _find_resource(resources: list[ResourceLibraryItem], resource_id: str | None) -> ResourceLibraryItem | None:
    if resource_id is None:
        return None
    return next((resource for resource in resources if resource.id == resource_id), None)


def reference_from_request(
    resources: list[ResourceLibraryItem],
    request: ChatRequest,
) -> ResourceReferenceContext | None:
    if request.resource_reference_action != "confirm":
        return None
    resource = _find_resource(resources, request.resource_reference_resource_id)
    if resource is None or request.resource_reference_chapter_id is None:
        return None
    return extract_reference_context(
        resource,
        request.resource_reference_chapter_id,
        user_query=request.message,
    )


def reference_prompt(match: ResourceMatch, request: ChatRequest) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=f"我在资料库里找到“{match.chapter_title}”。是否用这一章节来补全当前板书并继续讲解？",
        reason=match.reason,
        score=match.score,
    )

