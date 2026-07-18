from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.models import (
    EvidenceBundle,
    LearningClarificationStatus,
    LearningRequirementAuxiliaryFactor,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
    LearningSourceGrounding,
    LearningSourceReference,
    Lesson,
    RetrievalEvidence,
    SourceIngestionRecord,
    now_iso,
)
from app.services.source_evidence_store import source_evidence_store


@dataclass(frozen=True)
class OpenNotebookSourcePlan:
    requirement: LearningRequirementSheet
    clarification: LearningClarificationStatus
    teaching_plan: str


class OpenNotebookSourceGroundingError(RuntimeError):
    pass


def resolve_open_notebook_source_plan(
    *,
    owner_user_id: str,
    package_id: str,
    lesson: Lesson,
    source: SourceIngestionRecord,
    query: str,
) -> OpenNotebookSourcePlan:
    if not source.open_notebook_source_id:
        raise OpenNotebookSourceGroundingError(
            "这份资料还没有完成 OpenNotebook 处理，请稍后重试。"
        )

    from app.services.source_ingestion_service import (
        SourceIngestionError,
        source_ingestion_service,
    )

    try:
        raw_results = source_ingestion_service.search_open_notebook(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=8,
            source_ids=[source.id],
        )
    except SourceIngestionError as exc:
        raise OpenNotebookSourceGroundingError(str(exc)) from exc
    evidence = _open_notebook_evidence(source=source, raw_results=raw_results)
    if not evidence:
        raise OpenNotebookSourceGroundingError(
            "OpenNotebook 没有在这份资料中找到与本轮问题相关的正文。"
        )

    bundle = EvidenceBundle(
        owner_user_id=owner_user_id,
        package_id=package_id,
        lesson_id=lesson.id,
        purpose="board_generation",
        status="confirmed",
        query=query,
        evidence_items=evidence,
        context_text=_evidence_context_text(evidence),
        token_count=sum(item.token_count for item in evidence),
        confirmed_by_user=True,
        confirmed_at=now_iso(),
        metadata={
            "origin": "open_notebook_source_search",
            "source_ingestion_id": source.id,
            "open_notebook_source_id": source.open_notebook_source_id,
        },
    )
    source_evidence_store.save_bundle(bundle)
    reference = LearningSourceReference(
        evidence_bundle_id=bundle.id,
        source_ingestion_id=source.id,
        source_title=source.title,
        scope_kind="source",
        section_path=evidence[0].section_path,
        page_range=evidence[0].page_range,
        chunk_ids=_dedupe_chunk_ids(evidence),
        content_hash=_evidence_hash(evidence),
    )
    grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id=bundle.id,
        confirmed_at=bundle.confirmed_at,
        confirmed_references=[reference],
        frozen_evidence=evidence,
    )
    source_label = f"《{source.title}》"
    topic = query.strip() or source.title
    requirement = LearningRequirementSheet(
        teaching_type="knowledge_point",
        learning_content=topic,
        current_level="",
        target_scenario="",
        auxiliary_factors=[
            LearningRequirementAuxiliaryFactor(
                label="confirmed_source",
                value=source_label,
                evidence="open_notebook_source_search",
            )
        ],
        theme=topic,
        learning_goal=f"基于{source_label}回答本轮学习请求并建立可学习的板书。",
        level="",
        known_background="",
        current_questions=[topic],
        learning_need_checklist=["已确认整份资料"],
        target_depth="以 OpenNotebook 返回的相关正文范围为边界。",
        output_preference="结构化 Markdown 板书",
        boundary=source_label,
        board_scope=[source_label],
        success_criteria="内容忠实覆盖 OpenNotebook 命中的资料证据，不添加资料外事实。",
        board_workflow="generate_from_scratch",
        work_mode="knowledge_board",
        granularity="source_range",
        source_grounding=grounding,
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="资料已确认",
        reason="用户已明确引用整份 OpenNotebook 资料，本轮相关正文已冻结。",
        missing_items=[],
        can_start=True,
        summary=f"{source_label} / {topic}",
        key_facts=[
            LearningRequirementKeyFact(
                label="source",
                value=source_label,
                evidence="open_notebook_source_search",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="OpenNotebook 资料",
                is_clear=True,
                evidence="open_notebook_source_search",
            )
        ],
        work_mode="knowledge_board",
        granularity="source_range",
        ready_for_board=True,
    )
    return OpenNotebookSourcePlan(
        requirement=requirement,
        clarification=clarification,
        teaching_plan=(
            "只使用冻结的 OpenNotebook 检索证据，按照证据中的实际结构、定义、关系和例证生成板书。"
        ),
    )


def _open_notebook_evidence(
    *,
    source: SourceIngestionRecord,
    raw_results: list[dict[str, object]],
) -> list[RetrievalEvidence]:
    evidence: list[RetrievalEvidence] = []
    used_tokens = 0
    seen: set[str] = set()
    for raw in raw_results:
        expanded_text = _result_text(
            raw,
            "expanded_text",
            "context",
            "content",
            "text",
            "snippet",
            "chunk",
        )[:3_600]
        if not expanded_text:
            continue
        chunk_id = _result_text(raw, "chunk_id", "id", "embedding_id")
        dedupe_key = f"{chunk_id}:{expanded_text[:160]}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        token_count = max(1, (len(expanded_text) + 3) // 4)
        if used_tokens and used_tokens + token_count > 6_000:
            break
        used_tokens += token_count
        evidence.append(
            RetrievalEvidence(
                source_ingestion_id=source.id,
                open_notebook_source_id=source.open_notebook_source_id,
                source_title=source.title,
                source_uri=source.source_uri,
                section_path=_result_path(raw),
                page_range=_result_page_range(raw),
                chunk_ids=[chunk_id] if chunk_id else [],
                excerpt=expanded_text[:360],
                expanded_text=expanded_text,
                relevance_score=_result_score(raw),
                reason="OpenNotebook search result from the explicitly selected source.",
                token_count=token_count,
                metadata={**raw, "retrieval_mode": "open_notebook_search_only"},
            )
        )
        if len(evidence) >= 8:
            break
    return evidence


def _evidence_context_text(evidence: list[RetrievalEvidence]) -> str:
    return "\n\n".join(
        "\n".join(
            part
            for part in [
                item.source_title,
                " > ".join(item.section_path),
                item.page_range,
                item.expanded_text,
            ]
            if part
        )
        for item in evidence
    )


def _result_text(raw: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    nested = raw.get("source")
    return _result_text(nested, *keys) if isinstance(nested, dict) else ""


def _result_path(raw: dict[str, object]) -> list[str]:
    value = raw.get("section_path") or raw.get("heading_path") or raw.get("path")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [
            part.strip()
            for part in value.replace(">", "/").split("/")
            if part.strip()
        ]
    return []


def _result_page_range(raw: dict[str, object]) -> str:
    page_range = _result_text(raw, "page_range", "pages")
    if page_range:
        return page_range
    page = _result_text(raw, "page", "page_no", "page_number")
    return f"p. {page}" if page else ""


def _result_score(raw: dict[str, object]) -> float:
    for key in ("score", "relevance_score", "similarity"):
        value = raw.get(key)
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            continue
    return 0.0


def _dedupe_chunk_ids(evidence: list[RetrievalEvidence]) -> list[str]:
    return list(
        dict.fromkeys(
            chunk_id
            for item in evidence
            for chunk_id in item.chunk_ids
            if chunk_id
        )
    )


def _evidence_hash(evidence: list[RetrievalEvidence]) -> str:
    content = "\n".join(
        item.expanded_text for item in evidence if item.expanded_text
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
