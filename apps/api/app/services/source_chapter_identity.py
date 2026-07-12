from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.models import SelectionRef, SourceChapter


@dataclass(frozen=True)
class SourceChapterRebinding:
    chapter: SourceChapter | None = None
    matched_anchors: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return self.chapter is None and len(self.candidate_ids) > 1


def stable_source_chapter_id(
    *,
    source_ingestion_id: str,
    parent_path: Iterable[str],
    normalized_number: str,
    title: str,
    level: int,
    source_locator: str,
    order_index: int,
) -> str:
    """Return a stable ID for one chapter in one ingested source structure."""
    identity = "\x1f".join(
        [
            "v1",
            source_ingestion_id.strip(),
            ">".join(_normalize_text(part) for part in parent_path if part.strip()),
            _normalize_number(normalized_number),
            _normalize_text(title),
            str(max(1, level)),
            _normalize_text(source_locator),
            str(max(0, order_index)),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"sourcechapter_{digest}"


def rebind_stale_source_chapter_selection(
    *,
    selection: SelectionRef,
    source_ingestion_id: str,
    chapters: Iterable[SourceChapter],
) -> SourceChapterRebinding:
    """Resolve a stale interactive source selection only with structural anchors.

    This is intentionally limited to the originally selected source. It never
    falls back to semantic retrieval or chooses arbitrarily among candidates.
    """
    if selection.kind != "source" or selection.source_ingestion_id != source_ingestion_id:
        return SourceChapterRebinding()

    verified = [chapter for chapter in chapters if chapter.anchor_status == "verified"]
    if not verified:
        return SourceChapterRebinding()

    locator = _normalize_text(selection.source_locator)
    if locator:
        locator_matches = [
            chapter for chapter in verified if _normalize_text(chapter.source_locator) == locator
        ]
        if len(locator_matches) == 1:
            return SourceChapterRebinding(
                chapter=locator_matches[0],
                matched_anchors=("source_locator",),
                candidate_ids=(locator_matches[0].id,),
            )
        if len(locator_matches) > 1:
            return SourceChapterRebinding(candidate_ids=tuple(chapter.id for chapter in locator_matches))

    expected_number = _normalize_number(selection.source_chapter_number)
    expected_title = _normalize_text(selection.source_chapter_title)
    expected_path = tuple(_normalize_text(part) for part in selection.heading_path if part.strip())
    has_page_bounds = selection.source_page_start is not None or selection.source_page_end is not None

    anchors: list[tuple[str, list[SourceChapter]]] = []
    if expected_number:
        anchors.append(
            (
                "chapter_number",
                [
                    chapter
                    for chapter in verified
                    if _normalize_number(chapter.normalized_number or chapter.number) == expected_number
                ],
            )
        )
    if expected_title:
        anchors.append(
            (
                "chapter_title",
                [chapter for chapter in verified if _normalize_text(chapter.title) == expected_title],
            )
        )
    if expected_path:
        anchors.append(
            (
                "heading_path",
                [
                    chapter
                    for chapter in verified
                    if tuple(_normalize_text(part) for part in chapter.path if part.strip()) == expected_path
                ],
            )
        )
    if has_page_bounds:
        anchors.append(
            (
                "page_bounds",
                [
                    chapter
                    for chapter in verified
                    if chapter.page_start == selection.source_page_start
                    and chapter.page_end == selection.source_page_end
                ],
            )
        )

    # A stale ID is safe to rebind only when two or more independent structural
    # anchors agree on exactly one currently verified chapter.
    if len(anchors) < 2:
        return SourceChapterRebinding()
    matching_ids = set(chapter.id for chapter in anchors[0][1])
    for _anchor_name, candidates in anchors[1:]:
        matching_ids.intersection_update(chapter.id for chapter in candidates)
    candidates = [chapter for chapter in verified if chapter.id in matching_ids]
    if len(candidates) == 1:
        return SourceChapterRebinding(
            chapter=candidates[0],
            matched_anchors=tuple(anchor_name for anchor_name, _candidates in anchors),
            candidate_ids=(candidates[0].id,),
        )
    return SourceChapterRebinding(candidate_ids=tuple(chapter.id for chapter in candidates))


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_number(value: str) -> str:
    normalized = _normalize_text(value)
    parts = [part for part in normalized.split(".") if part]
    if not parts:
        return ""
    if not all(part.isdigit() for part in parts):
        return normalized
    return ".".join(str(int(part)) for part in parts)
