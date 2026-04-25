from __future__ import annotations

import html
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm

from app.models import BoardDocument, DocumentPageSettings


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
    page_settings: DocumentPageSettings | dict[str, Any] | None = None,
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
        "page_settings": page_settings or DocumentPageSettings(),
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
        or left.page_settings.model_dump(mode="json") != right.page_settings.model_dump(mode="json")
    )


def append_html_section(document: BoardDocument, section_html: str) -> BoardDocument:
    next_html = "\n".join(part for part in [document.content_html.strip(), section_html.strip()] if part)
    return build_document(
        title=document.title,
        content_html=next_html,
        document_id=document.id,
        page_settings=document.page_settings,
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
            return build_document(
                title=document.title,
                content_html=next_html,
                document_id=document.id,
                page_settings=document.page_settings,
            )

    if selected in document.content_text:
        next_text = document.content_text.replace(selected, replacement, 1)
        return build_document(
            title=document.title,
            content_text=next_text,
            document_id=document.id,
            page_settings=document.page_settings,
        )

    if escaped_selection in document.content_html:
        next_html = document.content_html.replace(escaped_selection, inline_replacement_html, 1)
        return build_document(
            title=document.title,
            content_html=next_html,
            document_id=document.id,
            page_settings=document.page_settings,
        )

    if selected in document.content_html:
        next_html = document.content_html.replace(selected, inline_replacement_html, 1)
        return build_document(
            title=document.title,
            content_html=next_html,
            document_id=document.id,
            page_settings=document.page_settings,
        )

    next_text = f"{document.content_text.rstrip()}\n\n{replacement}".strip()
    return build_document(
        title=document.title,
        content_text=next_text,
        document_id=document.id,
        page_settings=document.page_settings,
    )


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
        self.blocks: list[tuple[str, str, dict[str, Any]]] = []
        self._tag_stack: list[str] = []
        self._buffer: list[str] = []
        self._attrs_stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            self._flush()
            self._tag_stack.append(tag)
            self._attrs_stack.append(dict(attrs))
        elif tag == "img":
            attr_map = dict(attrs)
            src = (attr_map.get("src") or "").strip()
            alt = (attr_map.get("alt") or "").strip()
            self.blocks.append(("img", alt, {"src": src, "alt": alt}))
        elif tag == "br":
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            current = self._tag_stack.pop() if self._tag_stack else tag
            attrs = self._attrs_stack.pop() if self._attrs_stack else {}
            self._flush(current, attrs)

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def _flush(self, tag: str | None = None, attrs: dict[str, Any] | None = None) -> None:
        text = html.unescape("".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.blocks.append((tag or "p", re.sub(r"\s+", " ", text), attrs or {}))


def _page_size_cm(page_size: str) -> tuple[float, float]:
    if page_size == "letter":
        return 21.59, 27.94
    if page_size == "a3":
        return 29.7, 42.0
    return 21.0, 29.7


def _margin_cm(preset: str) -> float:
    if preset == "narrow":
        return 1.27
    if preset == "wide":
        return 3.18
    return 2.54


def _apply_page_settings(target: DocxDocument, settings: DocumentPageSettings) -> None:
    section = target.sections[0]
    width_cm, height_cm = _page_size_cm(settings.page_size)
    if settings.orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(height_cm)
        section.page_height = Cm(width_cm)
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(width_cm)
        section.page_height = Cm(height_cm)

    margin = Cm(_margin_cm(settings.margin_preset))
    section.top_margin = margin
    section.bottom_margin = margin
    section.left_margin = margin
    section.right_margin = margin

    cols = section._sectPr.xpath("./w:cols")
    if cols:
        cols[0].set(qn("w:num"), str(settings.columns))

    if settings.header_text:
        header_paragraph = section.header.paragraphs[0]
        header_paragraph.text = settings.header_text

    footer = section.footer
    footer.paragraphs[0].text = settings.footer_text or ""
    if settings.show_page_number:
        page_number_paragraph = footer.add_paragraph()
        page_number_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _append_page_number_field(page_number_paragraph)


def _append_page_number_field(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


def _decode_data_uri(data_uri: str) -> bytes | None:
    if not data_uri.startswith("data:") or "," not in data_uri:
        return None
    header, payload = data_uri.split(",", 1)
    if ";base64" not in header:
        return None
    try:
        import base64

        return base64.b64decode(payload)
    except Exception:
        return None


def export_docx(document: BoardDocument, path: Path) -> Path:
    target = DocxDocument()
    _apply_page_settings(target, document.page_settings)
    target.add_heading(document.title, level=0)

    parser = _DocxBlockParser()
    parser.feed(document.content_html or text_to_html(document.content_text))
    parser._flush()
    blocks = parser.blocks or [("p", line, {}) for line in document.content_text.splitlines() if line.strip()]

    for tag, text, attrs in blocks:
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
        elif tag == "img":
            src = str(attrs.get("src") or "").strip()
            image_bytes = _decode_data_uri(src)
            if image_bytes:
                target.add_picture(io.BytesIO(image_bytes))
            elif text:
                target.add_paragraph(f"[图片] {text}")
        else:
            target.add_paragraph(text)

    path.parent.mkdir(parents=True, exist_ok=True)
    target.save(path)
    return path
