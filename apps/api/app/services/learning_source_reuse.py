from __future__ import annotations

from app.models import EvidenceBundle
from app.services.source_chapter_evidence import explicit_chapter_number, explicit_source_chapter_id


def can_reuse_requirement_bundle(
    bundle: EvidenceBundle | None,
    *,
    package_id: str,
    lesson_id: str,
    requirement_run_id: str | None,
    retrieval_user_message: str,
    requested_source_ingestion_ids: tuple[str, ...] = (),
) -> bool:
    if (
        bundle is None
        or not requirement_run_id
        or bundle.package_id != package_id
        or bundle.lesson_id != lesson_id
        or bundle.purpose != "board_generation"
        or bundle.requirement_run_id != requirement_run_id
        or bundle.status not in {"candidate", "confirmed"}
    ):
        return False
    requested_chapter_id = explicit_source_chapter_id(retrieval_user_message)
    if requested_chapter_id and requested_chapter_id not in _bundle_chapter_ids(bundle):
        return False
    requested_source_ids = {source_id for source_id in requested_source_ingestion_ids if source_id}
    if requested_source_ids and _bundle_source_ids(bundle) != requested_source_ids:
        return False
    requested_number = explicit_chapter_number(retrieval_user_message)
    return not requested_number or requested_number in _bundle_chapter_numbers(bundle)


def _bundle_chapter_ids(bundle: EvidenceBundle) -> set[str]:
    return {
        chapter_id
        for item in bundle.evidence_items
        for chapter_id in (
            item.chapter_id,
            str(item.metadata.get("scope_chapter_id") or ""),
        )
        if chapter_id
    }


def _bundle_source_ids(bundle: EvidenceBundle) -> set[str]:
    return {
        item.source_ingestion_id
        for item in bundle.evidence_items
        if item.source_ingestion_id
    }


def _bundle_chapter_numbers(bundle: EvidenceBundle) -> set[str]:
    return {
        str(number)
        for item in bundle.evidence_items
        for number in (
            item.metadata.get("chapter_number"),
            item.metadata.get("requested_chapter_number"),
            explicit_chapter_number(" ".join(item.section_path)),
        )
        if number
    }
