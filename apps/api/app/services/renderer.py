from __future__ import annotations

from app.models import ResourceReferenceContext
from app.services.fallback_generator import generic_document_fallback_html, reference_document_fallback_html
from app.services.rich_document import build_document


def build_document_for_topic_render(
    topic: str,
    reference_context: ResourceReferenceContext | None = None,
):
    if reference_context is not None:
        content_html = reference_document_fallback_html(topic, reference_context)
    else:
        content_html = generic_document_fallback_html(topic)
    return build_document(title=topic, content_html=content_html)
