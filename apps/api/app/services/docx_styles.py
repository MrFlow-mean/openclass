from __future__ import annotations

from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

_BODY_FONT = "Times New Roman"
_BODY_EAST_ASIA_FONT = "STSong"
_HEADING_EAST_ASIA_FONT = "STHeiti"
_CODE_FONT = "Courier New"
_TEXT_COLOR = RGBColor(31, 31, 31)
_MUTED_COLOR = RGBColor(89, 89, 89)


def _set_style_font(
    style,
    *,
    size: float | None = None,
    bold: bool | None = None,
    color: RGBColor | None = None,
    latin_font: str = _BODY_FONT,
    east_asia_font: str = _BODY_EAST_ASIA_FONT,
) -> None:
    font = style.font
    font.name = latin_font
    if size is not None:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    if color is not None:
        font.color.rgb = color
    r_pr = style.element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:ascii"), latin_font)
    r_fonts.set(qn("w:hAnsi"), latin_font)
    r_fonts.set(qn("w:eastAsia"), east_asia_font)
    r_fonts.set(qn("w:cs"), latin_font)
    r_fonts.set(qn("w:hint"), "eastAsia")
    for theme_attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        r_fonts.attrib.pop(qn(f"w:{theme_attr}"), None)
    for inherited_tag in ("w:spacing", "w:kern"):
        inherited = r_pr.find(qn(inherited_tag))
        if inherited is not None:
            r_pr.remove(inherited)
    if size is not None:
        size_cs = r_pr.find(qn("w:szCs"))
        if size_cs is None:
            size_cs = OxmlElement("w:szCs")
            r_pr.append(size_cs)
        size_cs.set(qn("w:val"), str(round(size * 2)))


def _ensure_paragraph_style(document, style_name: str, base_style: str = "Normal"):
    styles = document.styles
    try:
        return styles[style_name]
    except KeyError:
        style = styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = styles[base_style]
        return style


def _set_paragraph_borders(style, *, color: str = "B7B7B7", size: str = "6") -> None:
    p_pr = style.element.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    for edge_name in ("top", "bottom"):
        edge = borders.find(qn(f"w:{edge_name}"))
        if edge is None:
            edge = OxmlElement(f"w:{edge_name}")
            borders.append(edge)
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), size)
        edge.set(qn("w:space"), "4")
        edge.set(qn("w:color"), color)


def _remove_paragraph_borders(style) -> None:
    p_pr = style.element.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is not None:
        p_pr.remove(borders)


def _apply_body_paragraph_tokens(style) -> None:
    paragraph = style.paragraph_format
    paragraph.line_spacing = 1.22
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(2)
    paragraph.widow_control = True


def apply_textbook_docx_styles(document) -> None:
    normal = document.styles["Normal"]
    _set_style_font(normal, size=10.5, color=_TEXT_COLOR)
    _apply_body_paragraph_tokens(normal)

    title = document.styles["Title"]
    _set_style_font(
        title,
        size=18,
        bold=True,
        color=_TEXT_COLOR,
        east_asia_font=_HEADING_EAST_ASIA_FONT,
    )
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(6)
    title.paragraph_format.keep_with_next = True
    title.paragraph_format.widow_control = True
    _remove_paragraph_borders(title)

    for style_name, size, before, after in (
        ("Heading 1", 15.5, 10, 3),
        ("Heading 2", 13, 7, 2),
        ("Heading 3", 11.5, 5, 2),
    ):
        style = document.styles[style_name]
        _set_style_font(
            style,
            size=size,
            bold=True,
            color=_TEXT_COLOR,
            east_asia_font=_HEADING_EAST_ASIA_FONT,
        )
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.keep_together = True
        style.paragraph_format.widow_control = True

    body_text = _ensure_paragraph_style(document, "OpenClass Body Text")
    _set_style_font(body_text, size=10.5, color=_TEXT_COLOR)
    _apply_body_paragraph_tokens(body_text)
    body_text.paragraph_format.first_line_indent = Cm(0.74)

    compact = _ensure_paragraph_style(document, "OpenClass Compact")
    _set_style_font(compact, size=9.5, color=_TEXT_COLOR)
    compact.paragraph_format.line_spacing = 1.08
    compact.paragraph_format.space_before = Pt(0)
    compact.paragraph_format.space_after = Pt(1)
    compact.paragraph_format.widow_control = True

    formula = _ensure_paragraph_style(document, "OpenClass Formula")
    _set_style_font(formula, size=10.5, color=_TEXT_COLOR)
    formula.paragraph_format.space_before = Pt(3)
    formula.paragraph_format.space_after = Pt(4)
    formula.paragraph_format.keep_together = True
    formula.paragraph_format.widow_control = True

    preformatted = _ensure_paragraph_style(document, "OpenClass Preformatted")
    _set_style_font(
        preformatted,
        size=9.2,
        color=_TEXT_COLOR,
        latin_font=_CODE_FONT,
        east_asia_font=_BODY_EAST_ASIA_FONT,
    )
    preformatted.paragraph_format.line_spacing = 1.05
    preformatted.paragraph_format.left_indent = Cm(0.25)
    preformatted.paragraph_format.right_indent = Cm(0.25)
    preformatted.paragraph_format.space_before = Pt(3)
    preformatted.paragraph_format.space_after = Pt(5)
    preformatted.paragraph_format.keep_together = True
    _set_paragraph_borders(preformatted)

    code_line = _ensure_paragraph_style(document, "OpenClass Code Line", base_style="OpenClass Compact")
    _set_style_font(
        code_line,
        size=9,
        color=_TEXT_COLOR,
        latin_font=_CODE_FONT,
        east_asia_font=_BODY_EAST_ASIA_FONT,
    )
    code_line.paragraph_format.line_spacing = 1.0
    code_line.paragraph_format.space_before = Pt(0)
    code_line.paragraph_format.space_after = Pt(0)

    code_number = _ensure_paragraph_style(document, "OpenClass Code Number", base_style="OpenClass Compact")
    _set_style_font(
        code_number,
        size=8.5,
        color=_MUTED_COLOR,
        latin_font=_CODE_FONT,
        east_asia_font=_BODY_EAST_ASIA_FONT,
    )
    code_number.paragraph_format.line_spacing = 1.0
    code_number.paragraph_format.space_before = Pt(0)
    code_number.paragraph_format.space_after = Pt(0)

    code_caption = _ensure_paragraph_style(document, "OpenClass Code Caption")
    _set_style_font(
        code_caption,
        size=9.5,
        bold=True,
        color=_TEXT_COLOR,
        east_asia_font=_HEADING_EAST_ASIA_FONT,
    )
    code_caption.paragraph_format.space_before = Pt(4)
    code_caption.paragraph_format.space_after = Pt(1)
    code_caption.paragraph_format.keep_with_next = True
    code_caption.paragraph_format.keep_together = True

    figure_caption = _ensure_paragraph_style(document, "OpenClass Figure Caption")
    _set_style_font(figure_caption, size=9.5, color=_MUTED_COLOR)
    figure_caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    figure_caption.paragraph_format.space_before = Pt(2)
    figure_caption.paragraph_format.space_after = Pt(6)
    figure_caption.paragraph_format.keep_together = True

    for list_style_name in ("List Bullet", "List Number"):
        try:
            list_style = document.styles[list_style_name]
        except KeyError:
            continue
        _set_style_font(list_style, size=10.5, color=_TEXT_COLOR)
        list_style.paragraph_format.line_spacing = 1.15
        list_style.paragraph_format.space_before = Pt(0)
        list_style.paragraph_format.space_after = Pt(1)
        list_style.paragraph_format.widow_control = True

    for furniture_style_name in ("Header", "Footer"):
        try:
            furniture = document.styles[furniture_style_name]
        except KeyError:
            continue
        _set_style_font(furniture, size=9, color=_MUTED_COLOR)
