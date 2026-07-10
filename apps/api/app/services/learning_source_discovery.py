from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import EvidenceBundle, LearningClarificationStatus, LearningRequirementSheet
from app.services.evidence_workflow import evidence_reference_text
from app.services.openai_course_ai import OpenAICourseAI, openai_course_ai
from app.services.resource_resolver import ResourceResolver, resource_resolver


LearningSourceDiscoveryStatus = Literal["not_needed", "matched", "no_match", "no_ready_sources"]


@dataclass(frozen=True)
class LearningSourceDiscoveryOutcome:
    status: LearningSourceDiscoveryStatus
    attempted: bool
    evidence_bundle: EvidenceBundle | None
    chatbot_message: str
    metadata: dict[str, object]


def run_learning_source_discovery(
    *,
    owner_user_id: str,
    package_id: str,
    lesson_id: str,
    visible_user_message: str,
    retrieval_user_message: str,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    requirement_run_id: str,
    base_chatbot_message: str,
    pre_resolved_evidence: EvidenceBundle | None = None,
    resolver: ResourceResolver = resource_resolver,
    course_ai: OpenAICourseAI = openai_course_ai,
) -> LearningSourceDiscoveryOutcome:
    source_requested = resolver.should_use_sources(retrieval_user_message)
    has_ready_sources = resolver.has_ready_sources(owner_user_id=owner_user_id, package_id=package_id)
    if not has_ready_sources and not source_requested:
        return LearningSourceDiscoveryOutcome(
            status="not_needed",
            attempted=False,
            evidence_bundle=None,
            chatbot_message=base_chatbot_message,
            metadata={"status": "not_needed", "attempted": False, "source_requested": False},
        )

    purpose = "board_generation" if clarification.ready_for_board else "chat"
    evidence_bundle = None
    attempted = False
    if has_ready_sources:
        attempted = True
        if pre_resolved_evidence is not None and purpose == "chat":
            evidence_bundle = pre_resolved_evidence
        else:
            evidence_bundle = resolver.resolve_for_learning_requirement(
                owner_user_id=owner_user_id,
                package_id=package_id,
                lesson_id=lesson_id,
                user_message=retrieval_user_message,
                requirements=requirements,
                requirement_run_id=requirement_run_id if purpose == "board_generation" else None,
                purpose=purpose,
            )
        status: LearningSourceDiscoveryStatus = "matched" if evidence_bundle is not None else "no_match"
    else:
        status = "no_ready_sources"

    evidence_references = evidence_reference_text(evidence_bundle) if evidence_bundle is not None else ""
    reply = course_ai.generate_learning_source_discovery_reply(
        base_chatbot_message=base_chatbot_message,
        user_message=visible_user_message,
        requirement_context=requirements.model_dump(mode="json"),
        clarification_context=clarification.model_dump(mode="json"),
        discovery_status=status,
        evidence_references=evidence_references,
        requires_confirmation=purpose == "board_generation" and evidence_bundle is not None,
    )
    chatbot_message = (reply.chatbot_message if reply else "").strip() or base_chatbot_message
    metadata: dict[str, object] = {
        "status": status,
        "attempted": attempted,
        "source_requested": source_requested,
        "purpose": purpose,
        "reused_pre_refinement_evidence": pre_resolved_evidence is evidence_bundle and evidence_bundle is not None,
        "evidence_bundle_id": evidence_bundle.id if evidence_bundle is not None else None,
        "evidence_count": len(evidence_bundle.evidence_items) if evidence_bundle is not None else 0,
    }
    return LearningSourceDiscoveryOutcome(
        status=status,
        attempted=attempted,
        evidence_bundle=evidence_bundle,
        chatbot_message=chatbot_message,
        metadata=metadata,
    )
