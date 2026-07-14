from __future__ import annotations

import app.services.evidence_workflow as evidence_workflow
from app.models import BoardTaskRequirementSheet, EvidenceBundle, RetrievalEvidence
from app.services.learning_source_reference_service import LearningSourceReferenceError


def _bundle(*, bundle_id: str, status: str) -> EvidenceBundle:
    return EvidenceBundle(
        id=bundle_id,
        owner_user_id="owner_1",
        package_id="package_1",
        lesson_id="lesson_1",
        board_task_run_id="run_1",
        purpose="board_edit",
        status=status,
        evidence_items=[
            RetrievalEvidence(
                source_ingestion_id="source_1",
                source_title="Reference",
                chapter_id="chapter_1",
                section_path=["Section 1"],
                chunk_ids=["chunk_1"],
                excerpt="Relevant excerpt",
                expanded_text="Relevant source text",
                token_count=8,
            )
        ],
        context_text="Relevant source text",
        token_count=8,
    )


def test_stale_confirmed_board_bundle_is_archived_and_replaced_with_candidate(
    monkeypatch,
) -> None:
    confirmed = _bundle(bundle_id="bundle_confirmed", status="confirmed")
    candidate = _bundle(bundle_id="bundle_candidate", status="candidate")
    archived: list[str] = []

    monkeypatch.setattr(
        evidence_workflow.resource_resolver,
        "latest_confirmed_bundle",
        lambda **_kwargs: confirmed,
    )
    monkeypatch.setattr(
        evidence_workflow,
        "validate_bundle_structure_versions",
        lambda _bundle: (_ for _ in ()).throw(
            LearningSourceReferenceError("Source structure changed.")
        ),
    )
    monkeypatch.setattr(
        evidence_workflow.resource_resolver.store,
        "archive_bundle",
        lambda **kwargs: archived.append(kwargs["bundle_id"]),
    )
    monkeypatch.setattr(
        evidence_workflow.resource_resolver,
        "resolve_for_board_task",
        lambda **_kwargs: candidate,
    )

    outcome = evidence_workflow.resolve_board_task_evidence_gate(
        owner_user_id="owner_1",
        package_id="package_1",
        lesson_id="lesson_1",
        user_message="Continue the source-based edit.",
        board_task=BoardTaskRequirementSheet(
            requested_action="edit",
            question_or_topic="Revise the target paragraph",
            progress=100,
        ),
        board_task_run_id="run_1",
        base_chatbot_message="",
    )

    assert archived == ["bundle_confirmed"]
    assert outcome.should_execute is False
    assert outcome.evidence_bundle == candidate
    assert "Relevant excerpt" in outcome.chatbot_message
