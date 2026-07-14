from __future__ import annotations

import hashlib
import json
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
    LearningSourceVisualReference,
    RetrievalEvidence,
    RetrievalVisualEvidence,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.resource_resolver import evidence_metadata, visual_manifest_hash
from app.services.source_evidence_store import source_evidence_store
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import source_structure_store
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION


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
        updated_requirements = requirements
        if _belongs_to_active_requirement(bundle, history_state):
            if requirements is not None and clarification is not None:
                skipped_grounding = LearningSourceGrounding(
                    requested_by_user=True,
                    confirmation_status="skipped",
                )
                updated_requirements = requirements.model_copy(
                    deep=True,
                    update={"source_grounding": skipped_grounding},
                )
                lesson.learning_requirements = updated_requirements
                stamp = recorder.record_update(
                    requirements=updated_requirements,
                    clarification=clarification,
                    change_summary="用户跳过了本轮候选资料证据。",
                    change_kind_override="source_reference_declined",
                    metadata={"evidence_bundle_id": bundle.id},
                )
            else:
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
                    "active_requirement_sheet_after": (
                        updated_requirements.model_dump(mode="json")
                        if updated_requirements is not None
                        else None
                    ),
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
            active_requirement_sheet=updated_requirements,
            requirement_run_id=recorder.snapshot.run_id,
            requirement_version_id=recorder.snapshot.latest_version_id,
            requirement_phase=recorder.snapshot.status,
        )

    validate_bundle_structure_versions(bundle)
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
                source_visual_index_version=view.structure.visual_index_version if view.structure else 0,
                content_hash=_content_hash(expanded_text or representative.excerpt),
            )
        )
    return _attach_visual_references(references, bundle)


def validate_bundle_structure_versions(bundle: EvidenceBundle) -> None:
    snapshots = bundle.metadata.get("source_structure_snapshots")
    if bundle.visual_items and visual_manifest_hash(bundle.visual_items) != str(
        bundle.metadata.get("visual_manifest_hash") or ""
    ):
        raise LearningSourceReferenceError("候选资料的视觉清单已经变化，请重新选择资料。")

    items_by_source: dict[str, list[RetrievalVisualEvidence]] = defaultdict(list)
    for item in bundle.visual_items:
        items_by_source[item.source_ingestion_id].append(item)
    source_ids = {
        item.source_ingestion_id
        for item in bundle.evidence_items
        if item.source_ingestion_id
    } | set(items_by_source)
    if source_ids and not isinstance(snapshots, dict):
        for source_id in source_ids:
            source = source_evidence_store.get_source(
                owner_user_id=bundle.owner_user_id,
                package_id=bundle.package_id,
                source_id=source_id,
            )
            if source is not None and source.status == "ready":
                SourceStructureIndexer(store=source_structure_store).ensure_structure(source)
        raise LearningSourceReferenceError("候选资料缺少视觉索引版本，请重新选择资料。")
    snapshots = snapshots if isinstance(snapshots, dict) else {}
    for source_id in source_ids:
        visual_items = items_by_source.get(source_id, [])
        source = source_evidence_store.get_source(
            owner_user_id=bundle.owner_user_id,
            package_id=bundle.package_id,
            source_id=source_id,
        )
        snapshot = snapshots.get(source_id)
        if source is None or source.status != "ready" or not isinstance(snapshot, dict):
            raise LearningSourceReferenceError("候选资料当前不可用，请重新选择资料。")
        structure = SourceStructureIndexer(store=source_structure_store).ensure_structure(source)
        if (
            structure is None
            or structure.status not in {"ready", "linear_only"}
            or structure.visual_index_status in {"pending", "failed"}
            or structure.visual_index_version != CURRENT_SOURCE_VISUAL_INDEX_VERSION
            or structure.id != str(snapshot.get("structure_id") or "")
            or structure.updated_at != str(snapshot.get("structure_updated_at") or "")
            or int(snapshot.get("visual_index_version") or 0)
            != CURRENT_SOURCE_VISUAL_INDEX_VERSION
        ):
            raise LearningSourceReferenceError("候选资料索引已经重建，请重新选择并确认资料。")
        current_by_id = {
            visual.id: visual
            for visual in source_structure_store.list_visuals(
                owner_user_id=bundle.owner_user_id,
                package_id=bundle.package_id,
                source_id=source_id,
            )
        }
        for candidate in visual_items:
            current = current_by_id.get(candidate.visual_id)
            if (
                current is None
                or current.content_hash != candidate.asset_hash
                or current.position_hash != candidate.anchor_hash
            ):
                raise LearningSourceReferenceError("候选资料中的图表已经变化，请重新选择并确认资料。")


def _attach_visual_references(
    references: list[LearningSourceReference],
    bundle: EvidenceBundle,
) -> list[LearningSourceReference]:
    if not references or not bundle.visual_items:
        return references
    assigned: dict[int, list[LearningSourceVisualReference]] = defaultdict(list)
    for visual in bundle.visual_items:
        source_indexes = [
            index
            for index, reference in enumerate(references)
            if reference.source_ingestion_id == visual.source_ingestion_id
        ]
        if not source_indexes:
            continue
        target_index = next(
            (
                index
                for index in source_indexes
                if references[index].source_chapter_id == (visual.chapter_id or "")
            ),
            None,
        )
        if target_index is None:
            target_index = next(
                (
                    index
                    for index in source_indexes
                    if references[index].scope_chapter_id == (visual.chapter_id or "")
                ),
                source_indexes[0],
            )
        assigned[target_index].append(
            LearningSourceVisualReference(
                visual_id=visual.visual_id,
                asset_hash=visual.asset_hash,
                anchor_hash=visual.anchor_hash,
            )
        )

    updated: list[LearningSourceReference] = []
    for index, reference in enumerate(references):
        visual_references = sorted(assigned.get(index, []), key=lambda item: item.visual_id)
        updated.append(
            reference.model_copy(
                update={
                    "visual_references": visual_references,
                    "visual_manifest_hash": _frozen_visual_manifest_hash(visual_references),
                }
            )
        )
    return updated


def _frozen_visual_manifest_hash(items: list[LearningSourceVisualReference]) -> str:
    if not items:
        return ""
    payload = json.dumps(
        [item.model_dump(mode="json") for item in sorted(items, key=lambda candidate: candidate.visual_id)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
