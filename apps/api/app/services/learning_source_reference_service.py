from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable

from app.models import (
    EvidenceBundle,
    EvidenceConfirmationAction,
    EvidenceConfirmationResult,
    LearningClarificationStatus,
    LearningRequirementSheet,
    LearningSourceGrounding,
    LearningSourceReference,
    RetrievalEvidence,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.resource_resolver import evidence_metadata
from app.services.source_evidence_store import source_evidence_store
from app.services.source_structure_store import source_structure_store


class LearningSourceReferenceError(RuntimeError):
    pass


def apply_evidence_confirmation(
    *,
    owner_user_id: str,
    lesson_id: str,
    bundle_id: str,
    action: EvidenceConfirmationAction,
) -> EvidenceConfirmationResult:
    workspace = workspace_state.load_workspace_for_user(owner_user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    bundle = source_evidence_store.get_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
    if bundle is None or bundle.lesson_id != lesson_id or bundle.package_id != package.id:
        raise LearningSourceReferenceError("Evidence bundle not found.")

    history_state = workspace_state.load_learning_requirement_history_state_for_user(owner_user_id, lesson_id)
    requirements = _history_model(history_state, "latest_sheet_json", LearningRequirementSheet)
    clarification = _history_model(history_state, "latest_clarification_json", LearningClarificationStatus)
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson_id,
        state=history_state,
    )
    original_bundle = bundle.model_copy(deep=True)

    if action == "skip":
        archived = source_evidence_store.archive_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
        if archived is None:
            raise LearningSourceReferenceError("Evidence bundle not found.")
        if _belongs_to_active_requirement(bundle, history_state):
            stamp = recorder.record_event(
                event_type="source_reference_declined",
                change_summary="用户跳过了本轮候选资料证据。",
                metadata={"evidence_bundle_id": bundle.id},
            )
            commit_operations(
                lesson,
                [],
                label="Source reference declined",
                message="Recorded a declined learning source reference",
                new_document=lesson.board_document,
                metadata={
                    "kind": "source_reference_declined",
                    **evidence_metadata(archived),
                    "document_changed": False,
                    "active_requirement_sheet_after": requirements.model_dump(mode="json") if requirements else None,
                    "requirement_run_id": stamp.run_id,
                    "requirement_version_id": stamp.version_id,
                    "requirement_phase": stamp.phase,
                },
            )
            _save_with_rollback(
                owner_user_id=owner_user_id,
                workspace=workspace,
                recorder=recorder,
                original_bundle=original_bundle,
            )
        return EvidenceConfirmationResult(
            evidence_bundle=archived,
            active_requirement_sheet=requirements,
            requirement_run_id=recorder.snapshot.run_id,
            requirement_version_id=recorder.snapshot.latest_version_id,
            requirement_phase=recorder.snapshot.status,
        )

    confirmed = source_evidence_store.confirm_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
    if confirmed is None:
        raise LearningSourceReferenceError("Evidence bundle not found.")

    if not _belongs_to_active_requirement(confirmed, history_state):
        return EvidenceConfirmationResult(evidence_bundle=confirmed)
    if requirements is None or clarification is None:
        source_evidence_store.save_bundle(original_bundle)
        raise LearningSourceReferenceError("Active learning requirement sheet not found.")

    references = _build_confirmed_references(confirmed)
    if not references:
        source_evidence_store.save_bundle(original_bundle)
        raise LearningSourceReferenceError("Confirmed evidence does not contain a usable source location.")
    grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id=confirmed.id,
        confirmed_at=confirmed.confirmed_at,
        confirmed_references=references,
    )
    updated_requirements = _apply_confirmed_source_scope(requirements, grounding)
    lesson.learning_requirements = updated_requirements
    stamp = recorder.record_update(
        requirements=updated_requirements,
        clarification=clarification,
        change_summary="用户确认了用于本轮学习和板书生成的资料位置。",
        change_kind_override="source_reference_confirmed",
        metadata={
            "evidence_bundle_id": confirmed.id,
            "source_ids": _dedupe(reference.source_ingestion_id for reference in references),
            "chapter_ids": _dedupe(reference.source_chapter_id for reference in references),
        },
    )
    commit_operations(
        lesson,
        [],
        label="Source reference confirmed",
        message="Recorded a confirmed learning source reference",
        new_document=lesson.board_document,
        metadata={
            "kind": "source_reference_confirmed",
            **evidence_metadata(confirmed),
            "document_changed": False,
            "active_requirement_sheet_after": updated_requirements.model_dump(mode="json"),
            "requirement_run_id": stamp.run_id,
            "requirement_version_id": stamp.version_id,
            "requirement_phase": stamp.phase,
        },
    )
    _save_with_rollback(
        owner_user_id=owner_user_id,
        workspace=workspace,
        recorder=recorder,
        original_bundle=original_bundle,
    )
    return EvidenceConfirmationResult(
        evidence_bundle=confirmed,
        active_requirement_sheet=updated_requirements,
        requirement_run_id=stamp.run_id,
        requirement_version_id=stamp.version_id,
        requirement_phase=stamp.phase,
    )


def _belongs_to_active_requirement(bundle: EvidenceBundle, history_state: dict[str, object] | None) -> bool:
    return bool(
        bundle.purpose == "board_generation"
        and bundle.requirement_run_id
        and history_state
        and history_state.get("run_id") == bundle.requirement_run_id
        and history_state.get("status") in {"collecting", "ready"}
    )


def _build_confirmed_references(bundle: EvidenceBundle) -> list[LearningSourceReference]:
    grouped: dict[tuple[str, str, tuple[str, ...]], list[RetrievalEvidence]] = defaultdict(list)
    for item in bundle.evidence_items:
        key = (item.source_ingestion_id, item.chapter_id, tuple(item.section_path))
        grouped[key].append(item)

    references: list[LearningSourceReference] = []
    for (source_id, chapter_id, _section_path), items in grouped.items():
        source = source_evidence_store.get_source(
            owner_user_id=bundle.owner_user_id,
            package_id=bundle.package_id,
            source_id=source_id,
        )
        if source is None or source.status != "ready":
            continue
        view = source_structure_store.get_structure_view(source=source, chunk_limit=0)
        chapter = next((candidate for candidate in view.chapters if candidate.id == chapter_id), None)
        representative = items[0]
        expanded_text = "\n\n".join(item.expanded_text.strip() for item in items if item.expanded_text.strip())
        references.append(
            LearningSourceReference(
                evidence_bundle_id=bundle.id,
                source_ingestion_id=source.id,
                source_title=source.title or representative.source_title,
                source_chapter_id=chapter_id,
                chapter_number=(chapter.normalized_number or chapter.number) if chapter else "",
                chapter_title=chapter.title if chapter else (representative.section_path[-1] if representative.section_path else ""),
                scope_kind=str(representative.metadata.get("scope_kind") or "section"),
                scope_chapter_id=str(representative.metadata.get("scope_chapter_id") or chapter_id),
                scope_chapter_number=str(representative.metadata.get("scope_chapter_number") or ""),
                scope_chapter_title=str(representative.metadata.get("scope_chapter_title") or ""),
                section_path=representative.section_path,
                source_locator=(chapter.source_locator if chapter else "")
                or str(representative.metadata.get("source_locator") or ""),
                page_range=representative.page_range,
                page_start=chapter.page_start if chapter else None,
                page_end=chapter.page_end if chapter else None,
                body_start_offset=chapter.body_start_offset if chapter else None,
                body_end_offset=chapter.body_end_offset if chapter else None,
                chunk_ids=_dedupe(chunk_id for item in items for chunk_id in item.chunk_ids),
                source_structure_id=view.structure.id if view.structure else "",
                source_structure_updated_at=view.structure.updated_at if view.structure else "",
                content_hash=_content_hash(expanded_text or representative.excerpt),
            )
        )
    return references


def _apply_confirmed_source_scope(
    requirements: LearningRequirementSheet,
    grounding: LearningSourceGrounding,
) -> LearningRequirementSheet:
    chapter_references = [reference for reference in grounding.confirmed_references if reference.scope_kind == "chapter"]
    scope_ids = {reference.scope_chapter_id for reference in chapter_references if reference.scope_chapter_id}
    if len(scope_ids) != 1:
        return requirements.model_copy(deep=True, update={"source_grounding": grounding})
    scope_title = next(
        (reference.scope_chapter_title.strip() for reference in chapter_references if reference.scope_chapter_title.strip()),
        "",
    )
    if not scope_title:
        return requirements.model_copy(deep=True, update={"source_grounding": grounding})
    section_scope = _dedupe(
        " ".join(part for part in [reference.chapter_number, reference.chapter_title] if part).strip()
        for reference in chapter_references
    )
    return requirements.model_copy(
        deep=True,
        update={
            "theme": scope_title,
            "learning_goal": scope_title,
            "boundary": scope_title,
            "board_scope": section_scope,
            "granularity": "source_chapter",
            "source_grounding": grounding,
        },
    )


def _save_with_rollback(
    *,
    owner_user_id: str,
    workspace,
    recorder: LearningRequirementHistoryRecorder,
    original_bundle: EvidenceBundle,
) -> None:
    try:
        workspace_state.save_workspace_and_learning_requirement_history_for_user(
            owner_user_id,
            workspace,
            learning_requirement_history_operations=recorder.operations,
        )
    except Exception:
        source_evidence_store.save_bundle(original_bundle)
        raise


def _history_model(history_state, key: str, schema):
    if not history_state:
        return None
    raw = history_state.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return schema.model_validate_json(raw)
    except Exception:
        return None


def _content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    return [value for value in values if value and not (value in seen or seen.add(value))]
