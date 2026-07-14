from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.models import EvidenceBundle, LearningRequirementSheet, LearningSourceReference, RetrievalEvidence
from app.services.resource_resolver import BOARD_CHUNK_LIMIT, BOARD_TOKEN_BUDGET, format_evidence_context, resource_resolver


class ConfirmedSourceContextError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfirmedSourceContext:
    evidence_bundle: EvidenceBundle | None
    context_text: str
    used_legacy_bundle: bool = False


def load_confirmed_source_context(
    *,
    owner_user_id: str,
    package_id: str,
    lesson_id: str,
    requirement_run_id: str | None,
    requirements: LearningRequirementSheet,
) -> ConfirmedSourceContext:
    grounding = requirements.source_grounding
    if grounding.confirmation_status == "stale":
        raise ConfirmedSourceContextError(
            "引用的资料章节当前不可用，请重新选择或重新解析资料后再生成板书。"
        )
    if grounding.confirmation_status == "skipped":
        return ConfirmedSourceContext(evidence_bundle=None, context_text="")
    if grounding.confirmation_status != "confirmed" or not grounding.confirmed_references:
        latest_bundle = (
            resource_resolver.latest_requirement_bundle(
                owner_user_id=owner_user_id,
                lesson_id=lesson_id,
                requirement_run_id=requirement_run_id,
            )
            if requirement_run_id
            else None
        )
        if latest_bundle is not None and latest_bundle.status == "candidate":
            raise ConfirmedSourceContextError("请先确认或跳过本轮候选资料，再开始生成板书。")
        legacy_bundle = latest_bundle if latest_bundle is not None and latest_bundle.status == "confirmed" else None
        return ConfirmedSourceContext(
            evidence_bundle=legacy_bundle,
            context_text=legacy_bundle.context_text if legacy_bundle else "",
            used_legacy_bundle=legacy_bundle is not None,
        )

    bundle = resource_resolver.store.get_bundle(
        owner_user_id=owner_user_id,
        bundle_id=grounding.confirmed_bundle_id,
    )
    if bundle is None or bundle.status != "confirmed" or not bundle.confirmed_by_user:
        raise ConfirmedSourceContextError("已确认资料证据不存在或状态已经失效，请重新确认资料。")
    if bundle.package_id != package_id or bundle.lesson_id != lesson_id:
        raise ConfirmedSourceContextError("已确认资料不属于当前课程或页面，请重新确认资料。")
    if requirement_run_id and bundle.requirement_run_id != requirement_run_id:
        raise ConfirmedSourceContextError("已确认资料不属于当前学习需求版本，请重新确认资料。")

    hydrated: list[RetrievalEvidence] = []
    for reference in grounding.confirmed_references:
        hydrated.extend(
            _hydrate_reference(
                owner_user_id=owner_user_id,
                package_id=package_id,
                reference=reference,
                bundle=bundle,
            )
        )
    if not hydrated:
        raise ConfirmedSourceContextError("已确认资料位置当前没有可读取正文，请重新解析或重新确认资料。")
    return ConfirmedSourceContext(
        evidence_bundle=bundle,
        context_text=format_evidence_context(hydrated),
    )


def _hydrate_reference(
    *,
    owner_user_id: str,
    package_id: str,
    reference: LearningSourceReference,
    bundle: EvidenceBundle,
) -> list[RetrievalEvidence]:
    source = resource_resolver.store.get_source(
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_id=reference.source_ingestion_id,
    )
    if source is None or source.status != "ready":
        raise ConfirmedSourceContextError(f"资料《{reference.source_title or '未命名资料'}》当前不可用，请重新确认。")
    view = resource_resolver.structure_store.get_structure_view(source=source, chunk_limit=0)
    if reference.source_structure_id:
        if view.structure is None or view.structure.id != reference.source_structure_id:
            raise ConfirmedSourceContextError("资料结构已经变化，请重新确认对应章节。")
        if (
            reference.source_structure_updated_at
            and view.structure.updated_at != reference.source_structure_updated_at
        ):
            raise ConfirmedSourceContextError("资料索引已经重新构建，请重新确认对应章节。")

    snapshot_items = [
        item
        for item in bundle.evidence_items
        if item.source_ingestion_id == reference.source_ingestion_id
        and (
            item.chapter_id == reference.source_chapter_id
            if reference.source_chapter_id
            else item.section_path == reference.section_path
        )
    ]
    if reference.source_chapter_id:
        current_items = resource_resolver.structure_store.chapter_evidence_by_id(
            owner_user_id=owner_user_id,
            package_id=package_id,
            chapter_id=reference.source_chapter_id,
            limit=BOARD_CHUNK_LIMIT,
            token_budget=BOARD_TOKEN_BUDGET,
        )
        if current_items and _reference_hash(current_items) == reference.content_hash:
            return current_items
    if snapshot_items and _reference_hash(snapshot_items) == reference.content_hash:
        return snapshot_items
    raise ConfirmedSourceContextError("已确认章节的正文内容已经变化，请重新确认后再生成板书。")


def _reference_hash(items: list[RetrievalEvidence]) -> str:
    text = "\n\n".join(item.expanded_text.strip() for item in items if item.expanded_text.strip())
    if not text:
        text = "\n\n".join(item.excerpt.strip() for item in items if item.excerpt.strip())
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
