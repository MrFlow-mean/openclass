from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardFocusRef,
    BoardPatchValidationResult,
    DiffPreviewItem,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    PatchOperation,
)
from app.services.document_ops import apply_board_patch, read_board_snapshot
from app.services.history import current_head_commit
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai
from app.services.board_segment_index import build_board_segment_index
from app.services.rich_document import (
    build_document,
    document_to_markdown,
    document_changed,
    html_to_markdown,
    html_to_text,
    is_document_empty,
    looks_like_html_content,
    replace_selection_in_document,
    rich_structure_counts,
    text_to_html,
)


@dataclass(frozen=True)
class BoardDocumentEditOutcome:
    chatbot_message: str
    new_document: BoardDocument
    board_decision: BoardDecision
    assistant_message_source: str
    operation: str | None
    summary: str
    section_titles: list[str]
    changed: bool = False
    operation_status: Literal["succeeded", "failed"] = "failed"
    failure_reason: str | None = None
    operations: list[PatchOperation] | None = None
    diff_preview: list[DiffPreviewItem] | None = None
    patch_validation: BoardPatchValidationResult | None = None
    patch_risk_level: str | None = None


def generate_from_requirements(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    requirement_run_id: str | None = None,
    frozen_requirement_version_id: str | None = None,
    resource_summary: str = "",
) -> BoardDocumentEditOutcome:
    if not is_document_empty(lesson.board_document):
        return _no_change(
            lesson,
            "当前板书不是空白文档，已阻止整体覆盖。",
        )

    learning_requirement_context = _requirement_context(
        requirements,
        clarification,
        requirement_run_id=requirement_run_id,
        frozen_requirement_version_id=frozen_requirement_version_id,
    )
    request_kwargs = {
        "intent": "generate_from_requirements",
        "lesson_title": lesson.title,
        "learning_requirement_context": learning_requirement_context,
        "current_document_title": lesson.board_document.title,
        "current_document_text": _document_text(lesson.board_document),
        "resource_summary": resource_summary,
        "selection_excerpt": None,
    }
    result = _request_board_document_edit(request_kwargs)
    if not result:
        return _no_change(lesson, "板书文档编辑 AI 没有返回生成结果。")

    content_text, content_html = _edit_payload(result, prefer_content_html=False)
    if not content_text and not content_html:
        return _no_change(lesson, "板书文档编辑 AI 返回了空内容。")

    new_document = build_document(
        title=result.title.strip() or lesson.board_document.title or lesson.title,
        content_text=content_text,
        content_html=content_html,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    return _changed(
        lesson=lesson,
        new_document=new_document,
        operation="replace_document",
        summary=result.summary.strip(),
        chatbot_message=result.chatbot_message.strip() or result.summary.strip(),
        section_titles=result.section_titles,
        reason="板书文档编辑 AI 已根据学习需求清单生成空白板书。",
    )


def edit_existing_document(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    resource_summary: str,
    conversation_summary: str,
    user_instruction: str,
    selection_excerpt: str | None,
    focus: BoardFocusRef | None = None,
    target_scope: str | None = None,
    allow_replace_document: bool = False,
) -> BoardDocumentEditOutcome:
    target_excerpt = _target_excerpt(selection_excerpt=selection_excerpt, focus=focus, current_document=lesson.board_document)
    is_append_request = requirements.action_type == "append_section"
    is_whole_document_scope = target_scope == "whole_document"
    if not target_excerpt and not is_document_empty(lesson.board_document) and not is_append_request and not is_whole_document_scope:
        return _no_change(
            lesson,
            "已有板书的局部编辑需要先解析目标位置。",
        )

    request_kwargs = {
        "intent": "edit_existing_document",
        "lesson_title": lesson.title,
        "learning_requirement_context": _requirement_context(requirements, clarification),
        "current_document_title": lesson.board_document.title,
        "current_document_text": _document_text(lesson.board_document),
        "resource_summary": resource_summary,
        "selection_excerpt": target_excerpt,
        "target_scope": target_scope,
        "allow_replace_document": allow_replace_document or is_whole_document_scope,
    }
    if not is_whole_document_scope:
        patch_outcome = _try_patch_existing_document(
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            resource_summary=resource_summary,
            selection_excerpt=target_excerpt,
            target_scope=target_scope,
            allow_replace_document=allow_replace_document,
        )
        if patch_outcome is not None:
            return patch_outcome

    result = _request_board_document_edit(request_kwargs)
    if not result:
        return _no_change(lesson, "板书文档编辑 AI 没有返回编辑结果。")

    operation = "append_section" if is_append_request else result.operation
    content_text, _content_html = _edit_payload(result, prefer_content_html=True)
    if not content_text:
        return _no_change(lesson, "板书文档编辑 AI 返回了空内容。")
    if (
        operation == "replace_document"
        and not is_document_empty(lesson.board_document)
        and not (allow_replace_document or is_whole_document_scope)
    ):
        return _no_change(lesson, "非全文编辑返回了整篇替换结果，已阻止写入。")

    new_document = _apply_edit_result(
        lesson=lesson,
        result=result,
        selection_excerpt=target_excerpt,
        operation_override=operation,
        allow_replace_document=allow_replace_document or is_whole_document_scope,
    )
    if not document_changed(lesson.board_document, new_document):
        return _no_change(lesson, "板书文档编辑 AI 的结果没有改变当前文档。")

    return _changed(
        lesson=lesson,
        new_document=new_document,
        operation=operation,
        summary=result.summary.strip(),
        chatbot_message=result.chatbot_message.strip() or result.summary.strip(),
        section_titles=result.section_titles,
        reason="板书文档编辑 AI 已根据解析出的目标位置和指令更新板书。",
    )


def _apply_edit_result(
    *,
    lesson: Lesson,
    result: BoardDocumentEditResult,
    selection_excerpt: str | None,
    operation_override: str | None = None,
    allow_replace_document: bool = False,
) -> BoardDocument:
    content_text, content_html = _edit_payload(result, prefer_content_html=True)
    if not content_text and not content_html:
        return lesson.board_document

    operation = operation_override or result.operation
    if operation == "replace_document":
        if is_document_empty(lesson.board_document) or allow_replace_document:
            return build_document(
                title=result.title.strip() or lesson.board_document.title or lesson.title,
                content_text=content_text,
                content_html=content_html,
                document_id=lesson.board_document.id,
                page_settings=lesson.board_document.page_settings,
            )
        return lesson.board_document

    if operation == "append_section":
        next_text = "\n\n".join(
            part for part in [_document_text(lesson.board_document).strip(), content_text] if part
        )
        return build_document(
            title=lesson.board_document.title,
            content_text=next_text,
            content_html="\n".join(
                part for part in [lesson.board_document.content_html.strip(), content_html] if part
            ),
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )

    if operation == "replace_selection" and selection_excerpt:
        return replace_selection_in_document(
            lesson.board_document,
            selection_text=selection_excerpt,
            replacement_text=content_text,
            replacement_html=content_html,
        )

    if is_document_empty(lesson.board_document):
        return build_document(
            title=result.title.strip() or lesson.board_document.title or lesson.title,
            content_text=content_text,
            content_html=content_html,
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )

    if selection_excerpt:
        return replace_selection_in_document(
            lesson.board_document,
            selection_text=selection_excerpt,
            replacement_text=content_text,
            replacement_html=content_html,
        )

    return lesson.board_document


def _try_patch_existing_document(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    resource_summary: str,
    selection_excerpt: str | None,
    target_scope: str | None,
    allow_replace_document: bool,
) -> BoardDocumentEditOutcome | None:
    head_commit = current_head_commit(lesson)
    board_snapshot = read_board_snapshot(lesson.board_document, source_commit_id=head_commit.id)
    patch = openai_course_ai.generate_board_patch_plan(
        lesson_title=lesson.title,
        learning_requirement_context=_requirement_context(requirements, clarification),
        board_snapshot=board_snapshot,
        resource_summary=resource_summary,
        selection_excerpt=selection_excerpt,
        target_scope=target_scope,
        user_instruction=requirements.action_instruction,
        allow_delete=allow_replace_document,
        allow_whole_document=allow_replace_document,
    )
    if patch is None:
        return None

    patch = patch.model_copy(
        update={
            "source_commit_id": patch.source_commit_id or head_commit.id,
            "source_document_hash": patch.source_document_hash or board_snapshot["source_document_hash"],
            "target_scope": patch.target_scope or target_scope,
        }
    )
    applied = apply_board_patch(
        lesson.board_document,
        patch,
        current_commit_id=head_commit.id,
        allow_high_risk=allow_replace_document,
    )
    if applied.validation.status == "failed":
        reason = "；".join(applied.validation.issues) or "Board patch validation failed."
        return _no_change(
            lesson,
            f"板书 patch 校验未通过：{reason}",
            patch_validation=applied.validation,
            patch_risk_level=patch.risk_level,
        )

    return _changed(
        lesson=lesson,
        new_document=applied.new_document,
        operation="board_patch",
        summary=patch.summary.strip() or "Applied a structured board patch.",
        chatbot_message=patch.summary.strip() or "已按定位结果更新板书。",
        section_titles=_patch_section_titles(applied.diff_preview),
        reason="板书编辑 AI 已生成结构化 patch，并由后端校验后应用。",
        operations=applied.operations,
        diff_preview=applied.diff_preview,
        patch_validation=applied.validation,
        patch_risk_level=patch.risk_level,
    )


def _markdown_heading_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in re.finditer(r"(?m)^\s*#{1,6}\s+(?P<title>.+?)\s*$", text or ""):
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        if title:
            titles.append(title)
    return titles


def _changed(
    *,
    lesson: Lesson,
    new_document: BoardDocument,
    operation: str,
    summary: str,
    chatbot_message: str,
    section_titles: list[str],
    reason: str,
    operations: list[PatchOperation] | None = None,
    diff_preview: list[DiffPreviewItem] | None = None,
    patch_validation: BoardPatchValidationResult | None = None,
    patch_risk_level: str | None = None,
) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=chatbot_message,
        new_document=new_document,
        board_decision=BoardDecision(action="edit_board", reason=reason),
        assistant_message_source="board_document_editor_ai",
        operation=operation,
        summary=summary,
        section_titles=[title.strip() for title in section_titles if title.strip()],
        changed=True,
        operation_status="succeeded",
        failure_reason=None,
        operations=operations or [],
        diff_preview=diff_preview or [],
        patch_validation=patch_validation,
        patch_risk_level=patch_risk_level,
    )


def _no_change(
    lesson: Lesson,
    reason: str,
    *,
    patch_validation: BoardPatchValidationResult | None = None,
    patch_risk_level: str | None = None,
) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="",
        new_document=lesson.board_document,
        board_decision=BoardDecision(action="no_change", reason=reason),
        assistant_message_source="workflow",
        operation=None,
        summary=reason,
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason=reason,
        operations=[],
        diff_preview=[],
        patch_validation=patch_validation,
        patch_risk_level=patch_risk_level,
    )


def _patch_section_titles(diff_preview: list[DiffPreviewItem]) -> list[str]:
    titles: list[str] = []
    for item in diff_preview:
        titles.extend(title for title in item.heading_path if title and title not in titles)
        for text in (item.after_text, item.before_text):
            for title in _markdown_heading_titles(text):
                if title not in titles:
                    titles.append(title)
    return titles


def _request_board_document_edit(request_kwargs: dict[str, object]) -> BoardDocumentEditResult | None:
    return openai_course_ai.generate_board_document_edit(**request_kwargs)


def _edit_payload(result: BoardDocumentEditResult, *, prefer_content_html: bool) -> tuple[str, str]:
    content_text = result.content_text.strip()
    model_content_html = result.content_html.strip()
    if content_text and looks_like_html_content(content_text):
        content_text = html_to_markdown(content_text)
    if not content_text and model_content_html:
        content_text = html_to_markdown(model_content_html)
    content_html = text_to_html(content_text) if content_text else ""
    return content_text, content_html


def _document_text(document: BoardDocument) -> str:
    return document_to_markdown(document) or document.content_text or html_to_text(document.content_html)


def _would_store_flat_initial_board(document: BoardDocument) -> bool:
    text = _document_text(document).strip()
    if len(text) < 1000:
        return False
    counts = rich_structure_counts(document)
    if counts.get("heading", 0) or counts.get("table", 0):
        return False
    return counts.get("paragraph", 0) >= 8


def _target_excerpt(
    *,
    selection_excerpt: str | None,
    focus: BoardFocusRef | None,
    current_document: BoardDocument | None = None,
) -> str | None:
    if focus and current_document is not None:
        expanded = _expanded_focus_excerpt(focus=focus, current_document=current_document)
        if expanded:
            return expanded
    if focus and focus.excerpt.strip():
        return focus.excerpt.strip()
    return selection_excerpt.strip() if selection_excerpt else None


def _expanded_focus_excerpt(*, focus: BoardFocusRef, current_document: BoardDocument) -> str:
    if focus.order_start is None or focus.order_end is None or focus.order_end <= focus.order_start:
        return ""
    index = build_board_segment_index(current_document)
    selected_segments = [
        segment
        for segment in index.segments
        if focus.order_start <= segment.order_index <= focus.order_end
        and (not focus.source_segment_ids or segment.segment_id in set(focus.source_segment_ids))
    ]
    if len(selected_segments) < 2:
        return ""
    expanded = "\n\n".join(segment.text for segment in selected_segments if segment.text.strip()).strip()
    if not expanded:
        return ""
    return expanded


def _requirement_context(
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    *,
    requirement_run_id: str | None = None,
    frozen_requirement_version_id: str | None = None,
) -> dict[str, object]:
    return {
        "requirement_run_id": requirement_run_id,
        "frozen_requirement_version_id": frozen_requirement_version_id,
        "summary": clarification.summary or clarification.reason or requirements.learning_goal,
        "key_facts": [item.model_dump(mode="json") for item in clarification.key_facts],
        "checklist": [item.model_dump(mode="json") for item in clarification.checklist],
        "sheet": requirements.model_dump(mode="json"),
        "clarification": clarification.model_dump(mode="json"),
        "learning_goal": requirements.learning_goal,
        "level": requirements.level,
        "known_background": requirements.known_background,
        "target_depth": requirements.target_depth,
        "output_preference": requirements.output_preference,
        "success_criteria": requirements.success_criteria,
        "action_type": requirements.action_type,
        "action_instruction": requirements.action_instruction,
        "target_location": requirements.target_location.model_dump(mode="json") if requirements.target_location else None,
    }
