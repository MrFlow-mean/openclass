from __future__ import annotations

from app.services.rich_document import build_document


def build_document_for_topic_render(
    topic: str,
):
    return build_document(title=topic)
