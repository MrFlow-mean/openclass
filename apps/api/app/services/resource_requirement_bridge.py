from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    LearningRequirementSheet,
    LearningResourceReference,
    ResourceAIQueryRequest,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ResourceLibraryItem,
)
from app.services.reference_utils import compact_reference_text, reference_key_points, reference_passages
from app.services.resource_ai import query_resource_ai
from app.services.resource_library import extract_reference_context


@dataclass(frozen=True)
class ResourceRequirementAlignment:
    requirement: LearningRequirementSheet
    resource_matches: list[ResourceMatch]
    reference_prompt: ResourceReferencePrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    changed: bool = False


def suggest_requirement_resource_reference(
    *,
    resources: list[ResourceLibraryItem],
    requirement: LearningRequirementSheet,
    user_message: str,
) -> ResourceRequirementAlignment:
    if not resources or _has_settled_reference(requirement):
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=[])

    query = _reference_query(requirement, user_message)
    if not query:
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=[])

    response = query_resource_ai(
        resources,
        ResourceAIQueryRequest(query=query, max_results=4, include_reference_context=False),
    )
    evidence = next((unit for unit in response.evidence_units if unit.chapter_id), None)
    if evidence is None or evidence.chapter_id is None or evidence.chapter_title is None:
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=response.resource_matches)

    reference = LearningResourceReference(
        resource_id=evidence.resource_id,
        resource_name=evidence.resource_name,
        chapter_id=evidence.chapter_id,
        chapter_title=evidence.chapter_title,
        query=query,
        excerpt=evidence.excerpt,
        page_no=evidence.page_no,
        page_idx=evidence.page_idx,
        source_locator=evidence.source_locator,
        reason=evidence.reason,
        score=evidence.score,
        status="suggested",
    )
    next_requirement = _with_selected_reference(requirement, reference)
    prompt = ResourceReferencePrompt(
        resource_id=reference.resource_id,
        chapter_id=reference.chapter_id,
        resource_name=reference.resource_name,
        chapter_title=reference.chapter_title,
        question=f"是否以《{reference.resource_name}》中的“{reference.chapter_title}”作为这次板书生成依据？",
        reason=_prompt_reason(reference),
        confirm_label="参考这一部分生成板书",
        skip_label="先不参考资料",
        score=reference.score,
    )
    return ResourceRequirementAlignment(
        requirement=next_requirement,
        resource_matches=response.resource_matches,
        reference_prompt=prompt,
        changed=next_requirement.model_dump(mode="json") != requirement.model_dump(mode="json"),
    )


def confirm_requirement_resource_reference(
    *,
    resources: list[ResourceLibraryItem],
    requirement: LearningRequirementSheet,
    resource_id: str,
    chapter_id: str,
    user_message: str,
) -> ResourceRequirementAlignment:
    resource = _find_resource(resources, resource_id)
    if resource is None:
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=[])
    chapter = next((candidate for candidate in resource.outline if candidate.id == chapter_id), None)
    if chapter is None:
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=[])

    query = _reference_query(requirement, user_message)
    response = query_resource_ai(
        [resource],
        ResourceAIQueryRequest(
            query=query or chapter.title,
            resource_id=resource.id,
            max_results=4,
            include_reference_context=False,
        ),
    )
    evidence = next(
        (unit for unit in response.evidence_units if unit.chapter_id == chapter.id),
        response.evidence_units[0] if response.evidence_units else None,
    )
    selected_reference = extract_reference_context(
        resource,
        chapter.id,
        user_query=query or user_message or chapter.title,
    )
    reference = LearningResourceReference(
        resource_id=resource.id,
        resource_name=resource.name,
        chapter_id=chapter.id,
        chapter_title=chapter.title,
        query=query,
        excerpt=evidence.excerpt if evidence is not None else chapter.summary,
        page_no=evidence.page_no if evidence is not None else chapter.page_start,
        page_idx=evidence.page_idx if evidence is not None else None,
        source_locator=evidence.source_locator if evidence is not None else chapter.locator_hint,
        reason=evidence.reason if evidence is not None else "用户确认以该资料章节作为板书生成依据。",
        score=evidence.score if evidence is not None else 1.0,
        status="confirmed",
    )
    next_requirement = _with_selected_reference(requirement, reference)
    return ResourceRequirementAlignment(
        requirement=next_requirement,
        resource_matches=response.resource_matches,
        selected_reference=selected_reference,
        changed=next_requirement.model_dump(mode="json") != requirement.model_dump(mode="json"),
    )


def skip_requirement_resource_reference(
    *,
    requirement: LearningRequirementSheet,
) -> ResourceRequirementAlignment:
    existing = requirement.selected_resource_reference
    if existing is None:
        return ResourceRequirementAlignment(requirement=requirement, resource_matches=[])
    skipped = existing.model_copy(update={"status": "skipped"})
    next_requirement = _with_selected_reference(requirement, skipped)
    return ResourceRequirementAlignment(
        requirement=next_requirement,
        resource_matches=[],
        changed=next_requirement.model_dump(mode="json") != requirement.model_dump(mode="json"),
    )


def resolve_confirmed_requirement_reference_context(
    *,
    resources: list[ResourceLibraryItem],
    requirement: LearningRequirementSheet,
) -> ResourceReferenceContext | None:
    reference = requirement.selected_resource_reference
    if reference is None or reference.status != "confirmed":
        return None
    resource = _find_resource(resources, reference.resource_id)
    if resource is None:
        return None
    return extract_reference_context(
        resource,
        reference.chapter_id,
        user_query=_reference_query(requirement, reference.query) or reference.chapter_title,
    )


def resource_summary_from_reference_context(reference_context: ResourceReferenceContext | None) -> str:
    if reference_context is None:
        return ""
    passages = reference_passages(reference_context, max_items=3)
    points = reference_key_points(reference_context, max_items=4)
    lines = [
        f"资料：{reference_context.resource_name}",
        f"位置：{reference_context.chapter_title}",
        f"摘要：{compact_reference_text(reference_context.summary, limit=500)}",
    ]
    for index, passage in enumerate(passages, start=1):
        lines.append(f"片段 {index}：{passage}")
    for index, point in enumerate(points, start=1):
        lines.append(f"要点 {index}：{compact_reference_text(point, limit=260)}")
    return "\n".join(line for line in lines if line.strip())


def _with_selected_reference(
    requirement: LearningRequirementSheet,
    reference: LearningResourceReference,
) -> LearningRequirementSheet:
    references = [
        item
        for item in requirement.resource_references
        if not (item.resource_id == reference.resource_id and item.chapter_id == reference.chapter_id)
    ]
    references.append(reference)
    return requirement.model_copy(
        deep=True,
        update={
            "resource_references": references,
            "selected_resource_reference": reference,
        },
    )


def _has_settled_reference(requirement: LearningRequirementSheet) -> bool:
    reference = requirement.selected_resource_reference
    return reference is not None and reference.status in {"confirmed", "skipped"}


def _reference_query(requirement: LearningRequirementSheet, user_message: str) -> str:
    for value in (
        requirement.learning_goal,
        requirement.theme,
        user_message,
        " ".join(requirement.current_questions),
    ):
        text = (value or "").strip()
        if text:
            return text
    return ""


def _prompt_reason(reference: LearningResourceReference) -> str:
    parts = []
    if reference.excerpt:
        parts.append(f"匹配片段：{compact_reference_text(reference.excerpt, limit=220)}")
    if reference.page_no is not None:
        parts.append(f"页码：{reference.page_no}")
    if reference.source_locator:
        parts.append(f"定位：{reference.source_locator}")
    if reference.reason:
        parts.append(reference.reason)
    return "；".join(parts)


def _find_resource(resources: list[ResourceLibraryItem], resource_id: str) -> ResourceLibraryItem | None:
    return next((resource for resource in resources if resource.id == resource_id), None)
