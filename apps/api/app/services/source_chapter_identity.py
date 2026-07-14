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

    anchors: list[tuple[str, list[SourceChapter]]] = []
    locator_matches: list[SourceChapter] = []
    locator = _normalize_text(selection.source_locator)
    if locator:
        locator_matches = [
            chapter for chapter in verified if _normalize_text(chapter.source_locator) == locator
        ]
        if locator_matches:
            anchors.append(("source_locator", locator_matches))

    expected_number = _normalize_number(selection.source_chapter_number)
    expected_title = _normalize_text(selection.source_chapter_title)
    expected_path = tuple(_normalize_text(part) for part in selection.heading_path if part.strip())
    has_page_bounds = selection.source_page_start is not None or selection.source_page_end is not None

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
                    if (
                        selection.source_page_start is None
                        or chapter.page_start == selection.source_page_start
                    )
                    and (
                        selection.source_page_end is None
                        or chapter.page_end == selection.source_page_end
                    )
                ],
            )
        )

    # A shared locator is common when a parent chapter and several descendants
    # begin on the same PDF page. Keep narrowing with every available structural
    # anchor instead of treating that one-to-many match as the final result.
    non_empty_anchors = [(name, candidates) for name, candidates in anchors if candidates]
    if len(non_empty_anchors) < 2:
        has_secondary_anchor_input = bool(
            expected_number or expected_title or expected_path or has_page_bounds
        )
        if len(locator_matches) == 1 and not has_secondary_anchor_input:
            return SourceChapterRebinding(
                chapter=locator_matches[0],
                matched_anchors=("source_locator",),
                candidate_ids=(locator_matches[0].id,),
            )
        return SourceChapterRebinding()

    matches_by_id: dict[str, list[str]] = {}
    for anchor_name, candidates in non_empty_anchors:
        for chapter in candidates:
            matches_by_id.setdefault(chapter.id, []).append(anchor_name)
    strongest_match_count = max((len(names) for names in matches_by_id.values()), default=0)
    if strongest_match_count < 2:
        return SourceChapterRebinding()
    strongest_ids = {
        chapter_id
        for chapter_id, names in matches_by_id.items()
        if len(names) == strongest_match_count
    }
    strongest_candidates = [chapter for chapter in verified if chapter.id in strongest_ids]
    if len(strongest_candidates) == 1:
        chapter = strongest_candidates[0]
        return SourceChapterRebinding(
            chapter=chapter,
            matched_anchors=tuple(matches_by_id[chapter.id]),
            candidate_ids=(chapter.id,),
        )

    content_match = _rebind_by_content_relevance(selection, strongest_candidates)
    if content_match is not None:
        return SourceChapterRebinding(
            chapter=content_match,
            matched_anchors=tuple([*matches_by_id[content_match.id], "content_relevance"]),
            candidate_ids=(content_match.id,),
        )
    return SourceChapterRebinding(candidate_ids=tuple(chapter.id for chapter in strongest_candidates))


def _rebind_by_content_relevance(
    selection: SelectionRef,
    candidates: list[SourceChapter],
) -> SourceChapter | None:
    query_tokens = _content_tokens(_selection_content_text(selection))
    if len(query_tokens) < 2:
        return None
    scored = sorted(
        (
            (_query_token_coverage(query_tokens, _content_tokens(chapter.excerpt)), chapter)
            for chapter in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not scored or scored[0][0] < 0.5:
        return None
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
    if scored[0][0] - runner_up_score < 0.2:
        return None
    return scored[0][1]


def _selection_content_text(selection: SelectionRef) -> str:
    text = _normalize_text(
        " ".join(
            [
                selection.source_excerpt,
                selection.before_text,
                selection.excerpt,
                selection.after_text,
            ]
        )
    )
    structural_labels = [
        selection.source_title,
        selection.source_chapter_number,
        selection.source_chapter_title,
        selection.source_page_range,
        *selection.heading_path,
    ]
    for label in structural_labels:
        normalized_label = _normalize_text(label)
        if normalized_label:
            text = text.replace(normalized_label, " ")
    return " ".join(text.split())


def _content_tokens(value: str) -> set[str]:
    normalized = _normalize_text(value)
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalized):
        if part.isascii():
            if len(part) >= 2:
                tokens.add(part)
            continue
        if len(part) == 1:
            tokens.add(part)
            continue
        tokens.update(part[index : index + 2] for index in range(len(part) - 1))
    return tokens


def _query_token_coverage(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens)


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
