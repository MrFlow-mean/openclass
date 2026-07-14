from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.models import (
    EvidenceBundle,
    LearningRequirementSheet,
    LearningSourceReference,
    LearningSourceVisualReference,
    RetrievalEvidence,
    RetrievalVisualEvidence,
)
from app.services.resource_resolver import (
    BOARD_CHUNK_LIMIT,
    BOARD_TOKEN_BUDGET,
    format_evidence_context,
    resource_resolver,
    visual_manifest_hash,
)
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION


class ConfirmedSourceContextError(RuntimeError):
    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


@dataclass(frozen=True)
class ConfirmedSourceContext:
    evidence_bundle: EvidenceBundle | None
    context_text: str
    visual_items: tuple[RetrievalVisualEvidence, ...] = ()
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
        if legacy_bundle is not None:
            legacy_manifest_hash = str(
                legacy_bundle.metadata.get("visual_manifest_hash") or ""
            ).strip()
            try:
                legacy_visual_count = int(legacy_bundle.metadata.get("visual_count") or 0)
            except (TypeError, ValueError):
                legacy_visual_count = 1
            if legacy_bundle.visual_items or legacy_manifest_hash or legacy_visual_count:
                raise ConfirmedSourceContextError(
                    "历史资料证据没有冻结视觉清单，请重新确认资料。",
                    stale=True,
                )
            source_ids = {
                item.source_ingestion_id
                for item in legacy_bundle.evidence_items
                if item.source_ingestion_id
            }
            for source_id in source_ids:
                source = resource_resolver.store.get_source(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    source_id=source_id,
                )
                # Legacy text-only bundles predate source identity snapshots. Their
                # frozen context remains usable even after the old source row has
                # disappeared, provided the bundle never carried visual evidence.
                if source is None:
                    if not legacy_bundle.context_text.strip():
                        raise ConfirmedSourceContextError(
                            "历史资料证据当前不可用，请重新确认资料。",
                            stale=True,
                        )
                    continue
                if source.status != "ready":
                    raise ConfirmedSourceContextError(
                        "历史资料证据当前不可用，请重新确认资料。",
                        stale=True,
                    )
                structure = SourceStructureIndexer(
                    store=resource_resolver.structure_store
                ).ensure_structure(source)
                if (
                    structure is None
                    or structure.status not in {"ready", "linear_only"}
                    or structure.visual_index_status in {"pending", "failed"}
                    or structure.visual_index_version != CURRENT_SOURCE_VISUAL_INDEX_VERSION
                ):
                    raise ConfirmedSourceContextError(
                        "历史资料的视觉索引不可用，请重新确认资料。",
                        stale=True,
                    )
            current_visuals = resource_resolver.visual_items_for_evidence(
                owner_user_id=owner_user_id,
                package_id=package_id,
                evidence=legacy_bundle.evidence_items,
            )
            if current_visuals:
                raise ConfirmedSourceContextError(
                    "历史资料证据没有冻结视觉清单，请重新确认资料。",
                    stale=True,
                )
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
    hydrated_visuals: list[RetrievalVisualEvidence] = []
    for reference in grounding.confirmed_references:
        hydrated.extend(
            _hydrate_reference(
                owner_user_id=owner_user_id,
                package_id=package_id,
                reference=reference,
                bundle=bundle,
            )
        )
        hydrated_visuals.extend(
            _hydrate_visual_reference(
                owner_user_id=owner_user_id,
                package_id=package_id,
                reference=reference,
                bundle=bundle,
            )
        )
    if "visual_manifest_hash" not in bundle.metadata:
        raise ConfirmedSourceContextError(
            "已确认资料缺少视觉清单版本，请重新确认资料。",
            stale=True,
        )
    frozen_manifest_hash = str(bundle.metadata.get("visual_manifest_hash") or "")
    if visual_manifest_hash(bundle.visual_items) != frozen_manifest_hash:
        raise ConfirmedSourceContextError(
            "已确认资料的视觉清单校验失败，请重新确认资料。",
            stale=True,
        )
    current_visuals = resource_resolver.visual_items_for_evidence(
        owner_user_id=owner_user_id,
        package_id=package_id,
        evidence=hydrated,
    )
    if (
        {item.visual_id for item in current_visuals}
        != {item.visual_id for item in bundle.visual_items}
        or visual_manifest_hash(current_visuals) != frozen_manifest_hash
    ):
        raise ConfirmedSourceContextError(
            "已确认资料的视觉清单已经变化，请重新确认资料。",
            stale=True,
        )
    if not hydrated:
        raise ConfirmedSourceContextError("已确认资料位置当前没有可读取正文，请重新解析或重新确认资料。")
    unique_visuals = list({item.visual_id: item for item in hydrated_visuals}.values())
    if {item.visual_id for item in unique_visuals} != {item.visual_id for item in bundle.visual_items}:
        raise ConfirmedSourceContextError(
            "已确认资料的视觉清单已经变化，请重新确认资料。",
            stale=True,
        )
    if visual_manifest_hash(unique_visuals) != frozen_manifest_hash:
        raise ConfirmedSourceContextError(
            "已确认资料的视觉清单校验失败，请重新确认资料。",
            stale=True,
        )
    return ConfirmedSourceContext(
        evidence_bundle=bundle,
        context_text=format_evidence_context(hydrated),
        visual_items=tuple(
            sorted(
                unique_visuals,
                key=lambda item: (item.source_ingestion_id, item.order_index, item.visual_id),
            )
        ),
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
        raise ConfirmedSourceContextError(
            f"资料《{reference.source_title or '未命名资料'}》当前不可用，请重新确认。",
            stale=True,
        )
    current_structure = SourceStructureIndexer(
        store=resource_resolver.structure_store
    ).ensure_structure(source)
    if (
        current_structure is None
        or current_structure.status not in {"ready", "linear_only"}
        or current_structure.visual_index_status in {"pending", "failed"}
        or current_structure.visual_index_version != CURRENT_SOURCE_VISUAL_INDEX_VERSION
        or reference.source_visual_index_version != CURRENT_SOURCE_VISUAL_INDEX_VERSION
    ):
        raise ConfirmedSourceContextError(
            "资料视觉索引已经升级或失效，请重新确认对应章节。",
            stale=True,
        )
    view = resource_resolver.structure_store.get_structure_view(source=source, chunk_limit=0)
    if reference.source_structure_id:
        if view.structure is None or view.structure.id != reference.source_structure_id:
            raise ConfirmedSourceContextError("资料结构已经变化，请重新确认对应章节。", stale=True)
        if (
            reference.source_structure_updated_at
            and view.structure.updated_at != reference.source_structure_updated_at
        ):
            raise ConfirmedSourceContextError("资料索引已经重新构建，请重新确认对应章节。", stale=True)
        if view.structure.visual_index_version != reference.source_visual_index_version:
            raise ConfirmedSourceContextError("资料视觉索引已经重新构建，请重新确认对应章节。", stale=True)

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
    raise ConfirmedSourceContextError(
        "已确认章节的正文内容已经变化，请重新确认后再生成板书。",
        stale=True,
    )


def _hydrate_visual_reference(
    *,
    owner_user_id: str,
    package_id: str,
    reference: LearningSourceReference,
    bundle: EvidenceBundle,
) -> list[RetrievalVisualEvidence]:
    if not reference.visual_references:
        return []
    if _visual_reference_hash(reference.visual_references) != reference.visual_manifest_hash:
        raise ConfirmedSourceContextError("已确认资料的视觉引用校验失败，请重新确认资料。", stale=True)
    source = resource_resolver.store.get_source(
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_id=reference.source_ingestion_id,
    )
    if source is None or source.status != "ready":
        raise ConfirmedSourceContextError("已确认资料当前不可用，请重新确认资料。", stale=True)
    view = resource_resolver.structure_store.get_structure_view(source=source, chunk_limit=0)
    current_by_id = {
        visual.id: visual
        for visual in resource_resolver.structure_store.list_visuals(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=reference.source_ingestion_id,
        )
    }
    snapshot_by_id = {
        visual.visual_id: visual
        for visual in bundle.visual_items
        if visual.source_ingestion_id == reference.source_ingestion_id
    }
    hydrated: list[RetrievalVisualEvidence] = []
    for frozen in reference.visual_references:
        current = current_by_id.get(frozen.visual_id)
        snapshot = snapshot_by_id.get(frozen.visual_id)
        if (
            current is None
            or snapshot is None
            or current.content_hash != frozen.asset_hash
            or current.position_hash != frozen.anchor_hash
            or snapshot.asset_hash != frozen.asset_hash
            or snapshot.anchor_hash != frozen.anchor_hash
        ):
            raise ConfirmedSourceContextError("已确认资料中的图表已经变化，请重新确认资料。", stale=True)
        hydrated.append(snapshot)
    return hydrated


def _visual_reference_hash(items: list[LearningSourceVisualReference]) -> str:
    if not items:
        return ""
    payload = json.dumps(
        [item.model_dump(mode="json") for item in sorted(items, key=lambda candidate: candidate.visual_id)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reference_hash(items: list[RetrievalEvidence]) -> str:
    text = "\n\n".join(item.expanded_text.strip() for item in items if item.expanded_text.strip())
    if not text:
        text = "\n\n".join(item.excerpt.strip() for item in items if item.excerpt.strip())
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
