from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models import (
    LibraryChapter,
    ResourceAIEvidenceUnit,
    ResourceAIIndexStatus,
    ResourceAIQueryRequest,
    ResourceAIQueryResponse,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceSourceUnit,
)
from app.services.rag_anything_adapter import source_units_to_rag_content_list
from app.services.resource_library import extract_reference_context


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_EXCERPT_LIMIT = 520


@dataclass(frozen=True)
class _EvidenceCandidate:
    resource: ResourceLibraryItem
    unit: ResourceSourceUnit
    chapter: LibraryChapter | None
    score: float
    excerpt: str
    reason: str


def build_resource_ai_index_status(resources: list[ResourceLibraryItem]) -> list[ResourceAIIndexStatus]:
    return [_resource_index_status(resource) for resource in resources]


def query_resource_ai(
    resources: list[ResourceLibraryItem],
    request: ResourceAIQueryRequest,
) -> ResourceAIQueryResponse:
    query = request.query.strip()
    if not query:
        raise ValueError("Resource AI query is required.")

    visible_resources = resources
    if request.resource_id:
        visible_resources = [resource for resource in resources if resource.id == request.resource_id]
    index_status = build_resource_ai_index_status(visible_resources)

    candidates = _rank_evidence_candidates(visible_resources, query)
    selected = candidates[: request.max_results]
    evidence_units = [_candidate_to_evidence_unit(candidate) for candidate in selected]
    resource_matches = _resource_matches_from_candidates(selected)
    selected_reference = None
    if request.include_reference_context and selected:
        top = selected[0]
        if top.chapter is not None:
            selected_reference = extract_reference_context(
                top.resource,
                top.chapter.id,
                user_query=query,
            )

    warnings: list[str] = []
    if not visible_resources:
        warnings.append("No visible resources are available for this lesson.")
    elif not evidence_units:
        warnings.append("No matching resource evidence was found.")

    return ResourceAIQueryResponse(
        query=query,
        backend="openclass_source_units",
        used_rag_anything=any(status.parser_provider.startswith("raganything") for status in index_status),
        index_status=index_status,
        evidence_units=evidence_units,
        resource_matches=resource_matches,
        selected_reference=selected_reference,
        warnings=warnings,
    )


def _resource_index_status(resource: ResourceLibraryItem) -> ResourceAIIndexStatus:
    text_unit_count = sum(1 for unit in resource.source_units if _normalize_content_type(unit.content_type) == "text")
    multimodal_unit_count = sum(1 for unit in resource.source_units if _normalize_content_type(unit.content_type) != "text")
    rag_content_list = source_units_to_rag_content_list(resource.source_units)
    warnings = list(resource.parse_warnings)
    if resource.ingestion_status == "failed" and resource.ingestion_error:
        warnings.append(resource.ingestion_error)
    if resource.parser_provider.startswith("raganything") and not rag_content_list:
        warnings.append("RAG-Anything parser metadata is present, but no reusable content list units were stored.")
    return ResourceAIIndexStatus(
        resource_id=resource.id,
        resource_name=resource.name,
        parser_provider=resource.parser_provider,
        source_type=resource.source_type,
        ingestion_status=resource.ingestion_status,
        ingestion_error=resource.ingestion_error,
        ingestion_progress=resource.ingestion_progress,
        ingestion_adapter=resource.ingestion_adapter,
        extracted_text_available=resource.extracted_text_available,
        source_unit_count=len(resource.source_units),
        text_unit_count=text_unit_count,
        multimodal_unit_count=multimodal_unit_count,
        chapter_count=len(resource.outline),
        rag_content_list_available=bool(rag_content_list),
        page_structure_available=resource.page_structure is not None,
        body_start_page_no=resource.page_structure.body_start_page_no if resource.page_structure else None,
        page_map_count=len(resource.page_structure.page_map) if resource.page_structure else 0,
        parser_artifacts_path=resource.parser_artifacts_path,
        warnings=warnings,
    )


def _rank_evidence_candidates(resources: list[ResourceLibraryItem], query: str) -> list[_EvidenceCandidate]:
    query_tokens = _tokens(query)
    candidates: list[_EvidenceCandidate] = []
    for resource in resources:
        for unit in _resource_units(resource):
            searchable_text = _unit_search_text(unit)
            score = _score_text(searchable_text, query, query_tokens)
            chapter = _chapter_for_unit(resource, unit, query=query, query_tokens=query_tokens)
            if chapter is not None:
                score = min(1.0, score + _score_chapter(chapter, query, query_tokens) * 0.25)
            if score <= 0:
                continue
            candidates.append(
                _EvidenceCandidate(
                    resource=resource,
                    unit=unit,
                    chapter=chapter,
                    score=score,
                    excerpt=_excerpt(searchable_text, query, query_tokens),
                    reason=_reason(unit, chapter),
                )
            )
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.score,
            -candidate.unit.order_index,
        ),
        reverse=True,
    )


def _resource_units(resource: ResourceLibraryItem) -> list[ResourceSourceUnit]:
    if resource.source_units:
        return [
            unit
            for unit in sorted(resource.source_units, key=lambda item: item.order_index)
            if _unit_search_text(unit)
        ]
    if resource.text_content:
        return [
            ResourceSourceUnit(
                content_type="text",
                text=resource.text_content,
                source_locator="openclass:resource:text_content",
                order_index=0,
            )
        ]
    return [
        ResourceSourceUnit(
            content_type="text",
            text="\n".join([chapter.title, chapter.summary, " ".join(chapter.keywords)]).strip(),
            source_locator=f"openclass:resource:chapter:{chapter.id}",
            order_index=index,
            metadata={"chapter_id": chapter.id},
        )
        for index, chapter in enumerate(resource.outline)
        if chapter.title or chapter.summary or chapter.keywords
    ]


def _chapter_for_unit(
    resource: ResourceLibraryItem,
    unit: ResourceSourceUnit,
    *,
    query: str,
    query_tokens: list[str],
) -> LibraryChapter | None:
    if not resource.outline:
        return None
    metadata_chapter_id = unit.metadata.get("chapter_id")
    if isinstance(metadata_chapter_id, str):
        match = next((chapter for chapter in resource.outline if chapter.id == metadata_chapter_id), None)
        if match is not None:
            return match
    if unit.page_no is not None:
        for chapter in resource.outline:
            if chapter.page_start is None:
                continue
            page_end = chapter.page_end if chapter.page_end is not None else chapter.page_start
            if chapter.page_start <= unit.page_no <= page_end:
                return chapter
    scored = [
        (_score_chapter(chapter, query, query_tokens), chapter)
        for chapter in resource.outline
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return resource.outline[0]


def _resource_matches_from_candidates(candidates: list[_EvidenceCandidate]) -> list[ResourceMatch]:
    best_by_chapter: dict[tuple[str, str], ResourceMatch] = {}
    for candidate in candidates:
        if candidate.chapter is None:
            continue
        key = (candidate.resource.id, candidate.chapter.id)
        previous = best_by_chapter.get(key)
        if previous is not None and previous.score >= candidate.score:
            continue
        best_by_chapter[key] = ResourceMatch(
            resource_id=candidate.resource.id,
            chapter_id=candidate.chapter.id,
            resource_name=candidate.resource.name,
            chapter_title=candidate.chapter.title,
            reason=candidate.reason,
            score=round(candidate.score, 4),
            is_high_overlap=candidate.score >= 0.66,
        )
    return sorted(best_by_chapter.values(), key=lambda match: match.score, reverse=True)


def _candidate_to_evidence_unit(candidate: _EvidenceCandidate) -> ResourceAIEvidenceUnit:
    metadata = _safe_metadata({**candidate.unit.metadata, **_unit_locator_metadata(candidate.unit)})
    return ResourceAIEvidenceUnit(
        resource_id=candidate.resource.id,
        resource_name=candidate.resource.name,
        chapter_id=candidate.chapter.id if candidate.chapter else None,
        chapter_title=candidate.chapter.title if candidate.chapter else None,
        content_type=_normalize_content_type(candidate.unit.content_type),
        excerpt=candidate.excerpt,
        page_no=candidate.unit.page_no,
        page_idx=candidate.unit.page_idx,
        source_locator=candidate.unit.source_locator,
        score=round(candidate.score, 4),
        reason=candidate.reason,
        metadata=metadata,
    )


def _score_chapter(chapter: LibraryChapter, query: str, query_tokens: list[str]) -> float:
    text = "\n".join(
        [
            chapter.title,
            chapter.summary,
            " ".join(chapter.path),
            " ".join(chapter.keywords),
        ]
    )
    return _score_text(text, query, query_tokens)


def _score_text(text: str, query: str, query_tokens: list[str]) -> float:
    compact_text = _compact(text).lower()
    compact_query = _compact(query).lower()
    if not compact_text or not compact_query:
        return 0.0

    score = 0.0
    if compact_query in compact_text:
        score += 0.45

    text_tokens = set(_tokens(text))
    if query_tokens and text_tokens:
        overlap = len(set(query_tokens) & text_tokens)
        score += min(0.5, overlap / max(len(set(query_tokens)), 1) * 0.5)

    for token in query_tokens:
        if len(token) >= 2 and token in compact_text:
            score += 0.03
    return min(score, 1.0)


def _excerpt(text: str, query: str, query_tokens: list[str]) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= _EXCERPT_LIMIT:
        return cleaned
    lower = cleaned.lower()
    anchors = [query.lower(), *[token.lower() for token in query_tokens if len(token) >= 2]]
    index = -1
    for anchor in anchors:
        if not anchor:
            continue
        index = lower.find(anchor)
        if index >= 0:
            break
    if index < 0:
        return cleaned[:_EXCERPT_LIMIT].rstrip() + "..."
    start = max(0, index - _EXCERPT_LIMIT // 3)
    end = min(len(cleaned), start + _EXCERPT_LIMIT)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(cleaned) else ""
    return f"{prefix}{cleaned[start:end].strip()}{suffix}"


def _reason(unit: ResourceSourceUnit, chapter: LibraryChapter | None) -> str:
    content_type = _normalize_content_type(unit.content_type)
    location = []
    if chapter is not None:
        location.append(f"章节：{chapter.title}")
    printed_page = unit.metadata.get("printed_page") if isinstance(unit.metadata, dict) else None
    page_role_label = unit.metadata.get("page_role_label") if isinstance(unit.metadata, dict) else None
    if isinstance(printed_page, int):
        location.append(f"书内页码：{printed_page}")
    if unit.page_no is not None:
        location.append(f"文件页码：{unit.page_no}")
    if isinstance(page_role_label, str) and page_role_label:
        location.append(f"页面分区：{page_role_label}")
    if unit.url:
        location.append(f"网页：{unit.url}")
    if unit.heading_path:
        location.append(f"标题路径：{' > '.join(unit.heading_path)}")
    if unit.paragraph_index is not None:
        location.append(f"段落：{unit.paragraph_index + 1}")
    if unit.timestamp_start is not None:
        timestamp = f"{unit.timestamp_start:g}s"
        if unit.timestamp_end is not None:
            timestamp = f"{timestamp}-{unit.timestamp_end:g}s"
        location.append(f"时间戳：{timestamp}")
    if unit.source_locator:
        location.append(f"定位：{unit.source_locator}")
    location_text = "；".join(location) if location else "资料来源单元"
    return f"匹配到 {content_type} 内容，{location_text}。"


def _unit_search_text(unit: ResourceSourceUnit) -> str:
    parts = [unit.text, " ".join(unit.heading_path)]
    parts.extend(_metadata_text_parts(unit.metadata))
    return "\n".join(part for part in parts if part).strip()


def _unit_locator_metadata(unit: ResourceSourceUnit) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if unit.url:
        metadata["url"] = unit.url
    if unit.heading_path:
        metadata["heading_path"] = unit.heading_path
    if unit.paragraph_index is not None:
        metadata["paragraph_index"] = unit.paragraph_index
    if unit.timestamp_start is not None:
        metadata["timestamp_start"] = unit.timestamp_start
    if unit.timestamp_end is not None:
        metadata["timestamp_end"] = unit.timestamp_end
    return metadata


def _metadata_text_parts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_metadata_text_parts(item))
        return parts
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_metadata_text_parts(item))
        return parts
    return []


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value)]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _normalize_content_type(value: str) -> str:
    raw = (value or "text").strip().lower()
    if raw in {"img", "figure"}:
        return "image"
    if raw in {"tabular"}:
        return "table"
    if raw in {"formula", "latex"}:
        return "equation"
    return raw or "text"


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list) and len(str(value)) <= 500:
            safe[key] = value
        elif isinstance(value, dict) and len(str(value)) <= 500:
            safe[key] = value
    return safe
