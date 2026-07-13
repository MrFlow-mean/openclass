from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models import (
    BoardTaskRequirementSheet,
    EvidenceBundle,
    EvidencePurpose,
    LearningRequirementSheet,
    RetrievalEvidence,
    SelectionRef,
)
from app.services.evidence_quality_gate import filter_relevant_local_evidence
from app.services.learning_source_intent import (
    mentioned_ready_source_ids,
    source_intent_requested,
)
from app.services.source_chapter_evidence import (
    explicit_chapter_number,
    explicit_source_chapter_id,
    resolve_verified_chapter_evidence,
)
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store
from app.services.source_structure_store import SourceStructureStore, source_structure_store


CHAT_CHUNK_LIMIT = 4
BOARD_CHUNK_LIMIT = 8
CHAT_TOKEN_BUDGET = 2000
BOARD_TOKEN_BUDGET = 6000
OCR_CHAT_PAGE_LIMIT = 4
OCR_BOARD_PAGE_LIMIT = 12


ResourceResolutionStatus = Literal["matched", "no_match", "ambiguous_source", "content_unavailable"]


@dataclass(frozen=True)
class ResourceResolutionOutcome:
    status: ResourceResolutionStatus
    evidence_bundle: EvidenceBundle | None = None
    metadata: dict[str, object] | None = None


class ResourceResolver:
    def __init__(
        self,
        *,
        adapter: object | None = None,
        store: SourceEvidenceStore = source_evidence_store,
        structure_store: SourceStructureStore | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.structure_store = structure_store or _structure_store_for_source_store(store)

    def should_use_sources(self, message: str) -> bool:
        return source_intent_requested(message)

    def has_ready_sources(self, *, owner_user_id: str, package_id: str) -> bool:
        return bool(self.store.ready_sources(owner_user_id=owner_user_id, package_id=package_id))

    def message_mentions_ready_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        message: str,
    ) -> bool:
        return bool(
            self.ready_source_ids_mentioned(
                owner_user_id=owner_user_id,
                package_id=package_id,
                message=message,
            )
        )

    def ready_source_ids_mentioned(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        message: str,
    ) -> list[str]:
        return mentioned_ready_source_ids(
            message=message,
            ready_sources=self.store.ready_sources(
                owner_user_id=owner_user_id,
                package_id=package_id,
            ),
        )

    def resolve_explicit_source_reference(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        source_chapter_id: str | None = None,
        requirement_run_id: str | None = None,
        purpose: EvidencePurpose = "chat",
    ) -> EvidenceBundle | None:
        query = _learning_query(user_message=user_message, requirements=None)
        if source_chapter_id:
            query = _compact(f"{query} source_chapter_id={source_chapter_id}", 900)
        if not explicit_source_chapter_id(query) and not explicit_chapter_number(query):
            return None
        limit = BOARD_CHUNK_LIMIT if purpose in {"board_generation", "board_edit"} else CHAT_CHUNK_LIMIT
        token_budget = BOARD_TOKEN_BUDGET if purpose in {"board_generation", "board_edit"} else CHAT_TOKEN_BUDGET
        evidence, resolution = self._resolve_verified_chapter(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
        )
        if not evidence or resolution is None:
            return None
        return self._save_bundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            evidence=evidence,
            requirement_run_id=requirement_run_id,
            metadata={
                "resolver": "source_structure_index",
                "retrieval_mode": evidence[0].metadata.get("retrieval_mode", "verified_chapter"),
                "source_reference_resolution": resolution,
            },
        )

    def resolve_for_learning_requirement(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        requirements: LearningRequirementSheet | None,
        requirement_run_id: str | None = None,
        purpose: EvidencePurpose = "board_generation",
        source_reference: SelectionRef | None = None,
    ) -> EvidenceBundle | None:
        return self.resolve_for_learning_requirement_outcome(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            user_message=user_message,
            requirements=requirements,
            requirement_run_id=requirement_run_id,
            purpose=purpose,
            source_reference=source_reference,
        ).evidence_bundle

    def resolve_for_learning_requirement_outcome(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        requirements: LearningRequirementSheet | None,
        requirement_run_id: str | None = None,
        purpose: EvidencePurpose = "board_generation",
        source_reference: SelectionRef | None = None,
    ) -> ResourceResolutionOutcome:
        query = _learning_query(user_message=user_message, requirements=requirements)
        return self._resolve_outcome(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            requirement_run_id=requirement_run_id,
            source_reference=source_reference,
        )

    def preview_for_learning_requirement(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        requirements: LearningRequirementSheet | None,
        topic_hint: str = "",
        purpose: EvidencePurpose = "board_generation",
        source_ingestion_ids: list[str] | tuple[str, ...] | None = None,
        source_reference: SelectionRef | None = None,
    ) -> ResourceResolutionOutcome:
        query = _learning_query(
            user_message=user_message,
            requirements=requirements,
            topic_hint=topic_hint,
        )
        return self._resolve_outcome(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            persist_bundle=False,
            source_ingestion_ids=source_ingestion_ids,
            source_reference=source_reference,
        )

    def bind_preview_bundle_to_requirement(
        self,
        *,
        bundle: EvidenceBundle,
        requirement_run_id: str,
    ) -> EvidenceBundle:
        bound = bundle.model_copy(
            deep=True,
            update={
                "requirement_run_id": requirement_run_id,
                "purpose": "board_generation",
            },
        )
        return self.store.save_bundle(bound)

    def resolve_for_board_task(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        board_task: BoardTaskRequirementSheet,
        board_task_run_id: str | None = None,
        purpose: EvidencePurpose = "board_edit",
        source_reference: SelectionRef | None = None,
    ) -> EvidenceBundle | None:
        query = _board_task_query(user_message=user_message, board_task=board_task)
        return self._resolve_outcome(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            board_task_run_id=board_task_run_id,
            source_reference=source_reference,
        ).evidence_bundle

    def latest_confirmed_bundle(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        purpose: EvidencePurpose | None = None,
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
    ) -> EvidenceBundle | None:
        return self.store.latest_bundle(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            status="confirmed",
            purpose=purpose,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
        )

    def latest_requirement_bundle(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        requirement_run_id: str,
    ) -> EvidenceBundle | None:
        return self.store.latest_requirement_bundle(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            requirement_run_id=requirement_run_id,
        )

    def requirement_bundle_by_id(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        bundle_id: str,
    ) -> EvidenceBundle | None:
        bundle = self.store.get_bundle(owner_user_id=owner_user_id, bundle_id=bundle_id)
        if bundle is None or bundle.package_id != package_id or bundle.lesson_id != lesson_id:
            return None
        return bundle

    def _resolve_outcome(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        query: str,
        purpose: EvidencePurpose,
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
        persist_bundle: bool = True,
        source_ingestion_ids: list[str] | tuple[str, ...] | None = None,
        source_reference: SelectionRef | None = None,
    ) -> ResourceResolutionOutcome:
        ready_sources = self.store.ready_sources(owner_user_id=owner_user_id, package_id=package_id)
        requested_source_ids = {source_id for source_id in source_ingestion_ids or [] if source_id}
        if source_reference is not None and source_reference.kind == "source" and source_reference.source_ingestion_id:
            requested_source_ids = {source_reference.source_ingestion_id}
        if requested_source_ids:
            ready_sources = [source for source in ready_sources if source.id in requested_source_ids]
        if not ready_sources or not query.strip():
            return ResourceResolutionOutcome(status="no_match")
        limit = BOARD_CHUNK_LIMIT if purpose in {"board_generation", "board_edit"} else CHAT_CHUNK_LIMIT
        token_budget = BOARD_TOKEN_BUDGET if purpose in {"board_generation", "board_edit"} else CHAT_TOKEN_BUDGET
        chapter_evidence, chapter_resolution = self._resolve_verified_chapter(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
            source_ingestion_ids=tuple(source.id for source in ready_sources),
            source_reference=source_reference,
        )
        if chapter_evidence:
            bundle = self._create_bundle(
                owner_user_id=owner_user_id,
                package_id=package_id,
                lesson_id=lesson_id,
                query=query,
                purpose=purpose,
                evidence=chapter_evidence,
                requirement_run_id=requirement_run_id,
                board_task_run_id=board_task_run_id,
                metadata={
                    "resolver": "source_structure_index",
                    "retrieval_mode": chapter_evidence[0].metadata.get("retrieval_mode", "verified_chapter"),
                    "source_reference_resolution": chapter_resolution,
                },
                persist=persist_bundle,
            )
            return ResourceResolutionOutcome(status="matched", evidence_bundle=bundle, metadata=chapter_resolution)
        if chapter_resolution is not None:
            resolution_status = str(chapter_resolution.get("status") or "")
            status: ResourceResolutionStatus = (
                "ambiguous_source"
                if resolution_status == "ambiguous"
                else "content_unavailable"
                if resolution_status == "content_unavailable"
                else "no_match"
            )
            return ResourceResolutionOutcome(status=status, metadata=chapter_resolution)
        local_evidence = self.structure_store.chunk_evidence_search(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
            source_ingestion_ids=tuple(source.id for source in ready_sources),
        )
        local_evidence = filter_relevant_local_evidence(query=query, evidence=local_evidence)
        if not local_evidence:
            return ResourceResolutionOutcome(status="no_match")
        bundle = self._create_bundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            evidence=local_evidence,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
            metadata={"resolver": "source_structure_index", "retrieval_mode": "local_chunk_search"},
            persist=persist_bundle,
        )
        return ResourceResolutionOutcome(status="matched", evidence_bundle=bundle)

    def _create_bundle(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        query: str,
        purpose: EvidencePurpose,
        evidence: list[RetrievalEvidence],
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
        persist: bool,
    ) -> EvidenceBundle:
        if persist:
            return self._save_bundle(
                owner_user_id=owner_user_id,
                package_id=package_id,
                lesson_id=lesson_id,
                query=query,
                purpose=purpose,
                evidence=evidence,
                requirement_run_id=requirement_run_id,
                board_task_run_id=board_task_run_id,
                metadata=metadata,
            )
        return EvidenceBundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
            purpose=purpose,
            status="candidate",
            query=query,
            evidence_items=evidence,
            context_text=format_evidence_context(evidence),
            token_count=sum(item.token_count for item in evidence),
            confirmed_by_user=False,
            metadata=metadata or {},
        )

    def _save_bundle(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        query: str,
        purpose: EvidencePurpose,
        evidence: list[RetrievalEvidence],
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> EvidenceBundle:
        context_text = format_evidence_context(evidence)
        bundle = EvidenceBundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
            purpose=purpose,
            status="candidate",
            query=query,
            evidence_items=evidence,
            context_text=context_text,
            token_count=sum(item.token_count for item in evidence),
            confirmed_by_user=False,
            metadata=metadata or {},
        )
        return self.store.save_bundle(bundle)

    def _resolve_verified_chapter(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        limit: int,
        token_budget: int,
        source_ingestion_ids: tuple[str, ...] | None = None,
        source_reference: SelectionRef | None = None,
    ) -> tuple[list[RetrievalEvidence], dict[str, object] | None]:
        return resolve_verified_chapter_evidence(
            source_store=self.store,
            structure_store=self.structure_store,
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
            page_limit=OCR_BOARD_PAGE_LIMIT if token_budget == BOARD_TOKEN_BUDGET else OCR_CHAT_PAGE_LIMIT,
            source_ingestion_ids=source_ingestion_ids,
            source_reference=source_reference,
        )



def format_evidence_context(items: list[RetrievalEvidence]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        location_parts = [
            item.source_title or "未命名资料",
            " > ".join(item.section_path) if item.section_path else "",
            item.page_range,
        ]
        location = " / ".join(part for part in location_parts if part)
        lines.append(
            f"[资料证据 {index}] {location}\n"
            f"来源ID: {item.source_ingestion_id or item.open_notebook_source_id}\n"
            f"摘录: {item.expanded_text or item.excerpt}"
        )
    return "\n\n".join(lines)


def evidence_metadata(bundle: EvidenceBundle | None) -> dict[str, object]:
    if bundle is None:
        return {
            "evidence_bundle_id": None,
            "source_ids": [],
            "chunk_ids": [],
            "confirmed_by_user": False,
        }
    source_ids = [
        item.source_ingestion_id or item.open_notebook_source_id
        for item in bundle.evidence_items
        if item.source_ingestion_id or item.open_notebook_source_id
    ]
    chunk_ids = [chunk_id for item in bundle.evidence_items for chunk_id in item.chunk_ids]
    chapter_ids = [item.chapter_id for item in bundle.evidence_items if item.chapter_id]
    return {
        "evidence_bundle_id": bundle.id,
        "source_ids": source_ids,
        "chunk_ids": chunk_ids,
        "chapter_ids": chapter_ids,
        "confirmed_by_user": bundle.confirmed_by_user,
        "evidence_bundle_status": bundle.status,
        "evidence_purpose": bundle.purpose,
        "source_reference_resolution": bundle.metadata.get("source_reference_resolution"),
    }


def _learning_query(
    *,
    user_message: str,
    requirements: LearningRequirementSheet | None,
    topic_hint: str = "",
) -> str:
    parts = [user_message, topic_hint]
    if requirements is not None:
        parts.extend(
            [
                requirements.learning_goal,
                requirements.theme,
                requirements.boundary,
                " ".join(
                    " ".join(
                        part
                        for part in [
                            reference.source_title,
                            reference.chapter_number,
                            reference.chapter_title,
                            " ".join(reference.section_path),
                        ]
                        if part
                    )
                    for reference in requirements.source_grounding.confirmed_references
                ),
            ]
        )
    return _compact(" ".join(part for part in parts if part), 900)


def _board_task_query(*, user_message: str, board_task: BoardTaskRequirementSheet) -> str:
    parts = [
        user_message,
        board_task.question_or_topic,
        board_task.target_hint,
        board_task.requested_action or "",
    ]
    if board_task.interaction_rule_draft is not None:
        parts.append(board_task.interaction_rule_draft.reference_instruction)
        parts.append(board_task.interaction_rule_draft.interaction_goal)
    return _compact(" ".join(part for part in parts if part), 900)


def _compact(text: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"


def _structure_store_for_source_store(store: SourceEvidenceStore) -> SourceStructureStore:
    if getattr(store, "_path", None) is None:
        return source_structure_store
    return SourceStructureStore(store.path)


resource_resolver = ResourceResolver()
