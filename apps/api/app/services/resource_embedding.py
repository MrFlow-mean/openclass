from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass

from openai import OpenAI

from app.models import ResourceSegment
from app.services.ai_logging import ai_usage_logger
from app.services.ai_model_catalog import OPENAI_OFFICIAL_BASE_URL


DEFAULT_RESOURCE_EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass(frozen=True)
class ResourceEmbeddingSpec:
    provider: str
    model: str
    dimensions: int | None = None


@dataclass(frozen=True)
class SegmentEmbeddingRecord:
    resource_id: str
    segment_id: str
    text_hash: str
    provider: str
    model: str
    dimensions: int
    embedding: list[float]


class ResourceEmbeddingService:
    def is_enabled(self) -> bool:
        return _env_truthy("OPENCLASS_RESOURCE_EMBEDDINGS_ENABLED")

    def current_spec(self) -> ResourceEmbeddingSpec:
        return ResourceEmbeddingSpec(
            provider="openai",
            model=os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_RESOURCE_EMBEDDING_MODEL),
            dimensions=_env_optional_int("OPENAI_EMBEDDING_DIMENSIONS"),
        )

    def embed_segments(self, segments: list[ResourceSegment]) -> dict[str, SegmentEmbeddingRecord]:
        if not self.is_enabled():
            return {}
        candidates = [segment for segment in segments if segment.text.strip()]
        if not candidates:
            return {}
        spec = self.current_spec()
        vectors = self._embed_texts([_segment_embedding_input(segment) for segment in candidates], spec=spec)
        records: dict[str, SegmentEmbeddingRecord] = {}
        for segment, vector in zip(candidates, vectors, strict=False):
            if not vector:
                continue
            records[segment.segment_id] = SegmentEmbeddingRecord(
                resource_id=segment.resource_id,
                segment_id=segment.segment_id,
                text_hash=segment.text_hash,
                provider=spec.provider,
                model=spec.model,
                dimensions=len(vector),
                embedding=vector,
            )
        return records

    def embed_query(self, query: str) -> list[float]:
        if not self.is_enabled():
            return []
        compact = _compact_text(query, limit=1800)
        if not compact:
            return []
        spec = self.current_spec()
        vectors = self._embed_texts([compact], spec=spec)
        return vectors[0] if vectors else []

    def _embed_texts(self, texts: list[str], *, spec: ResourceEmbeddingSpec) -> list[list[float]]:
        api_key = _normalize_optional_secret(os.getenv("OPENAI_API_KEY"))
        if not api_key:
            ai_usage_logger.log_event(
                "openai_embedding_skipped",
                model=spec.model,
                reason="missing_openai_api_key",
            )
            return []
        client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL") or OPENAI_OFFICIAL_BASE_URL)
        batch_size = max(1, min(_env_optional_int("OPENCLASS_RESOURCE_EMBEDDING_BATCH_SIZE") or 64, 256))
        vectors: list[list[float]] = []
        for start_index in range(0, len(texts), batch_size):
            batch = texts[start_index : start_index + batch_size]
            started_at = time.perf_counter()
            payload: dict[str, object] = {"model": spec.model, "input": batch}
            if spec.dimensions:
                payload["dimensions"] = spec.dimensions
            try:
                response = client.embeddings.create(**payload)
            except Exception as exc:
                ai_usage_logger.log_event(
                    "openai_embedding_error",
                    model=spec.model,
                    batch_size=len(batch),
                    error=str(exc),
                )
                return []
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])
            ai_usage_logger.log_event(
                "openai_embedding_call",
                model=spec.model,
                batch_size=len(batch),
                dimensions=spec.dimensions or (len(vectors[-1]) if vectors else None),
                duration_ms=max(0, round((time.perf_counter() - started_at) * 1000)),
                usage=getattr(response, "usage", None),
            )
        return vectors


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _segment_embedding_input(segment: ResourceSegment) -> str:
    heading = " / ".join(segment.heading_path)
    return _compact_text(f"{heading}\n{segment.text}", limit=6000)


def _compact_text(value: str | None, *, limit: int = 1200) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith("你的_") or normalized.startswith("your_"):
        return None
    return normalized


resource_embedding_service = ResourceEmbeddingService()
