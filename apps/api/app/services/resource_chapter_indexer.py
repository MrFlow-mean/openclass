from __future__ import annotations

import re
from collections import Counter
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


@dataclass(frozen=True)
class _PageProjection:
    physical_minus_toc: int | None = None
    evidence_count: int = 0


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
    page_projection = _infer_page_projection(chapters, sorted_units)
    explicit_starts: dict[str, _ChapterMatch] = {}
    for chapter in chapters:
        explicit_starts[chapter.id] = _explicit_start(chapter, sorted_units, page_projection)

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
        fallback = _page_start_match(chapter, sorted_units, page_projection)
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
        trimmed_text = _trim_unit_text_for_chapter(text, chapter)
        omitted_parent_label = trimmed_text != text
        text = trimmed_text
        label = unit.heading_path[-1] if unit.heading_path else ""
        if label and not omitted_parent_label and not text.startswith(label):
            parts.append(f"{label}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _trim_unit_text_for_chapter(text: str, chapter: LibraryChapter) -> str:
    if chapter.body_start_order != chapter.body_end_order:
        return text
    if chapter.body_match_status not in {"weak_title_match", "page_window"}:
        return text
    short_title = _strip_number(chapter.title)
    if len(re.sub(r"\s+", "", short_title)) < 3:
        return text
    index = text.find(short_title)
    if index <= 0:
        return text
    return text[index:].strip()


def _explicit_start(
    chapter: LibraryChapter,
    sorted_units: list[ResourceSourceUnit],
    page_projection: _PageProjection,
) -> _ChapterMatch:
    best_order: int | None = None
    best_score = -1.0
    best_reason = ""
    for unit in sorted_units:
        score, reason = _unit_match_score(chapter, unit, page_projection)
        if score > best_score:
            best_score = score
            best_order = unit.order_index if score > 0 else None
            best_reason = reason
    if best_order is None:
        return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")
    if best_score >= 8:
        return _ChapterMatch(best_order, min(0.98, best_score / 10), "title_match", best_reason)
    if best_score >= 5:
        return _ChapterMatch(best_order, min(0.78, best_score / 9), "weak_title_match", best_reason)
    return _ChapterMatch(best_order, 0.48, "page_window", best_reason)


def _unit_match_score(
    chapter: LibraryChapter,
    unit: ResourceSourceUnit,
    page_projection: _PageProjection,
) -> tuple[float, str]:
    if not _unit_can_anchor_chapter(chapter, unit):
        return 0.0, ""
    score = 0.0
    reasons: list[str] = []
    unit_text = unit.text or ""
    normalized_unit = _normalize(unit_text[:1600])
    normalized_title = _normalize(chapter.title)
    short_title = _normalize(_strip_number(chapter.title))
    number = _entry_number(chapter.title)
    if number and _unit_looks_like_outline_listing(unit_text) and not _number_at_unit_start(unit_text, number):
        return 0.0, ""

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
    elif short_title and len(short_title) >= 4 and short_title in normalized_unit and (
        not number or _number_near_title(unit_text, number, short_title)
    ):
        score += 4
        reasons.append("正文单元包含目录标题关键词")
    elif (
        chapter.level >= 3
        and short_title
        and len(short_title) >= 4
        and short_title in normalized_unit
        and _unit_heading_matches_parent(chapter, unit)
    ):
        score += 5
        reasons.append("正文单元位于父章节内，并包含子章节标题关键词")

    if chapter.page_start is not None and chapter.page_start in _unit_page_candidates(unit, page_projection):
        score += 2
        if page_projection.physical_minus_toc is not None:
            reasons.append("正文单元页码命中目录页码映射")
        else:
            reasons.append("正文单元页码匹配目录页码")

    return score, "；".join(reasons)


def _page_start_match(
    chapter: LibraryChapter,
    sorted_units: list[ResourceSourceUnit],
    page_projection: _PageProjection,
) -> _ChapterMatch:
    if chapter.page_start is None:
        return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")
    candidate_units = [unit for unit in sorted_units if _unit_can_anchor_chapter(chapter, unit)] or sorted_units
    best: tuple[int, int] | None = None
    for unit in candidate_units:
        candidates = _unit_page_candidates(unit, page_projection)
        if not candidates:
            continue
        distance = min(abs(candidate - chapter.page_start) for candidate in candidates)
        if any(candidate >= chapter.page_start for candidate in candidates):
            if best is None or distance < best[0]:
                best = (distance, unit.order_index)
                if distance == 0:
                    break
    if best is None:
        return _ChapterMatch(None, 0.0, "unmatched", "未找到标题或页码匹配。")
    confidence = 0.54 if best[0] == 0 and page_projection.physical_minus_toc is not None else 0.45
    reason = "未命中标题，按目录页码映射窗口定位。" if page_projection.physical_minus_toc is not None else "未命中标题，按目录页码窗口定位。"
    return _ChapterMatch(best[1], confidence, "page_window", reason)


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
    matches.sort(key=lambda chapter: (chapter.body_start_order or -1, chapter.level, chapter.order_index))
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
        for page in [_display_page(unit)]
        if page is not None
    ]
    if not pages:
        return None, None
    return min(pages), max(pages)


def _infer_page_projection(chapters: list[LibraryChapter], sorted_units: list[ResourceSourceUnit]) -> _PageProjection:
    offsets: Counter[int] = Counter()
    for chapter in chapters:
        if chapter.page_start is None:
            continue
        for unit in sorted_units:
            if not _unit_can_anchor_chapter(chapter, unit):
                continue
            physical_page = unit.page_no or (unit.page_idx + 1 if unit.page_idx is not None else None)
            if physical_page is None:
                continue
            score, _ = _title_only_match_score(chapter, unit)
            if score < 6:
                continue
            offsets[physical_page - chapter.page_start] += 1
    if not offsets:
        return _PageProjection()
    offset, count = offsets.most_common(1)[0]
    if count < 2:
        return _PageProjection()
    return _PageProjection(physical_minus_toc=offset, evidence_count=count)


def _unit_can_anchor_chapter(chapter: LibraryChapter, unit: ResourceSourceUnit) -> bool:
    role = str((unit.metadata or {}).get("page_role") or "")
    if role in {"toc", "cover", "copyright"}:
        return _is_front_matter_chapter(chapter.title, role)
    if role == "preface":
        return _is_preface_chapter(chapter.title)
    return True


def _unit_looks_like_outline_listing(text: str) -> bool:
    sample = (text or "")[:5000]
    compact = re.sub(r"\s+", "", sample)
    if not compact:
        return False
    chapter_markers = re.findall(
        r"(?:第[0-9一二三四五六七八九十百〇零两]+章|chapter\d+)",
        compact,
        flags=re.IGNORECASE,
    )
    dotted_markers = re.findall(r"\d{1,2}[.．]\d{1,2}(?:[.．]\d{1,2}){0,4}", compact)
    marker_count = len(chapter_markers) + len(dotted_markers)
    page_like_count = len(re.findall(r"(?<![A-Fa-f0-9])[1-9]\d{2,3}(?![A-Fa-f0-9])", sample))
    sentence_count = sum(1 for segment in re.split(r"[。！？!?；;]\s*", sample) if len(re.sub(r"\s+", "", segment)) >= 24)
    if marker_count >= 8 and sentence_count <= 2:
        return True
    if marker_count >= 5 and page_like_count >= max(3, marker_count // 2) and sentence_count <= 3:
        return True
    return False


def _is_front_matter_chapter(title: str, role: str) -> bool:
    compact = _normalize(title)
    if role == "toc":
        return compact in {"contents", "目录", "目次"} or "tableofcontents" in compact
    if role == "cover":
        return compact in {"cover", "封面"}
    if role == "copyright":
        return "copyright" in compact or "版权" in compact
    return False


def _is_preface_chapter(title: str) -> bool:
    compact = _normalize(title)
    return compact in {"preface", "foreword", "前言", "序", "序言", "绪言"} or compact.endswith("preface")


def _title_only_match_score(chapter: LibraryChapter, unit: ResourceSourceUnit) -> tuple[float, str]:
    unit_text = unit.text or ""
    normalized_unit = _normalize(unit_text[:1200])
    normalized_title = _normalize(chapter.title)
    short_title = _normalize(_strip_number(chapter.title))
    number = _entry_number(chapter.title)
    if normalized_title and normalized_title in normalized_unit:
        return 8, "完整标题命中"
    if number and _number_at_unit_start(unit_text, number) and short_title and short_title in normalized_unit:
        return 7, "编号和标题关键词命中"
    return 0, ""


def _unit_page_candidates(unit: ResourceSourceUnit, page_projection: _PageProjection) -> set[int]:
    candidates: set[int] = set()
    printed_page = _metadata_int(unit.metadata.get("printed_page") if unit.metadata else None)
    if printed_page is not None:
        candidates.add(printed_page)
    candidates.update(_visible_page_numbers(unit.text))
    physical_page = unit.page_no or (unit.page_idx + 1 if unit.page_idx is not None else None)
    if physical_page is not None:
        candidates.add(physical_page)
        if page_projection.physical_minus_toc is not None:
            candidates.add(physical_page - page_projection.physical_minus_toc)
    return candidates


def _visible_page_numbers(text: str) -> set[int]:
    sample = (text or "").strip()[:260]
    candidates: set[int] = set()
    leading = re.match(r"^(\d{1,4})\s+(?:Chapter|Section|Part)\b", sample, flags=re.IGNORECASE)
    if leading:
        candidates.add(int(leading.group(1)))
    section = re.match(r"^Section\s+\d+(?:[.．]\d+){1,4}\b.{0,120}\s+(\d{1,4})(?:\s|$)", sample, flags=re.IGNORECASE)
    if section:
        candidates.add(int(section.group(1)))
    chinese = re.match(r"^第\s*[0-9一二三四五六七八九十百〇零两]+\s*章.{0,80}\s+(\d{1,4})(?:\s|$)", sample)
    if chinese:
        candidates.add(int(chinese.group(1)))
    return candidates


def _display_page(unit: ResourceSourceUnit) -> int | None:
    visible_pages = _visible_page_numbers(unit.text)
    if visible_pages:
        return min(visible_pages)
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


def _number_near_title(text: str, number: str, normalized_title: str) -> bool:
    compact = _normalize(text[:2400])
    compact_number = _normalize(number)
    if not compact_number or not normalized_title:
        return False
    title_index = compact.find(normalized_title)
    if title_index < 0:
        return False
    nearby_start = max(0, title_index - 40)
    nearby_end = min(len(compact), title_index + len(normalized_title) + 40)
    return compact_number in compact[nearby_start:nearby_end]


def _unit_heading_matches_parent(chapter: LibraryChapter, unit: ResourceSourceUnit) -> bool:
    if not chapter.path or not unit.heading_path:
        return False
    heading_path = [_normalize(item) for item in unit.heading_path if item]
    for parent_title in reversed(chapter.path[:-1]):
        parent_short = _normalize(_strip_number(parent_title))
        if not parent_short:
            continue
        if any(parent_short in heading or heading in parent_short for heading in heading_path if heading):
            return True
    return False


def _strip_number(title: str) -> str:
    stripped = re.sub(r"^第\s*[0-9一二三四五六七八九十百〇零两]+\s*章\s*", "", title).strip()
    stripped = re.sub(r"^\d+(?:[.．]\d+){1,4}\s*", "", stripped).strip()
    return stripped or title


def _normalize(text: str) -> str:
    return re.sub(r"[\s.．·•…:：,，;；、/\\|()（）\[\]【】_-]+", "", text or "").lower()
