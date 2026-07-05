from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import LibraryChapter, ResourcePageStructure, ResourceSourceUnit


_CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CHINESE_UNITS = {"十": 10, "百": 100}


@dataclass(frozen=True)
class _ChapterMatch:
    order: int | None
    confidence: float
    status: str
    reason: str


def index_resource_chapters(
    chapters: list[LibraryChapter],
    source_units: list[ResourceSourceUnit],
    *,
    page_structure: ResourcePageStructure | None = None,
) -> tuple[list[LibraryChapter], list[ResourceSourceUnit]]:
    if not chapters or not source_units:
        return chapters, source_units

    sorted_units = sorted(source_units, key=lambda unit: unit.order_index)
    unit_orders = [unit.order_index for unit in sorted_units]
    explicit_starts: dict[str, _ChapterMatch] = {}
    for chapter in chapters:
        explicit_starts[chapter.id] = _explicit_start(chapter, sorted_units)

    children_by_id: dict[str, list[LibraryChapter]] = {}
    for chapter in chapters:
        if chapter.parent_id:
            children_by_id.setdefault(chapter.parent_id, []).append(chapter)

    start_cache: dict[str, _ChapterMatch] = {}

    def resolved_start(chapter: LibraryChapter) -> _ChapterMatch:
        cached = start_cache.get(chapter.id)
        if cached is not None:
            return cached
        explicit = explicit_starts.get(chapter.id, _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。"))
        if explicit.order is not None:
            start_cache[chapter.id] = explicit
            return explicit
        child_matches = [resolved_start(child) for child in children_by_id.get(chapter.id, [])]
        child_orders = [match.order for match in child_matches if match.order is not None]
        if child_orders:
            match = _ChapterMatch(
                min(child_orders),
                0.58,
                "child_range",
                "未直接命中父章节标题，使用最早子章节正文作为父章节起点。",
            )
            start_cache[chapter.id] = match
            return match
        fallback = _page_start_match(chapter, sorted_units)
        start_cache[chapter.id] = fallback
        return fallback

    indexed_chapters: list[LibraryChapter] = []
    for index, chapter in enumerate(chapters):
        start = resolved_start(chapter)
        end_order = _range_end_order(chapters, index, resolved_start, unit_orders)
        if start.order is None:
            indexed_chapters.append(chapter)
            continue
        if end_order is None or end_order < start.order:
            end_order = start.order
        page_start, page_end = _page_range_for_orders(sorted_units, start.order, end_order)
        indexed_chapters.append(
            chapter.model_copy(
                update={
                    "body_start_order": start.order,
                    "body_end_order": end_order,
                    "body_page_start": page_start or chapter.page_start,
                    "body_page_end": page_end or chapter.page_end or page_start or chapter.page_start,
                    "body_match_status": start.status,
                    "body_match_confidence": start.confidence,
                    "body_match_reason": start.reason,
                }
            )
        )

    enriched_units = [_tag_source_unit(unit, indexed_chapters) for unit in source_units]
    return indexed_chapters, enriched_units


def text_for_chapter_source_units(source_units: list[ResourceSourceUnit], chapter: LibraryChapter) -> str:
    if chapter.body_start_order is None or chapter.body_end_order is None:
        return ""
    parts: list[str] = []
    for unit in sorted(source_units, key=lambda item: item.order_index):
        if unit.order_index < chapter.body_start_order or unit.order_index > chapter.body_end_order:
            continue
        text = unit.text.strip()
        if not text:
            continue
        label = unit.heading_path[-1] if unit.heading_path else ""
        if label and not text.startswith(label):
            parts.append(f"{label}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _explicit_start(chapter: LibraryChapter, sorted_units: list[ResourceSourceUnit]) -> _ChapterMatch:
    best_order: int | None = None
    best_score = -1.0
    best_reason = ""
    for unit in sorted_units:
        score, reason = _unit_match_score(chapter, unit)
        if score > best_score:
            best_score = score
            best_order = unit.order_index if score > 0 else None
            best_reason = reason
    if best_order is None:
        return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")
    if best_score >= 8:
        return _ChapterMatch(best_order, min(0.98, best_score / 10), "title_match", best_reason)
    if best_score >= 4:
        return _ChapterMatch(best_order, min(0.75, best_score / 9), "weak_title_match", best_reason)
    return _ChapterMatch(best_order, 0.48, "page_window", best_reason)


def _unit_match_score(chapter: LibraryChapter, unit: ResourceSourceUnit) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    unit_text = unit.text or ""
    normalized_unit = _normalize(unit_text[:1600])
    normalized_title = _normalize(chapter.locator_hint or chapter.title)
    short_title = _normalize(_strip_number(chapter.locator_hint or chapter.title))
    number = _entry_number(chapter.title)

    heading_path = [_normalize(item) for item in unit.heading_path]
    chapter_path = [_normalize(item) for item in chapter.path]
    if normalized_title and normalized_title in heading_path:
        score += 10
        reasons.append("source unit heading_path 精确命中目录标题")
    elif chapter_path and all(path_item in heading_path for path_item in chapter_path if path_item):
        score += 9
        reasons.append("source unit heading_path 命中目录路径")

    if normalized_title and normalized_title in normalized_unit:
        score += 8
        reasons.append("正文单元文本包含完整目录标题")
    elif number and _number_at_unit_start(unit_text, number):
        score += 5
        reasons.append("正文单元开头命中目录编号")
        if short_title and short_title in normalized_unit:
            score += 2
            reasons.append("正文单元同时包含目录标题关键词")
    elif short_title and len(short_title) >= 4 and short_title in normalized_unit:
        score += 4
        reasons.append("正文单元包含目录标题关键词")

    if chapter.page_start is not None and _unit_page(unit) == chapter.page_start:
        score += 2
        reasons.append("正文单元页码匹配目录页码")

    return score, "；".join(reasons)


def _page_start_match(chapter: LibraryChapter, sorted_units: list[ResourceSourceUnit]) -> _ChapterMatch:
    if chapter.page_start is None:
        return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")
    for unit in sorted_units:
        page = _unit_page(unit)
        if page is not None and page >= chapter.page_start:
            return _ChapterMatch(unit.order_index, 0.45, "page_window", "未命中标题，按目录页码窗口定位。")
    return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")


def _range_end_order(
    chapters: list[LibraryChapter],
    index: int,
    resolved_start,
    unit_orders: list[int],
) -> int | None:
    current = chapters[index]
    start = resolved_start(current).order
    if start is None:
        return None
    next_start: int | None = None
    for candidate in chapters[index + 1 :]:
        if candidate.level > current.level:
            continue
        candidate_start = resolved_start(candidate).order
        if candidate_start is not None and candidate_start >= start:
            next_start = candidate_start
            break
    if next_start is None:
        return unit_orders[-1] if unit_orders else start
    previous_orders = [order for order in unit_orders if start <= order < next_start]
    return previous_orders[-1] if previous_orders else start


def _tag_source_unit(unit: ResourceSourceUnit, chapters: list[LibraryChapter]) -> ResourceSourceUnit:
    match = _deepest_covering_chapter(unit.order_index, chapters)
    if match is None:
        return unit
    metadata = dict(unit.metadata or {})
    metadata.update(
        {
            "chapter_id": match.id,
            "chapter_title": match.title,
            "chapter_path": match.path or [match.title],
            "chapter_level": match.level,
            "chapter_match_status": match.body_match_status,
            "chapter_match_confidence": match.body_match_confidence,
        }
    )
    heading_path = unit.heading_path or match.path or [match.title]
    return unit.model_copy(update={"metadata": metadata, "heading_path": heading_path})


def _deepest_covering_chapter(order: int, chapters: list[LibraryChapter]) -> LibraryChapter | None:
    matches = [
        chapter
        for chapter in chapters
        if chapter.body_start_order is not None
        and chapter.body_end_order is not None
        and chapter.body_start_order <= order <= chapter.body_end_order
    ]
    if not matches:
        return None
    matches.sort(key=lambda chapter: (chapter.level, chapter.body_start_order or -1, chapter.order_index))
    return matches[-1]


def _page_range_for_orders(
    sorted_units: list[ResourceSourceUnit],
    start_order: int,
    end_order: int,
) -> tuple[int | None, int | None]:
    pages = [
        page
        for unit in sorted_units
        if start_order <= unit.order_index <= end_order
        for page in [_unit_page(unit)]
        if page is not None
    ]
    if not pages:
        return None, None
    return min(pages), max(pages)


def _unit_page(unit: ResourceSourceUnit) -> int | None:
    printed_page = _metadata_int(unit.metadata.get("printed_page") if unit.metadata else None)
    if printed_page is not None:
        return printed_page
    if unit.page_no is not None:
        return unit.page_no
    if unit.page_idx is not None:
        return unit.page_idx + 1
    return None


def _metadata_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _entry_number(title: str) -> str | None:
    chapter = re.match(r"^第\s*([0-9一二三四五六七八九十百〇零两]+)\s*章", title)
    if chapter:
        parsed = _parse_chapter_number(chapter.group(1))
        return str(parsed) if parsed is not None else chapter.group(1)
    dotted = re.match(r"^(\d+(?:[.．]\d+){1,4})", title)
    return dotted.group(1).replace("．", ".") if dotted else None


def _parse_chapter_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    total = 0
    current = 0
    seen = False
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
            seen = True
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
        seen = True
    return total + current if seen else None


def _number_at_unit_start(text: str, number: str) -> bool:
    compact = text.strip()[:120]
    if number.isdigit():
        return bool(re.match(rf"^(?:第\s*{re.escape(number)}\s*章|{re.escape(number)}(?:\s|[.．]))", compact))
    return bool(re.match(rf"^{re.escape(number)}(?:\s|[.．])", compact))


def _strip_number(title: str) -> str:
    stripped = re.sub(r"^第\s*[0-9一二三四五六七八九十百〇零两]+\s*章\s*", "", title).strip()
    stripped = re.sub(r"^\d+(?:[.．]\d+){1,4}\s*", "", stripped).strip()
    return stripped or title


def _normalize(text: str) -> str:
    return re.sub(r"[\s.．·•…:：,，;；、/\\|()（）\[\]【】_-]+", "", text or "").lower()
