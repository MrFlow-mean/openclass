from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.models import BoardDocument
from app.services.board_asset_store import board_asset_id_from_url
from app.services.rich_document import core as rd
from app.services.docx_quality_check import assert_docx_export_quality
from app.services.docx_styles import apply_textbook_docx_styles


AssetResolver = Callable[[str], tuple[str, bytes] | None]


def export_docx(
    document: BoardDocument,
    path: Path,
    *,
    asset_resolver: AssetResolver | None = None,
) -> Path:
    target = DocxDocument()
    apply_textbook_docx_styles(target)
    rd._apply_page_settings(target, document.page_settings)
    target.add_heading(document.title, level=0)
    current_page_units = 4.0

    parser = rd._DocxBlockParser()
    content_html = (document.content_html or "").strip()
    content_text = (document.content_text or "").strip()
    content_json = document.content_json if isinstance(document.content_json, dict) else {}
    json_nodes = content_json.get("content")
    if _has_meaningful_tiptap_nodes(json_nodes):
        canonical_html = rd.tiptap_doc_to_html(content_json)
        content_html = (
            rd.text_to_html(content_text)
            if content_text and rd._html_has_visible_raw_math_text(canonical_html)
            else canonical_html
        )
    elif content_html and content_text and rd._html_has_visible_raw_math_text(content_html):
        content_html = rd.text_to_html(content_text)
    elif content_html:
        content_html = rd._repair_suspicious_math_html(content_html)
    else:
        content_html = rd.text_to_html(content_text)
    parser.feed(content_html)
    parser._flush()
    blocks = parser.blocks or [("p", [("text", line)], {}) for line in document.content_text.splitlines() if line.strip()]
    blocks = rd._normalize_fenced_docx_blocks(blocks)

    for tag, fragments, attrs in blocks:
        text = rd._fragment_text(fragments)
        if tag == "h1":
            paragraph = target.add_heading("", level=1)
            paragraph.paragraph_format.keep_with_next = True
            rd._append_fragments(paragraph, fragments)
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "h2":
            paragraph = target.add_heading("", level=2)
            paragraph.paragraph_format.keep_with_next = True
            rd._append_fragments(paragraph, fragments)
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "h3":
            paragraph = target.add_heading("", level=3)
            paragraph.paragraph_format.keep_with_next = True
            rd._append_fragments(paragraph, fragments)
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "li":
            rd._add_fragment_paragraph(target, fragments, style="List Bullet")
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "blockquote":
            rd._add_fragment_paragraph(target, fragments, style="Intense Quote")
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "img":
            src = str(attrs.get("src") or "").strip()
            image_bytes = rd._decode_data_uri(src)
            image_mime_type = ""
            asset_id = str(attrs.get("asset_id") or "").strip() or board_asset_id_from_url(src)
            if image_bytes is None and asset_id and asset_resolver is not None:
                resolved = asset_resolver(asset_id)
                if resolved is not None:
                    image_mime_type, image_bytes = resolved
            if image_bytes:
                compatible_bytes = _docx_compatible_image_bytes(image_bytes, image_mime_type)
                shape = target.add_picture(io.BytesIO(compatible_bytes))
                section = target.sections[-1]
                max_width = section.page_width - section.left_margin - section.right_margin
                if shape.width > max_width:
                    scale = max_width / shape.width
                    shape.width = max_width
                    shape.height = int(shape.height * scale)
            elif text:
                target.add_paragraph(f"[图片] {text}")
            current_page_units = rd._advance_page_units(current_page_units, 10)
        elif tag == "pageBreak":
            target.add_page_break()
            current_page_units = 0
        elif tag == "table":
            rows = attrs.get("rows")
            if isinstance(rows, list):
                current_page_units = rd._maybe_page_break_before_table(target, rows, current_page_units)
                rd._add_fragment_table(target, rows)
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_table_units(rows))
        elif tag == "pre":
            rd._add_preformatted_paragraph(target, text)
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "math" or (len(fragments) == 1 and fragments[0][0] == "math"):
            paragraph = target.add_paragraph(style="OpenClass Formula")
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            rd._append_fragments(paragraph, fragments, auto_math=False, display_math=True)
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units("math", text))
        else:
            fenced_text = rd._fenced_code_text(text)
            if fenced_text is not None:
                rd._add_preformatted_paragraph(target, fenced_text)
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units("pre", fenced_text))
                continue
            formula_latex = rd._formula_only_latex(text)
            if formula_latex:
                paragraph = target.add_paragraph(style="OpenClass Formula")
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                rd._append_math(paragraph, formula_latex, display=True)
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units("math", text))
            else:
                rd._add_fragment_paragraph(target, fragments, style="OpenClass Body Text")
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))

    path.parent.mkdir(parents=True, exist_ok=True)
    target.save(path)
    assert_docx_export_quality(path)
    return path


def _docx_compatible_image_bytes(content: bytes, mime_type: str) -> bytes:
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    is_webp = (
        normalized_mime == "image/webp"
        or (len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP")
    )
    if not is_webp:
        return content

    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        image.seek(0)
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        converted = image.convert("RGBA" if has_alpha else "RGB")
        output = io.BytesIO()
        converted.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _has_meaningful_tiptap_nodes(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for node in value:
        if not isinstance(node, dict):
            continue
        if node.get("type") != "paragraph":
            return True
        content = node.get("content")
        if isinstance(content, list) and content:
            return True
    return False
