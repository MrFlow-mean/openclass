from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.models import (
    ResourcePageMapEntry,
    ResourcePageRole,
    ResourcePageSection,
    ResourcePageStructure,
    ResourceSourceUnit,
)


_ROLE_LABELS: dict[str, str] = {
    "cover": "封面",
    "copyright": "版权页",
    "toc": "目录",
    "preface": "前言",
    "body": "正文",
    "appendix": "附录",
    "back_matter": "尾声",
    "unknown": "未知页面",
}


def build_pdf_page_structure(
    reader: Any,
    *,
    read_page_text: Callable[[int], str] | None = None,
    max_scan_pages: int = 120,
) -> ResourcePageStructure:
    page_count = len(getattr(reader, "pages", []) or [])
    texts: list[str] = []
    scan_count = min(max(page_count, 0), max_scan_pages)
    for page_idx in range(scan_count):
        if read_page_text is not None:
            texts.append(read_page_text(page_idx))
            continue
        try:
            texts.append(str(reader.pages[page_idx].extract_text() or ""))
        except Exception:
            texts.append("")
    return build_page_structure_from_texts(texts, page_count=page_count)


def build_page_structure_from_texts(
    page_texts: list[str],
    *,
    page_count: int | None = None,
) -> ResourcePageStructure:
    total_pages = page_count if page_count is not None else len(page_texts)
    roles = [_classify_page(text, page_idx=index) for index, text in enumerate(page_texts)]
    body_start_idx, body_confidence, diagnostics = _detect_body_start(page_texts, roles, total_pages)
    if body_start_idx is not None:
        roles = _promote_body_roles(roles, body_start_idx, total_pages)
    sections = _sections_from_roles(roles)
    page_map = _page_map_from_roles(
        roles,
        page_texts,
        total_pages=total_pages,
        body_start_idx=body_start_idx,
        body_confidence=body_confidence,
    )
    toc_page_indices = [index for index, role in enumerate(roles) if role == "toc"]
    return ResourcePageStructure(
        page_count=total_pages,
        body_start_page_idx=body_start_idx,
        body_start_page_no=body_start_idx + 1 if body_start_idx is not None else None,
        toc_page_indices=toc_page_indices,
        sections=sections,
        page_map=page_map,
        diagnostics=diagnostics,
        confidence=body_confidence,
    )


def build_page_structure_from_source_units(units: list[ResourceSourceUnit]) -> ResourcePageStructure | None:
    page_texts_by_idx: dict[int, list[str]] = {}
    for unit in units:
        page_idx = unit.page_idx if unit.page_idx is not None else (unit.page_no - 1 if unit.page_no else None)
        if page_idx is None or page_idx < 0:
            continue
        text_parts = [unit.text, str(unit.metadata.get("caption") or "") if unit.metadata else ""]
        text = "\n".join(part for part in text_parts if part).strip()
        if not text:
            continue
        page_texts_by_idx.setdefault(page_idx, []).append(text)
    if not page_texts_by_idx:
        return None
    page_count = max(page_texts_by_idx) + 1
    page_texts = [""] * page_count
    for page_idx, texts in page_texts_by_idx.items():
        page_texts[page_idx] = "\n".join(texts)
    return build_page_structure_from_texts(page_texts, page_count=page_count)


def physical_page_candidates_for_printed_page(
    page_structure: ResourcePageStructure | None,
    printed_page: int | None,
) -> list[int]:
    if page_structure is None or printed_page is None:
        return []
    candidates: list[int] = []
    for entry in page_structure.page_map:
        if entry.printed_page == printed_page and entry.role == "body":
            candidates.append(entry.page_no)
    return candidates


def page_metadata(
    page_structure: ResourcePageStructure | None,
    *,
    page_idx: int | None = None,
    page_no: int | None = None,
) -> dict[str, Any]:
    if page_structure is None:
        return {}
    entry = _entry_for_page(page_structure, page_idx=page_idx, page_no=page_no)
    if entry is None:
        return {}
    metadata: dict[str, Any] = {
        "page_role": entry.role,
        "page_role_label": page_role_label(entry.role),
        "page_structure_confidence": entry.confidence,
    }
    if entry.printed_page is not None:
        metadata["printed_page"] = entry.printed_page
    if entry.body_offset is not None:
        metadata["body_offset"] = entry.body_offset
    if page_structure.body_start_page_no is not None:
        metadata["body_start_page_no"] = page_structure.body_start_page_no
    return metadata


def enrich_source_units_with_page_structure(
    units: list[ResourceSourceUnit],
    page_structure: ResourcePageStructure | None,
) -> list[ResourceSourceUnit]:
    if page_structure is None or not units:
        return units
    enriched: list[ResourceSourceUnit] = []
    for unit in units:
        unit_page_idx = unit.page_idx
        unit_page_no = unit.page_no
        metadata = page_metadata(page_structure, page_idx=unit_page_idx, page_no=unit_page_no)
        if not metadata:
            enriched.append(unit)
            continue
        next_metadata = {**metadata, **unit.metadata}
        enriched.append(unit.model_copy(deep=True, update={"metadata": next_metadata}))
    return enriched


def page_role_label(role: str | None) -> str:
    return _ROLE_LABELS.get(str(role or "unknown"), "未知页面")


def _classify_page(text: str, *, page_idx: int) -> ResourcePageRole:
    normalized = _normalize(text)
    compact = re.sub(r"\s+", "", normalized).lower()
    if not compact:
        return "unknown"
    if _looks_like_toc(normalized):
        return "toc"
    if _contains_any(compact, ("版权", "版权所有", "copyright", "isbn", "版次", "印次")):
        return "copyright"
    if _looks_like_preface(normalized):
        return "preface"
    if _contains_any(compact, ("附录", "appendix")):
        return "appendix"
    if _contains_any(compact, ("参考文献", "索引", "后记", "致谢", "bibliography", "references", "index")):
        return "back_matter"
    if page_idx == 0 and len(compact) <= 180:
        return "cover"
    return "unknown"


def _detect_body_start(
    page_texts: list[str],
    roles: list[ResourcePageRole],
    total_pages: int,
) -> tuple[int | None, float, list[str]]:
    diagnostics: list[str] = []
    if total_pages <= 0:
        return None, 0.0, ["资料没有可分析页面。"]
    front_roles = {"cover", "copyright", "toc", "preface"}
    front_indices = [index for index, role in enumerate(roles) if role in front_roles]
    if front_indices:
        candidate = max(front_indices) + 1
        if candidate < total_pages:
            diagnostics.append(
                f"正文起点按前置信息推断为文件第 {candidate + 1} 页。"
            )
            return candidate, 0.82 if any(role == "toc" for role in roles) else 0.66, diagnostics
    for index, text in enumerate(page_texts):
        if roles[index] in front_roles or roles[index] in {"appendix", "back_matter"}:
            continue
        if _looks_like_body_start(text):
            diagnostics.append(f"正文起点按正文标题特征推断为文件第 {index + 1} 页。")
            return index, 0.7, diagnostics
    diagnostics.append("未能稳定识别正文起点，保留原始页码候选。")
    return None, 0.0, diagnostics


def _promote_body_roles(
    roles: list[ResourcePageRole],
    body_start_idx: int,
    total_pages: int,
) -> list[ResourcePageRole]:
    promoted = list(roles)
    for index in range(body_start_idx, total_pages):
        if index >= len(promoted):
            promoted.append("body")
            continue
        if promoted[index] == "unknown":
            promoted[index] = "body"
    return promoted


def _sections_from_roles(roles: list[ResourcePageRole]) -> list[ResourcePageSection]:
    if not roles:
        return []
    sections: list[ResourcePageSection] = []
    start = 0
    current = roles[0]
    for index, role in enumerate(roles[1:], start=1):
        if role == current:
            continue
        sections.append(_section(current, start, index - 1))
        start = index
        current = role
    sections.append(_section(current, start, len(roles) - 1))
    return sections


def _section(role: ResourcePageRole, start: int, end: int) -> ResourcePageSection:
    return ResourcePageSection(
        role=role,
        page_idx_start=start,
        page_idx_end=end,
        page_no_start=start + 1,
        page_no_end=end + 1,
        title=page_role_label(role),
        confidence=0.8 if role != "unknown" else 0.0,
    )


def _page_map_from_roles(
    roles: list[ResourcePageRole],
    page_texts: list[str],
    *,
    total_pages: int,
    body_start_idx: int | None,
    body_confidence: float,
) -> list[ResourcePageMapEntry]:
    entries: list[ResourcePageMapEntry] = []
    for page_idx in range(total_pages):
        role = roles[page_idx] if page_idx < len(roles) else ("body" if body_start_idx is not None and page_idx >= body_start_idx else "unknown")
        body_offset = page_idx - body_start_idx if body_start_idx is not None and role == "body" else None
        printed_page = body_offset + 1 if body_offset is not None else None
        entries.append(
            ResourcePageMapEntry(
                page_idx=page_idx,
                page_no=page_idx + 1,
                role=role,
                printed_page=printed_page,
                body_offset=body_offset,
                confidence=body_confidence if role == "body" else (0.8 if role != "unknown" else 0.0),
                evidence_excerpt=_excerpt(page_texts[page_idx]) if page_idx < len(page_texts) else "",
            )
        )
    return entries


def _entry_for_page(
    page_structure: ResourcePageStructure,
    *,
    page_idx: int | None,
    page_no: int | None,
) -> ResourcePageMapEntry | None:
    for entry in page_structure.page_map:
        if page_idx is not None and entry.page_idx == page_idx:
            return entry
        if page_no is not None and entry.page_no == page_no:
            return entry
    return None


def _looks_like_toc(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return "目录" in compact or "contents" in compact or len(_toc_like_lines(text)) >= 2


def _looks_like_preface(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = re.sub(r"\s+", "", lines[0]).lower() if lines else ""
    return first in {"前言", "序", "序言", "绪言", "preface", "foreword"}


def _looks_like_body_start(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(
            r"(?:第\s*[0-9一二三四五六七八九十百〇零两]+\s*章|\bchapter\s+\d+|\d+\s*[.．]\s*\d*)",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _toc_like_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" .·•\t")
        line = re.sub(r"[.．·•…]{2,}", " ", line)
        if re.search(r".+\s+\d{1,4}$", line):
            lines.append(line)
    return lines


def _normalize(text: str) -> str:
    cleaned = text.replace("\x00", "").replace("\r\n", "\n")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _excerpt(text: str, *, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", _normalize(text)).strip()
    return compact[:limit]
