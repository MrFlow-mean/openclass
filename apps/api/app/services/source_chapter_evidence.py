from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Collection

from app.models import RetrievalEvidence, SelectionRef, SourceChapter, SourceIngestionRecord
from app.services.image_ocr import extract_pdf_pages_text
from app.services.source_chapter_identity import rebind_stale_source_chapter_selection
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_store import SourceStructureStore


SOURCE_TITLE_OVERLAP_MIN = 4


def resolve_verified_chapter_evidence(
    *,
    source_store: SourceEvidenceStore,
    structure_store: SourceStructureStore,
    owner_user_id: str,
    package_id: str,
    query: str,
    limit: int,
    token_budget: int,
    page_limit: int,
    source_ingestion_ids: Collection[str] | None = None,
    source_reference: SelectionRef | None = None,
) -> tuple[list[RetrievalEvidence], dict[str, object] | None]:
    match = _match_verified_chapter(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id=owner_user_id,
        package_id=package_id,
        query=query,
        source_ingestion_ids=source_ingestion_ids,
        source_reference=source_reference,
    )
    if match is None:
        return [], None
    if isinstance(match, dict):
        return [], match
    source, chapter, resolution = match
    requested_chapter_number = str(resolution.get("requested_chapter_number") or "")
    targets = _source_scope_targets(structure_store=structure_store, source=source, chapter=chapter)
    evidence = _indexed_scope_evidence(
        structure_store=structure_store,
        owner_user_id=owner_user_id,
        package_id=package_id,
        targets=targets,
        limit=limit,
        token_budget=token_budget,
    )
    if evidence:
        resolution["body_retrieval"] = "indexed_chunks"
        _attach_scope_metadata(
            evidence,
            scope_chapter=chapter,
            covers_multiple_sections=len(targets) > 1,
            requested_chapter_number=requested_chapter_number,
        )
        _attach_scope_resolution(resolution, chapter=chapter, targets=targets)
        return evidence, resolution
    ocr_evidence = _ocr_chapter_evidence(
        source=source,
        chapter=chapter,
        targets=targets,
        token_budget=token_budget,
        page_limit=page_limit,
    )
    if ocr_evidence:
        resolution["body_retrieval"] = "macos_vision_ocr"
        resolution["ocr_partial"] = any(bool(item.metadata.get("ocr_partial")) for item in ocr_evidence)
        _attach_requested_chapter_number(ocr_evidence, requested_chapter_number)
        _attach_scope_resolution(resolution, chapter=chapter, targets=targets)
        return ocr_evidence, resolution
    resolution["status"] = "content_unavailable"
    resolution["body_retrieval"] = "unavailable"
    return [], resolution


def explicit_chapter_number(query: str) -> str:
    patterns = (
        r"第\s*([零〇一二两三四五六七八九十百千万\d]+)\s*[章节]",
        r"\b(?:chapter|chap\.?)[\s_-]*([0-9]+(?:\.[0-9]+){0,8})\b",
        r"(?<!\d)(\d{1,4})\s*[章节]",
        r"(?<!\d)(\d+(?:\.\d+){1,8})(?![\d.])",
    )
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.I)
        if not match:
            continue
        raw_number = match.group(1)
        if raw_number.isdigit() or "." in raw_number:
            return _normalize_chapter_number(raw_number)
        chinese_number = _chinese_integer(raw_number)
        if chinese_number is not None:
            return str(chinese_number)
    return ""


def explicit_source_chapter_id(query: str) -> str:
    match = re.search(r"\bsource_chapter_id\s*=\s*([A-Za-z0-9_-]{8,})\b", query)
    return match.group(1) if match else ""


def _match_verified_chapter(
    *,
    source_store: SourceEvidenceStore,
    structure_store: SourceStructureStore,
    owner_user_id: str,
    package_id: str,
    query: str,
    source_ingestion_ids: Collection[str] | None = None,
    source_reference: SelectionRef | None = None,
) -> tuple[SourceIngestionRecord, SourceChapter, dict[str, object]] | dict[str, object] | None:
    ready_sources = source_store.ready_sources(owner_user_id=owner_user_id, package_id=package_id)
    requested_source_ids = {source_id for source_id in source_ingestion_ids or [] if source_id}
    selected_source_id = (
        source_reference.source_ingestion_id
        if source_reference is not None and source_reference.kind == "source"
        else None
    )
    if selected_source_id:
        requested_source_ids = {selected_source_id}
    if requested_source_ids:
        ready_sources = [source for source in ready_sources if source.id in requested_source_ids]
    explicit_id = explicit_source_chapter_id(query)
    requested_number = explicit_chapter_number(query)
    candidates: list[tuple[SourceIngestionRecord, SourceChapter, int]] = []
    ordinal_candidates: list[tuple[SourceIngestionRecord, SourceChapter, int]] = []
    for source in ready_sources:
        view = structure_store.get_structure_view(source=source, chunk_limit=0)
        if view.structure is None or view.structure.status != "ready":
            continue
        title_overlap = _longest_common_substring_length(query, source.title)
        if explicit_id:
            direct_match = next(
                (
                    chapter
                    for chapter in view.chapters
                    if chapter.anchor_status == "verified" and chapter.id == explicit_id
                ),
                None,
            )
            if direct_match is not None:
                return source, direct_match, _source_resolution(
                    source=source,
                    chapter=direct_match,
                    match_mode="explicit_source_chapter_id",
                    source_title_overlap=title_overlap,
                    requested_chapter_id=explicit_id,
                )
            if source_reference is not None:
                rebound = rebind_stale_source_chapter_selection(
                    selection=source_reference,
                    source_ingestion_id=source.id,
                    chapters=view.chapters,
                )
                if rebound.chapter is not None:
                    return source, rebound.chapter, _source_resolution(
                        source=source,
                        chapter=rebound.chapter,
                        match_mode="stale_source_chapter_selection_rebound",
                        source_title_overlap=title_overlap,
                        requested_chapter_id=explicit_id,
                        rebound_anchors=rebound.matched_anchors,
                    )
                if rebound.is_ambiguous:
                    return {
                        "status": "ambiguous",
                        "intent_signals": ["explicit_source_chapter_id", "stale_source_chapter_selection"],
                        "selected_action": "clarify_source_chapter",
                        "role_executed": "resource_resolver",
                        "document_changed": False,
                        "requested_chapter_id": explicit_id,
                        "source_ingestion_id": source.id,
                        "candidate_chapter_ids": list(rebound.candidate_ids),
                        "reason": "资料目录已重建，旧章节引用对应多个当前章节，需要重新选择。",
                    }
            continue
        for chapter in view.chapters:
            if chapter.anchor_status != "verified":
                continue
            chapter_number = _chapter_number(chapter)
            if requested_number and chapter_number == requested_number:
                candidates.append((source, chapter, title_overlap))
        ordinal_chapter = _unnumbered_top_level_chapter_at(view.chapters, requested_number)
        if ordinal_chapter is not None:
            ordinal_candidates.append((source, ordinal_chapter, title_overlap))
    if explicit_id:
        return {
            "status": "not_found",
            "intent_signals": ["explicit_source_chapter_id"],
            "selected_action": "resolve_source_chapter",
            "role_executed": "resource_resolver",
            "document_changed": False,
            "reason": "指定的资料章节已经不存在或不再是已验证节点。",
        }
    if not requested_number:
        return None
    match_mode = "unique_verified_chapter_number"
    source_scoped_ordinal_candidates = [
        ordinal_candidate
        for ordinal_candidate in ordinal_candidates
        if ordinal_candidate[2] >= SOURCE_TITLE_OVERLAP_MIN
        and not any(
            numbered_candidate[0].id == ordinal_candidate[0].id
            for numbered_candidate in candidates
        )
    ]
    if source_scoped_ordinal_candidates:
        candidates = source_scoped_ordinal_candidates
        match_mode = "unnumbered_top_level_ordinal"
    elif not candidates:
        candidates = ordinal_candidates
        match_mode = "unnumbered_top_level_ordinal"
    if not candidates:
        return None
    if len(candidates) == 1:
        source, chapter, overlap = candidates[0]
        return source, chapter, _source_resolution(
            source=source,
            chapter=chapter,
            match_mode=match_mode,
            source_title_overlap=overlap,
            requested_chapter_number=requested_number if match_mode == "unnumbered_top_level_ordinal" else "",
        )
    ranked = sorted(candidates, key=lambda item: item[2], reverse=True)
    top_overlap = ranked[0][2]
    top_candidates = [candidate for candidate in ranked if candidate[2] == top_overlap]
    if top_overlap < SOURCE_TITLE_OVERLAP_MIN or len(top_candidates) != 1:
        return {
            "status": "ambiguous",
            "intent_signals": ["explicit_chapter_locator"],
            "selected_action": "clarify_source_chapter",
            "role_executed": "resource_resolver",
            "document_changed": False,
            "chapter_number": requested_number,
            "candidates": [
                {
                    "source_ingestion_id": source.id,
                    "source_title": source.title,
                    "chapter_id": chapter.id,
                    "chapter_title": chapter.title,
                }
                for source, chapter, _overlap in ranked
            ],
            "reason": "当前课程包中有多份资料包含相同章节号，需要先确认具体资料。",
        }
    source, chapter, overlap = top_candidates[0]
    return source, chapter, _source_resolution(
        source=source,
        chapter=chapter,
        match_mode=(
            "source_title_and_unnumbered_top_level_ordinal"
            if match_mode == "unnumbered_top_level_ordinal"
            else "source_title_and_chapter_number"
        ),
        source_title_overlap=overlap,
        requested_chapter_number=requested_number if match_mode == "unnumbered_top_level_ordinal" else "",
    )


def _unnumbered_top_level_chapter_at(
    chapters: list[SourceChapter],
    requested_number: str,
) -> SourceChapter | None:
    if not requested_number.isdigit() or "." in requested_number:
        return None
    ordinal = int(requested_number)
    if ordinal < 1:
        return None
    top_level = [
        chapter
        for chapter in chapters
        if chapter.anchor_status == "verified" and chapter.parent_id is None
    ]
    if not top_level or any(_chapter_number(chapter) for chapter in top_level):
        return None
    return top_level[ordinal - 1] if ordinal <= len(top_level) else None


def _chapter_number(chapter: SourceChapter) -> str:
    return _normalize_chapter_number(
        chapter.normalized_number or chapter.number or explicit_chapter_number(chapter.title)
    )


def _ocr_chapter_evidence(
    *,
    source: SourceIngestionRecord,
    chapter: SourceChapter,
    targets: list[SourceChapter],
    token_budget: int,
    page_limit: int,
) -> list[RetrievalEvidence]:
    raw_path = source.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return []
    path = Path(raw_path).expanduser()
    if path.suffix.lower() != ".pdf" or not path.exists() or chapter.page_start is None:
        return []
    targets = targets or [chapter]
    page_budget = max(1, page_limit // len(targets))
    token_share = max(1, token_budget // len(targets))
    evidence: list[RetrievalEvidence] = []
    for target in targets:
        if target.page_start is None:
            continue
        page_start = max(1, target.page_start)
        chapter_page_end = max(page_start, (target.page_end or page_start + 1) - 1)
        pages_to_read = min(page_budget, chapter_page_end - page_start + 1)
        ocr_page_end = page_start + pages_to_read - 1
        text = extract_pdf_pages_text(
            path,
            page_start=page_start,
            page_end=ocr_page_end,
            max_pages=pages_to_read,
        )
        if not text:
            continue
        expanded_text = _trim_to_token_budget(text, max_tokens=token_share)
        token_count = _estimate_tokens(expanded_text)
        page_range = f"p. {page_start}" if ocr_page_end == page_start else f"p. {page_start}-{ocr_page_end}"
        evidence.append(
            RetrievalEvidence(
                source_ingestion_id=source.id,
                open_notebook_source_id=source.open_notebook_source_id,
                source_title=source.title,
                source_uri=source.source_uri,
                chapter_id=target.id,
                section_path=target.path or [target.title],
                page_range=page_range,
                chunk_ids=[],
                excerpt=_compact(expanded_text, 360),
                expanded_text=expanded_text,
                relevance_score=target.confidence,
                reason="命中已验证目录节点；正文文本层为空，读取对应扫描页 OCR 摘录。",
                token_count=token_count,
                metadata={
                    "retrieval_mode": "verified_chapter_ocr",
                    "ocr_provider": "macos_vision",
                    "ocr_page_start": page_start,
                    "ocr_page_end": ocr_page_end,
                    "chapter_page_end_exclusive": target.page_end,
                    "ocr_partial": ocr_page_end < chapter_page_end,
                    "source_locator": target.source_locator,
                    "scope_kind": "chapter" if len(targets) > 1 else "section",
                    "scope_chapter_id": chapter.id,
                    "scope_chapter_number": chapter.normalized_number or chapter.number,
                    "scope_chapter_title": chapter.title,
                },
            )
        )
    return evidence


def _source_scope_targets(
    *,
    structure_store: SourceStructureStore,
    source: SourceIngestionRecord,
    chapter: SourceChapter,
) -> list[SourceChapter]:
    view = structure_store.get_structure_view(source=source, chunk_limit=0)
    direct_children = [
        candidate
        for candidate in view.chapters
        if candidate.parent_id == chapter.id
        and candidate.anchor_status == "verified"
    ]
    return direct_children or [chapter]


def _indexed_scope_evidence(
    *,
    structure_store: SourceStructureStore,
    owner_user_id: str,
    package_id: str,
    targets: list[SourceChapter],
    limit: int,
    token_budget: int,
) -> list[RetrievalEvidence]:
    if not targets:
        return []
    per_target_limit = max(1, limit // len(targets))
    per_target_budget = max(1, token_budget // len(targets))
    evidence: list[RetrievalEvidence] = []
    for target in targets:
        target_evidence = structure_store.chapter_evidence_by_id(
            owner_user_id=owner_user_id,
            package_id=package_id,
            chapter_id=target.id,
            limit=per_target_limit,
            token_budget=per_target_budget,
        )
        evidence.extend(item for item in target_evidence if _has_substantive_evidence(item))
        if len(evidence) >= limit:
            break
    if len(targets) > 1 and {item.chapter_id for item in evidence} != {target.id for target in targets}:
        return []
    return evidence[:limit]


def _attach_scope_metadata(
    evidence: list[RetrievalEvidence],
    *,
    scope_chapter: SourceChapter,
    covers_multiple_sections: bool,
    requested_chapter_number: str = "",
) -> None:
    for item in evidence:
        item.metadata = {
            **item.metadata,
            "scope_kind": "chapter" if covers_multiple_sections else "section",
            "scope_chapter_id": scope_chapter.id,
            "scope_chapter_number": scope_chapter.normalized_number or scope_chapter.number,
            "scope_chapter_title": scope_chapter.title,
        }
    _attach_requested_chapter_number(evidence, requested_chapter_number)


def _attach_requested_chapter_number(
    evidence: list[RetrievalEvidence],
    requested_chapter_number: str,
) -> None:
    if not requested_chapter_number:
        return
    for item in evidence:
        item.metadata = {**item.metadata, "requested_chapter_number": requested_chapter_number}


def _attach_scope_resolution(
    resolution: dict[str, object],
    *,
    chapter: SourceChapter,
    targets: list[SourceChapter],
) -> None:
    if len(targets) <= 1:
        return
    resolution["scope_kind"] = "chapter"
    resolution["scope_chapter_id"] = chapter.id
    resolution["scope_chapter_title"] = chapter.title
    resolution["scope_section_count"] = len(targets)
    resolution["scope_coverage"] = "all_direct_sections"


def _has_substantive_evidence(item: RetrievalEvidence) -> bool:
    text = item.expanded_text or item.excerpt
    without_page_markers = re.sub(r"\[Page\s+\d+\]", "", text, flags=re.I)
    return bool(without_page_markers.strip())


def _source_resolution(
    *,
    source: SourceIngestionRecord,
    chapter: SourceChapter,
    match_mode: str,
    source_title_overlap: int,
    requested_chapter_number: str = "",
    requested_chapter_id: str = "",
    rebound_anchors: tuple[str, ...] = (),
) -> dict[str, object]:
    resolution: dict[str, object] = {
        "status": "matched",
        "intent_signals": ["explicit_chapter_locator"],
        "matched_rules": [match_mode],
        "selected_action": "resolve_source_chapter",
        "role_executed": "resource_resolver",
        "document_changed": False,
        "source_ingestion_id": source.id,
        "chapter_id": chapter.id,
        "chapter_number": _chapter_number(chapter),
        "chapter_title": chapter.title,
        "source_title_overlap": source_title_overlap,
        "reason": "唯一已验证章节由显式章节定位和当前课程包资料结构共同确定。",
    }
    if requested_chapter_id:
        resolution["requested_chapter_id"] = requested_chapter_id
        resolution["resolved_chapter_id"] = chapter.id
    if rebound_anchors:
        resolution["rebound_anchors"] = list(rebound_anchors)
    if requested_chapter_number:
        resolution["requested_chapter_number"] = requested_chapter_number
        resolution["chapter_ordinal"] = int(requested_chapter_number)
    return resolution


def _normalize_chapter_number(number: str) -> str:
    parts = [part for part in number.strip().split(".") if part]
    if not parts:
        return ""
    normalized: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized.append(str(int(part)))
            continue
        chinese_number = _chinese_integer(part)
        if chinese_number is None:
            return ""
        normalized.append(str(chinese_number))
    return ".".join(normalized)


def _chinese_integer(value: str) -> int | None:
    cleaned = value.strip().replace("两", "二").replace("〇", "零")
    if not cleaned or any(char not in "零一二三四五六七八九十百千万" for char in cleaned):
        return None
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if all(char in digits for char in cleaned):
        return int("".join(str(digits[char]) for char in cleaned))
    total = 0
    section = 0
    current = 0
    for char in cleaned:
        if char in digits:
            current = digits[char]
            continue
        unit = units[char]
        if unit == 10000:
            section = (section + current) * unit
            total += section
            section = 0
            current = 0
            continue
        section += (current or 1) * unit
        current = 0
    return total + section + current


def _longest_common_substring_length(left: str, right: str) -> int:
    normalized_left = _normalize_match_text(left)[:300]
    normalized_right = _normalize_match_text(Path(right).stem)[:300]
    if not normalized_left or not normalized_right:
        return 0
    previous = [0] * (len(normalized_right) + 1)
    best = 0
    for left_char in normalized_left:
        current = [0]
        for index, right_char in enumerate(normalized_right, start=1):
            length = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(length)
            best = max(best, length)
        previous = current
    return best


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ascii_chars = sum(1 for char in stripped if ord(char) < 128)
    non_ascii_chars = len(stripped) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def _trim_to_token_budget(text: str, *, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text.strip()
    return text.strip()[: max_tokens * 3].rstrip()


def _compact(text: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"
