from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import ResourceVisualEvidence
from app.services.ai_logging import ai_usage_logger
from app.services.openai_course_ai import openai_course_ai


VisualRecreationKind = Literal["table", "equation", "diagram", "svg", "original"]
VisualRecreationStatus = Literal["recreated", "original_only", "unsupported"]

_AI_MAX_IMAGE_DATA_URL_CHARS = int(os.getenv("OPENCLASS_VISUAL_RECREATION_MAX_DATA_URL_CHARS", "1600000"))
_AI_ENABLED = os.getenv("OPENCLASS_VISUAL_RECREATION_AI", "").strip().lower() in {"1", "true", "yes", "on"}
_ARROW_RE = re.compile(r"\s*(?:->|=>|→|⇒|➜|⟶|⟹)\s*")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(frozen=True)
class VisualRecreationResult:
    kind: VisualRecreationKind
    status: VisualRecreationStatus
    html: str
    confidence: float = 0.0
    note: str = ""


class _AIVisualRecreation(BaseModel):
    status: VisualRecreationStatus = "unsupported"
    kind: VisualRecreationKind = "original"
    html: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = ""


def recreate_resource_visual_evidence(visual: ResourceVisualEvidence) -> VisualRecreationResult:
    """Build a trusted teaching-document recreation before exposing the original visual."""
    content_type = (visual.content_type or "").strip().lower()
    if content_type == "table":
        table_html = _table_html_from_text(visual.source_text or visual.caption)
        if table_html:
            return VisualRecreationResult(kind="table", status="recreated", html=table_html, confidence=0.88)

    if content_type == "equation":
        equation_html = _equation_html_from_text(visual.source_text or visual.caption)
        if equation_html:
            return VisualRecreationResult(kind="equation", status="recreated", html=equation_html, confidence=0.82)

    diagram_html = _diagram_svg_from_text(visual.source_text or visual.caption)
    if diagram_html:
        return VisualRecreationResult(kind="diagram", status="recreated", html=diagram_html, confidence=0.72)

    ai_result = _ai_recreate_visual(visual)
    if ai_result is not None and ai_result.status == "recreated" and ai_result.html:
        return ai_result

    return VisualRecreationResult(
        kind="original",
        status="original_only",
        html=_original_only_html(visual),
        confidence=0.0,
        note="no_reliable_recreation",
    )


def _table_html_from_text(text: str) -> str:
    rows = _table_rows_from_text(text)
    if len(rows) < 2:
        return ""
    width = max(len(row) for row in rows)
    if width < 2:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in rows]
    head, body = normalized[0], normalized[1:]
    parts = ['<table class="openclass-resource-visual__replica-table">', "<thead><tr>"]
    parts.extend(f"<th>{html.escape(cell)}</th>" for cell in head)
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        parts.extend(f"<td>{html.escape(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _table_rows_from_text(text: str) -> list[list[str]]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return []
    rows: list[list[str]] = []
    if any("|" in line for line in lines):
        for line in lines:
            if _TABLE_SEPARATOR_RE.match(line):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 2:
                rows.append(cells)
        return rows

    for line in lines:
        cells = [cell.strip() for cell in re.split(r"\t+|\s{2,}", line) if cell.strip()]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def _equation_html_from_text(text: str) -> str:
    formula = _clean_formula_text(text)
    if not formula:
        return ""
    return (
        '<div class="openclass-resource-visual__formula" '
        f'data-openclass-replica-kind="equation">{html.escape(formula)}</div>'
    )


def _clean_formula_text(text: str) -> str:
    compact = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    if not compact:
        return ""
    if compact.startswith("$$") and compact.endswith("$$"):
        compact = compact[2:-2].strip()
    if compact.startswith(r"\[") and compact.endswith(r"\]"):
        compact = compact[2:-2].strip()
    if compact.startswith("$") and compact.endswith("$"):
        compact = compact[1:-1].strip()
    if not re.search(r"[=<>≤≥≈≠+\-*/^_\\]|\\(?:frac|sum|int|sqrt|lim|alpha|beta|gamma|delta)", compact):
        return ""
    return compact[:240]


def _diagram_svg_from_text(text: str) -> str:
    labels = [part.strip() for part in _ARROW_RE.split(html.unescape(text or "")) if part.strip()]
    labels = [re.sub(r"\s+", " ", label)[:24] for label in labels]
    if not 2 <= len(labels) <= 5:
        return ""

    box_width = 118
    gap = 52
    width = len(labels) * box_width + (len(labels) - 1) * gap + 24
    height = 86
    y = 20
    parts = [
        (
            f'<svg class="openclass-resource-visual__replica-svg" viewBox="0 0 {width} {height}" '
            'role="img" aria-label="复刻流程图" xmlns="http://www.w3.org/2000/svg">'
        ),
        "<defs><marker id=\"openclass-arrow\" markerWidth=\"8\" markerHeight=\"8\" refX=\"7\" refY=\"4\" orient=\"auto\">"
        "<path d=\"M0,0 L8,4 L0,8 Z\" fill=\"#334155\" /></marker></defs>",
    ]
    for index, label in enumerate(labels):
        x = 12 + index * (box_width + gap)
        parts.append(f'<rect x="{x}" y="{y}" width="{box_width}" height="44" rx="7" fill="#f8fafc" stroke="#94a3b8" />')
        parts.append(
            f'<text x="{x + box_width / 2:.1f}" y="{y + 27}" text-anchor="middle" '
            'font-size="13" fill="#111827">'
            f"{html.escape(label)}</text>"
        )
        if index < len(labels) - 1:
            line_x1 = x + box_width + 8
            line_x2 = x + box_width + gap - 8
            parts.append(
                f'<line x1="{line_x1}" y1="{y + 22}" x2="{line_x2}" y2="{y + 22}" '
                'stroke="#334155" stroke-width="1.8" marker-end="url(#openclass-arrow)" />'
            )
    parts.append("</svg>")
    return "".join(parts)


def _ai_recreate_visual(visual: ResourceVisualEvidence) -> VisualRecreationResult | None:
    if not _AI_ENABLED or openai_course_ai.client is None or not _supported_image_data_url(visual.image_src):
        return None

    model = os.getenv("OPENCLASS_VISUAL_RECREATION_MODEL") or openai_course_ai.config.default_model
    started_at = time.perf_counter()
    try:
        response = openai_course_ai.client.responses.parse(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You recreate visual evidence for a general teaching document. "
                        "Return only structured data. Use a compact, sanitized SVG or simple HTML table/formula. "
                        "Do not invent subject-specific content. If the image cannot be faithfully recreated, "
                        "return original_only with a short note."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Recreate the visual as a compact educational diagram. "
                                f"Caption/OCR text: {visual.source_text or visual.caption or 'unknown'}"
                            ),
                        },
                        {"type": "input_image", "image_url": visual.image_src, "detail": "high"},
                    ],
                },
            ],
            text_format=_AIVisualRecreation,
        )
        parsed = response.output_parsed
        if not isinstance(parsed, _AIVisualRecreation):
            return None
        safe_html = sanitize_visual_recreation_html(parsed.html)
        ai_usage_logger.log_event(
            "resource_visual_recreation",
            provider="openai",
            model=model,
            source_locator=visual.source_locator or "",
            content_type=visual.content_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            parsed_output=parsed,
        )
        if parsed.status != "recreated" or not safe_html:
            return None
        return VisualRecreationResult(
            kind=parsed.kind,
            status="recreated",
            html=safe_html,
            confidence=parsed.confidence,
            note=parsed.note,
        )
    except Exception as exc:  # pragma: no cover - provider/runtime dependent
        ai_usage_logger.log_event(
            "resource_visual_recreation_error",
            provider="openai",
            model=model,
            source_locator=visual.source_locator or "",
            content_type=visual.content_type,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            error=str(exc),
        )
        return None


def sanitize_visual_recreation_html(value: str) -> str:
    parser = _SafeVisualHTMLParser()
    parser.feed(value or "")
    parser.close()
    return parser.html.strip()


def _original_only_html(visual: ResourceVisualEvidence) -> str:
    label = visual.caption or "资料视觉素材"
    return (
        '<p class="openclass-resource-visual__unrecreated">'
        f"{html.escape(label)}：暂未可靠复刻，已保留原图来源供核对。</p>"
    )


def _supported_image_data_url(value: str) -> bool:
    return value.startswith("data:image/") and 0 < len(value) <= _AI_MAX_IMAGE_DATA_URL_CHARS


class _SafeVisualHTMLParser(HTMLParser):
    _ALLOWED_TAGS = {
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "defs",
        "marker",
        "title",
        "desc",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "div",
        "p",
        "span",
        "strong",
        "em",
        "br",
    }
    _VOID_TAGS = {"br"}
    _ALLOWED_ATTRS = {
        "class",
        "role",
        "aria-label",
        "xmlns",
        "viewbox",
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "fill",
        "stroke",
        "stroke-width",
        "markerWidth",
        "markerHeight",
        "markerwidth",
        "markerheight",
        "refX",
        "refY",
        "refx",
        "refy",
        "orient",
        "marker-end",
        "text-anchor",
        "font-size",
        "font-family",
        "colspan",
        "rowspan",
        "data-openclass-replica-kind",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._stack: list[str] = []

    @property
    def html(self) -> str:
        while self._stack:
            self._parts.append(f"</{self._stack.pop()}>")
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized not in self._ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        for key, value in attrs:
            if not key:
                continue
            normalized_key = key.strip()
            if normalized_key.lower().startswith("on") or normalized_key not in self._ALLOWED_ATTRS:
                continue
            if value is None:
                continue
            safe_attrs.append(f'{normalized_key}="{html.escape(value, quote=True)}"')
        suffix = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self._parts.append(f"<{normalized}{suffix}>")
        if normalized not in self._VOID_TAGS:
            self._stack.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized not in self._ALLOWED_TAGS:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            current = self._stack.pop()
            self._parts.append(f"</{current}>")
            if current == normalized:
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(html.escape(data))
