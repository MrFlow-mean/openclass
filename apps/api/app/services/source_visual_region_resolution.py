from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from collections import defaultdict
from typing import Callable, Protocol, Sequence

from pydantic import BaseModel, Field, ValidationError

from app.models import (
    AgentActivityEvent,
    LearningRequirementSheet,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services.ai_logging import ai_usage_logger
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import source_download_path, source_local_path
from app.services.source_structure_store import source_structure_store
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION
from app.services.source_visual_extraction_pdf import (
    render_pdf_normalized_region,
    render_pdf_visual_clue_page,
    trim_normalized_bbox_before_caption,
)
from app.services.source_visual_storage import (
    persist_source_visual_asset,
    resolve_source_visual_storage_key,
    source_visual_staging,
)


MAX_VISUAL_CLUE_PAGES_PER_REFERENCE = 12
MAX_VISUAL_CLUES_PER_PAGE = 80
MIN_COMPLETE_REGION_AREA = 0.008
MAX_COMPLETE_REGION_AREA = 0.90
MIN_RESOLUTION_CONFIDENCE = 0.72
RESOLVED_REGION_PADDING = 0.008


class VisualClueAnalysisAdapter(Protocol):
    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str: ...


class _FigureRegionCandidate(BaseModel):
    complete: bool = False
    clue_ids: list[str] = Field(default_factory=list)
    bbox: list[float] = Field(default_factory=list)
    caption: str = ""
    description: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class _FigureRegionResponse(BaseModel):
    figures: list[_FigureRegionCandidate] = Field(default_factory=list)


def resolve_visual_clues_for_requirement(
    *,
    adapter: VisualClueAnalysisAdapter,
    requirement: LearningRequirementSheet,
    owner_user_id: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> LearningRequirementSheet:
    """Resolve incomplete PDF objects into verified full visuals for a frozen scope.

    Indexed objects are only location clues. Codex identifies a complete page
    region; the backend validates that region, renders the original PDF pixels,
    persists lineage, and only then promotes it to insertable visual evidence.
    """

    prepared = requirement.model_copy(deep=True)
    grounding = prepared.source_grounding
    if grounding.confirmation_status != "confirmed":
        return prepared

    resolved_evidence: list[SourceVisualEvidence] = []
    updated_references = []
    for reference in grounding.confirmed_references:
        bundle = source_evidence_store.get_bundle(
            owner_user_id=owner_user_id,
            bundle_id=reference.evidence_bundle_id,
        )
        package_id = bundle.package_id if bundle is not None else _reference_package_id(
            reference,
            grounding.frozen_visual_evidence,
        )
        source = source_evidence_store.get_source(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_id=reference.source_ingestion_id,
        )
        if source is None:
            updated_references.append(reference)
            continue
        mime_type = source.mime_type.split(";", 1)[0].strip().lower()
        if mime_type != "application/pdf" and not source.file_name.lower().endswith(".pdf"):
            updated_references.append(reference)
            continue
        path = source_local_path(source) or source_download_path(source)
        if path is None:
            updated_references.append(reference)
            continue

        page_start = reference.page_start
        page_end = reference.page_end
        existing = source_structure_store.visual_evidence_for_scope(
            owner_user_id=owner_user_id,
            package_id=source.package_id,
            source_ingestion_id=source.id,
            chapter_id=reference.source_chapter_id or None,
            page_start=page_start,
            page_end=page_end,
        )
        clues = source_structure_store.visual_clues_for_scope(
            owner_user_id=owner_user_id,
            package_id=source.package_id,
            source_ingestion_id=source.id,
            chapter_id=reference.source_chapter_id or None,
            page_start=page_start,
            page_end=page_end,
        )
        if clues:
            _resolve_pdf_clue_pages(
                adapter=adapter,
                source=source,
                path=path,
                reference=reference,
                clues=clues,
                existing=existing,
                owner_user_id=owner_user_id,
                is_cancelled=is_cancelled,
                on_activity=on_activity,
            )
            existing = source_structure_store.visual_evidence_for_scope(
                owner_user_id=owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                chapter_id=reference.source_chapter_id or None,
                page_start=page_start,
                page_end=page_end,
            )
        resolved_evidence.extend(existing)
        visual_ids = [item.visual_id for item in existing]
        updated_reference = reference.model_copy(update={"visual_ids": visual_ids})
        updated_references.append(updated_reference)
        if bundle is not None:
            source_evidence_store.save_bundle(
                bundle.model_copy(
                    update={
                        "visual_items": existing,
                        "metadata": {
                            **bundle.metadata,
                            "visual_clue_resolution": {
                                "status": "completed",
                                "resolved_visual_ids": visual_ids,
                            },
                        },
                    }
                )
            )

    grounding.confirmed_references = updated_references
    grounding.frozen_visual_evidence = _dedupe_evidence(
        [*grounding.frozen_visual_evidence, *resolved_evidence]
    )
    return prepared


def _reference_package_id(reference, frozen_visuals: Sequence[SourceVisualEvidence]) -> str:
    return next(
        (
            item.package_id
            for item in frozen_visuals
            if item.source_ingestion_id == reference.source_ingestion_id and item.package_id
        ),
        "",
    )


def _resolve_pdf_clue_pages(
    *,
    adapter: VisualClueAnalysisAdapter,
    source,
    path,
    reference,
    clues: list[SourceVisualAsset],
    existing: list[SourceVisualEvidence],
    owner_user_id: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> None:
    by_page: dict[int, list[SourceVisualAsset]] = defaultdict(list)
    for clue in clues:
        if clue.page_start is not None:
            by_page[clue.page_start].append(clue)
    existing_by_page: dict[int, list[SourceVisualEvidence]] = defaultdict(list)
    for item in existing:
        if item.page_start is not None:
            existing_by_page[item.page_start].append(item)

    for page_no in sorted(by_page)[:MAX_VISUAL_CLUE_PAGES_PER_REFERENCE]:
        page_clues = by_page[page_no][:MAX_VISUAL_CLUES_PER_PAGE]
        clue_by_visual_id = {clue.id: clue for clue in page_clues}
        cached_visual_ids = _refresh_cached_page_resolutions(
            source=source,
            path=path,
            reference=reference,
            page_no=page_no,
            clue_by_visual_id=clue_by_visual_id,
            existing=existing_by_page.get(page_no, []),
        )
        unresolved_clues = [clue for clue in page_clues if clue.id not in cached_visual_ids]
        if not unresolved_clues:
            continue
        clue_pairs = [(f"C{index + 1}", clue) for index, clue in enumerate(unresolved_clues)]
        page_clue_ids = {clue.id for clue in page_clues}
        blocking_existing = [
            item
            for item in existing_by_page.get(page_no, [])
            if not page_clue_ids.intersection(
                str(value) for value in item.metadata.get("component_visual_ids", [])
            )
        ]
        rendered = render_pdf_visual_clue_page(path, page_no=page_no, clues=clue_pairs)
        if rendered is None:
            continue
        prompt = _clue_resolution_prompt(
            source_title=source.title,
            page_no=page_no,
            clues=clue_pairs,
            existing=blocking_existing,
        )
        try:
            raw = adapter.analyze_image_batch(
                prompt=prompt,
                image_inputs=[
                    _png_data_url(rendered.original_png),
                    _png_data_url(rendered.clue_map_png),
                ],
                is_cancelled=is_cancelled,
                on_activity=on_activity,
            )
            response = _FigureRegionResponse.model_validate(_extract_json(raw))
        except (RuntimeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            ai_usage_logger.log_event(
                "source_visual_clue_resolution_failed",
                owner_user_id=owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                page_no=page_no,
                error=str(exc),
            )
            continue
        used_clues: set[str] = set()
        clue_map = dict(clue_pairs)
        for candidate in response.figures:
            validated = _validated_candidate_bbox(
                candidate,
                clue_map=clue_map,
                existing=blocking_existing,
                used_clues=used_clues,
            )
            if validated is None:
                continue
            rendered_region = _render_complete_region(
                path=path,
                page_no=page_no,
                bbox=validated,
            )
            if rendered_region is None:
                continue
            validated, crop = rendered_region
            content, width, height = crop
            selected_clues = [clue_map[clue_id] for clue_id in candidate.clue_ids]
            with source_visual_staging():
                asset = _resolved_asset(
                    source=source,
                    reference=reference,
                    page_no=page_no,
                    bbox=validated,
                    candidate=candidate,
                    selected_clues=selected_clues,
                    content=content,
                    width=width,
                    height=height,
                )
                source_structure_store.upsert_resolved_visual_asset(asset)
            used_clues.update(candidate.clue_ids)
            ai_usage_logger.log_event(
                "source_visual_clue_resolved",
                owner_user_id=owner_user_id,
                package_id=source.package_id,
                source_ingestion_id=source.id,
                visual_id=asset.id,
                page_no=page_no,
                component_visual_ids=[item.id for item in selected_clues],
                bbox=validated,
            )


def _refresh_cached_page_resolutions(
    *,
    source,
    path,
    reference,
    page_no: int,
    clue_by_visual_id: dict[str, SourceVisualAsset],
    existing: Sequence[SourceVisualEvidence],
) -> set[str]:
    """Reuse a prior full-page decision and apply newer deterministic crop guards."""

    resolved_clue_ids: set[str] = set()
    for evidence in existing:
        if evidence.metadata.get("pdf_region_type") != "codex_resolved_complete_region":
            continue
        component_ids = [
            str(value)
            for value in evidence.metadata.get("component_visual_ids", [])
            if str(value) in clue_by_visual_id
        ]
        if not component_ids:
            continue
        resolved_clue_ids.update(component_ids)
        expected_id = _resolved_visual_id(
            source_ingestion_id=source.id,
            page_no=page_no,
            component_ids=component_ids,
        )
        rendered_region = _render_complete_region(
            path=path,
            page_no=page_no,
            bbox=evidence.bbox,
        )
        if rendered_region is None:
            continue
        trimmed, crop = rendered_region
        if evidence.visual_id == expected_id and trimmed == evidence.bbox:
            continue
        content, width, height = crop
        selected_clues = [clue_by_visual_id[visual_id] for visual_id in component_ids]
        cached_reason = str(evidence.metadata.get("resolution_reason") or "").strip()
        guard_note = "Backend reapplied the current caption-boundary guard."
        candidate = _FigureRegionCandidate(
            complete=True,
            clue_ids=[],
            bbox=trimmed,
            caption=evidence.caption,
            description=evidence.extracted_text,
            confidence=evidence.confidence,
            reason=(
                cached_reason
                if guard_note in cached_reason
                else f"{cached_reason} {guard_note}".strip()
            ),
        )
        with source_visual_staging():
            asset = _resolved_asset(
                source=source,
                reference=reference,
                page_no=page_no,
                bbox=trimmed,
                candidate=candidate,
                selected_clues=selected_clues,
                content=content,
                width=width,
                height=height,
            )
            source_structure_store.upsert_resolved_visual_asset(asset)
    return resolved_clue_ids


def _render_complete_region(
    *,
    path,
    page_no: int,
    bbox: Sequence[float],
) -> tuple[list[float], tuple[bytes, int, int]] | None:
    trimmed = trim_normalized_bbox_before_caption(path, page_no=page_no, bbox=bbox)
    if not _valid_normalized_bbox(trimmed):
        return None
    crop = render_pdf_normalized_region(path, page_no=page_no, bbox=trimmed)
    if crop is None:
        return None
    partial_trim_ratio = _partial_bottom_content_trim_ratio(crop[0])
    if partial_trim_ratio is not None:
        top, bottom = trimmed[1], trimmed[3]
        adjusted_bottom = top + (bottom - top) * partial_trim_ratio
        adjusted = [trimmed[0], top, trimmed[2], round(adjusted_bottom, 6)]
        rerendered = render_pdf_normalized_region(path, page_no=page_no, bbox=adjusted)
        if rerendered is not None:
            return adjusted, rerendered
    return list(trimmed), crop


def _partial_bottom_content_trim_ratio(content: bytes) -> float | None:
    """Detect a clipped caption fragment touching the crop's bottom edge."""

    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as source:
            image = source.convert("L")
        if image.width < 80 or image.height < 80:
            return None
        sample_width = min(1000, image.width)
        sample_height = max(80, round(image.height * sample_width / image.width))
        if (sample_width, sample_height) != image.size:
            image = image.resize((sample_width, sample_height), Image.Resampling.LANCZOS)
        pixels = list(image.get_flattened_data())
        densities = [
            sum(pixel < 220 for pixel in pixels[y * image.width : (y + 1) * image.width])
            / image.width
            for y in range(image.height)
        ]
        if max(densities[-3:]) < 0.01:
            return None
        minimum_gap = max(5, round(image.height * 0.015))
        search_start = max(1, round(image.height * 0.62))
        runs: list[tuple[int, int]] = []
        run_start: int | None = None
        for index in range(search_start, image.height):
            if densities[index] <= 0.002:
                run_start = index if run_start is None else run_start
            elif run_start is not None:
                if index - run_start >= minimum_gap:
                    runs.append((run_start, index))
                run_start = None
        if not runs:
            return None
        start, end = runs[-1]
        if not any(value >= 0.01 for value in densities[:start]):
            return None
        if not any(value >= 0.01 for value in densities[end:]):
            return None
        ratio = max(0.70, min(0.985, (end - 2) / image.height))
        return round(ratio, 6)
    except Exception:
        return None


def _clue_resolution_prompt(
    *,
    source_title: str,
    page_no: int,
    clues: Sequence[tuple[str, SourceVisualAsset]],
    existing: Sequence[SourceVisualEvidence],
) -> str:
    payload = {
        "task": (
            "The first image is the complete original PDF page. The second is the same page with "
            "numbered boxes around incomplete visual objects. Treat every box only as a location "
            "clue, not as the target crop. Group clues that belong to one logical figure and locate "
            "the complete figure boundary on the original page, including axes, labels, arrows, "
            "nodes, legends, and other essential parts. Exclude surrounding prose and the printed "
            "caption from bbox. If the complete figure cannot be identified safely, omit it."
        ),
        "output_contract": {
            "format": "Return only one JSON object with a figures array.",
            "figure_fields": {
                "complete": "true only when no essential visual part is cut off",
                "clue_ids": "one or more IDs from clues",
                "bbox": "[left, top, right, bottom] normalized to 0..1 on the full page",
                "caption": "visible caption text if readable",
                "description": "brief visible-content description",
                "confidence": "0..1",
                "reason": "brief boundary evidence",
            },
        },
        "source_title": source_title,
        "page_no": page_no,
        "clues": [
            {
                "clue_id": clue_id,
                "visual_id": clue.id,
                "bbox": clue.bbox,
                "kind": clue.kind,
                "locator": clue.source_locator,
                "caption_hint": clue.caption,
                "object_type": clue.metadata.get("pdf_region_type"),
            }
            for clue_id, clue in clues
        ],
        "already_verified_regions": [
            {"visual_id": item.visual_id, "bbox": item.bbox}
            for item in existing
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _validated_candidate_bbox(
    candidate: _FigureRegionCandidate,
    *,
    clue_map: dict[str, SourceVisualAsset],
    existing: Sequence[SourceVisualEvidence],
    used_clues: set[str],
) -> list[float] | None:
    if (
        not candidate.complete
        or candidate.confidence < MIN_RESOLUTION_CONFIDENCE
        or not candidate.clue_ids
        or len(candidate.bbox) < 4
        or any(clue_id not in clue_map or clue_id in used_clues for clue_id in candidate.clue_ids)
    ):
        return None
    try:
        bbox = [float(value) for value in candidate.bbox[:4]]
    except (TypeError, ValueError):
        return None
    if not _valid_normalized_bbox(bbox):
        return None
    selected = [clue_map[clue_id] for clue_id in candidate.clue_ids]
    clue_union = _bbox_union([item.bbox for item in selected])
    if clue_union is None or not _bbox_contains(bbox, clue_union, tolerance=0.012):
        return None
    padded = [
        max(0.0, min(bbox[0], clue_union[0]) - RESOLVED_REGION_PADDING),
        max(0.0, min(bbox[1], clue_union[1]) - RESOLVED_REGION_PADDING),
        min(1.0, max(bbox[2], clue_union[2]) + RESOLVED_REGION_PADDING),
        min(1.0, max(bbox[3], clue_union[3]) + RESOLVED_REGION_PADDING),
    ]
    area = _bbox_area(padded)
    if area < MIN_COMPLETE_REGION_AREA or area > MAX_COMPLETE_REGION_AREA:
        return None
    if any(_bbox_overlap_ratio(padded, item.bbox) >= 0.82 for item in existing):
        return None
    return [round(value, 6) for value in padded]


def _resolved_asset(
    *,
    source,
    reference,
    page_no: int,
    bbox: list[float],
    candidate: _FigureRegionCandidate,
    selected_clues: list[SourceVisualAsset],
    content: bytes,
    width: int,
    height: int,
) -> SourceVisualAsset:
    storage_key, content_hash = persist_source_visual_asset(content, mime_type="image/png")
    component_ids = sorted(item.id for item in selected_clues)
    position_payload = {
        "source_ingestion_id": source.id,
        "page_no": page_no,
        "bbox": bbox,
        "component_visual_ids": component_ids,
    }
    position_hash = hashlib.sha256(
        json.dumps(position_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    visual_id = _resolved_visual_id(
        source_ingestion_id=source.id,
        page_no=page_no,
        component_ids=component_ids,
    )
    first = min(selected_clues, key=lambda item: item.order_index)
    last = max(selected_clues, key=lambda item: item.order_index)
    return SourceVisualAsset(
        id=visual_id,
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        structure_id=reference.source_structure_id,
        structure_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
        chapter_id=reference.source_chapter_id or first.chapter_id,
        kind="diagram" if any(item.kind == "diagram" for item in selected_clues) else "image",
        source_locator=f"pdf:page:{page_no}:resolved-figure:{position_hash[:16]}",
        page_start=page_no,
        page_end=page_no,
        bbox=bbox,
        before_chunk_id=first.before_chunk_id,
        after_chunk_id=last.after_chunk_id,
        caption=candidate.caption.strip()[:1000],
        extracted_text=candidate.description.strip()[:8000],
        surrounding_text="\n\n".join(
            dict.fromkeys(item.surrounding_text for item in selected_clues if item.surrounding_text)
        )[:8000],
        anchor_status="verified",
        mime_type="image/png",
        storage_key=storage_key,
        asset_path=str(resolve_source_visual_storage_key(storage_key)),
        order_index=first.order_index,
        content_hash=content_hash,
        position_hash=position_hash,
        width=width,
        height=height,
        confidence=candidate.confidence,
        metadata={
            "pdf_region_type": "codex_resolved_complete_region",
            "visual_completeness_verified": True,
            "visual_completeness_status": "verified_from_full_page_clues",
            "component_visual_ids": component_ids,
            "component_source_locators": [item.source_locator for item in selected_clues],
            "resolution_bbox_source": "codex_full_page_bbox_backend_validated",
            "resolution_reason": candidate.reason.strip()[:1200],
            "codex_render_policy": "recreate_simple_or_keep_original",
        },
    )


def _resolved_visual_id(
    *,
    source_ingestion_id: str,
    page_no: int,
    component_ids: Sequence[str],
) -> str:
    stable_identity = hashlib.sha256(
        json.dumps(
            {
                "source_ingestion_id": source_ingestion_id,
                "page_no": page_no,
                "component_visual_ids": sorted(component_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"sourcevisual_{stable_identity[:24]}"


def _png_data_url(content: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}"


def _extract_json(raw: str) -> object:
    stripped = (raw or "").strip()
    if not stripped:
        raise ValueError("Visual clue analysis returned an empty response.")
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            stripped = parts[1].strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _valid_normalized_bbox(bbox: Sequence[float]) -> bool:
    return (
        len(bbox) >= 4
        and all(math.isfinite(value) for value in bbox[:4])
        and 0.0 <= bbox[0] < bbox[2] <= 1.0
        and 0.0 <= bbox[1] < bbox[3] <= 1.0
    )


def _bbox_union(boxes: Sequence[Sequence[float]]) -> list[float] | None:
    valid = [list(map(float, box[:4])) for box in boxes if _valid_normalized_bbox(box)]
    if not valid:
        return None
    return [
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    ]


def _bbox_contains(outer: Sequence[float], inner: Sequence[float], *, tolerance: float) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def _bbox_area(bbox: Sequence[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_overlap_ratio(first: Sequence[float], second: Sequence[float]) -> float:
    if not _valid_normalized_bbox(first) or not _valid_normalized_bbox(second):
        return 0.0
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    return intersection / max(1e-9, min(_bbox_area(first), _bbox_area(second)))


def _dedupe_evidence(items: Sequence[SourceVisualEvidence]) -> list[SourceVisualEvidence]:
    by_id: dict[str, SourceVisualEvidence] = {}
    for item in items:
        by_id[item.visual_id] = item
    return sorted(by_id.values(), key=lambda item: (item.order_index, item.visual_id))
