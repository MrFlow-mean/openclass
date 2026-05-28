from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    LibraryChapter,
    ResourceContextChunk,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceAction,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ResourceSegment,
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
    reference_segment_id: str | None = None,
    allow_direct_reference: bool = False,
) -> ResourceResolution:
    if reference_action == "skip":
        return ResourceResolution(matches=[], status="skipped")

    if reference_action == "confirm" and reference_resource_id and reference_chapter_id:
        match = _explicit_match(resources, reference_resource_id, reference_chapter_id, reference_segment_id)
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

    scored: list[tuple[float, ResourceLibraryItem, LibraryChapter, ResourceSegment | None, str]] = []
    compact_message = _compact_text(user_message, limit=500)
    for resource in resources:
        chapters_by_id = {chapter.id: chapter for chapter in resource.outline}
        for segment in resource.segments:
            chapter = chapters_by_id.get(segment.chapter_id)
            if chapter is None:
                continue
            score = _segment_score(query_terms, compact_message, resource, chapter, segment)
            if score <= 0:
                continue
            reason = "根据用户描述与资料正文片段、标题路径和关键词的匹配度定位。"
            scored.append((score, resource, chapter, segment, reason))
        for chapter in resource.outline:
            score = _chapter_score(query_terms, compact_message, resource, chapter)
            if score <= 0:
                continue
            reason = "根据用户描述与资料目录、章节摘要和关键词的匹配度定位。"
            scored.append((score, resource, chapter, None, reason))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []

    max_score = max(scored[0][0], 0.01)
    matches: list[ResourceMatch] = []
    seen: set[tuple[str, str]] = set()
    for score, resource, chapter, segment, reason in scored:
        key = (resource.id, segment.segment_id if segment else chapter.id)
        if key in seen:
            continue
        seen.add(key)
        normalized = min(1.0, 0.34 + (score / max_score) * 0.32 + min(score, 0.34))
        matches.append(
            ResourceMatch(
                resource_id=resource.id,
                chapter_id=chapter.id,
                segment_id=segment.segment_id if segment else None,
                resource_name=resource.name,
                chapter_title=chapter.title,
                heading_path=segment.heading_path if segment else chapter.path,
                excerpt=_match_excerpt(segment.text, query_terms) if segment else "",
                before_text=_neighbor_excerpt(resource, segment.before_segment_id) if segment else "",
                after_text=_neighbor_excerpt(resource, segment.after_segment_id) if segment else "",
                text_hash=segment.text_hash if segment else None,
                reason=reason,
                score=round(normalized, 3),
                is_high_overlap=normalized >= DIRECT_REFERENCE_THRESHOLD,
            )
        )
        if len(matches) >= 8:
            break
    return matches


def _segment_score(
    query_terms: set[str],
    compact_message: str,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    segment: ResourceSegment,
) -> float:
    title_text = " ".join([resource.name, *segment.heading_path, chapter.title])
    body_text = " ".join([segment.text, " ".join(segment.keywords)])
    title_terms = _value_terms(title_text)
    body_terms = _value_terms(body_text)
    title_overlap = query_terms & title_terms
    body_overlap = query_terms & body_terms
    if not title_overlap and not body_overlap:
        return 0.0

    score = len(title_overlap) / max(len(query_terms), 1) * 0.48
    score += len(body_overlap) / max(len(query_terms), 1) * 0.92

    lower_message = compact_message.lower()
    compact_body = re.sub(r"\s+", "", body_text).lower()
    compact_path = _compact_text(" ".join(segment.heading_path), limit=240).lower()
    if compact_path and compact_path in lower_message:
        score += 0.24
    for term in query_terms:
        if len(term) >= 4 and term in compact_body:
            score += 0.18
    for keyword in segment.keywords[:8]:
        normalized = keyword.strip().lower()
        if len(normalized) >= 2 and normalized in lower_message:
            score += 0.12
    return score


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
    segment_id: str | None = None,
) -> ResourceMatch | None:
    for resource in resources:
        if resource.id != resource_id:
            continue
        if segment_id:
            chapters_by_id = {chapter.id: chapter for chapter in resource.outline}
            segment = next((candidate for candidate in resource.segments if candidate.segment_id == segment_id), None)
            if segment is not None and segment.chapter_id == chapter_id:
                chapter = chapters_by_id.get(chapter_id)
                if chapter is None:
                    return None
                return ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    segment_id=segment.segment_id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    heading_path=segment.heading_path,
                    excerpt=_compact_text(segment.text, limit=360),
                    before_text=_neighbor_excerpt(resource, segment.before_segment_id),
                    after_text=_neighbor_excerpt(resource, segment.after_segment_id),
                    text_hash=segment.text_hash,
                    reason="用户确认了要参考的资料片段。",
                    score=1.0,
                    is_high_overlap=True,
                )
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
    if match.segment_id:
        return _extract_segment_reference(resource, match)
    return extract_reference_context(
        resource,
        match.chapter_id,
        user_query=user_message,
    )


def _extract_segment_reference(resource: ResourceLibraryItem, match: ResourceMatch) -> ResourceReferenceContext | None:
    segment = next((candidate for candidate in resource.segments if candidate.segment_id == match.segment_id), None)
    if segment is None:
        return None
    chapter = next((candidate for candidate in resource.outline if candidate.id == segment.chapter_id), None)
    if chapter is None:
        return None
    before = next((candidate for candidate in resource.segments if candidate.segment_id == segment.before_segment_id), None)
    after = next((candidate for candidate in resource.segments if candidate.segment_id == segment.after_segment_id), None)
    title = " / ".join(segment.heading_path) or chapter.title
    chunks: list[ResourceContextChunk] = []
    if before is not None:
        chunks.append(
            ResourceContextChunk(
                title=f"{title} / 前文",
                excerpt=_compact_text(before.text, limit=420),
                teaching_hint="作为目标片段的前置上下文参考。",
                segment_id=before.segment_id,
                heading_path=before.heading_path,
                text_hash=before.text_hash,
            )
        )
    chunks.append(
        ResourceContextChunk(
            title=f"{title} / 目标片段",
            excerpt=_compact_text(segment.text, limit=620),
            teaching_hint="优先围绕这一段提取概念、关系、步骤和可讲解例子。",
            segment_id=segment.segment_id,
            heading_path=segment.heading_path,
            before_text=_neighbor_excerpt(resource, segment.before_segment_id),
            after_text=_neighbor_excerpt(resource, segment.after_segment_id),
            text_hash=segment.text_hash,
        )
    )
    if after is not None:
        chunks.append(
            ResourceContextChunk(
                title=f"{title} / 后文",
                excerpt=_compact_text(after.text, limit=420),
                teaching_hint="作为目标片段的后续上下文参考。",
                segment_id=after.segment_id,
                heading_path=after.heading_path,
                text_hash=after.text_hash,
            )
        )
    full_text = "\n\n".join(chunk.excerpt for chunk in chunks if chunk.excerpt)
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=chapter.id,
        segment_id=segment.segment_id,
        resource_name=resource.name,
        chapter_title=chapter.title,
        summary=f"《{resource.name}》中“{title}”附近的资料片段可以作为本次参考。",
        teaching_points=segment.keywords[:6],
        chunks=chunks,
        full_text=full_text or segment.text,
    )


def _reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    target_label = "资料片段" if match.segment_id else "资料章节"
    confirm_label = "参考这一片段" if match.segment_id else "参考这一章节"
    heading = " / ".join(match.heading_path) if match.heading_path else match.chapter_title
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        segment_id=match.segment_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=f"我找到了可能相关的{target_label}：{match.resource_name} / {heading}。要先参考它再继续吗？",
        reason=match.reason,
        confirm_label=confirm_label,
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


def _match_excerpt(text: str, query_terms: set[str], *, limit: int = 360) -> str:
    compact = _compact_text(text, limit=1600)
    lowered = compact.lower()
    ordered_terms = sorted((term for term in query_terms if len(term) >= 2), key=len, reverse=True)
    for term in ordered_terms:
        index = lowered.find(term.lower())
        if index < 0:
            continue
        start = max(0, index - 90)
        end = min(len(compact), index + len(term) + limit - 120)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(compact) else ""
        return f"{prefix}{compact[start:end].strip()}{suffix}"
    return _compact_text(compact, limit=limit)


def _neighbor_excerpt(resource: ResourceLibraryItem, segment_id: str | None) -> str:
    if not segment_id:
        return ""
    segment = next((candidate for candidate in resource.segments if candidate.segment_id == segment_id), None)
    if segment is None:
        return ""
    return _compact_text(segment.text, limit=240)
