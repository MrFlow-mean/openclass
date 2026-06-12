from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.models import (
    LibraryChapter,
    ResourceBodyBlock,
    ResourceContextChunk,
    ResourceEvidenceBundle,
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
    # 资料解析结果：可能直接选中一个章节，也可能只给出“是否参考此章节”的确认提示。
    matches: list[ResourceMatch]
    selected_reference: ResourceReferenceContext | None = None
    evidence_bundle: ResourceEvidenceBundle | None = None
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
    # ResourceResolver 只负责选资料证据，不把所有上传资料默认塞进 Chatbot prompt。
    if reference_action == "skip":
        return ResourceResolution(matches=[], status="skipped")

    if reference_action == "confirm" and reference_resource_id and reference_chapter_id:
        match = _explicit_match(resources, reference_resource_id, reference_chapter_id)
        if match is None:
            return ResourceResolution(matches=[], status="missing")
        reference = _extract_reference(resources, match, user_message)
        evidence_bundle = _extract_evidence_bundle(resources, match, user_message)
        if reference is None or evidence_bundle is None:
            return ResourceResolution(matches=[match], status="missing")
        return ResourceResolution(
            matches=[match],
            selected_reference=reference,
            evidence_bundle=evidence_bundle,
            status="selected",
        )

    matches = _rank_resource_matches(resources, user_message)
    if not matches:
        return ResourceResolution(matches=[], status="none")

    best = matches[0]
    if allow_direct_reference and best.score >= DIRECT_REFERENCE_THRESHOLD:
        # 高置信度且本轮允许直接引用时，才自动抽取资料片段交给后续 AI。
        reference = _extract_reference(resources, best, user_message)
        evidence_bundle = _extract_evidence_bundle(resources, best, user_message)
        if reference is not None and evidence_bundle is not None:
            return ResourceResolution(
                matches=matches[:5],
                selected_reference=reference,
                evidence_bundle=evidence_bundle,
                status="resolved",
            )

    if best.score >= REFERENCE_PROMPT_THRESHOLD:
        # 中等置信度时先问学生要不要参考，避免资料上下文污染无关问题。
        return ResourceResolution(
            matches=matches[:5],
            evidence_bundle=_extract_evidence_bundle(resources, best, user_message),
            reference_prompt=_reference_prompt(best),
            status="prompt",
        )

    return ResourceResolution(matches=matches[:5], status="low_confidence")


def _rank_resource_matches(resources: list[ResourceLibraryItem], user_message: str) -> list[ResourceMatch]:
    # 精确的章节 / 正文页目标先走目录和正文逻辑路径，避免把目录页码当全文页码。
    exact_matches = _exact_body_path_matches(resources, user_message)
    if exact_matches:
        return exact_matches

    # 相关内容检索以章节 shard 为单位并行打分；每个 shard 只引用正文 block。
    query_terms = _query_terms(user_message)
    if not query_terms:
        return []

    compact_message = _compact_text(user_message, limit=500)
    shard_inputs = [
        (resource, chapter)
        for resource in resources
        for chapter in _chapters_for_parallel_search(resource)
    ]
    if not shard_inputs:
        return []

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(shard_inputs)))) as executor:
        scored = list(
            executor.map(
                lambda item: _score_parallel_chapter(query_terms, compact_message, item[0], item[1]),
                shard_inputs,
            )
        )
    scored = [item for item in scored if item[0] > 0]

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


def _chapters_for_parallel_search(resource: ResourceLibraryItem) -> list[LibraryChapter]:
    if not resource.chapter_shards:
        return resource.outline
    by_id = {chapter.id: chapter for chapter in resource.outline}
    chapters: list[LibraryChapter] = []
    for shard in resource.chapter_shards:
        chapter = by_id.get(shard.chapter_id)
        if chapter is not None:
            chapters.append(chapter)
    return chapters or resource.outline


def _score_parallel_chapter(
    query_terms: set[str],
    compact_message: str,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
) -> tuple[float, ResourceLibraryItem, LibraryChapter, str]:
    shard = next((candidate for candidate in resource.chapter_shards if candidate.chapter_id == chapter.id), None)
    body_text = " ".join(
        block.text
        for block in resource.body_blocks
        if block.chapter_id == chapter.id and block.text.strip()
    )
    shard_text = ""
    if shard is not None:
        shard_text = " ".join(
            [
                shard.summary,
                " ".join(shard.keywords),
                " ".join(shard.heading_path),
            ]
        )
    score = _chapter_score(
        query_terms,
        compact_message,
        resource,
        chapter.model_copy(
            update={
                "summary": " ".join(part for part in [chapter.summary, shard_text, body_text[:1600]] if part),
                "keywords": list({*chapter.keywords, *(_value_terms(body_text[:1200]) if body_text else set())})[:16],
            }
        ),
    )
    reason = "按章节 shard 并行检索正文块后命中相关内容。"
    return score, resource, chapter, reason


def _exact_body_path_matches(resources: list[ResourceLibraryItem], user_message: str) -> list[ResourceMatch]:
    target = _parse_explicit_body_target(user_message)
    if target is None:
        return []
    matches: list[ResourceMatch] = []
    for resource in resources:
        if target.get("body_page_no") is not None:
            body_page_no = int(target["body_page_no"])
            block = next((candidate for candidate in resource.body_blocks if candidate.body_page_no == body_page_no), None)
            if block is None or block.chapter_id is None:
                continue
            chapter = next((candidate for candidate in resource.outline if candidate.id == block.chapter_id), None)
            if chapter is None:
                continue
            matches.append(
                ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    reason=f"用户指定正文第 {body_page_no} 页，已通过正文逻辑页码定位到正文块。",
                    score=1.0,
                    is_high_overlap=True,
                )
            )
            continue
        chapter_no = target.get("chapter_no")
        section_no = target.get("section_no")
        for entry in resource.toc_entries:
            entry_ref = _parse_explicit_body_target(" ".join([*entry.heading_path, entry.title]))
            if entry_ref is None:
                continue
            if chapter_no is not None and entry_ref.get("chapter_no") != chapter_no:
                continue
            if section_no is not None and entry_ref.get("section_no") != section_no:
                continue
            if entry.chapter_id is None:
                continue
            if not any(block.chapter_id == entry.chapter_id and block.text.strip() for block in resource.body_blocks):
                continue
            matches.append(
                ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=entry.chapter_id,
                    resource_name=resource.name,
                    chapter_title=entry.title,
                    reason="用户指定章节目标，已通过目录索引映射到正文逻辑路径。",
                    score=1.0,
                    is_high_overlap=True,
                )
            )
    return matches[:8]


def _parse_explicit_body_target(text: str) -> dict[str, int] | None:
    compact = _compact_text(text, limit=300)
    body_page = re.search(r"正文\s*第\s*([0-9一二两三四五六七八九十百]+)\s*页", compact)
    if body_page:
        return {"body_page_no": _parse_number(body_page.group(1))}
    dotted = re.search(r"\b([0-9]{1,3})\s*[.．]\s*([0-9]{1,3})\b", compact)
    chapter = re.search(r"第\s*([0-9一二两三四五六七八九十百]+)\s*章", compact)
    section = re.search(r"第\s*([0-9一二两三四五六七八九十百]+)\s*(?:节|小节)", compact)
    if dotted:
        return {"chapter_no": int(dotted.group(1)), "section_no": int(dotted.group(2))}
    if chapter:
        result = {"chapter_no": _parse_number(chapter.group(1))}
        if section:
            result["section_no"] = _parse_number(section.group(1))
        return result
    return None


def _parse_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "百" in value:
        left, _, right = value.partition("百")
        return (digits.get(left, 1) * 100) + (_parse_number(right) if right else 0)
    if "十" in value:
        left, _, right = value.partition("十")
        return (digits.get(left, 1) * 10) + (digits.get(right, 0) if right else 0)
    return digits.get(value, 0)


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
    body_reference = _reference_from_body_blocks(resource, match, user_message)
    if body_reference is not None:
        return body_reference
    return extract_reference_context(
        resource,
        match.chapter_id,
        user_query=user_message,
    )


def _reference_from_body_blocks(
    resource: ResourceLibraryItem,
    match: ResourceMatch,
    user_message: str,
) -> ResourceReferenceContext | None:
    blocks = [block for block in resource.body_blocks if block.chapter_id == match.chapter_id and block.text.strip()]
    if not blocks:
        return None
    compact_text = "\n\n".join(block.text for block in blocks[:4])
    if not compact_text.strip():
        return None
    chapter_title = match.chapter_title
    body_pages = _page_range(_body_page_values(blocks))
    physical_pages = _page_range([block.physical_page_no for block in blocks])
    source_locations = _source_location_range(blocks)
    summary_parts = [f"《{resource.name}》的《{chapter_title}》已通过正文逻辑路径定位。"]
    if body_pages:
        summary_parts.append(f"正文页码：{body_pages}。")
    if physical_pages:
        summary_parts.append(f"全文物理页：{physical_pages}。")
    if source_locations and not physical_pages:
        summary_parts.append(f"原始位置：{source_locations}。")
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=match.chapter_id,
        resource_name=resource.name,
        chapter_title=chapter_title,
        summary="".join(summary_parts),
        teaching_points=[
            "只依据正文逻辑块讲解，不引用封面、前言、目录或附录内容。",
            "先说明本节在目录结构中的位置，再展开正文证据。",
            "保留页码映射，方便回到原文核对。",
        ],
        chunks=[
            ResourceContextChunk(
                title=f"{chapter_title} / 正文证据 {index}",
                excerpt=block.text[:420],
                teaching_hint=f"结合用户问题“{_compact_text(user_message, limit=80)}”解释这一段正文。",
            )
            for index, block in enumerate(blocks[:4], start=1)
        ],
        full_text=compact_text,
    )


def _extract_evidence_bundle(
    resources: list[ResourceLibraryItem],
    match: ResourceMatch,
    user_message: str,
) -> ResourceEvidenceBundle | None:
    resource = next((candidate for candidate in resources if candidate.id == match.resource_id), None)
    if resource is None:
        return None
    blocks = [block for block in resource.body_blocks if block.chapter_id == match.chapter_id]
    if not blocks:
        return None
    chapter_title = match.chapter_title
    body_pages = _body_page_values(blocks)
    physical_pages = [block.physical_page_no for block in blocks if block.physical_page_no is not None]
    body_page_range = _page_range(body_pages)
    if body_page_range is None:
        return None
    return ResourceEvidenceBundle(
        resource_id=resource.id,
        resource_name=resource.name,
        query=user_message,
        target_id=match.chapter_id,
        target_title=chapter_title,
        body_page_range=body_page_range,
        physical_page_range=_page_range(physical_pages),
        source_location_range=_source_location_range(blocks),
        score=match.score,
        evidence=[match.reason, "证据只来自 page_role=body 的正文逻辑块。"],
        chunks=[
            ResourceContextChunk(
                title=f"{chapter_title} / 正文证据 {index}",
                excerpt=block.text[:420],
                teaching_hint=f"围绕“{chapter_title}”的正文证据展开，不引用目录或前言内容。",
            )
            for index, block in enumerate(blocks[:3], start=1)
        ],
    )


def _page_range(values: list[int | None]) -> str | None:
    pages = sorted({value for value in values if value is not None})
    if not pages:
        return None
    if len(pages) == 1:
        return str(pages[0])
    return f"{pages[0]}-{pages[-1]}"


def _body_page_values(blocks: list[ResourceBodyBlock]) -> list[int | None]:
    values: list[int | None] = []
    for block in blocks:
        if block.body_page_no is not None:
            values.append(block.body_page_no)
        elif block.body_page_idx is not None:
            values.append(block.body_page_idx + 1)
        elif block.block_order is not None:
            values.append(block.block_order + 1)
    return values


def _source_location_range(blocks: list[ResourceBodyBlock]) -> str | None:
    locations = [block.source_location_range for block in blocks if block.source_location_range]
    if not locations:
        return None
    first = locations[0]
    last = locations[-1]
    return first if first == last else f"{first} - {last}"


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
