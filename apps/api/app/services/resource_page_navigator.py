from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.models import (
    LibraryChapter,
    ResourceContextChunk,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceMatchEvidence,
    ResourceReferenceContext,
    ResourceSegment,
)


_STRUCTURED_SECTION_RE = re.compile(
    r"(?<!\d)(?P<primary>\d{1,3})\s*[.．]\s*(?P<secondary>\d{1,3})(?!\s*[.．]\s*\d)"
)
_HEADING_GUARD_BEFORE = set("图表式公式（([<")
_HEADING_GUARD_WORDS = ("figure", "fig", "table", "equation", "eq")
_GENERIC_REFERENCE_WORDS = {
    "章节",
    "小节",
    "部分",
    "内容",
    "正文",
    "板书",
    "呈现",
    "生成",
    "讲解",
    "给我",
    "为我",
    "开始",
    "资料",
    "文件",
}
_MAX_NAVIGATION_UNITS = 80
_MAX_REFERENCE_CHARS = 5600


@dataclass(frozen=True)
class StructuredReference:
    primary: int
    secondary: int
    raw: str
    title_hint: str = ""

    @property
    def label(self) -> str:
        return f"{self.primary}.{self.secondary}"

    @property
    def next_label(self) -> str:
        return f"{self.primary}.{self.secondary + 1}"


@dataclass(frozen=True)
class NavigationUnit:
    resource_id: str
    chapter_id: str
    unit_id: str | None
    heading_path: list[str]
    order_index: int
    text: str
    text_hash: str
    page_range: str | None
    text_source: str


@dataclass(frozen=True)
class NavigationResult:
    resource: ResourceLibraryItem
    chapter: LibraryChapter
    reference: StructuredReference
    start_unit: NavigationUnit
    end_unit: NavigationUnit
    extracted_text: str
    page_range: str | None
    start_label: str
    end_label: str | None
    score: float

    @property
    def text_hash(self) -> str:
        return _text_hash(self.extracted_text)


def is_structured_resource_reference(text: str) -> bool:
    return parse_structured_reference(text) is not None


def parse_structured_reference(text: str) -> StructuredReference | None:
    match = _STRUCTURED_SECTION_RE.search(text or "")
    if match is None:
        return None
    raw = match.group(0)
    title_hint = _title_hint_after_reference(text[match.end() :])
    return StructuredReference(
        primary=int(match.group("primary")),
        secondary=int(match.group("secondary")),
        raw=raw,
        title_hint=title_hint,
    )


def find_navigated_matches(
    resources: list[ResourceLibraryItem],
    user_message: str,
    *,
    limit: int = 3,
) -> list[ResourceMatch]:
    reference = parse_structured_reference(user_message)
    if reference is None:
        return []

    results = [
        result
        for resource in resources
        if (result := navigate_to_section(resource, reference, query=user_message)) is not None
    ]
    results.sort(key=lambda item: item.score, reverse=True)
    return [_navigation_match(result) for result in results[:limit]]


def extract_navigated_reference_context(
    resource: ResourceLibraryItem,
    match: ResourceMatch,
    user_query: str,
) -> ResourceReferenceContext | None:
    reference = parse_structured_reference(user_query)
    if reference is None:
        return None
    result = navigate_to_section(resource, reference, query=user_query, preferred_chapter_id=match.chapter_id)
    if result is None:
        return None

    title = _target_section_title(result)
    heading_path = [*result.start_unit.heading_path]
    if not heading_path or heading_path[-1] != title:
        heading_path = [*heading_path, title]
    chunks = _reference_chunks(result, heading_path)
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=result.chapter.id,
        segment_id=result.start_unit.unit_id if _is_segment_id(result.start_unit.unit_id) else None,
        resource_name=resource.name,
        chapter_title=title,
        summary=(
            f"已由资料导航器定位到《{resource.name}》的 {result.reference.label}，"
            f"范围覆盖 {result.page_range or '目标页窗'}。"
        ),
        teaching_points=[
            f"结构编号 {result.reference.label}",
            f"起始标题 {result.start_label}",
            *([f"结束于 {result.end_label} 前"] if result.end_label else []),
        ],
        chunks=chunks,
        text_evidence_available=bool(result.extracted_text.strip()),
        text_evidence_status="page_navigator",
        full_text=result.extracted_text,
    )


def navigate_to_section(
    resource: ResourceLibraryItem,
    reference: StructuredReference,
    *,
    query: str,
    preferred_chapter_id: str | None = None,
) -> NavigationResult | None:
    chapters = _candidate_chapters(resource, reference, preferred_chapter_id=preferred_chapter_id)
    best: NavigationResult | None = None
    for chapter in chapters:
        units = _chapter_navigation_units(resource, chapter)
        if not units:
            continue
        result = _find_section_in_units(resource, chapter, reference, units, query=query)
        if result is None:
            continue
        if best is None or result.score > best.score:
            best = result
    return best


def _candidate_chapters(
    resource: ResourceLibraryItem,
    reference: StructuredReference,
    *,
    preferred_chapter_id: str | None = None,
) -> list[LibraryChapter]:
    chapters = list(resource.outline)
    if preferred_chapter_id:
        preferred = [chapter for chapter in chapters if chapter.id == preferred_chapter_id]
        if preferred:
            return preferred
    numbered = [chapter for chapter in chapters if _chapter_number(chapter.title) == reference.primary]
    if numbered:
        return numbered
    return chapters


def _chapter_navigation_units(resource: ResourceLibraryItem, chapter: LibraryChapter) -> list[NavigationUnit]:
    segment_units = [
        NavigationUnit(
            resource_id=resource.id,
            chapter_id=chapter.id,
            unit_id=segment.segment_id,
            heading_path=segment.heading_path or chapter.path or [chapter.title],
            order_index=segment.order_index,
            text=segment.text,
            text_hash=segment.text_hash,
            page_range=segment.page_range or chapter.page_range,
            text_source=segment.text_source,
        )
        for segment in sorted(resource.segments, key=lambda item: item.order_index)
        if segment.chapter_id == chapter.id and segment.text.strip()
    ]
    if segment_units:
        return segment_units[:_MAX_NAVIGATION_UNITS]
    if resource.mime_type != "application/pdf" or not resource.source_path:
        return []
    return _pdf_page_units(resource, chapter)


def _pdf_page_units(resource: ResourceLibraryItem, chapter: LibraryChapter) -> list[NavigationUnit]:
    try:
        reader = PdfReader(str(Path(resource.source_path)))
    except Exception:
        return []
    total_pages = len(reader.pages)
    start = max(1, chapter.page_start or 1)
    end = min(total_pages, chapter.page_end or min(total_pages, start + 12))
    units: list[NavigationUnit] = []
    for actual_page in range(start, end + 1):
        try:
            text = reader.pages[actual_page - 1].extract_text() or ""
        except Exception:
            text = ""
        text = _normalize_text(text)
        if not text:
            continue
        units.append(
            NavigationUnit(
                resource_id=resource.id,
                chapter_id=chapter.id,
                unit_id=f"page:{actual_page}",
                heading_path=chapter.path or [chapter.title],
                order_index=actual_page,
                text=text,
                text_hash=_text_hash(text),
                page_range=str(actual_page),
                text_source="pdf_page",
            )
        )
    return units


def _find_section_in_units(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    reference: StructuredReference,
    units: list[NavigationUnit],
    *,
    query: str,
) -> NavigationResult | None:
    start = _find_reference_position(units, reference, title_hint=reference.title_hint, query=query)
    if start is None:
        return None
    start_index, start_offset, start_title, start_score = start
    end = _find_next_reference_position(units, reference, start_index=start_index, start_offset=start_offset)
    if end is None:
        end_index, end_offset, end_label = len(units) - 1, len(units[-1].text), None
    else:
        end_index, end_offset, end_label = end
    if end_index < start_index or (end_index == start_index and end_offset <= start_offset):
        return None
    extracted = _extract_unit_range(units, start_index, start_offset, end_index, end_offset)
    extracted = _normalize_text(extracted)
    if not extracted:
        return None
    page_range = _merge_page_ranges(units[start_index].page_range, units[end_index].page_range)
    score = start_score + _chapter_match_bonus(chapter, reference) + min(len(extracted), 2400) / 24000
    return NavigationResult(
        resource=resource,
        chapter=chapter,
        reference=reference,
        start_unit=units[start_index],
        end_unit=units[end_index],
        extracted_text=extracted[:_MAX_REFERENCE_CHARS],
        page_range=page_range,
        start_label=start_title or reference.label,
        end_label=end_label,
        score=score,
    )


def _find_reference_position(
    units: list[NavigationUnit],
    reference: StructuredReference,
    *,
    title_hint: str,
    query: str,
) -> tuple[int, int, str, float] | None:
    best: tuple[int, int, str, float] | None = None
    for unit_index, unit in enumerate(units):
        for match in _section_pattern(reference.label).finditer(unit.text):
            if not _looks_like_heading_occurrence(unit.text, match.start(), match.end()):
                continue
            tail = _normalize_text(unit.text[match.end() : match.end() + 80])
            title = _heading_title_from_tail(tail)
            score = 1.0
            if title_hint and _compact(title_hint) and _compact(title_hint) in _compact(tail):
                score += 0.35
            elif title and _compact(title) in _compact(query):
                score += 0.2
            if match.start() < 40 or unit.text[max(0, match.start() - 2) : match.start()].strip() == "":
                score += 0.12
            if best is None or score > best[3]:
                best = (unit_index, match.start(), title, score)
    return best


def _find_next_reference_position(
    units: list[NavigationUnit],
    reference: StructuredReference,
    *,
    start_index: int,
    start_offset: int,
) -> tuple[int, int, str] | None:
    pattern = _section_pattern(reference.next_label)
    for unit_index in range(start_index, len(units)):
        text = units[unit_index].text
        search_from = start_offset + 1 if unit_index == start_index else 0
        for match in pattern.finditer(text, search_from):
            if _looks_like_heading_occurrence(text, match.start(), match.end()):
                title = _heading_title_from_tail(_normalize_text(text[match.end() : match.end() + 80]))
                return unit_index, match.start(), title or reference.next_label
    return None


def _section_pattern(label: str) -> re.Pattern[str]:
    primary, secondary = label.split(".", 1)
    return re.compile(rf"(?<!\d){re.escape(primary)}\s*[.．]\s*{re.escape(secondary)}(?!\s*[.．]\s*\d)")


def _looks_like_heading_occurrence(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 24) : start].strip()
    after = text[end : min(len(text), end + 3)].strip()
    if before and before[-1] in _HEADING_GUARD_BEFORE:
        return False
    if before and any(before.lower().endswith(word) for word in _HEADING_GUARD_WORDS):
        return False
    if after and after[0] in "）)]":
        return False
    if before.endswith(("图", "表", "式")):
        return False
    tail = text[end : min(len(text), end + 60)]
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", tail):
        return False
    return True


def _extract_unit_range(
    units: list[NavigationUnit],
    start_index: int,
    start_offset: int,
    end_index: int,
    end_offset: int,
) -> str:
    parts: list[str] = []
    for unit_index in range(start_index, end_index + 1):
        text = units[unit_index].text
        if unit_index == start_index and unit_index == end_index:
            parts.append(text[start_offset:end_offset])
        elif unit_index == start_index:
            parts.append(text[start_offset:])
        elif unit_index == end_index:
            parts.append(text[:end_offset])
        else:
            parts.append(text)
    return "\n\n".join(part.strip() for part in parts if part.strip())


def _navigation_match(result: NavigationResult) -> ResourceMatch:
    excerpt = _compact_text(result.extracted_text, limit=760)
    target_title = _target_section_title(result)
    return ResourceMatch(
        resource_id=result.resource.id,
        chapter_id=result.chapter.id,
        segment_id=result.start_unit.unit_id if _is_segment_id(result.start_unit.unit_id) else None,
        resource_name=result.resource.name,
        chapter_title=target_title,
        heading_path=[*result.start_unit.heading_path, target_title],
        excerpt=excerpt,
        before_text="",
        after_text="",
        text_hash=result.text_hash,
        page_range=result.page_range,
        text_source="page_navigator",
        reason="由资料导航器按结构编号定位：先命中章节，再在正文页窗内找到目标小节边界。",
        evidence=[
            ResourceMatchEvidence(label="导航模式", value="page_navigator"),
            ResourceMatchEvidence(label="结构编号", value=result.reference.label),
            ResourceMatchEvidence(label="起始标题", value=result.start_label),
            *(
                [ResourceMatchEvidence(label="结束边界", value=f"{result.end_label} 之前")]
                if result.end_label
                else []
            ),
            *([ResourceMatchEvidence(label="页码", value=result.page_range)] if result.page_range else []),
            ResourceMatchEvidence(label="正文片段", value=excerpt),
        ],
        score_breakdown={"navigator": round(result.score, 3)},
        score=1.0,
        is_high_overlap=True,
    )


def _target_section_title(result: NavigationResult) -> str:
    label = result.reference.label
    title = result.start_label.strip()
    if not title or title == label:
        return label
    if _compact(title).startswith(_compact(label)):
        return title
    return f"{label} {title}"


def _reference_chunks(result: NavigationResult, heading_path: list[str]) -> list[ResourceContextChunk]:
    chunks: list[ResourceContextChunk] = []
    for index, text in enumerate(_split_reference_text(result.extracted_text), start=1):
        chunks.append(
            ResourceContextChunk(
                title=f"{result.reference.label} / 正文证据 {index}",
                excerpt=text,
                teaching_hint="这是资料导航器按结构编号定位后抽出的目标小节正文证据。",
                segment_id=result.start_unit.unit_id if _is_segment_id(result.start_unit.unit_id) else None,
                heading_path=heading_path,
                text_hash=_text_hash(text),
                page_range=result.page_range,
                text_source="page_navigator",
            )
        )
    return chunks


def _split_reference_text(text: str) -> list[str]:
    compact = _compact_text(text, limit=_MAX_REFERENCE_CHARS)
    if len(compact) <= 1200:
        return [compact]
    chunks: list[str] = []
    cursor = 0
    while cursor < len(compact) and len(chunks) < 4:
        end = min(len(compact), cursor + 1200)
        split_at = max(compact.rfind("。", cursor, end), compact.rfind("\n", cursor, end))
        if split_at <= cursor + 300:
            split_at = end
        chunks.append(compact[cursor:split_at].strip())
        cursor = split_at + 1
    return [chunk for chunk in chunks if chunk]


def _title_hint_after_reference(tail: str) -> str:
    text = _normalize_text(tail)
    words = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,16}", text[:80])
    hints = [word for word in words if word not in _GENERIC_REFERENCE_WORDS]
    return hints[0] if hints else ""


def _heading_title_from_tail(tail: str) -> str:
    tail = tail.strip(" ：:.-—_")
    match = re.match(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9（）() -]{0,28})", tail)
    return _normalize_text(match.group(1)) if match else ""


def _chapter_match_bonus(chapter: LibraryChapter, reference: StructuredReference) -> float:
    return 0.3 if _chapter_number(chapter.title) == reference.primary else 0.0


def _chapter_number(title: str) -> int | None:
    match = re.search(r"第\s*([一二三四五六七八九十百零〇两\d]{1,8})\s*[章节編编部]", title)
    if match:
        value = match.group(1)
        if value.isdigit():
            return int(value)
        return _chinese_number_to_int(value)
    match = re.search(r"\bchapter\s+(\d{1,3})\b", title, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.match(r"\s*(\d{1,3})(?:\s+|[.．]\s+|$)", title)
    if match:
        return int(match.group(1))
    return None


def _chinese_number_to_int(value: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for char in value:
        if char not in digits:
            return None
        total = total * 10 + digits[char]
    return total


def _merge_page_ranges(first: str | None, last: str | None) -> str | None:
    if not first:
        return last
    if not last or first == last:
        return first
    first_start = first.split("-", 1)[0]
    last_end = last.rsplit("-", 1)[-1]
    return f"{first_start}-{last_end}"


def _normalize_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _compact_text(text: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()[:16]


def _is_segment_id(unit_id: str | None) -> bool:
    return bool(unit_id and not unit_id.startswith("page:"))
