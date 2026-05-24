from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    LibraryChapter,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceAction,
    ResourceReferenceContext,
    ResourceReferencePrompt,
)
from app.services.resource_library import extract_reference_context


DIRECT_REFERENCE_THRESHOLD = 0.68
REFERENCE_PROMPT_THRESHOLD = 0.42

GENERIC_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("为什么", "原因", "机制", "形成", "影响因素", "来源"),
    ("定义", "概念", "含义", "是什么", "意思"),
    ("例子", "示例", "案例", "举例"),
    ("步骤", "流程", "过程", "方法", "操作"),
    ("结论", "总结", "要点", "重点"),
    ("区别", "对比", "不同", "比较"),
    ("表格", "图表", "数据", "列表"),
    ("章节", "小节", "部分", "目录", "材料", "资料"),
)


@dataclass(frozen=True)
class ResourceResolution:
    matches: list[ResourceMatch]
    selected_reference: ResourceReferenceContext | None = None
    reference_prompt: ResourceReferencePrompt | None = None
    status: str = "none"

    @property
    def has_reference(self) -> bool:
        return self.selected_reference is not None


def resolve_resource_reference(
    *,
    resources: list[ResourceLibraryItem],
    user_message: str,
    reference_action: ResourceReferenceAction | None = None,
    reference_resource_id: str | None = None,
    reference_chapter_id: str | None = None,
    allow_direct_reference: bool = False,
) -> ResourceResolution:
    if reference_action == "skip":
        return ResourceResolution(matches=[], status="skipped")

    if reference_action == "confirm" and reference_resource_id and reference_chapter_id:
        match = _explicit_match(resources, reference_resource_id, reference_chapter_id)
        if match is None:
            return ResourceResolution(matches=[], status="missing")
        reference = _extract_reference(resources, match, user_message)
        return ResourceResolution(
            matches=[match],
            selected_reference=reference,
            status="selected" if reference else "missing",
        )

    matches = _rank_resource_matches(resources, user_message)
    if not matches:
        return ResourceResolution(matches=[], status="none")

    best = matches[0]
    if allow_direct_reference and best.score >= DIRECT_REFERENCE_THRESHOLD:
        reference = _extract_reference(resources, best, user_message)
        if reference is not None:
            return ResourceResolution(
                matches=matches[:5],
                selected_reference=reference,
                status="resolved",
            )

    if best.score >= REFERENCE_PROMPT_THRESHOLD:
        return ResourceResolution(
            matches=matches[:5],
            reference_prompt=_reference_prompt(best),
            status="prompt",
        )

    return ResourceResolution(matches=matches[:5], status="low_confidence")


def _rank_resource_matches(resources: list[ResourceLibraryItem], user_message: str) -> list[ResourceMatch]:
    query_terms = _query_terms(user_message)
    if not query_terms:
        return []

    scored: list[tuple[float, ResourceLibraryItem, LibraryChapter, str]] = []
    compact_message = _compact_text(user_message, limit=500)
    for resource in resources:
        for chapter in resource.outline:
            score = _chapter_score(query_terms, compact_message, resource, chapter)
            if score <= 0:
                continue
            reason = "根据用户描述与资料目录、章节摘要和关键词的匹配度定位。"
            scored.append((score, resource, chapter, reason))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []

    max_score = max(scored[0][0], 0.01)
    matches: list[ResourceMatch] = []
    seen: set[tuple[str, str]] = set()
    for score, resource, chapter, reason in scored:
        key = (resource.id, chapter.id)
        if key in seen:
            continue
        seen.add(key)
        normalized = min(1.0, 0.34 + (score / max_score) * 0.32 + min(score, 0.34))
        matches.append(
            ResourceMatch(
                resource_id=resource.id,
                chapter_id=chapter.id,
                resource_name=resource.name,
                chapter_title=chapter.title,
                reason=reason,
                score=round(normalized, 3),
                is_high_overlap=normalized >= DIRECT_REFERENCE_THRESHOLD,
            )
        )
        if len(matches) >= 8:
            break
    return matches


def _chapter_score(
    query_terms: set[str],
    compact_message: str,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
) -> float:
    title_text = " ".join([resource.name, *chapter.path, chapter.title])
    body_text = " ".join([chapter.summary, " ".join(chapter.keywords), chapter.locator_hint or ""])
    title_terms = _value_terms(title_text)
    body_terms = _value_terms(body_text)
    title_overlap = query_terms & title_terms
    body_overlap = query_terms & body_terms
    if not title_overlap and not body_overlap:
        return 0.0

    score = len(title_overlap) / max(len(query_terms), 1) * 0.78
    score += len(body_overlap) / max(len(query_terms), 1) * 0.42

    compact_title = _compact_text(chapter.title, limit=120).lower()
    compact_path = _compact_text(" ".join(chapter.path), limit=240).lower()
    lower_message = compact_message.lower()
    if compact_title and compact_title in lower_message:
        score += 0.55
    if compact_path and compact_path in lower_message:
        score += 0.26
    for keyword in chapter.keywords[:8]:
        normalized = keyword.strip().lower()
        if len(normalized) >= 2 and normalized in lower_message:
            score += 0.12
    return score


def _explicit_match(
    resources: list[ResourceLibraryItem],
    resource_id: str,
    chapter_id: str,
) -> ResourceMatch | None:
    for resource in resources:
        if resource.id != resource_id:
            continue
        for chapter in resource.outline:
            if chapter.id == chapter_id:
                return ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    reason="用户确认了要参考的资料章节。",
                    score=1.0,
                    is_high_overlap=True,
                )
    return None


def _extract_reference(
    resources: list[ResourceLibraryItem],
    match: ResourceMatch,
    user_message: str,
) -> ResourceReferenceContext | None:
    resource = next((candidate for candidate in resources if candidate.id == match.resource_id), None)
    if resource is None:
        return None
    return extract_reference_context(
        resource,
        match.chapter_id,
        user_query=user_message,
    )


def _reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=f"我找到了可能相关的资料章节：{match.resource_name} / {match.chapter_title}。要先参考这一章再继续吗？",
        reason=match.reason,
        confirm_label="参考这一章节",
        skip_label="先不参考",
        score=match.score,
    )


def _query_terms(text: str) -> set[str]:
    compact = _compact_text(text, limit=500)
    terms = _value_terms(compact)
    for group in GENERIC_CONCEPT_GROUPS:
        if any(item in compact for item in group):
            terms.update(group)
    return {term for term in terms if len(term.strip()) >= 2}


def _value_terms(value: str) -> set[str]:
    compact = _compact_text(value, limit=1600).lower()
    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact)}
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {term for term in terms if len(term.strip()) >= 2}


def _compact_text(value: str | None, *, limit: int = 1200) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."
