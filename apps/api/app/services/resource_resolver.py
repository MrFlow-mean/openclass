from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    LibraryChapter,
    ResourceContextChunk,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceMatchEvidence,
    ResourceReferenceAction,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ResourceSegment,
)
from app.services.resource_embedding import cosine_similarity, resource_embedding_service
from app.services.resource_library import extract_reference_context
from app.services.resource_page_navigator import (
    extract_navigated_reference_context,
    find_navigated_matches,
)


DIRECT_REFERENCE_THRESHOLD = 0.68
REFERENCE_PROMPT_THRESHOLD = 0.42
SEMANTIC_MATCH_THRESHOLD = 0.3

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
class RankedResourceCandidate:
    score: float
    resource: ResourceLibraryItem
    chapter: LibraryChapter
    segment: ResourceSegment | None
    reason: str
    evidence: list[ResourceMatchEvidence]
    score_breakdown: dict[str, float]


@dataclass(frozen=True)
class OutlineReference:
    primary: int
    secondary: int | None = None


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
        if reference is None:
            reference = _outline_only_reference(resources, match)
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
        reference = _extract_reference(resources, best, user_message)
        return ResourceResolution(
            matches=matches[:5],
            reference_prompt=_reference_prompt(best, text_evidence_available=reference is not None),
            status="prompt",
        )

    return ResourceResolution(matches=matches[:5], status="low_confidence")


def _rank_resource_matches(resources: list[ResourceLibraryItem], user_message: str) -> list[ResourceMatch]:
    navigated_matches = find_navigated_matches(resources, user_message)
    query_terms = _query_terms(user_message)
    compact_message = _compact_text(user_message, limit=500)
    embedded_segments = [
        segment
        for resource in resources
        for segment in resource.segments
        if segment.embedding
    ]
    query_vector = resource_embedding_service.embed_query(compact_message) if embedded_segments else []
    if not query_terms and not query_vector:
        return navigated_matches

    candidates: list[RankedResourceCandidate] = []
    for resource in resources:
        chapters_by_id = {chapter.id: chapter for chapter in resource.outline}
        for segment in resource.segments:
            chapter = chapters_by_id.get(segment.chapter_id)
            if chapter is None:
                continue
            candidate = _rank_segment_candidate(
                query_terms,
                compact_message,
                query_vector,
                resource,
                chapter,
                segment,
            )
            if candidate is None:
                continue
            candidates.append(candidate)
        for chapter in resource.outline:
            candidate = _rank_chapter_candidate(query_terms, compact_message, resource, chapter)
            if candidate is None:
                continue
            candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)
    if not candidates:
        return navigated_matches

    max_score = max(candidates[0].score, 0.01)
    matches: list[ResourceMatch] = []
    seen: set[tuple[str, str]] = set()
    for match in navigated_matches:
        key = (match.resource_id, match.segment_id or match.chapter_id)
        if key in seen:
            continue
        seen.add(key)
        matches.append(match)
    for candidate in candidates:
        score = candidate.score
        resource = candidate.resource
        chapter = candidate.chapter
        segment = candidate.segment
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
                page_range=segment.page_range if segment else chapter.page_range,
                text_source=segment.text_source if segment else "metadata_only",
                reason=candidate.reason,
                evidence=candidate.evidence,
                score_breakdown=candidate.score_breakdown,
                score=round(normalized, 3),
                is_high_overlap=normalized >= DIRECT_REFERENCE_THRESHOLD,
            )
        )
        if len(matches) >= 8:
            break
    return matches


def _rank_segment_candidate(
    query_terms: set[str],
    compact_message: str,
    query_vector: list[float],
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    segment: ResourceSegment,
) -> RankedResourceCandidate | None:
    lexical_score = _segment_score(query_terms, compact_message, resource, chapter, segment)
    semantic_similarity = _semantic_segment_similarity(query_vector, segment)
    semantic_score = semantic_similarity * 1.18 if semantic_similarity >= SEMANTIC_MATCH_THRESHOLD else 0.0
    if lexical_score <= 0 and semantic_score <= 0:
        return None

    heading_score = _overlap_ratio(query_terms, _value_terms(" ".join([resource.name, *segment.heading_path, chapter.title])))
    body_score = _overlap_ratio(query_terms, _value_terms(segment.text))
    keyword_score = _overlap_ratio(query_terms, set(segment.keywords))
    evidence_bonus = 0.04 if segment.heading_path else 0.0
    evidence_bonus += 0.04 if segment.text.strip() else 0.0
    score = max(lexical_score, semantic_score)
    score += min(lexical_score, semantic_score) * 0.18
    score += heading_score * 0.1
    score += body_score * 0.12
    score += keyword_score * 0.06
    score += evidence_bonus

    evidence = _segment_evidence(
        segment=segment,
        query_terms=query_terms,
        semantic_similarity=semantic_similarity,
    )
    if semantic_score > lexical_score:
        reason = "根据用户描述与资料正文片段的语义向量相似度定位，并结合标题路径和正文证据重排。"
    else:
        reason = "根据用户描述与资料正文片段、标题路径、关键词和引用证据的综合重排定位。"
    return RankedResourceCandidate(
        score=score,
        resource=resource,
        chapter=chapter,
        segment=segment,
        reason=reason,
        evidence=evidence,
        score_breakdown=_score_breakdown(
            {
                "lexical": lexical_score,
                "semantic": semantic_similarity,
                "heading": heading_score,
                "body": body_score,
                "keyword": keyword_score,
                "rerank": score,
            }
        ),
    )


def _rank_chapter_candidate(
    query_terms: set[str],
    compact_message: str,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
) -> RankedResourceCandidate | None:
    score = _chapter_score(query_terms, compact_message, resource, chapter)
    if score <= 0:
        return None
    heading_score = _overlap_ratio(query_terms, _value_terms(" ".join([resource.name, *chapter.path, chapter.title])))
    summary_score = _overlap_ratio(query_terms, _value_terms(chapter.summary))
    rerank_score = score + heading_score * 0.1 + summary_score * 0.08
    evidence = _chapter_evidence(chapter)
    return RankedResourceCandidate(
        score=rerank_score,
        resource=resource,
        chapter=chapter,
        segment=None,
        reason="根据用户描述与资料目录、章节摘要、关键词和引用证据的综合重排定位。",
        evidence=evidence,
        score_breakdown=_score_breakdown(
            {
                "lexical": score,
                "heading": heading_score,
                "summary": summary_score,
                "rerank": rerank_score,
            }
        ),
    )


def _semantic_segment_similarity(query_vector: list[float], segment: ResourceSegment) -> float:
    similarity = cosine_similarity(query_vector, segment.embedding)
    return max(0.0, similarity)


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
    score += _outline_reference_match_score(
        _outline_references(compact_message),
        _outline_references(title_text),
        partial_bonus=0.42,
        exact_bonus=0.72,
    )
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
    score += _outline_reference_match_score(
        _outline_references(compact_message),
        _outline_references(title_text),
        partial_bonus=0.72,
        exact_bonus=1.08,
    )
    for keyword in chapter.keywords[:8]:
        normalized = keyword.strip().lower()
        if len(normalized) >= 2 and normalized in lower_message:
            score += 0.12
    return score


def _outline_reference_match_score(
    query_refs: set[OutlineReference],
    target_refs: set[OutlineReference],
    *,
    partial_bonus: float,
    exact_bonus: float,
) -> float:
    if not query_refs or not target_refs:
        return 0.0
    score = 0.0
    for query_ref in query_refs:
        for target_ref in target_refs:
            if query_ref == target_ref:
                score = max(score, exact_bonus)
            elif query_ref.primary == target_ref.primary:
                score = max(score, partial_bonus)
    return score


def _outline_references(text: str) -> set[OutlineReference]:
    compact = _compact_text(text, limit=800)
    refs: set[OutlineReference] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*[.．]\s*(\d{1,3})(?!\d)", compact):
        refs.add(OutlineReference(primary=int(match.group(1)), secondary=int(match.group(2))))
    for match in re.finditer(r"第\s*([0-9一二三四五六七八九十百〇零两]{1,8})\s*[章节部分卷册]", compact):
        parsed = _parse_outline_number(match.group(1))
        if parsed is not None:
            refs.add(OutlineReference(primary=parsed))
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*(?:章|节|部分|chapter|section)\b", compact, flags=re.IGNORECASE):
        refs.add(OutlineReference(primary=int(match.group(1))))
    for match in re.finditer(r"\b(?:chapter|section)\s*(\d{1,3})(?!\d)", compact, flags=re.IGNORECASE):
        refs.add(OutlineReference(primary=int(match.group(1))))
    return refs


def _parse_outline_number(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if cleaned in digits:
        return digits[cleaned]
    if "百" in cleaned:
        left, _, right = cleaned.partition("百")
        hundreds = digits.get(left, 1 if left == "" else 0)
        tail = _parse_outline_number(right) if right else 0
        return hundreds * 100 + (tail or 0)
    if "十" in cleaned:
        left, _, right = cleaned.partition("十")
        tens = digits.get(left, 1 if left == "" else 0)
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    if all(char in digits for char in cleaned):
        value_int = 0
        for char in cleaned:
            value_int = value_int * 10 + digits[char]
        return value_int
    return None


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
                    page_range=segment.page_range,
                    text_source=segment.text_source,
                    reason="用户确认了要参考的资料片段。",
                    evidence=_segment_evidence(segment=segment, query_terms=set(), semantic_similarity=0.0),
                    score_breakdown={"rerank": 1.0},
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
                    heading_path=chapter.path,
                    page_range=chapter.page_range,
                    text_source="metadata_only",
                    reason="用户确认了要参考的资料章节。",
                    evidence=_chapter_evidence(chapter),
                    score_breakdown={"rerank": 1.0},
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
    navigated_reference = extract_navigated_reference_context(resource, match, user_message)
    if navigated_reference is not None:
        return navigated_reference
    if match.segment_id:
        return _extract_segment_reference(resource, match)
    return extract_reference_context(
        resource,
        match.chapter_id,
        user_query=user_message,
    )


def _outline_only_reference(
    resources: list[ResourceLibraryItem],
    match: ResourceMatch,
) -> ResourceReferenceContext | None:
    resource = next((candidate for candidate in resources if candidate.id == match.resource_id), None)
    if resource is None:
        return None
    chapter = next((candidate for candidate in resource.outline if candidate.id == match.chapter_id), None)
    if chapter is None:
        return None
    heading = " / ".join(match.heading_path or chapter.path or [chapter.title])
    excerpt = chapter.summary or f"只定位到资料目录项“{heading}”，当前没有抽到这一章的可引用正文。"
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=chapter.id,
        segment_id=match.segment_id,
        resource_name=resource.name,
        chapter_title=chapter.title,
        summary=(
            f"已定位到《{resource.name}》的目录项“{heading}”，"
            "但没有抽到可引用正文；继续生成时只能把目录定位作为线索。"
        ),
        teaching_points=["先说明当前只命中资料结构，不能声称已引用原文正文。"],
        chunks=[
            ResourceContextChunk(
                title=f"{chapter.title} / 目录定位",
                excerpt=excerpt,
                teaching_hint="只能作为目录级定位线索，不作为原文证据。",
                heading_path=match.heading_path or chapter.path,
                page_range=match.page_range or chapter.page_range,
                text_source="metadata_only",
            )
        ],
        text_evidence_available=False,
        text_evidence_status="metadata_only",
        full_text="",
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
                page_range=before.page_range,
                text_source=before.text_source,
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
            page_range=segment.page_range,
            text_source=segment.text_source,
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
                page_range=after.page_range,
                text_source=after.text_source,
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
        text_evidence_available=True,
        text_evidence_status=segment.text_source,
        full_text=full_text or segment.text,
    )


def _reference_prompt(match: ResourceMatch, *, text_evidence_available: bool = True) -> ResourceReferencePrompt:
    target_label = "资料片段" if match.segment_id else "资料章节"
    confirm_label = "参考这一片段" if match.segment_id else "参考这一章节"
    heading = " / ".join(match.heading_path) if match.heading_path else match.chapter_title
    if not text_evidence_available:
        return ResourceReferencePrompt(
            resource_id=match.resource_id,
            chapter_id=match.chapter_id,
            segment_id=match.segment_id,
            resource_name=match.resource_name,
            chapter_title=match.chapter_title,
            question=(
                f"我只定位到可能相关的{target_label}：{match.resource_name} / {heading}，"
                "但还没有抽到这部分的正文。要在缺少原文正文证据的情况下继续吗？"
            ),
            reason="只命中资料目录或章节结构，当前没有可引用的正文片段。",
            confirm_label="确认继续",
            skip_label="先不生成",
            score=match.score,
            text_evidence_available=False,
            requires_text_fallback_confirmation=True,
        )
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
        text_evidence_available=True,
        requires_text_fallback_confirmation=False,
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


def _overlap_ratio(query_terms: set[str], value_terms: set[str]) -> float:
    if not query_terms or not value_terms:
        return 0.0
    return len(query_terms & value_terms) / max(len(query_terms), 1)


def _segment_evidence(
    *,
    segment: ResourceSegment,
    query_terms: set[str],
    semantic_similarity: float,
) -> list[ResourceMatchEvidence]:
    evidence: list[ResourceMatchEvidence] = []
    heading = " / ".join(segment.heading_path).strip()
    if heading:
        evidence.append(ResourceMatchEvidence(label="标题路径", value=heading))
    excerpt = _match_excerpt(segment.text, query_terms, limit=420)
    if excerpt:
        evidence.append(ResourceMatchEvidence(label="正文片段", value=excerpt))
    if segment.page_range:
        evidence.append(ResourceMatchEvidence(label="页码", value=segment.page_range))
    if segment.text_source:
        evidence.append(ResourceMatchEvidence(label="来源", value=segment.text_source))
    matched_keywords = [keyword for keyword in segment.keywords[:8] if keyword in query_terms]
    if matched_keywords:
        evidence.append(ResourceMatchEvidence(label="命中关键词", value="、".join(matched_keywords[:5])))
    if semantic_similarity >= SEMANTIC_MATCH_THRESHOLD:
        evidence.append(ResourceMatchEvidence(label="语义相似度", value=f"{semantic_similarity:.2f}"))
    return evidence[:5]


def _chapter_evidence(chapter: LibraryChapter) -> list[ResourceMatchEvidence]:
    evidence: list[ResourceMatchEvidence] = []
    heading = " / ".join(chapter.path).strip() or chapter.title
    if heading:
        evidence.append(ResourceMatchEvidence(label="章节路径", value=heading))
    if chapter.summary:
        evidence.append(ResourceMatchEvidence(label="章节摘要", value=_compact_text(chapter.summary, limit=260)))
    if chapter.page_range:
        evidence.append(ResourceMatchEvidence(label="页码", value=chapter.page_range))
    if chapter.keywords:
        evidence.append(ResourceMatchEvidence(label="章节关键词", value="、".join(chapter.keywords[:5])))
    return evidence[:4]


def _score_breakdown(raw: dict[str, float]) -> dict[str, float]:
    return {
        key: round(value, 3)
        for key, value in raw.items()
        if value > 0
    }


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
