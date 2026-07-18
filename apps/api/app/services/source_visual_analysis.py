from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import re
import threading
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.models import (
    AgentActivityEvent,
    LearningRequirementSheet,
    SourceVisualAsset,
    SourceVisualEvidence,
    now_iso,
)
from app.services.source_structure_store import source_structure_store


CURRENT_SOURCE_VISUAL_ANALYSIS_VERSION = 1
DEFAULT_SOURCE_VISUAL_ANALYSIS_CONCURRENCY = 4
MAX_SOURCE_VISUAL_ANALYSIS_CONCURRENCY = 8
MAX_SOURCE_VISUAL_BYTES = 4 * 1024 * 1024
_SUPPORTED_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


class SourceVisualAnalysisAdapter(Protocol):
    def analyze_image_batch(
        self,
        *,
        prompt: str,
        image_inputs: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_activity: Callable[[AgentActivityEvent], None] | None,
    ) -> str: ...


class SourceVisualAnalysisItem(BaseModel):
    visual_id: str
    description: str
    visible_text: str = ""
    relationships: str = ""
    recommended_handling: Literal["original_asset", "editable_recreation"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SourceVisualAnalysisResponse(BaseModel):
    visuals: list[SourceVisualAnalysisItem] = Field(default_factory=list)


def source_visual_analysis_enabled() -> bool:
    value = os.getenv(
        "OPENCLASS_CODEX_VISUAL_ANALYSIS_ENABLED",
        "1",
    ).strip().lower()
    return value not in {"0", "false", "no", "off"}


def analyze_frozen_source_visuals(
    *,
    adapter: SourceVisualAnalysisAdapter,
    requirement: LearningRequirementSheet,
    owner_user_id: str,
    model: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> LearningRequirementSheet:
    """Ensure every frozen raster visual has one persisted Codex analysis."""
    prepared = requirement.model_copy(deep=True)
    if not source_visual_analysis_enabled():
        return prepared
    visuals = prepared.source_grounding.frozen_visual_evidence
    raster_visuals = [item for item in visuals if not _is_structured_table(item)]
    analyzed: dict[str, SourceVisualEvidence] = {}
    pending: list[tuple[SourceVisualEvidence, SourceVisualAsset, bytes]] = []

    for evidence in raster_visuals:
        stored = _read_verified_visual(owner_user_id=owner_user_id, evidence=evidence)
        if stored is None:
            raise RuntimeError(
                f"Frozen source visual {evidence.visual_id} cannot be safely loaded"
            )
        asset, content = stored
        cached = _cached_analysis(asset)
        if cached is not None:
            analyzed[evidence.visual_id] = _evidence_with_analysis(evidence, cached)
            continue
        pending.append((evidence, asset, content))

    if pending:
        activity_lock = threading.Lock()

        def emit_activity(event: AgentActivityEvent) -> None:
            if on_activity is None:
                return
            with activity_lock:
                on_activity(event)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(_analysis_concurrency(), len(pending)),
            thread_name_prefix="source-visual-codex",
        ) as executor:
            futures = {
                executor.submit(
                    _analyze_one_visual,
                    adapter=adapter,
                    evidence=evidence,
                    asset=asset,
                    content=content,
                    owner_user_id=owner_user_id,
                    model=model,
                    is_cancelled=is_cancelled,
                    on_activity=emit_activity if on_activity is not None else None,
                ): evidence.visual_id
                for evidence, asset, content in pending
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    visual_id, updated = future.result()
                    analyzed[visual_id] = updated
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    prepared.source_grounding.frozen_visual_evidence = [
        analyzed.get(item.visual_id, item) for item in visuals
    ]
    return prepared


def _analyze_one_visual(
    *,
    adapter: SourceVisualAnalysisAdapter,
    evidence: SourceVisualEvidence,
    asset: SourceVisualAsset,
    content: bytes,
    owner_user_id: str,
    model: str,
    is_cancelled: Callable[[], bool] | None,
    on_activity: Callable[[AgentActivityEvent], None] | None,
) -> tuple[str, SourceVisualEvidence]:
    image_input = _image_data_url(asset, content)
    if not image_input:
        raise RuntimeError(
            f"Source visual {evidence.visual_id} has an unsupported image type"
        )
    prompt = json.dumps(
        {
            "task": (
                "Analyze this one source visual. Describe only visible content. Preserve readable "
                "labels, values, axes, arrows, topology, spatial relationships, and qualifications."
            ),
            "output_contract": {
                "format": "Return only one JSON object with a visuals array containing exactly one item.",
                "required_fields": [
                    "visual_id",
                    "description",
                    "visible_text",
                    "relationships",
                    "recommended_handling",
                    "confidence",
                ],
                "recommended_handling": [
                    "original_asset",
                    "editable_recreation",
                ],
            },
            "visuals": [
                {
                    "visual_id": evidence.visual_id,
                    "image_input_index": 0,
                    "kind": evidence.kind,
                    "caption": evidence.caption,
                    "source_locator": evidence.source_locator,
                    "page_start": evidence.page_start,
                    "page_end": evidence.page_end,
                    "slide_no": evidence.slide_no,
                    "sheet_name": evidence.sheet_name,
                }
            ],
        },
        ensure_ascii=False,
    )
    raw = adapter.analyze_image_batch(
        prompt=prompt,
        image_inputs=[image_input],
        is_cancelled=is_cancelled,
        on_activity=on_activity,
    )
    response = _parse_response(raw)
    if len(response.visuals) != 1 or response.visuals[0].visual_id != evidence.visual_id:
        raise RuntimeError(
            "Codex source visual analysis did not return exactly one result for its image"
        )
    result = response.visuals[0]
    if not result.description.strip() or result.confidence <= 0:
        raise RuntimeError(
            f"Codex source visual analysis is incomplete for {evidence.visual_id}"
        )
    payload = {
        "status": "completed",
        "version": CURRENT_SOURCE_VISUAL_ANALYSIS_VERSION,
        "model": model,
        "analyzed_at": now_iso(),
        "content_hash": asset.content_hash,
        "position_hash": asset.position_hash,
        **result.model_dump(mode="json"),
    }
    saved = source_structure_store.save_visual_codex_analysis(
        owner_user_id=owner_user_id,
        package_id=asset.package_id,
        source_ingestion_id=asset.source_ingestion_id,
        visual_id=asset.id,
        content_hash=asset.content_hash,
        position_hash=asset.position_hash,
        analysis=payload,
    )
    if saved is None:
        raise RuntimeError(
            f"Source visual changed before analysis could be saved: {asset.id}"
        )
    return evidence.visual_id, _evidence_with_analysis(evidence, payload)


def _analysis_concurrency() -> int:
    raw = os.getenv(
        "OPENCLASS_CODEX_VISUAL_ANALYSIS_CONCURRENCY",
        str(DEFAULT_SOURCE_VISUAL_ANALYSIS_CONCURRENCY),
    )
    try:
        configured = int(raw)
    except (TypeError, ValueError):
        configured = DEFAULT_SOURCE_VISUAL_ANALYSIS_CONCURRENCY
    return max(1, min(MAX_SOURCE_VISUAL_ANALYSIS_CONCURRENCY, configured))


def analysis_text(analysis: object) -> str:
    if not isinstance(analysis, dict) or analysis.get("status") != "completed":
        return ""
    return "\n".join(
        part
        for part in (
            str(analysis.get("description") or "").strip(),
            str(analysis.get("visible_text") or "").strip(),
            str(analysis.get("relationships") or "").strip(),
        )
        if part
    ).strip()


def _cached_analysis(asset: SourceVisualAsset) -> dict[str, object] | None:
    raw = asset.metadata.get("codex_visual_analysis")
    if not isinstance(raw, dict):
        return None
    if (
        raw.get("status") != "completed"
        or raw.get("version") != CURRENT_SOURCE_VISUAL_ANALYSIS_VERSION
        or raw.get("content_hash") != asset.content_hash
        or raw.get("position_hash") != asset.position_hash
        or not analysis_text(raw)
    ):
        return None
    return raw


def _evidence_with_analysis(
    evidence: SourceVisualEvidence,
    analysis: dict[str, object],
) -> SourceVisualEvidence:
    text = analysis_text(analysis)
    extracted = evidence.extracted_text.strip()
    if text and text not in extracted:
        extracted = "\n\n".join(part for part in (extracted, text) if part)
    return evidence.model_copy(
        update={
            "extracted_text": extracted,
            "metadata": {
                **evidence.metadata,
                "codex_visual_analysis": analysis,
            },
        }
    )


def _read_verified_visual(
    *,
    owner_user_id: str,
    evidence: SourceVisualEvidence,
) -> tuple[SourceVisualAsset, bytes] | None:
    if not evidence.package_id or not evidence.source_ingestion_id or not evidence.visual_id:
        return None
    stored = source_structure_store.read_visual_bytes(
        owner_user_id=owner_user_id,
        package_id=evidence.package_id,
        source_id=evidence.source_ingestion_id,
        visual_id=evidence.visual_id,
    )
    if stored is None:
        return None
    asset, content = stored
    if (
        evidence.anchor_status != "verified"
        or asset.anchor_status != "verified"
        or not evidence.content_hash
        or evidence.content_hash != asset.content_hash
        or not evidence.position_hash
        or evidence.position_hash != asset.position_hash
        or len(content) > MAX_SOURCE_VISUAL_BYTES
    ):
        return None
    return asset, content


def _image_data_url(asset: SourceVisualAsset, content: bytes) -> str:
    mime_type = asset.mime_type.split(";", 1)[0].strip().lower()
    if mime_type not in _SUPPORTED_IMAGE_MIMES:
        return ""
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


def _is_structured_table(evidence: SourceVisualEvidence) -> bool:
    return evidence.kind == "table" and bool(evidence.table_data)


def _parse_response(raw: str) -> SourceVisualAnalysisResponse:
    try:
        return SourceVisualAnalysisResponse.model_validate_json(raw)
    except ValidationError:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S | re.I)
        candidate = fenced.group(1) if fenced else ""
        if not candidate:
            start = raw.find("{")
            end = raw.rfind("}")
            candidate = raw[start : end + 1] if start >= 0 and end > start else ""
        if not candidate:
            raise RuntimeError("Codex source visual analysis returned invalid JSON")
        try:
            return SourceVisualAnalysisResponse.model_validate_json(candidate)
        except ValidationError as exc:
            raise RuntimeError("Codex source visual analysis returned invalid JSON") from exc
