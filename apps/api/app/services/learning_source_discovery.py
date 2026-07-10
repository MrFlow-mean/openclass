from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from app.models import EvidenceBundle, LearningRequirementSheet
from app.services.evidence_workflow import evidence_reference_text
from app.services.resource_resolver import ResourceResolver, resource_resolver
from app.services.source_chapter_evidence import explicit_chapter_number


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
        return self.evidence_bundle.context_text if self.evidence_bundle is not None else self.evidence_references


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
    pre_resolved_evidence: EvidenceBundle | None = None,
    resolver: ResourceResolver = resource_resolver,
) -> LearningSourceDiscoveryOutcome:
    source_requested = source_requested_by_user or resolver.should_use_sources(retrieval_user_message)
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
            },
        )

    evidence_bundle = None
    resolution_metadata: dict[str, object] | None = None
    provisional_bundle = False
    reused_pre_refinement_evidence = False
    if _can_reuse_requirement_bundle(
        pre_resolved_evidence,
        requirement_run_id=active_requirement_run_id,
        retrieval_user_message=retrieval_user_message,
    ):
        evidence_bundle = pre_resolved_evidence
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


def _can_reuse_requirement_bundle(
    bundle: EvidenceBundle | None,
    *,
    requirement_run_id: str | None,
    retrieval_user_message: str,
) -> bool:
    if (
        bundle is None
        or not requirement_run_id
        or bundle.purpose != "board_generation"
        or bundle.requirement_run_id != requirement_run_id
        or bundle.status not in {"candidate", "confirmed"}
    ):
        return False
    requested_number = explicit_chapter_number(retrieval_user_message)
    if not requested_number:
        return True
    evidence_numbers = {
        str(number)
        for item in bundle.evidence_items
        for number in (
            item.metadata.get("chapter_number"),
            item.metadata.get("requested_chapter_number"),
            explicit_chapter_number(" ".join(item.section_path)),
        )
        if number
    }
    return requested_number in evidence_numbers


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
