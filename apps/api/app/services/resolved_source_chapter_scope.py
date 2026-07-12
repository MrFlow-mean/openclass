from __future__ import annotations

from dataclasses import dataclass

from app.models import EvidenceBundle, SelectionRef


@dataclass(frozen=True)
class ResolvedSourceChapterScope:
    """A chapter boundary proven by the current verified source resolution."""

    source_ingestion_id: str
    source_chapter_id: str
    chapter_title: str
    chapter_number: str = ""


def resolved_source_chapter_scope(
    *,
    source_reference: SelectionRef | None,
    discovery_status: str,
    evidence_bundle: EvidenceBundle | None,
    discovery_metadata: dict[str, object] | None,
) -> ResolvedSourceChapterScope | None:
    """Return a canonical chapter scope only for an exact verified source match.

    A structured selection or an explicit chapter locator is user intent, while
    the resolver result is the authority for the current source/chapter identity.
    Keeping those checks together prevents a model-produced ``source_chapter``
    label from becoming a ready requirement.
    """

    if (
        discovery_status != "matched"
        or evidence_bundle is None
    ):
        return None
    if source_reference is not None and (
        source_reference.kind != "source"
        or not source_reference.source_ingestion_id
        or not source_reference.source_chapter_id
    ):
        return None

    resolution = _resolution_metadata(evidence_bundle, discovery_metadata)
    if resolution is None or _text(resolution.get("status")) != "matched":
        return None
    source_ingestion_id = _text(resolution.get("source_ingestion_id"))
    source_chapter_id = _text(
        resolution.get("resolved_chapter_id") or resolution.get("chapter_id")
    )
    if not source_chapter_id:
        return None
    if source_reference is not None and source_ingestion_id != source_reference.source_ingestion_id:
        return None
    if source_reference is None and not _has_explicit_chapter_locator(resolution):
        return None

    matched_evidence = next(
        (
            item
            for item in evidence_bundle.evidence_items
            if item.source_ingestion_id == source_ingestion_id
            and (
                _text(item.metadata.get("scope_chapter_id")) == source_chapter_id
                or item.chapter_id == source_chapter_id
            )
        ),
        None,
    )
    if matched_evidence is None:
        return None

    chapter_title = _first_text(
        _text(resolution.get("chapter_title")),
        _text(resolution.get("scope_chapter_title")),
        _text(matched_evidence.metadata.get("scope_chapter_title")),
        source_reference.source_chapter_title if source_reference is not None else "",
    )
    if not chapter_title:
        return None
    return ResolvedSourceChapterScope(
        source_ingestion_id=source_ingestion_id,
        source_chapter_id=source_chapter_id,
        chapter_title=chapter_title,
        chapter_number=_first_text(
            _text(resolution.get("chapter_number")),
            _text(matched_evidence.metadata.get("scope_chapter_number")),
            source_reference.source_chapter_number if source_reference is not None else "",
        ),
    )


def _resolution_metadata(
    evidence_bundle: EvidenceBundle,
    discovery_metadata: dict[str, object] | None,
) -> dict[str, object] | None:
    candidate = discovery_metadata.get("resolution") if discovery_metadata else None
    if not isinstance(candidate, dict):
        candidate = evidence_bundle.metadata.get("source_reference_resolution")
    return candidate if isinstance(candidate, dict) else None


def _first_text(*values: str) -> str:
    return next((value.strip() for value in values if value.strip()), "")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _has_explicit_chapter_locator(resolution: dict[str, object]) -> bool:
    signals = resolution.get("intent_signals")
    return isinstance(signals, list) and "explicit_chapter_locator" in signals
