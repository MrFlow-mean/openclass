from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from app.models import RetrievalEvidence, SourceChapter, SourceIngestionRecord
from app.services.image_ocr import extract_pdf_pages_text
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
) -> tuple[list[RetrievalEvidence], dict[str, object] | None]:
    match = _match_verified_chapter(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id=owner_user_id,
        package_id=package_id,
        query=query,
    )
    if match is None:
        return [], None
    if isinstance(match, dict):
        return [], match
    source, chapter, resolution = match
    evidence = structure_store.chapter_evidence_by_id(
        owner_user_id=owner_user_id,
        package_id=package_id,
        chapter_id=chapter.id,
        limit=limit,
        token_budget=token_budget,
    )
    if evidence:
        resolution["body_retrieval"] = "indexed_chunks"
        return evidence, resolution
    ocr_evidence = _ocr_chapter_evidence(
        source=source,
        chapter=chapter,
        token_budget=token_budget,
        page_limit=page_limit,
    )
    if ocr_evidence:
        resolution["body_retrieval"] = "macos_vision_ocr"
        resolution["ocr_partial"] = bool(ocr_evidence[0].metadata.get("ocr_partial"))
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
) -> tuple[SourceIngestionRecord, SourceChapter, dict[str, object]] | dict[str, object] | None:
    ready_sources = source_store.ready_sources(owner_user_id=owner_user_id, package_id=package_id)
    explicit_id = explicit_source_chapter_id(query)
    requested_number = explicit_chapter_number(query)
    candidates: list[tuple[SourceIngestionRecord, SourceChapter, int]] = []
    for source in ready_sources:
        view = structure_store.get_structure_view(source=source, chunk_limit=0)
        if view.structure is None or view.structure.status != "ready":
            continue
        title_overlap = _longest_common_substring_length(query, source.title)
        for chapter in view.chapters:
            if chapter.anchor_status != "verified":
                continue
            if explicit_id:
                if chapter.id == explicit_id:
                    return source, chapter, _source_resolution(
                        source=source,
                        chapter=chapter,
                        match_mode="explicit_source_chapter_id",
                        source_title_overlap=title_overlap,
                    )
                continue
            chapter_number = _normalize_chapter_number(
                chapter.normalized_number or chapter.number or explicit_chapter_number(chapter.title)
            )
            if requested_number and chapter_number == requested_number:
                candidates.append((source, chapter, title_overlap))
    if explicit_id:
        return {
            "status": "not_found",
            "intent_signals": ["explicit_source_chapter_id"],
            "selected_action": "resolve_source_chapter",
            "role_executed": "resource_resolver",
            "document_changed": False,
            "reason": "指定的资料章节已经不存在或不再是已验证节点。",
        }
    if not requested_number or not candidates:
        return None
    if len(candidates) == 1:
        source, chapter, overlap = candidates[0]
        return source, chapter, _source_resolution(
            source=source,
            chapter=chapter,
            match_mode="unique_verified_chapter_number",
            source_title_overlap=overlap,
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
        match_mode="source_title_and_chapter_number",
        source_title_overlap=overlap,
    )


def _ocr_chapter_evidence(
    *,
    source: SourceIngestionRecord,
    chapter: SourceChapter,
    token_budget: int,
    page_limit: int,
) -> list[RetrievalEvidence]:
    raw_path = source.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return []
    path = Path(raw_path).expanduser()
    if path.suffix.lower() != ".pdf" or not path.exists() or chapter.page_start is None:
        return []
    page_start = max(1, chapter.page_start)
    chapter_page_end = max(page_start, (chapter.page_end or page_start + 1) - 1)
    pages_to_read = min(page_limit, chapter_page_end - page_start + 1)
    ocr_page_end = page_start + pages_to_read - 1
    text = extract_pdf_pages_text(
        path,
        page_start=page_start,
        page_end=ocr_page_end,
        max_pages=pages_to_read,
    )
    if not text:
        return []
    expanded_text = _trim_to_token_budget(text, max_tokens=token_budget)
    token_count = _estimate_tokens(expanded_text)
    page_range = f"p. {page_start}" if ocr_page_end == page_start else f"p. {page_start}-{ocr_page_end}"
    return [
        RetrievalEvidence(
            source_ingestion_id=source.id,
            open_notebook_source_id=source.open_notebook_source_id,
            source_title=source.title,
            source_uri=source.source_uri,
            chapter_id=chapter.id,
            section_path=chapter.path or [chapter.title],
            page_range=page_range,
            chunk_ids=[],
            excerpt=_compact(expanded_text, 360),
            expanded_text=expanded_text,
            relevance_score=chapter.confidence,
            reason="命中已验证目录节点；正文文本层为空，读取对应扫描页 OCR 摘录。",
            token_count=token_count,
            metadata={
                "retrieval_mode": "verified_chapter_ocr",
                "ocr_provider": "macos_vision",
                "ocr_page_start": page_start,
                "ocr_page_end": ocr_page_end,
                "chapter_page_end_exclusive": chapter.page_end,
                "ocr_partial": ocr_page_end < chapter_page_end,
                "source_locator": chapter.source_locator,
            },
        )
    ]


def _source_resolution(
    *,
    source: SourceIngestionRecord,
    chapter: SourceChapter,
    match_mode: str,
    source_title_overlap: int,
) -> dict[str, object]:
    return {
        "status": "matched",
        "intent_signals": ["explicit_chapter_locator"],
        "matched_rules": [match_mode],
        "selected_action": "resolve_source_chapter",
        "role_executed": "resource_resolver",
        "document_changed": False,
        "source_ingestion_id": source.id,
        "chapter_id": chapter.id,
        "chapter_number": _normalize_chapter_number(
            chapter.normalized_number or chapter.number or explicit_chapter_number(chapter.title)
        ),
        "source_title_overlap": source_title_overlap,
        "reason": "唯一已验证章节由显式章节定位和当前课程包资料结构共同确定。",
    }


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
