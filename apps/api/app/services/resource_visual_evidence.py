from __future__ import annotations

import base64
import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.models import (
    BoardDocument,
    LibraryChapter,
    ResourceLibraryItem,
    ResourceReferenceContext,
    ResourceSourceUnit,
    ResourceVisualEvidence,
)
from app.services.rich_document import build_document


_VISUAL_CONTENT_TYPES = {"image", "table", "equation"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_VISUAL_BYTES = 4 * 1024 * 1024
_HEADING_RE = re.compile(r"<h(?P<level>[1-6])\b[^>]*>.*?</h(?P=level)>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_.-]{1,}")


@dataclass(frozen=True)
class _VisualCandidate:
    evidence: ResourceVisualEvidence
    order_index: int
    relevance_score: float


def select_resource_visual_evidence(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    *,
    query: str,
    max_items: int = 2,
) -> list[ResourceVisualEvidence]:
    """Select parser-extracted visual assets that belong to the located chapter."""
    if max_items <= 0 or not resource.source_units:
        return []

    sorted_units = sorted(resource.source_units, key=lambda unit: unit.order_index)
    order_window = _chapter_order_window(resource, chapter, sorted_units)
    has_order_window = order_window is not None
    has_page_window = chapter.page_start is not None or chapter.page_end is not None
    query_terms = _keywords(f"{query}\n{chapter.title}\n{' '.join(chapter.path)}")
    candidates: list[_VisualCandidate] = []

    for index, unit in enumerate(sorted_units):
        if unit.content_type not in _VISUAL_CONTENT_TYPES:
            continue
        image_src = _safe_asset_data_uri(resource, unit)
        if not image_src:
            continue

        in_page_window = _unit_in_chapter_page_window(unit, chapter)
        if has_page_window and not in_page_window:
            continue

        in_order_window = _unit_in_order_window(unit, order_window)
        if has_order_window and not in_order_window:
            continue

        neighbor_text = _neighbor_text(sorted_units, index)
        caption = _caption_for_unit(unit, neighbor_text)
        relevance_text = "\n".join([caption, neighbor_text, unit.text or "", str(unit.metadata or "")])
        overlap = len(query_terms & _keywords(relevance_text))
        score = 0.0
        reasons: list[str] = []
        if in_page_window:
            score += 4.0
            reasons.append("位于命中章节页码范围内")
        if in_order_window:
            score += 3.0
            reasons.append("位于命中章节 source unit 顺序窗口内")
        if overlap:
            score += min(3.0, overlap * 0.75)
            reasons.append("caption 或邻近文字与用户问题相关")
        if caption:
            score += 0.4
        if unit.content_type in {"table", "equation"}:
            score += 0.2

        if not reasons:
            continue

        evidence = ResourceVisualEvidence(
            content_type=unit.content_type,
            caption=caption,
            page_no=unit.page_no,
            page_idx=unit.page_idx,
            bbox=unit.bbox,
            source_locator=unit.source_locator,
            relevance_reason="；".join(reasons),
            relevance_score=round(score, 3),
            image_src=image_src,
        )
        candidates.append(_VisualCandidate(evidence=evidence, order_index=unit.order_index, relevance_score=score))

    candidates.sort(key=lambda item: (-item.relevance_score, item.order_index))
    selected: list[ResourceVisualEvidence] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.evidence.source_locator or candidate.evidence.caption or candidate.evidence.id
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate.evidence)
        if len(selected) >= max_items:
            break
    return selected


def visual_evidence_metadata(visual_evidence: Iterable[ResourceVisualEvidence]) -> list[dict[str, object]]:
    return [
        item.model_dump(mode="json", exclude={"image_src"})
        for item in visual_evidence
        if item.caption or item.source_locator or item.page_no is not None
    ]


def augment_document_with_resource_visual_evidence(
    document: BoardDocument,
    *,
    reference_context: ResourceReferenceContext,
    max_items: int = 2,
) -> BoardDocument:
    visuals = [item for item in getattr(reference_context, "visual_evidence", []) if item.image_src][:max_items]
    if not visuals:
        return document

    additions: list[str] = []
    for visual in visuals:
        marker = _visual_marker(visual)
        if marker in document.content_html:
            continue
        additions.append(_visual_html(reference_context, visual, marker=marker))
    if not additions:
        return document

    visual_section = "\n".join(["<h3>资料图示</h3>", *additions])
    next_html = _insert_visual_section(document.content_html.strip(), visual_section, reference_context.chapter_title)
    return build_document(
        title=document.title,
        content_html=next_html,
        document_id=document.id,
        page_settings=document.page_settings,
    )


def _safe_asset_data_uri(resource: ResourceLibraryItem, unit: ResourceSourceUnit) -> str | None:
    if not unit.asset_path or not resource.parser_artifacts_path:
        return None
    base_path = _resolve_base_path(resource.parser_artifacts_path)
    if base_path is None:
        return None
    asset_path = Path(unit.asset_path).expanduser()
    try:
        candidate = asset_path.resolve() if asset_path.is_absolute() else (base_path / asset_path).resolve()
        candidate.relative_to(base_path)
    except (OSError, RuntimeError, ValueError):
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    try:
        if candidate.stat().st_size > _MAX_VISUAL_BYTES:
            return None
        raw = candidate.read_bytes()
    except OSError:
        return None
    mime_type = mimetypes.guess_type(candidate.name)[0] or ""
    if not mime_type.startswith("image/"):
        return None
    return f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"


def _resolve_base_path(value: str) -> Path | None:
    try:
        base = Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return base if base.is_dir() else None


def _chapter_order_window(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    sorted_units: list[ResourceSourceUnit],
) -> tuple[int, int] | None:
    start = _find_unit_order_for_title(sorted_units, chapter.locator_hint or chapter.title)
    if start is None:
        return None

    end = max(unit.order_index for unit in sorted_units) if sorted_units else start
    next_chapter = _next_sibling_or_parent_chapter(resource, chapter)
    if next_chapter is not None:
        next_start = _find_unit_order_for_title(sorted_units, next_chapter.locator_hint or next_chapter.title)
        if next_start is not None and next_start > start:
            end = next_start - 1
    return start, max(start, end)


def _next_sibling_or_parent_chapter(resource: ResourceLibraryItem, chapter: LibraryChapter) -> LibraryChapter | None:
    ordered = sorted(resource.outline, key=lambda item: item.order_index)
    for candidate in ordered:
        if candidate.order_index <= chapter.order_index:
            continue
        if candidate.level <= chapter.level:
            return candidate
    return None


def _find_unit_order_for_title(sorted_units: list[ResourceSourceUnit], title: str | None) -> int | None:
    normalized_title = _normalize(title or "")
    if not normalized_title:
        return None
    for unit in sorted_units:
        if unit.content_type != "text":
            continue
        text = _normalize(unit.text[:800])
        if normalized_title in text:
            return unit.order_index
    return None


def _unit_in_chapter_page_window(unit: ResourceSourceUnit, chapter: LibraryChapter) -> bool:
    if chapter.page_start is None and chapter.page_end is None:
        return False
    unit_page = unit.page_no
    if unit_page is None and unit.page_idx is not None:
        unit_page = unit.page_idx + 1
    if unit_page is None:
        return False
    start = chapter.page_start or chapter.page_end or unit_page
    end = chapter.page_end or chapter.page_start or unit_page
    return start <= unit_page <= end


def _unit_in_order_window(unit: ResourceSourceUnit, window: tuple[int, int] | None) -> bool:
    if window is None:
        return False
    return window[0] <= unit.order_index <= window[1]


def _neighbor_text(sorted_units: list[ResourceSourceUnit], index: int) -> str:
    parts: list[str] = []
    for offset in (-2, -1, 1, 2):
        neighbor_index = index + offset
        if neighbor_index < 0 or neighbor_index >= len(sorted_units):
            continue
        unit = sorted_units[neighbor_index]
        if unit.content_type == "text" and unit.text.strip():
            parts.append(unit.text.strip())
    return _compact_text("\n".join(parts), limit=900)


def _caption_for_unit(unit: ResourceSourceUnit, neighbor_text: str) -> str:
    for value in (
        unit.text,
        str(unit.metadata.get("caption") or "") if unit.metadata else "",
        str(unit.metadata.get("title") or "") if unit.metadata else "",
        neighbor_text,
    ):
        caption = _compact_text(value, limit=160)
        if caption:
            return caption
    return ""


def _visual_html(reference_context: ResourceReferenceContext, visual: ResourceVisualEvidence, *, marker: str) -> str:
    page_text = f"，页码 {visual.page_no}" if visual.page_no is not None else ""
    caption = visual.caption or _content_type_label(visual.content_type)
    source = f"{reference_context.resource_name} / {reference_context.chapter_title}{page_text}"
    alt = html.escape(caption[:120] or source, quote=True)
    escaped_marker = html.escape(marker, quote=True)
    return "\n".join(
        [
            f'<p data-openclass-resource-visual="{escaped_marker}">{html.escape(caption)}（来源：{html.escape(source)}）</p>',
            f'<img src="{html.escape(visual.image_src, quote=True)}" alt="{alt}" />',
        ]
    )


def _insert_visual_section(content_html: str, visual_section: str, chapter_title: str) -> str:
    if not content_html:
        return visual_section
    normalized_chapter = _normalize(chapter_title)
    for match in _HEADING_RE.finditer(content_html):
        heading_text = _normalize(_TAG_RE.sub("", match.group(0)))
        if normalized_chapter and (normalized_chapter in heading_text or heading_text in normalized_chapter):
            return f"{content_html[:match.end()]}\n{visual_section}\n{content_html[match.end():]}"
    first_heading = _HEADING_RE.search(content_html)
    if first_heading:
        return f"{content_html[:first_heading.end()]}\n{visual_section}\n{content_html[first_heading.end():]}"
    return "\n".join(part for part in [content_html, visual_section] if part.strip())


def _visual_marker(visual: ResourceVisualEvidence) -> str:
    return visual.source_locator or visual.caption or visual.id


def _content_type_label(content_type: str) -> str:
    return {"image": "资料图片", "table": "资料表格", "equation": "资料公式"}.get(content_type, "资料视觉素材")


def _keywords(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "") if len(token.strip()) >= 2}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", html.unescape(text or "")).lower()


def _compact_text(text: str, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    return compact[:limit].strip()
