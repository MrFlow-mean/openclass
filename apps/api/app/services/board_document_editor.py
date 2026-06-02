from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardFocusRef,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai
from app.services.rich_document import (
    build_document,
    document_to_markdown,
    document_changed,
    html_to_text,
    is_document_empty,
    replace_selection_in_document,
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


def generate_from_requirements(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    resource_summary: str,
    requirement_run_id: str | None = None,
    frozen_requirement_version_id: str | None = None,
) -> BoardDocumentEditOutcome:
    if not is_document_empty(lesson.board_document):
        return _no_change(
            lesson,
            "当前板书不是空白文档，已阻止整体覆盖。",
        )

    result = _generate_board_document_edit_with_retry(
        intent="generate_from_requirements",
        lesson_title=lesson.title,
        learning_requirement_context=_requirement_context(
            requirements,
            clarification,
            requirement_run_id=requirement_run_id,
            frozen_requirement_version_id=frozen_requirement_version_id,
        ),
        current_document_title=lesson.board_document.title,
        current_document_text=_document_text(lesson.board_document),
        resource_summary=resource_summary,
        selection_excerpt=None,
    )
    if not result:
        return _no_change(
            lesson,
            "板书文档编辑 AI 没有返回生成结果。",
        )

    content_text, content_html = _edit_payload(result, prefer_content_html=False)
    if not content_text and not content_html:
        return _no_change(
            lesson,
            "板书文档编辑 AI 返回了空内容。",
        )

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
    target_excerpt = _target_excerpt(selection_excerpt=selection_excerpt, focus=focus)
    is_append_request = requirements.action_type == "append_section"
    is_whole_document_scope = target_scope == "whole_document"
    if not target_excerpt and not is_document_empty(lesson.board_document) and not is_append_request and not is_whole_document_scope:
        return _no_change(
            lesson,
            "已有板书的局部编辑需要先解析目标位置。",
        )

    result = _generate_board_document_edit_with_retry(
        intent="edit_existing_document",
        lesson_title=lesson.title,
        learning_requirement_context=_requirement_context(requirements, clarification),
        current_document_title=lesson.board_document.title,
        current_document_text=_document_text(lesson.board_document),
        resource_summary=resource_summary,
        selection_excerpt=target_excerpt,
        target_scope=target_scope,
        allow_replace_document=allow_replace_document or is_whole_document_scope,
    )
    if not result:
        return _no_change(
            lesson,
            "板书文档编辑 AI 没有返回编辑结果。",
        )

    operation = "append_section" if is_append_request else result.operation
    content_text, _content_html = _edit_payload(result, prefer_content_html=True)
    if (
        operation == "replace_document"
        and not is_document_empty(lesson.board_document)
        and not (allow_replace_document or is_whole_document_scope)
    ):
        return _no_change(
            lesson,
            "非全文编辑返回了整篇替换结果，已阻止写入。",
        )
    if (
        operation == "replace_selection"
        and target_excerpt
        and _looks_like_whole_document_replacement(
            current_document=lesson.board_document,
            selection_excerpt=target_excerpt,
            replacement_text=content_text,
        )
    ):
        return _no_change(
            lesson,
            "局部替换结果看起来像整份文档，已阻止写入。",
        )
    new_document = _apply_edit_result(
        lesson=lesson,
        result=result,
        selection_excerpt=target_excerpt,
        operation_override=operation,
        allow_replace_document=allow_replace_document or is_whole_document_scope,
    )
    if not document_changed(lesson.board_document, new_document):
        return _no_change(
            lesson,
            "板书文档编辑 AI 的结果没有改变当前文档。",
        )
    if _would_flatten_rich_document(
        current_document=lesson.board_document,
        new_document=new_document,
        operation=operation,
    ):
        return _no_change(
            lesson,
            "全文替换结果丢失了原有标题、列表、加粗或表格结构，已阻止写入。",
        )

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


def _looks_like_whole_document_replacement(
    *,
    current_document: BoardDocument,
    selection_excerpt: str,
    replacement_text: str,
) -> bool:
    current_text = _document_text(current_document).strip()
    selection = (selection_excerpt or "").strip()
    replacement = (replacement_text or "").strip()
    if not current_text or not selection or not replacement:
        return False
    heading_count = len(re.findall(r"(?m)^\s*#{1,6}\s+\S+", replacement))
    if heading_count >= 2 and len(replacement) > max(len(selection) * 2, 240):
        return True
    if len(replacement) > max(len(selection) * 4, 1200) and len(replacement) > len(current_text) * 0.6:
        return True
    prefix = current_text[: min(240, len(current_text))]
    return len(prefix) >= 80 and prefix in replacement and len(replacement) > len(selection) * 2


def _changed(
    *,
    lesson: Lesson,
    new_document: BoardDocument,
    operation: str,
    summary: str,
    chatbot_message: str,
    section_titles: list[str],
    reason: str,
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
    )


def _no_change(lesson: Lesson, reason: str) -> BoardDocumentEditOutcome:
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
    )


def _generate_board_document_edit_with_retry(**kwargs) -> BoardDocumentEditResult | None:
    result = openai_course_ai.generate_board_document_edit(**kwargs)
    if result is not None:
        return result
    return openai_course_ai.generate_board_document_edit(**kwargs)


def _edit_payload(result: BoardDocumentEditResult, *, prefer_content_html: bool) -> tuple[str, str]:
    content_text = result.content_text.strip()
    content_html = result.content_html.strip()
    if not content_text and content_html:
        content_text = html_to_text(content_html)
    if content_text and (not content_html or not prefer_content_html):
        content_html = text_to_html(content_text)
    return content_text, content_html


def _document_text(document: BoardDocument) -> str:
    return document_to_markdown(document) or document.content_text or html_to_text(document.content_html)


def _rich_structure_counts(document: BoardDocument) -> dict[str, int]:
    counts = {
        "heading": 0,
        "bold": 0,
        "italic": 0,
        "bulletList": 0,
        "orderedList": 0,
        "listItem": 0,
        "table": 0,
        "blockquote": 0,
        "paragraph": 0,
    }

    def walk(value) -> None:
        if isinstance(value, dict):
            node_type = value.get("type")
            if isinstance(node_type, str) and node_type in counts:
                counts[node_type] += 1
            marks = value.get("marks")
            if isinstance(marks, list):
                for mark in marks:
                    if isinstance(mark, dict):
                        mark_type = mark.get("type")
                        if isinstance(mark_type, str) and mark_type in counts:
                            counts[mark_type] += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document.content_json if isinstance(document.content_json, dict) else {})
    return counts


def _rich_structure_score(counts: dict[str, int]) -> int:
    return (
        counts.get("heading", 0) * 3
        + counts.get("table", 0) * 4
        + counts.get("bulletList", 0) * 2
        + counts.get("orderedList", 0) * 2
        + counts.get("listItem", 0)
        + counts.get("blockquote", 0) * 2
        + counts.get("bold", 0)
        + counts.get("italic", 0)
    )


def _would_flatten_rich_document(
    *,
    current_document: BoardDocument,
    new_document: BoardDocument,
    operation: str | None,
) -> bool:
    if operation != "replace_document" or is_document_empty(current_document):
        return False
    old_counts = _rich_structure_counts(current_document)
    old_score = _rich_structure_score(old_counts)
    if old_score < 8:
        return False
    new_counts = _rich_structure_counts(new_document)
    new_score = _rich_structure_score(new_counts)
    if new_counts.get("heading", 0) or new_counts.get("table", 0):
        return False
    if new_score > max(2, old_score // 10):
        return False
    return new_counts.get("paragraph", 0) >= max(8, old_counts.get("paragraph", 0) // 2)


def _target_excerpt(*, selection_excerpt: str | None, focus: BoardFocusRef | None) -> str | None:
    if focus and focus.excerpt.strip():
        return focus.excerpt.strip()
    return selection_excerpt.strip() if selection_excerpt else None


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
