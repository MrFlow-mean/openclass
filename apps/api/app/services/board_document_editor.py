from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardFocusRef,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
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
    would_flatten_rich_document,
)


_MAX_BOARD_DOCUMENT_QUALITY_ATTEMPTS = 3
_QUALITY_REPAIR_EXCERPT_CHARS = 2400
_QUALITY_REVIEW_CONTENT_CHARS = 10000


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
    quality_repair_attempts: int = 0
    quality_review_status: str = "not_run"


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

    request_kwargs = {
        "intent": "generate_from_requirements",
        "lesson_title": lesson.title,
        "learning_requirement_context": _requirement_context(
            requirements,
            clarification,
            requirement_run_id=requirement_run_id,
            frozen_requirement_version_id=frozen_requirement_version_id,
        ),
        "current_document_title": lesson.board_document.title,
        "current_document_text": _document_text(lesson.board_document),
        "resource_summary": resource_summary,
        "selection_excerpt": None,
    }
    failure_reason = "板书文档编辑 AI 没有返回生成结果。"
    repair_feedback: dict[str, object] | None = None
    quality_repair_attempts = 0
    quality_review_status = "not_run"
    for attempt in range(_MAX_BOARD_DOCUMENT_QUALITY_ATTEMPTS):
        result = _request_board_document_edit(request_kwargs, repair_feedback=repair_feedback)
        if not result:
            failure_reason = "板书文档编辑 AI 没有返回生成结果。"
            break

        format_issue = _model_output_quality_issue(result)
        if format_issue:
            failure_reason = format_issue
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue

        content_text, content_html = _edit_payload(result, prefer_content_html=False)
        if not content_text and not content_html:
            failure_reason = "板书文档编辑 AI 返回了空内容。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue

        new_document = build_document(
            title=result.title.strip() or lesson.board_document.title or lesson.title,
            content_text=content_text,
            content_html=content_html,
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        if _would_store_flat_initial_board(new_document):
            failure_reason = "首次板书生成结果缺少标题层级，已阻止写入。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        consistency_issue = _document_quality_review_issue(
            intent="generate_from_requirements",
            lesson=lesson,
            request_kwargs=request_kwargs,
            result=result,
            operation="replace_document",
            new_document=new_document,
            selection_excerpt=None,
            target_scope=None,
        )
        if consistency_issue:
            failure_reason = consistency_issue
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        return _changed(
            lesson=lesson,
            new_document=new_document,
            operation="replace_document",
            summary=result.summary.strip(),
            chatbot_message=result.chatbot_message.strip() or result.summary.strip(),
            section_titles=result.section_titles,
            reason="板书文档编辑 AI 已根据学习需求清单生成空白板书。",
            quality_repair_attempts=quality_repair_attempts,
            quality_review_status="pass",
        )
    return _no_change(
        lesson,
        failure_reason,
        quality_repair_attempts=quality_repair_attempts,
        quality_review_status=quality_review_status,
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
    failure_reason = "板书文档编辑 AI 没有返回编辑结果。"
    repair_feedback: dict[str, object] | None = None
    quality_repair_attempts = 0
    quality_review_status = "not_run"
    for attempt in range(_MAX_BOARD_DOCUMENT_QUALITY_ATTEMPTS):
        result = _request_board_document_edit(request_kwargs, repair_feedback=repair_feedback)
        if not result:
            failure_reason = "板书文档编辑 AI 没有返回编辑结果。"
            break

        format_issue = _model_output_quality_issue(result)
        if format_issue:
            failure_reason = format_issue
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue

        operation = "append_section" if is_append_request else result.operation
        content_text, _content_html = _edit_payload(result, prefer_content_html=True)
        if not content_text:
            failure_reason = "板书文档编辑 AI 返回了空内容。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        if (
            operation == "replace_document"
            and not is_document_empty(lesson.board_document)
            and not (allow_replace_document or is_whole_document_scope)
        ):
            failure_reason = "非全文编辑返回了整篇替换结果，已阻止写入。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        if (
            operation == "replace_selection"
            and target_excerpt
            and _looks_like_whole_document_replacement(
                current_document=lesson.board_document,
                selection_excerpt=target_excerpt,
                replacement_text=content_text,
            )
        ):
            failure_reason = "局部替换结果看起来像整份文档，已阻止写入。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        new_document = _apply_edit_result(
            lesson=lesson,
            result=result,
            selection_excerpt=target_excerpt,
            operation_override=operation,
            allow_replace_document=allow_replace_document or is_whole_document_scope,
        )
        if not document_changed(lesson.board_document, new_document):
            failure_reason = "板书文档编辑 AI 的结果没有改变当前文档。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        if would_flatten_rich_document(
            current_document=lesson.board_document,
            new_document=new_document,
            operation=operation,
        ):
            failure_reason = "全文替换结果丢失了原有标题、列表、加粗或表格结构，已阻止写入。"
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue
        consistency_issue = _document_quality_review_issue(
            intent="edit_existing_document",
            lesson=lesson,
            request_kwargs=request_kwargs,
            result=result,
            operation=operation,
            new_document=new_document,
            selection_excerpt=target_excerpt,
            target_scope=target_scope,
        )
        if consistency_issue:
            failure_reason = consistency_issue
            quality_repair_attempts = attempt + 1
            quality_review_status = "repair_required"
            repair_feedback = _quality_repair_feedback(
                reason=failure_reason,
                attempt=attempt,
                result=result,
            )
            continue

        return _changed(
            lesson=lesson,
            new_document=new_document,
            operation=operation,
            summary=result.summary.strip(),
            chatbot_message=result.chatbot_message.strip() or result.summary.strip(),
            section_titles=result.section_titles,
            reason="板书文档编辑 AI 已根据解析出的目标位置和指令更新板书。",
            quality_repair_attempts=quality_repair_attempts,
            quality_review_status="pass",
        )

    return _no_change(
        lesson,
        failure_reason,
        quality_repair_attempts=quality_repair_attempts,
        quality_review_status=quality_review_status,
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
    current_heading_titles = set(_document_heading_titles(current_document) or _markdown_heading_titles(current_text))
    replacement_heading_titles = set(_markdown_heading_titles(replacement))
    reused_document_headings = current_heading_titles.intersection(replacement_heading_titles)
    if len(reused_document_headings) >= 2 and len(replacement) > len(selection) * 2:
        return True
    if len(replacement) > max(len(selection) * 4, 1200) and len(replacement) > len(current_text) * 0.6:
        return True
    prefix = current_text[: min(240, len(current_text))]
    return len(prefix) >= 80 and prefix in replacement and len(replacement) > len(selection) * 2


def _markdown_heading_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in re.finditer(r"(?m)^\s*#{1,6}\s+(?P<title>.+?)\s*$", text or ""):
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        if title:
            titles.append(title)
    return titles


def _document_heading_titles(document: BoardDocument) -> list[str]:
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    content = content_json.get("content")
    if not isinstance(content, list):
        return []
    titles: list[str] = []

    def node_text(value: Any) -> str:
        if isinstance(value, dict):
            if value.get("type") == "text":
                text = value.get("text")
                return text if isinstance(text, str) else ""
            children = value.get("content")
            if isinstance(children, list):
                return "".join(node_text(child) for child in children)
        return ""

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "heading":
                title = re.sub(r"\s+", " ", node_text(value)).strip()
                if title:
                    titles.append(title)
            children = value.get("content")
            if isinstance(children, list):
                for child in children:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(content)
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
    quality_repair_attempts: int = 0,
    quality_review_status: str = "not_run",
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
        quality_repair_attempts=quality_repair_attempts,
        quality_review_status=quality_review_status,
    )


def _no_change(
    lesson: Lesson,
    reason: str,
    *,
    quality_repair_attempts: int = 0,
    quality_review_status: str = "not_run",
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
        quality_repair_attempts=quality_repair_attempts,
        quality_review_status=quality_review_status,
    )


def _generate_board_document_edit_with_retry(**kwargs) -> BoardDocumentEditResult | None:
    result = openai_course_ai.generate_board_document_edit(**kwargs)
    if result is not None:
        return result
    return openai_course_ai.generate_board_document_edit(**kwargs)


def _request_board_document_edit(
    request_kwargs: dict[str, object],
    *,
    repair_feedback: dict[str, object] | None,
) -> BoardDocumentEditResult | None:
    kwargs = dict(request_kwargs)
    if repair_feedback:
        context = dict(kwargs.get("learning_requirement_context") or {})
        context["document_quality_repair"] = repair_feedback
        kwargs["learning_requirement_context"] = context
    return _generate_board_document_edit_with_retry(**kwargs)


def _model_output_quality_issue(result: BoardDocumentEditResult) -> str | None:
    if looks_like_html_content(result.content_text):
        return "板书文档编辑 AI 在 content_text 中返回了 HTML 标签，必须改写为 Markdown / 普通文本。"
    if result.content_html.strip():
        return "板书文档编辑 AI 返回了 content_html；模型输出必须只提供 Markdown / 普通文本 content_text。"
    return None


def _document_quality_review_issue(
    *,
    intent: str,
    lesson: Lesson,
    request_kwargs: dict[str, object],
    result: BoardDocumentEditResult,
    operation: str,
    new_document: BoardDocument,
    selection_excerpt: str | None,
    target_scope: str | None,
) -> str | None:
    review = openai_course_ai.generate_board_document_quality_review(
        intent=intent,
        lesson_title=str(request_kwargs.get("lesson_title") or lesson.title),
        learning_requirement_context=dict(request_kwargs.get("learning_requirement_context") or {}),
        operation=operation,
        candidate_title=result.title.strip() or new_document.title,
        candidate_content_text=_compact_text(_document_text(new_document), limit=_QUALITY_REVIEW_CONTENT_CHARS),
        resource_summary=str(request_kwargs.get("resource_summary") or ""),
        current_document_title=lesson.board_document.title,
        target_scope=target_scope,
        selection_excerpt=selection_excerpt,
        section_titles=result.section_titles,
    )
    if review is None or review.status != "repair_required":
        return None

    issues = [item.strip() for item in review.issues if item.strip()]
    instruction = review.repair_instruction.strip()
    if issues and instruction:
        return f"板书候选内容一致性审查未通过：{'；'.join(issues)}。修复要求：{instruction}"
    if issues:
        return f"板书候选内容一致性审查未通过：{'；'.join(issues)}。"
    if instruction:
        return f"板书候选内容一致性审查未通过。修复要求：{instruction}"
    return "板书候选内容一致性审查未通过。"


def _quality_repair_feedback(
    *,
    reason: str,
    attempt: int,
    result: BoardDocumentEditResult | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "previous_output_rejected",
        "attempt_number": attempt + 1,
        "failure_reason": reason,
        "repair_instruction": (
            "上一版板书编辑结果没有通过后端质量门禁。请不要放宽格式要求，"
            "而是重写不合格内容，返回合格的 operation 和 Markdown / 普通文本 content_text；"
            "content_html 必须为空字符串。"
        ),
        "required_contract": [
            "content_text 使用 ChatGPT 风格 Markdown / 普通文本，不包含 HTML 标签。",
            "content_html 为空字符串。",
            "真实公式以外的普通文字不得使用公式定界符或公式节点。",
            "全文改写或缩短必须保留标题、列表、加粗、表格等必要文档结构。",
            "局部编辑必须返回目标片段，不得把整篇文档塞进局部替换。",
        ],
    }
    if result is not None:
        payload.update(
            {
                "previous_operation": result.operation,
                "previous_title": result.title,
                "previous_content_text_excerpt": _compact_repair_excerpt(result.content_text),
                "previous_content_html_excerpt": _compact_repair_excerpt(result.content_html),
                "previous_summary": result.summary,
            }
        )
    return payload


def _compact_repair_excerpt(value: str) -> str:
    return _compact_text(value, limit=_QUALITY_REPAIR_EXCERPT_CHARS)


def _compact_text(value: str, *, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n..."


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
    focus_key = re.sub(r"\s+", "", focus.excerpt or "")
    expanded_key = re.sub(r"\s+", "", expanded)
    if focus_key and focus_key not in expanded_key:
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
