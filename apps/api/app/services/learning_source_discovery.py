from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from app.models import EvidenceBundle, LearningRequirementSheet, SelectionRef
from app.services.evidence_workflow import evidence_reference_text
from app.services.learning_source_reuse import can_reuse_requirement_bundle
from app.services.resource_resolver import ResourceResolver, resource_resolver


LearningSourceDiscoveryStatus = Literal[
    "not_needed",
    "matched",
    "no_match",
    "ambiguous_source",
    "content_unavailable",
    "no_ready_sources",
]


@dataclass(frozen=True)
class LearningSourceDiscoveryOutcome:
    status: LearningSourceDiscoveryStatus
    attempted: bool
    evidence_bundle: EvidenceBundle | None
    evidence_references: str
    source_requested_by_user: bool
    provisional_bundle: bool
    persisted_this_turn: bool
    metadata: dict[str, object]

    @property
    def context_text(self) -> str:
        # Requirement refinement only needs source identity, location, and a short excerpt.
        # Full candidate text is reserved for the confirmed board-generation path.
        return self.evidence_references


def discover_learning_sources(
    *,
    owner_user_id: str,
    package_id: str,
    lesson_id: str,
    retrieval_user_message: str,
    requirements: LearningRequirementSheet | None,
    active_requirement_run_id: str | None,
    topic_hint: str = "",
    source_requested_by_user: bool = False,
    requested_source_ingestion_ids: list[str] | tuple[str, ...] | None = None,
    source_reference: SelectionRef | None = None,
    pre_resolved_evidence: EvidenceBundle | None = None,
    resolver: ResourceResolver = resource_resolver,
) -> LearningSourceDiscoveryOutcome:
    scoped_source_ids = tuple(
        dict.fromkeys(source_id for source_id in requested_source_ingestion_ids or [] if source_id)
    )
    if requested_source_ingestion_ids is None:
        scoped_source_ids = _ready_source_ids_mentioned(
            resolver,
            owner_user_id=owner_user_id,
            package_id=package_id,
            message=retrieval_user_message,
        )
    source_requested = (
        source_requested_by_user
        or resolver.should_use_sources(retrieval_user_message)
        or bool(scoped_source_ids)
    )
    reusable_bundle = (
        pre_resolved_evidence
        if can_reuse_requirement_bundle(
            pre_resolved_evidence,
            package_id=package_id,
            lesson_id=lesson_id,
            requirement_run_id=active_requirement_run_id,
            retrieval_user_message=retrieval_user_message,
            requested_source_ingestion_ids=scoped_source_ids,
        )
        else None
    )
    reusable_confirmed_bundle = (
        reusable_bundle
        if reusable_bundle is not None
        and reusable_bundle.status == "confirmed"
        and reusable_bundle.confirmed_by_user
        else None
    )
    if not source_requested and reusable_confirmed_bundle is None:
        return LearningSourceDiscoveryOutcome(
            status="not_needed",
            attempted=False,
            evidence_bundle=None,
            evidence_references="",
            source_requested_by_user=False,
            provisional_bundle=False,
            persisted_this_turn=False,
            metadata={
                "status": "not_needed",
                "attempted": False,
                "source_requested": False,
                "auto_triggered": False,
                "purpose": "board_generation",
                "reused_pre_refinement_evidence": False,
                "ignored_unconfirmed_bundle_id": (
                    reusable_bundle.id if reusable_bundle is not None else None
                ),
                "evidence_bundle_id": None,
                "evidence_count": 0,
                "resolution": None,
                "requested_source_ingestion_ids": list(scoped_source_ids),
            },
        )

    if reusable_confirmed_bundle is not None:
        evidence_references = evidence_reference_text(reusable_confirmed_bundle)
        return LearningSourceDiscoveryOutcome(
            status="matched",
            attempted=False,
            evidence_bundle=reusable_confirmed_bundle,
            evidence_references=evidence_references,
            source_requested_by_user=source_requested,
            provisional_bundle=False,
            persisted_this_turn=False,
            metadata={
                "status": "matched",
                "attempted": False,
                "source_requested": source_requested,
                "auto_triggered": False,
                "purpose": "board_generation",
                "reused_pre_refinement_evidence": True,
                "provisional_bundle": False,
                "evidence_bundle_id": reusable_confirmed_bundle.id,
                "evidence_count": len(reusable_confirmed_bundle.evidence_items),
                "resolution": reusable_confirmed_bundle.metadata.get("source_reference_resolution"),
                "requested_source_ingestion_ids": list(scoped_source_ids),
            },
        )

    has_ready_sources = resolver.has_ready_sources(owner_user_id=owner_user_id, package_id=package_id)
    if not has_ready_sources:
        status: LearningSourceDiscoveryStatus = "no_ready_sources" if source_requested else "not_needed"
        return LearningSourceDiscoveryOutcome(
            status=status,
            attempted=False,
            evidence_bundle=None,
            evidence_references="",
            source_requested_by_user=source_requested,
            provisional_bundle=False,
            persisted_this_turn=False,
            metadata={
                "status": status,
                "attempted": False,
                "source_requested": source_requested,
                "auto_triggered": False,
                "purpose": "board_generation",
                "evidence_bundle_id": None,
                "evidence_count": 0,
                "resolution": None,
                "requested_source_ingestion_ids": list(scoped_source_ids),
            },
        )

    evidence_bundle = None
    resolution_metadata: dict[str, object] | None = None
    provisional_bundle = False
    reused_pre_refinement_evidence = False
    if reusable_bundle is not None:
        evidence_bundle = reusable_bundle
        status = "matched"
        reused_pre_refinement_evidence = True
    else:
        resolution = resolver.preview_for_learning_requirement(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            user_message=retrieval_user_message,
            requirements=requirements,
            topic_hint=topic_hint,
            purpose="board_generation",
            source_ingestion_ids=scoped_source_ids,
            source_reference=source_reference,
        )
        evidence_bundle = resolution.evidence_bundle
        status = resolution.status
        resolution_metadata = resolution.metadata
        provisional_bundle = evidence_bundle is not None

    evidence_references = (
        evidence_reference_text(evidence_bundle)
        if evidence_bundle is not None
        else _resolution_reference_text(resolution_metadata)
    )
    metadata: dict[str, object] = {
        "status": status,
        "attempted": True,
        "source_requested": source_requested,
        "auto_triggered": not source_requested,
        "purpose": "board_generation",
        "reused_pre_refinement_evidence": reused_pre_refinement_evidence,
        "provisional_bundle": provisional_bundle,
        "evidence_bundle_id": evidence_bundle.id if evidence_bundle is not None and not provisional_bundle else None,
        "preview_evidence_count": len(evidence_bundle.evidence_items) if evidence_bundle is not None else 0,
        "evidence_count": len(evidence_bundle.evidence_items) if evidence_bundle is not None else 0,
        "resolution": resolution_metadata,
        "requested_source_ingestion_ids": list(scoped_source_ids),
    }
    return LearningSourceDiscoveryOutcome(
        status=status,
        attempted=True,
        evidence_bundle=evidence_bundle,
        evidence_references=evidence_references,
        source_requested_by_user=source_requested,
        provisional_bundle=provisional_bundle,
        persisted_this_turn=False,
        metadata=metadata,
    )


def bind_learning_source_discovery(
    outcome: LearningSourceDiscoveryOutcome,
    *,
    requirement_run_id: str | None,
    resolver: ResourceResolver = resource_resolver,
) -> LearningSourceDiscoveryOutcome:
    if outcome.evidence_bundle is None or not outcome.provisional_bundle:
        return outcome
    if not requirement_run_id:
        return replace(
            outcome,
            evidence_bundle=None,
            provisional_bundle=False,
            metadata={
                **outcome.metadata,
                "provisional_bundle": False,
                "evidence_bundle_id": None,
                "discarded_unbound_bundle": True,
            },
        )
    bound = resolver.bind_preview_bundle_to_requirement(
        bundle=outcome.evidence_bundle,
        requirement_run_id=requirement_run_id,
    )
    return replace(
        outcome,
        evidence_bundle=bound,
        provisional_bundle=False,
        persisted_this_turn=True,
        metadata={
            **outcome.metadata,
            "provisional_bundle": False,
            "evidence_bundle_id": bound.id,
            "evidence_count": len(bound.evidence_items),
        },
    )


def rollback_learning_source_discovery(
    outcome: LearningSourceDiscoveryOutcome | None,
    *,
    resolver: ResourceResolver = resource_resolver,
) -> None:
    if outcome is None or not outcome.persisted_this_turn or outcome.evidence_bundle is None:
        return
    resolver.store.archive_bundle(
        owner_user_id=outcome.evidence_bundle.owner_user_id,
        bundle_id=outcome.evidence_bundle.id,
    )


def _ready_source_ids_mentioned(
    resolver: ResourceResolver,
    *,
    owner_user_id: str,
    package_id: str,
    message: str,
) -> tuple[str, ...]:
    matcher = getattr(resolver, "ready_source_ids_mentioned", None)
    if not callable(matcher):
        return ()
    matched = matcher(
        owner_user_id=owner_user_id,
        package_id=package_id,
        message=message,
    )
    if not isinstance(matched, (list, tuple, set)):
        return ()
    return tuple(dict.fromkeys(str(source_id) for source_id in matched if str(source_id)))


def _resolution_reference_text(metadata: dict[str, object] | None) -> str:
    if not metadata:
        return ""
    candidates = metadata.get("candidates")
    if not isinstance(candidates, list):
        return str(metadata.get("reason") or "")
    lines: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        source_title = str(candidate.get("source_title") or "未命名资料")
        chapter_title = str(candidate.get("chapter_title") or "")
        lines.append(f"{index}. {source_title}{f' / {chapter_title}' if chapter_title else ''}")
    return "\n".join(lines)
