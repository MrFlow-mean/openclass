from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.image import Image as DocxImage
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.models import BoardDocument, DocumentPageSettings
from app.services.docx_quality_check import assert_docx_export_quality
from app.services.docx_styles import apply_textbook_docx_styles
from app.services.rich_document import core as rd
from app.services.teaching_diagram import parse_teaching_diagram, render_teaching_diagram_png

_CODE_FONT = "Courier New"
_CODE_LANGUAGE_LABELS = {
    "bash": "Shell",
    "c": "C",
    "cpp": "C++",
    "css": "CSS",
    "html": "HTML",
    "java": "Java",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "json": "JSON",
    "pseudocode": "伪代码",
    "py": "Python",
    "python": "Python",
    "rust": "Rust",
    "sql": "SQL",
    "ts": "TypeScript",
    "typescript": "TypeScript",
}


def _normalized_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"^[#\s]+", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip().rstrip("。:：")


def _chapter_prefix(title: str) -> str:
    match = re.match(r"\s*(\d+)(?:[.．]\d+)*", unicodedata.normalize("NFKC", title or ""))
    return match.group(1) if match else ""


def _sequence_number(prefix: str, index: int) -> str:
    return f"{prefix}-{index}" if prefix else str(index)


def _effective_page_settings(document: BoardDocument) -> DocumentPageSettings:
    settings = document.page_settings
    if settings.model_dump(mode="json") == DocumentPageSettings().model_dump(mode="json"):
        return settings.model_copy(
            update={
                "header_text": document.title,
                "show_page_number": True,
            }
        )
    return settings


def _set_cell_margins(cell, *, top: int = 35, start: int = 80, bottom: int = 35, end: int = 80) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _set_cell_width(cell, width_twips: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(width_twips))
    tc_w.set(qn("w:type"), "dxa")


def _set_code_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = borders.find(qn(f"w:{edge_name}"))
        if edge is None:
            edge = OxmlElement(f"w:{edge_name}")
            borders.append(edge)
        if edge_name in {"top", "bottom"}:
            edge.set(qn("w:val"), "single")
            edge.set(qn("w:sz"), "8")
            edge.set(qn("w:color"), "8C8C8C")
        else:
            edge.set(qn("w:val"), "nil")


def _set_table_grid(table, widths: tuple[int, int]) -> None:
    table.autofit = False
    tbl_grid = table._tbl.tblGrid
    for child in list(tbl_grid):
        tbl_grid.remove(child)
    for width in widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(width))
        tbl_grid.append(grid_col)
    tbl_w = table._tbl.tblPr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        table._tbl.tblPr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")


def _add_code_listing(
    target: DocxDocument,
    value: str,
    *,
    language: str,
    title: str,
    number: str,
) -> None:
    caption = target.add_paragraph(style="OpenClass Code Caption")
    caption_text = f"代码清单 {number}"
    if title:
        caption_text += f"　{title}"
    caption.add_run(caption_text)

    lines = value.splitlines() or [""]
    table = target.add_table(rows=len(lines), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_grid(table, (620, 8240))
    _set_code_table_borders(table)

    for index, (row, line) in enumerate(zip(table.rows, lines), start=1):
        rd._set_table_row_flag(row, "cantSplit")
        number_cell, code_cell = row.cells
        _set_cell_width(number_cell, 620)
        _set_cell_width(code_cell, 8240)
        _set_cell_margins(number_cell, start=30, end=90)
        _set_cell_margins(code_cell, start=100, end=60)
        number_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        code_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

        number_paragraph = number_cell.paragraphs[0]
        number_paragraph.style = target.styles["OpenClass Code Number"]
        number_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        number_paragraph.paragraph_format.space_before = Pt(0)
        number_paragraph.paragraph_format.space_after = Pt(0)
        number_paragraph.paragraph_format.keep_with_next = index < len(lines)
        number_run = number_paragraph.add_run(str(index))
        rd._set_run_font(number_run, _CODE_FONT, east_asia_font="STSong")
        number_run.font.size = Pt(8.5)
        number_run.font.color.rgb = RGBColor(118, 118, 118)
        rd._disable_run_proofing(number_run)

        code_paragraph = code_cell.paragraphs[0]
        code_paragraph.style = target.styles["OpenClass Code Line"]
        code_paragraph.paragraph_format.space_before = Pt(0)
        code_paragraph.paragraph_format.space_after = Pt(0)
        code_paragraph.paragraph_format.line_spacing = 1.0
        code_paragraph.paragraph_format.keep_with_next = index < len(lines)
        code_run = code_paragraph.add_run(line)
        rd._set_run_font(code_run, _CODE_FONT, east_asia_font="STSong")
        code_run.font.size = Pt(9)
        rd._disable_run_proofing(code_run)


def _add_figure_caption(target: DocxDocument, *, number: str, caption: str) -> None:
    paragraph = target.add_paragraph(style="OpenClass Figure Caption")
    text = f"图 {number}"
    if caption:
        text += f"　{caption}"
    paragraph.add_run(text)


def _add_picture_figure(
    target: DocxDocument,
    image_bytes: bytes,
    *,
    number: str,
    caption: str,
    width_cm: float = 14.2,
    max_height_cm: float = 11.5,
) -> None:
    paragraph = target.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.paragraph_format.space_after = Pt(1)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.keep_together = True
    try:
        image = DocxImage.from_blob(image_bytes)
        aspect_ratio = image.px_width / image.px_height
        fitted_width_cm = min(width_cm, max_height_cm * aspect_ratio)
    except (AttributeError, ValueError, ZeroDivisionError):
        fitted_width_cm = width_cm
    paragraph.add_run().add_picture(io.BytesIO(image_bytes), width=Cm(fitted_width_cm))
    _add_figure_caption(target, number=number, caption=caption)


def _code_title(language: str, explicit_title: str) -> str:
    if explicit_title:
        return explicit_title
    return _CODE_LANGUAGE_LABELS.get(language.lower(), language)


def export_docx(document: BoardDocument, path: Path) -> Path:
    target = DocxDocument()
    apply_textbook_docx_styles(target)
    rd._apply_page_settings(target, _effective_page_settings(document))
    target.add_heading(document.title, level=0)
    current_page_units = 3.0

    parser = rd._DocxBlockParser()
    content_html = (document.content_html or "").strip()
    content_text = (document.content_text or "").strip()
    if content_html and content_text and rd._html_has_visible_raw_math_text(content_html):
        content_html = rd.text_to_html(content_text)
    elif content_html:
        content_html = rd._repair_suspicious_math_html(content_html)
    else:
        content_html = rd.text_to_html(content_text)
    parser.feed(content_html)
    parser._flush()
    blocks = parser.blocks or [("p", [("text", line)], {}) for line in document.content_text.splitlines() if line.strip()]
    blocks = rd._normalize_fenced_docx_blocks(blocks)

    chapter_prefix = _chapter_prefix(document.title)
    code_listing_index = 0
    figure_index = 0
    first_h1_checked = False

    for tag, fragments, attrs in blocks:
        text = rd._fragment_text(fragments)
        if tag == "h1":
            if not first_h1_checked:
                first_h1_checked = True
                if _normalized_title(text) == _normalized_title(document.title):
                    continue
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
            paragraph = rd._add_fragment_paragraph(target, fragments, style="List Bullet")
            paragraph.paragraph_format.widow_control = True
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "blockquote":
            paragraph = rd._add_fragment_paragraph(target, fragments, style="Intense Quote")
            paragraph.paragraph_format.keep_together = True
            current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
        elif tag == "img":
            src = str(attrs.get("src") or "").strip()
            image_bytes = rd._decode_data_uri(src)
            if image_bytes:
                figure_index += 1
                _add_picture_figure(
                    target,
                    image_bytes,
                    number=_sequence_number(chapter_prefix, figure_index),
                    caption=str(attrs.get("alt") or text).strip(),
                )
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
            language = str(attrs.get("language") or "").strip()
            block_kind = str(attrs.get("blockKind") or "").strip()
            if block_kind == "diagram" or language == "openclass-diagram":
                spec = parse_teaching_diagram(text)
                figure_index += 1
                _add_picture_figure(
                    target,
                    render_teaching_diagram_png(spec),
                    number=_sequence_number(chapter_prefix, figure_index),
                    caption=spec.caption,
                )
                current_page_units = rd._advance_page_units(current_page_units, 12)
            elif language:
                code_listing_index += 1
                number = str(attrs.get("listingNumber") or "").strip() or _sequence_number(
                    chapter_prefix,
                    code_listing_index,
                )
                _add_code_listing(
                    target,
                    text,
                    language=language,
                    title=_code_title(language, str(attrs.get("listingTitle") or "").strip()),
                    number=number,
                )
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))
            else:
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
            has_inline_code = any(kind == "code" for kind, _value in fragments)
            formula_latex = None if has_inline_code else rd._formula_only_latex(text)
            if formula_latex:
                paragraph = target.add_paragraph(style="OpenClass Formula")
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                rd._append_math(paragraph, formula_latex, display=True)
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units("math", text))
            else:
                paragraph = rd._add_fragment_paragraph(target, fragments, style="OpenClass Body Text")
                paragraph.paragraph_format.widow_control = True
                current_page_units = rd._advance_page_units(current_page_units, rd._estimated_block_units(tag, text))

    path.parent.mkdir(parents=True, exist_ok=True)
    target.save(path)
    assert_docx_export_quality(path)
    return path
