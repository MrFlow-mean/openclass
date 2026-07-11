from __future__ import annotations

import re
from collections.abc import Iterable

from app.models import SourceIngestionRecord


SOURCE_INTENT_PHRASES = (
    "资料",
    "文件",
    "上传",
    "网页",
    "链接",
    "视频",
    "字幕",
    "音频",
)
SOURCE_INTENT_TERMS = (
    "url",
    "source",
    "sources",
    "file",
    "document",
    "reference",
    "youtube",
)
READY_SOURCE_BARE_NAME_MIN_LENGTH = 6
READY_SOURCE_CONTEXT_NAME_MIN_LENGTH = 4


def source_intent_requested(message: str) -> bool:
    lowered = message.casefold()
    if any(phrase in lowered for phrase in SOURCE_INTENT_PHRASES):
        return True
    return any(
        re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", lowered)
        for term in SOURCE_INTENT_TERMS
    )


def mentioned_ready_source_ids(
    *,
    message: str,
    ready_sources: Iterable[SourceIngestionRecord],
) -> list[str]:
    normalized_message = _normalize_source_name(message)
    if not normalized_message:
        return []
    source_intent = source_intent_requested(message)
    matched_source_ids: list[str] = []
    for source in ready_sources:
        for candidate in (source.title, source.file_name):
            if _is_explicit_ready_source_name_match(
                normalized_message=normalized_message,
                normalized_candidate=_normalize_source_name(candidate),
                source_intent=source_intent,
                explicit_name_marker=_has_explicit_source_name_marker(
                    message=message,
                    candidate=candidate,
                ),
            ):
                matched_source_ids.append(source.id)
                break
    return list(dict.fromkeys(matched_source_ids))


def _normalize_source_name(value: str) -> str:
    normalized = re.sub(r"\.[a-z0-9]{1,8}$", "", (value or "").strip().casefold())
    return re.sub(r"[\s《》〈〉\[\]【】()（）._-]+", "", normalized)


def _is_explicit_ready_source_name_match(
    *,
    normalized_message: str,
    normalized_candidate: str,
    source_intent: bool,
    explicit_name_marker: bool,
) -> bool:
    if len(normalized_candidate) < 2 or normalized_candidate not in normalized_message:
        return False
    return (
        explicit_name_marker
        or len(normalized_candidate) >= READY_SOURCE_BARE_NAME_MIN_LENGTH
        or (
            source_intent
            and len(normalized_candidate) >= READY_SOURCE_CONTEXT_NAME_MIN_LENGTH
        )
    )


def _has_explicit_source_name_marker(*, message: str, candidate: str) -> bool:
    normalized_message = message.casefold()
    normalized_candidate = candidate.strip().casefold()
    if not normalized_candidate:
        return False
    if re.search(r"\.[a-z0-9]{1,8}$", normalized_candidate) and normalized_candidate in normalized_message:
        return True
    base_name = re.sub(r"\.[a-z0-9]{1,8}$", "", normalized_candidate).strip()
    return any(
        f"{left}{base_name}{right}" in normalized_message
        for left, right in (
            ("《", "》"),
            ("〈", "〉"),
            ("【", "】"),
            ("[", "]"),
            ('"', '"'),
            ("'", "'"),
        )
    )
