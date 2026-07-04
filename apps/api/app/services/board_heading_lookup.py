from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import BoardSegment
from app.services.board_segment_index import compact_segment_text


MULTI_LEVEL_HEADING_REF_PATTERN = re.compile(
    r"(?:第\s*)?(?P<label>[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)+)\s*(?:章|节|小节|部分|段)?"
)


@dataclass(frozen=True)
class HeadingLookupMatch:
    segment: BoardSegment
    confidence: float
    reason: str
    score_breakdown: dict[str, float]


def find_heading_lookup_matches(
    *,
    query_text: str,
    scope_hint: str,
    segments: list[BoardSegment],
) -> list[HeadingLookupMatch]:
    headings = [segment for segment in segments if segment.kind == "heading" and segment.text.strip()]
    if not headings:
        return []

    matches = _matches_by_numbered_heading_ref(query_text=query_text, scope_hint=scope_hint, headings=headings)
    if matches:
        return matches
    return _matches_by_exact_heading_text(query_text=query_text, scope_hint=scope_hint, headings=headings)


def heading_reference_label(*, query_text: str, scope_hint: str) -> str:
    refs = _numbered_heading_refs(" ".join([scope_hint or "", query_text or ""]))
    return refs[0] if refs else ""


def _matches_by_numbered_heading_ref(
    *,
    query_text: str,
    scope_hint: str,
    headings: list[BoardSegment],
) -> list[HeadingLookupMatch]:
    refs = _numbered_heading_refs(" ".join([scope_hint or "", query_text or ""]))
    if not refs:
        return []

    matches: list[HeadingLookupMatch] = []
    seen: set[str] = set()
    for ref in refs:
        for heading in headings:
            if heading.segment_id in seen:
                continue
            if not _heading_starts_with_numbered_ref(heading.text, ref):
                continue
            seen.add(heading.segment_id)
            matches.append(
                HeadingLookupMatch(
                    segment=heading,
                    confidence=0.98,
                    reason=f"根据用户给出的多级标题编号 {ref} 精确定位到板书标题。",
                    score_breakdown={"heading_ref_exact": 0.98},
                )
            )
    return matches


def _matches_by_exact_heading_text(
    *,
    query_text: str,
    scope_hint: str,
    headings: list[BoardSegment],
) -> list[HeadingLookupMatch]:
    lookup_text = _heading_text_key(" ".join([scope_hint or "", query_text or ""]))
    if not lookup_text:
        return []

    matches: list[HeadingLookupMatch] = []
    for heading in headings:
        heading_key = _heading_text_key(heading.text)
        if len(heading_key) < 6:
            continue
        if heading_key not in lookup_text:
            continue
        matches.append(
            HeadingLookupMatch(
                segment=heading,
                confidence=0.96,
                reason="根据用户给出的完整标题文本精确定位到板书标题。",
                score_breakdown={"heading_text_exact": 0.96},
            )
        )
    return matches


def _numbered_heading_refs(text: str) -> list[str]:
    compact = compact_segment_text(text, limit=1000)
    refs: list[str] = []
    seen: set[str] = set()
    for match in MULTI_LEVEL_HEADING_REF_PATTERN.finditer(compact):
        ref = _normalize_numbered_ref(match.group("label"))
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    refs.sort(key=lambda item: (item.count("."), len(item)), reverse=True)
    return refs


def _heading_starts_with_numbered_ref(heading_text: str, ref: str) -> bool:
    heading = _normalize_numbered_ref(heading_text)
    if not heading.startswith(ref):
        return False
    if len(heading) == len(ref):
        return True
    return heading[len(ref)] != "."


def _normalize_numbered_ref(value: str) -> str:
    normalized = (value or "").translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    normalized = re.sub(r"\s+", "", normalized)
    match = re.match(r"(?P<label>[0-9]+(?:\.[0-9]+)*)", normalized)
    return match.group("label") if match else ""


def _heading_text_key(value: str) -> str:
    compact = compact_segment_text(value, limit=1200)
    compact = compact.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    return re.sub(r"[\s#>*_`：:，,。；;.!?！？（）()\\/\-]+", "", compact).casefold()
