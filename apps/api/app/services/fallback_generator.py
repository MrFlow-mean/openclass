from __future__ import annotations

import html

from app.models import ResourceReferenceContext
from app.services.reference_utils import compact_reference_text, reference_key_points, reference_passages


def reference_document_fallback_html(topic: str, reference_context: ResourceReferenceContext) -> str:
    title = html.escape(reference_context.chapter_title or topic)
    lead = compact_reference_text(reference_context.summary, limit=520)
    passages = reference_passages(reference_context)
    points = reference_key_points(reference_context)

    body = [f"<h1>{title}</h1>"]
    seen: set[str] = set()
    for item in [*passages[:6], *points[:6], lead]:
        compact = compact_reference_text(item, limit=700)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        body.append(f"<p>{html.escape(compact)}</p>")
    return "\n".join(body).strip()
