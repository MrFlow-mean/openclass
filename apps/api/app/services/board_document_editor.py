from __future__ import annotations

from dataclasses import dataclass

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


def generate_from_requirements(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
    resource_summary: str,
    conversation_summary: str,
    user_instruction: str,
) -> BoardDocumentEditOutcome:
    if not is_document_empty(lesson.board_document):
        return _no_change(
            lesson,
            "当前板书不是空白文档，已阻止整体覆盖。",
        )

    result = openai_course_ai.generate_board_document_edit(
        intent="generate_from_requirements",
        lesson_title=lesson.title,
        learning_requirement_context=_requirement_context(requirements, clarification),
        current_document_title=lesson.board_document.title,
        current_document_text=_document_text(lesson.board_document),
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_instruction=user_instruction,
        selection_excerpt=None,
    )
    if not result:
        return _no_change(
            lesson,
            "板书文档编辑 AI 没有返回生成结果。",
        )

    content_text, content_html = _edit_payload(result)
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
) -> BoardDocumentEditOutcome:
    target_excerpt = _target_excerpt(selection_excerpt=selection_excerpt, focus=focus)
    if not target_excerpt and not is_document_empty(lesson.board_document):
        return _no_change(
            lesson,
            "已有板书的局部编辑需要先解析目标位置。",
        )

    result = openai_course_ai.generate_board_document_edit(
        intent="edit_existing_document",
        lesson_title=lesson.title,
        learning_requirement_context=_requirement_context(requirements, clarification),
        current_document_title=lesson.board_document.title,
        current_document_text=_document_text(lesson.board_document),
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_instruction=user_instruction,
        selection_excerpt=target_excerpt,
    )
    if not result:
        return _no_change(
            lesson,
            "板书文档编辑 AI 没有返回编辑结果。",
        )

    new_document = _apply_edit_result(
        lesson=lesson,
        result=result,
        selection_excerpt=target_excerpt,
    )
    if not document_changed(lesson.board_document, new_document):
        return _no_change(
            lesson,
            "板书文档编辑 AI 的结果没有改变当前文档。",
        )

    return _changed(
        lesson=lesson,
        new_document=new_document,
        operation=result.operation,
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
) -> BoardDocument:
    content_text, content_html = _edit_payload(result)
    if not content_text and not content_html:
        return lesson.board_document

    if result.operation == "append_section":
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

    if result.operation == "replace_selection" and selection_excerpt:
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
    )


def _edit_payload(result: BoardDocumentEditResult) -> tuple[str, str]:
    content_text = result.content_text.strip()
    content_html = result.content_html.strip()
    if not content_text and content_html:
        content_text = html_to_text(content_html)
    if content_text:
        content_html = text_to_html(content_text)
    return content_text, content_html


def _document_text(document: BoardDocument) -> str:
    return document.content_text or html_to_text(document.content_html)


def _target_excerpt(*, selection_excerpt: str | None, focus: BoardFocusRef | None) -> str | None:
    if focus and focus.excerpt.strip():
        return focus.excerpt.strip()
    return selection_excerpt.strip() if selection_excerpt else None


def _requirement_context(
    requirements: LearningRequirementSheet,
    clarification: LearningClarificationStatus,
) -> dict[str, object]:
    return {
        "summary": clarification.summary or clarification.reason or requirements.learning_goal,
        "key_facts": [item.model_dump(mode="json") for item in clarification.key_facts],
        "checklist": [item.model_dump(mode="json") for item in clarification.checklist],
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
