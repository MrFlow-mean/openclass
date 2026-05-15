from __future__ import annotations

import html

from app.models import (
    BoardDocument,
    ChatRequest,
    LearningRequirementSheet,
    Lesson,
    ResourceReferenceContext,
)
from app.services.fallback_generator import reference_document_fallback_html
from app.services.rich_document import (
    append_html_section,
    build_document,
    is_document_empty,
)
from app.services.workflow_roles.shared import compact, message_topic


def request_section_html(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> str:
    del requirements
    topic = html.escape(message_topic(lesson, request))
    selected = compact(request.selection.excerpt, limit=260) if request.selection else ""
    parts = [f"<h1>{topic}</h1>"]
    if selected:
        parts.append(f"<blockquote>{html.escape(selected)}</blockquote>")
    return "\n".join(parts)


def append_or_replace_document(document: BoardDocument, section_html: str) -> BoardDocument:
    if is_document_empty(document):
        return build_document(
            title=document.title,
            content_html=section_html,
            document_id=document.id,
            page_settings=document.page_settings,
        )
    return append_html_section(document, section_html)


def reference_section_html(lesson: Lesson, reference_context: ResourceReferenceContext) -> str:
    return reference_document_fallback_html(lesson.title, reference_context)

