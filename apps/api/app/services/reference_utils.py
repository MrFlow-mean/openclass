from __future__ import annotations

import re

from app.models import ResourceReferenceContext


def _strip_reference_noise(value: str) -> str:
    text = value or ""
    text = re.sub(r"(?:（\d+\s*-\s*\d+）\s*){2,}", "", text)
    text = re.sub(r"\bc\d+；）。max\s*p（x\d+,\d+）。", "", text, flags=re.IGNORECASE)
    return text


def compact_reference_text(value: str, limit: int = 420) -> str:
    compact = re.sub(r"\s+", " ", _strip_reference_noise(value)).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def reference_passages(reference_context: ResourceReferenceContext, *, max_items: int = 4) -> list[str]:
    passages = [
        compact_reference_text(chunk.excerpt)
        for chunk in reference_context.chunks
        if chunk.excerpt.strip()
    ]
    if passages:
        return passages[:max_items]

    raw_passages = [
        compact_reference_text(segment)
        for segment in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", reference_context.full_text)
        if len(segment.strip()) >= 8
    ]
    if raw_passages:
        return raw_passages[:max_items]

    summary = compact_reference_text(reference_context.summary)
    return [summary] if summary else []


def reference_key_points(reference_context: ResourceReferenceContext, *, max_items: int = 5) -> list[str]:
    points = [
        point
        for point in reference_context.teaching_points
        if point.strip() and "不要照搬原文" not in point
    ]
    if points:
        return points[:max_items]

    hints = [
        chunk.teaching_hint
        for chunk in reference_context.chunks
        if chunk.teaching_hint.strip()
    ]
    return hints[:max_items]
