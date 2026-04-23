from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument

from app.models import BoardDocument


EMPTY_TIPTAP_DOC: dict[str, Any] = {"type": "doc", "content": [{"type": "paragraph"}]}


def html_to_text(content_html: str) -> str:
    without_tags = re.sub(r"</(h[1-6]|p|li|blockquote|tr)>", "\n", content_html)
    without_tags = re.sub(r"<br\s*/?>", "\n", without_tags, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", without_tags)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    compact_lines: list[str] = []
    for line in lines:
        if not line and (not compact_lines or not compact_lines[-1]):
            continue
        compact_lines.append(line)
    return "\n".join(compact_lines).strip()


def text_to_html(content_text: str) -> str:
    parts: list[str] = []
    for raw_line in content_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        escaped = html.escape(line)
        if line.startswith("# "):
            parts.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            parts.append(f"<h2>{html.escape(line[3:].strip())}</h2>")
        elif re.match(r"^\d+[.、]\s+", line):
            parts.append(f"<p>{escaped}</p>")
        else:
            parts.append(f"<p>{escaped}</p>")
    return "\n".join(parts) or "<p></p>"


def text_to_tiptap_doc(content_text: str) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for raw_line in content_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            nodes.append(
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": line[2:].strip()}],
                }
            )
        elif line.startswith("## "):
            nodes.append(
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": line[3:].strip()}],
                }
            )
        else:
            nodes.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
    return {"type": "doc", "content": nodes or [{"type": "paragraph"}]}


def build_document(
    *,
    title: str,
    content_html: str | None = None,
    content_text: str | None = None,
    content_json: dict[str, Any] | None = None,
    document_id: str | None = None,
) -> BoardDocument:
    normalized_html = (content_html or "").strip()
    normalized_text = (content_text or "").strip()
    if not normalized_text and normalized_html:
        normalized_text = html_to_text(normalized_html)
    if not normalized_html and normalized_text:
        normalized_html = text_to_html(normalized_text)
    if not normalized_text and not normalized_html:
        normalized_html = "<p></p>"
    normalized_json = content_json or text_to_tiptap_doc(normalized_text)
    kwargs: dict[str, Any] = {
        "title": title,
        "content_json": normalized_json,
        "content_html": normalized_html,
        "content_text": normalized_text,
    }
    if document_id:
        kwargs["id"] = document_id
    return BoardDocument(**kwargs)


def is_document_empty(document: BoardDocument) -> bool:
    return not document.content_text.strip() and html_to_text(document.content_html) == ""


def document_changed(left: BoardDocument, right: BoardDocument) -> bool:
    return (
        left.title != right.title
        or left.content_html.strip() != right.content_html.strip()
        or left.content_text.strip() != right.content_text.strip()
    )


def append_html_section(document: BoardDocument, section_html: str) -> BoardDocument:
    next_html = "\n".join(part for part in [document.content_html.strip(), section_html.strip()] if part)
    return build_document(
        title=document.title,
        content_html=next_html,
        document_id=document.id,
    )


def replace_selection_in_document(
    document: BoardDocument,
    *,
    selection_text: str,
    replacement_text: str,
) -> BoardDocument:
    selected = selection_text.strip()
    replacement = replacement_text.strip()
    if not selected:
        return document

    escaped_selection = html.escape(selected)
    replacement_html = text_to_html(replacement)
    inline_replacement_html = html.escape(replacement).replace("\n", "<br>")
    for tag in ("p", "h1", "h2", "h3", "li", "blockquote"):
        exact_block_html = f"<{tag}>{escaped_selection}</{tag}>"
        if exact_block_html in document.content_html:
            next_html = document.content_html.replace(exact_block_html, replacement_html, 1)
            return build_document(title=document.title, content_html=next_html, document_id=document.id)

    if selected in document.content_text:
        next_text = document.content_text.replace(selected, replacement, 1)
        return build_document(title=document.title, content_text=next_text, document_id=document.id)

    if escaped_selection in document.content_html:
        next_html = document.content_html.replace(escaped_selection, inline_replacement_html, 1)
        return build_document(title=document.title, content_html=next_html, document_id=document.id)

    if selected in document.content_html:
        next_html = document.content_html.replace(selected, inline_replacement_html, 1)
        return build_document(title=document.title, content_html=next_html, document_id=document.id)

    next_text = f"{document.content_text.rstrip()}\n\n{replacement}".strip()
    return build_document(title=document.title, content_text=next_text, document_id=document.id)


def import_docx(path: Path, *, title: str | None = None) -> BoardDocument:
    source = DocxDocument(path)
    html_parts: list[str] = []
    text_parts: list[str] = []
    inferred_title = title or path.stem

    for paragraph in source.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = (paragraph.style.name if paragraph.style else "").lower()
        escaped = html.escape(text)
        if "heading 1" in style_name or "title" in style_name:
            inferred_title = inferred_title if title else text
            html_parts.append(f"<h1>{escaped}</h1>")
        elif "heading 2" in style_name:
            html_parts.append(f"<h2>{escaped}</h2>")
        elif "heading 3" in style_name:
            html_parts.append(f"<h3>{escaped}</h3>")
        else:
            html_parts.append(f"<p>{escaped}</p>")
        text_parts.append(text)

    for table in source.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [html.escape(cell.text.strip()) for cell in row.cells]
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
            text_parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        if rows:
            html_parts.append("<table><tbody>" + "".join(rows) + "</tbody></table>")

    return build_document(
        title=inferred_title,
        content_html="\n".join(html_parts),
        content_text="\n".join(text_parts),
    )


class _DocxBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, str]] = []
        self._tag_stack: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            self._flush()
            self._tag_stack.append(tag)
        elif tag == "br":
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            current = self._tag_stack.pop() if self._tag_stack else tag
            self._flush(current)

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def _flush(self, tag: str | None = None) -> None:
        text = html.unescape("".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.blocks.append((tag or "p", re.sub(r"\s+", " ", text)))


def export_docx(document: BoardDocument, path: Path) -> Path:
    target = DocxDocument()
    target.add_heading(document.title, level=0)

    parser = _DocxBlockParser()
    parser.feed(document.content_html or text_to_html(document.content_text))
    parser._flush()
    blocks = parser.blocks or [("p", line) for line in document.content_text.splitlines() if line.strip()]

    for tag, text in blocks:
        if tag == "h1":
            target.add_heading(text, level=1)
        elif tag == "h2":
            target.add_heading(text, level=2)
        elif tag == "h3":
            target.add_heading(text, level=3)
        elif tag == "li":
            target.add_paragraph(text, style="List Bullet")
        elif tag == "blockquote":
            target.add_paragraph(text, style="Intense Quote")
        else:
            target.add_paragraph(text)

    path.parent.mkdir(parents=True, exist_ok=True)
    target.save(path)
    return path
