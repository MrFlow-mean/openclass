from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from app.services.image_ocr import OCRLineLayout, OCRPageLayout, extract_pdf_pages_layout

MAX_OCR_TOC_PAGES = 24
ROW_Y_TOLERANCE = 0.006


@dataclass(frozen=True)
class PdfOutlineAnchor:
    title: str
    page_no: int
    level: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PdfTocNode:
    title: str
    printed_page: int
    toc_page: int
    level: int = 1
    number: str = ""
    physical_page: int | None = None
    verified: bool = False
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PdfTocExtraction:
    nodes: list[PdfTocNode] = field(default_factory=list)
    toc_page_start: int | None = None
    toc_page_end: int | None = None
    printed_page_offset: int | None = None
    mapping_support: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _RawTocRow:
    title: str
    printed_page: int | None
    toc_page: int
    x: float
    height: float
    number: str = ""
    level: int = 1
    printed_page_inferred: bool = False


def extract_pdf_toc(
    path: Path,
    *,
    outline: list[PdfOutlineAnchor],
    page_count: int,
) -> PdfTocExtraction:
    toc_range = _toc_page_range(outline, page_count=page_count)
    if toc_range is None:
        return PdfTocExtraction()

    toc_page_start, toc_page_end = toc_range
    layouts = extract_pdf_pages_layout(
        path,
        page_start=toc_page_start,
        page_end=toc_page_end,
        max_pages=MAX_OCR_TOC_PAGES,
    )
    if not layouts:
        return PdfTocExtraction(
            toc_page_start=toc_page_start,
            toc_page_end=toc_page_end,
            warnings=["扫描目录页 OCR 未返回可用布局，已保留 PDF 原生书签。"],
        )

    rows = _toc_rows(layouts)
    if not rows:
        return PdfTocExtraction(
            toc_page_start=toc_page_start,
            toc_page_end=toc_page_end,
            warnings=["扫描目录页未识别出标题与印刷页码对应关系，已保留 PDF 原生书签。"],
        )

    _fill_missing_printed_pages(rows, outline)
    rows = [row for row in rows if row.printed_page is not None]
    _assign_levels(rows)
    offset, support = _printed_page_mapping(rows, outline)
    mapping_is_verified = offset is not None and support >= 2
    outline_by_title = {_normalized_title(anchor.title): anchor for anchor in outline if _normalized_title(anchor.title)}

    nodes: list[PdfTocNode] = []
    for row in rows:
        normalized_title = _normalized_title(row.title)
        matching_outline = outline_by_title.get(normalized_title)
        physical_page: int | None = None
        verified = False
        confidence = 0.64
        verification = "toc_candidate"
        if matching_outline is not None:
            physical_page = matching_outline.page_no
            verified = True
            confidence = 0.94
            verification = "native_outline_anchor"
        elif mapping_is_verified and offset is not None and row.printed_page is not None:
            mapped_page = row.printed_page + offset
            if 1 <= mapped_page <= page_count:
                physical_page = mapped_page
                verified = True
                confidence = 0.81 if row.printed_page_inferred else 0.87
                verification = (
                    "verified_printed_page_mapping_inferred"
                    if row.printed_page_inferred
                    else "verified_printed_page_mapping"
                )

        nodes.append(
            PdfTocNode(
                title=matching_outline.title if matching_outline else row.title,
                number=row.number,
                level=row.level,
                printed_page=row.printed_page,
                toc_page=row.toc_page,
                physical_page=physical_page,
                verified=verified,
                confidence=confidence,
                metadata={
                    "source": "pdf_toc_ocr",
                    "printed_page": row.printed_page,
                    "toc_page": row.toc_page,
                    "verification": verification,
                    "printed_page_offset": offset,
                    "mapping_support": support,
                    "printed_page_inferred": row.printed_page_inferred,
                    "ocr_x": round(row.x, 4),
                    "outline_title": matching_outline.title if matching_outline else "",
                    "outline_page": matching_outline.page_no if matching_outline else None,
                },
            )
        )

    warnings: list[str] = []
    if not mapping_is_verified:
        warnings.append("已识别完整目录候选，但印刷页与物理页映射不足；未绑定正文的节点不可直接引用。")
    return PdfTocExtraction(
        nodes=nodes,
        toc_page_start=toc_page_start,
        toc_page_end=toc_page_end,
        printed_page_offset=offset,
        mapping_support=support,
        warnings=warnings,
    )


def extract_pdf_toc_from_range(
    path: Path,
    *,
    page_start: int,
    page_end: int,
) -> PdfTocExtraction:
    """Read a printed TOC when the PDF has no native outline anchors."""
    layouts = extract_pdf_pages_layout(
        path,
        page_start=page_start,
        page_end=page_end,
        max_pages=MAX_OCR_TOC_PAGES,
    )
    if not layouts:
        return PdfTocExtraction(
            toc_page_start=page_start,
            toc_page_end=page_end,
            warnings=["目录页版面 OCR 未返回可用结果，已回退到 PDF 文字层。"],
        )
    rows = _toc_rows(layouts)
    _assign_levels(rows)
    nodes = [
        PdfTocNode(
            title=row.title,
            number=row.number,
            level=row.level,
            printed_page=row.printed_page or 0,
            toc_page=row.toc_page,
            confidence=0.72,
            metadata={
                "source": "pdf_toc_layout_ocr",
                "printed_page": row.printed_page,
                "toc_page": row.toc_page,
                "verification": "toc_candidate",
                "ocr_x": round(row.x, 4),
            },
        )
        for row in rows
        if _explicit_level(row.title, row.number) is not None
    ]
    return PdfTocExtraction(
        nodes=nodes,
        toc_page_start=page_start,
        toc_page_end=page_end,
    )


def _toc_page_range(outline: list[PdfOutlineAnchor], *, page_count: int) -> tuple[int, int] | None:
    ordered = sorted((anchor for anchor in outline if anchor.page_no > 0), key=lambda anchor: anchor.page_no)
    for index, anchor in enumerate(ordered):
        if not _is_toc_heading(anchor.title):
            continue
        next_page = next(
            (candidate.page_no for candidate in ordered[index + 1 :] if candidate.page_no > anchor.page_no),
            min(page_count + 1, anchor.page_no + MAX_OCR_TOC_PAGES),
        )
        end_page = min(page_count, next_page - 1, anchor.page_no + MAX_OCR_TOC_PAGES - 1)
        return anchor.page_no, max(anchor.page_no, end_page)
    return None


def _toc_rows(layouts: list[OCRPageLayout]) -> list[_RawTocRow]:
    rows: list[_RawTocRow] = []
    seen: set[tuple[str, int]] = set()
    for page in layouts:
        for grouped_lines in _group_page_rows(page.lines):
            row = _parse_layout_row(grouped_lines, toc_page=page.page_no)
            if row is None:
                continue
            key = (_normalized_title(row.title), row.printed_page or -1)
            if not key[0] or key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def _group_page_rows(lines: list[OCRLineLayout]) -> list[list[OCRLineLayout]]:
    ordered = sorted(lines, key=lambda line: (-line.y, line.x))
    groups: list[list[OCRLineLayout]] = []
    group_y: list[float] = []
    for line in ordered:
        if not groups or abs(line.y - group_y[-1]) > ROW_Y_TOLERANCE:
            groups.append([line])
            group_y.append(line.y)
            continue
        groups[-1].append(line)
        group_y[-1] = sum(item.y for item in groups[-1]) / len(groups[-1])
    return [sorted(group, key=lambda line: line.x) for group in groups]


def _parse_layout_row(lines: list[OCRLineLayout], *, toc_page: int) -> _RawTocRow | None:
    visible = [line for line in lines if line.text.strip()]
    if not visible:
        return None

    page_line: OCRLineLayout | None = None
    printed_page: int | None = None
    for line in reversed(visible):
        parsed_page = _printed_page_number(line.text)
        if parsed_page is not None and line.x >= 0.68:
            page_line = line
            printed_page = parsed_page
            break
    page_x = page_line.x if page_line is not None else 0.68
    title_lines = [
        line
        for line in visible
        if line is not page_line and line.x < page_x and _printed_page_number(line.text) is None
    ]
    title_parts = [_clean_title_part(line.text) for line in title_lines]
    title_parts = [part for part in title_parts if part and not _is_leader(part)]
    if not title_parts:
        return None
    title = _clean_title(" ".join(title_parts))
    if len(title) < 2 or len(title) > 180 or _printed_page_number(title) is not None:
        return None
    if printed_page is None and not _chapter_number(title) and not re.match(
        r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章节篇部]",
        title,
    ):
        return None
    title_lines = [line for line, part in zip(title_lines, [_clean_title_part(line.text) for line in title_lines]) if part]
    x = min((line.x for line in title_lines), default=0.0)
    height = max((line.height for line in title_lines), default=0.0)
    return _RawTocRow(
        title=title,
        printed_page=printed_page,
        toc_page=toc_page,
        x=x,
        height=height,
        number=_chapter_number(title),
    )


def _assign_levels(rows: list[_RawTocRow]) -> None:
    rows_by_page: dict[int, list[_RawTocRow]] = {}
    for row in rows:
        rows_by_page.setdefault(row.toc_page, []).append(row)
    page_indents: dict[int, tuple[float, float]] = {}
    for page_no, page_rows in rows_by_page.items():
        explicit_root_x = [row.x for row in page_rows if _explicit_level(row.title, row.number) == 1]
        explicit_child_x = [row.x for row in page_rows if (_explicit_level(row.title, row.number) or 0) > 1]
        child_x = median(explicit_child_x) if explicit_child_x else 0.0
        root_x = median(explicit_root_x) if explicit_root_x else max(0.0, child_x - 0.016)
        if not explicit_child_x:
            child_x = root_x + 0.016
        page_indents[page_no] = (root_x, child_x)
    current_root = False
    for row in rows:
        root_x, child_x = page_indents[row.toc_page]
        explicit_level = _explicit_level(row.title, row.number)
        if explicit_level is not None:
            row.level = explicit_level
        elif current_root:
            row.level = 1 if abs(row.x - root_x) <= abs(row.x - child_x) else 2
        else:
            row.level = 1
        if row.level == 1:
            current_root = True


def _printed_page_mapping(rows: list[_RawTocRow], outline: list[PdfOutlineAnchor]) -> tuple[int | None, int]:
    outline_by_title = {_normalized_title(anchor.title): anchor for anchor in outline if _normalized_title(anchor.title)}
    offsets: list[int] = []
    for row in rows:
        if row.printed_page is None:
            continue
        anchor = outline_by_title.get(_normalized_title(row.title))
        if anchor is not None:
            offsets.append(anchor.page_no - row.printed_page)
    if not offsets:
        return None, 0
    offset, support = Counter(offsets).most_common(1)[0]
    return offset, support


def _fill_missing_printed_pages(rows: list[_RawTocRow], outline: list[PdfOutlineAnchor]) -> None:
    offset, _support = _printed_page_mapping(rows, outline)
    outline_by_title = {_normalized_title(anchor.title): anchor for anchor in outline if _normalized_title(anchor.title)}
    for row in rows:
        if row.printed_page is not None or offset is None:
            continue
        anchor = outline_by_title.get(_normalized_title(row.title))
        if anchor is not None:
            row.printed_page = max(1, anchor.page_no - offset)
            row.printed_page_inferred = True

    for index, row in enumerate(rows):
        if row.printed_page is not None:
            continue
        previous_page = next(
            (candidate.printed_page for candidate in reversed(rows[:index]) if candidate.printed_page is not None),
            None,
        )
        next_page = next(
            (candidate.printed_page for candidate in rows[index + 1 :] if candidate.printed_page is not None),
            None,
        )
        if previous_page is not None and next_page is not None and 0 <= next_page - previous_page <= 2:
            row.printed_page = previous_page
            row.printed_page_inferred = True


def _explicit_level(title: str, number: str) -> int | None:
    if re.match(r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章篇部]", title):
        return 1
    if re.match(r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*节", title):
        return 2
    if re.match(r"^[A-Z](?:\s+|(?=[\u4e00-\u9fff]))", title):
        return 2
    if number:
        return max(1, len(number.split(".")))
    return None


def _chapter_number(title: str) -> str:
    cleaned = _clean_title(title)
    decimal = re.match(r"^(\d+(?:\.\d+){0,8})(?=\s|[.:：、-]|[\u4e00-\u9fff]|$)", cleaned)
    if decimal:
        return decimal.group(1)
    arabic_chapter = re.match(r"^第\s*(\d+)\s*[章节篇部]", cleaned)
    if arabic_chapter:
        return arabic_chapter.group(1)
    letter = re.match(r"^([A-Z])(?=\s|[.:：、-]|[\u4e00-\u9fff])", cleaned)
    return letter.group(1) if letter else ""


def _printed_page_number(value: str) -> int | None:
    cleaned = value.strip()
    match = re.fullmatch(
        r"[.．·•⋯…:：\s]*[（(\[]?\s*([0-9]{1,4})\s*[）)\]]?[.．·•⋯…\s]*",
        cleaned,
    )
    if not match:
        return None
    return int(match.group(1))


def _clean_title_part(value: str) -> str:
    return re.sub(r"^[.．。·•⋯…\s]+|[.．。·•⋯…\s]+$", "", value or "").strip()


def _clean_title(value: str) -> str:
    cleaned = re.sub(r"\s*[=+•\-]*\s*[（(][A-Z0-9]{1,6}[）)]\s*$", "", value or "", flags=re.I)
    cleaned = re.sub(r"[.．。·•⋯…！!+=\-\s]+$", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_leader(value: str) -> bool:
    return bool(re.fullmatch(r"[.．。·•⋯…\s]+", value or ""))


def _normalized_title(value: str) -> str:
    cleaned = _clean_title(value).lower()
    cleaned = re.sub(r"^第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章节篇部]\s*", "", cleaned)
    cleaned = re.sub(r"^chapter\s+[0-9ivxlcdm]+\s*", "", cleaned)
    cleaned = re.sub(r"^\d+(?:\.\d+)*\s*", "", cleaned)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", cleaned)


def _is_toc_heading(value: str) -> bool:
    normalized = re.sub(r"[^a-z\u4e00-\u9fff]+", "", (value or "").lower())
    return normalized in {"目录", "目次", "contents", "tableofcontents", "sommaire", "inhalt"}
