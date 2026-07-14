from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_NS = {"w": _WORD_NS, "m": _MATH_NS}
_RAW_LATEX_RE = re.compile(
    r"\\(?:frac|dfrac|tfrac|begin|end|left|right|xi|Phi|Delta|sum|int|lim|varepsilon|epsilon)\b"
)
_RAW_MARKERS = (
    r"\(",
    r"\[",
    "$$",
    "displaystyle",
    "begincases",
    "a ^ x",
    "Phi(x)",
)
_HTML_RE = re.compile(r"</?(?:h[1-6]|p|span|div|table|thead|tbody|tr|td|th|ul|ol|li|strong|em)\b", re.I)
_CODE_IN_MATH_RE = re.compile(
    r"\b(?:def\s+[A-Za-z_]\w*\s*\(|return\b|for\s+\w+\s+in\b|while\s+.+:|class\s+\w+)"
)
_SCRIPT_GLYPHS = set("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ")


class DocxExportQualityError(ValueError):
    pass


@dataclass(frozen=True)
class DocxExportQualityReport:
    raw_latex_markers: tuple[str, ...]
    html_markers: tuple[str, ...]
    plaintext_formula_markers: tuple[str, ...]
    omml_count: int
    display_omml_count: int
    table_count: int
    code_listing_count: int
    code_line_count: int
    code_math_count: int
    math_code_markers: tuple[str, ...]
    figure_count: int
    figure_caption_count: int
    media_count: int
    duplicate_title_count: int
    page_number_field_count: int

    @property
    def passed(self) -> bool:
        return (
            not self.raw_latex_markers
            and not self.html_markers
            and not self.plaintext_formula_markers
            and not self.math_code_markers
            and self.code_math_count == 0
            and self.duplicate_title_count == 0
            and (self.code_listing_count == 0 or self.code_line_count >= self.code_listing_count)
            and self.figure_count == self.figure_caption_count
            and (self.figure_count == 0 or self.media_count > 0)
        )


def _document_xml(path: Path) -> str:
    with ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8", errors="replace")


def inspect_docx_export(path: Path) -> DocxExportQualityReport:
    xml_text = _document_xml(path)
    root = ET.fromstring(xml_text.encode("utf-8"))
    visible_text = "".join(node.text or "" for node in root.findall(".//w:t", _NS))
    raw_latex = [marker for marker in _RAW_MARKERS if marker in xml_text]
    raw_latex.extend(sorted(set(_RAW_LATEX_RE.findall(xml_text))))
    html_markers = sorted(set(_HTML_RE.findall(visible_text)))

    formula_plaintext: list[str] = []
    if any(glyph in visible_text for glyph in _SCRIPT_GLYPHS) and any(op in visible_text for op in ("∫", "∑", "lim")):
        formula_plaintext.append("unicode-script-formula-text")

    def paragraph_style(paragraph: ET.Element) -> str:
        style = paragraph.find("./w:pPr/w:pStyle", _NS)
        return style.attrib.get(f"{{{_WORD_NS}}}val", "") if style is not None else ""

    paragraphs = root.findall(".//w:p", _NS)
    code_line_paragraphs = [paragraph for paragraph in paragraphs if paragraph_style(paragraph) == "OpenClassCodeLine"]
    title_texts = [
        "".join(node.text or "" for node in paragraph.findall(".//w:t", _NS)).strip()
        for paragraph in paragraphs
        if paragraph_style(paragraph) == "Title"
    ]
    heading_one_texts = [
        "".join(node.text or "" for node in paragraph.findall(".//w:t", _NS)).strip()
        for paragraph in paragraphs
        if paragraph_style(paragraph) == "Heading1"
    ]
    normalized_titles = {re.sub(r"\s+", " ", value).strip().rstrip("。:：") for value in title_texts if value}
    normalized_heading_ones = {
        re.sub(r"\s+", " ", value).strip().rstrip("。:：") for value in heading_one_texts if value
    }
    math_code_markers = sorted(
        {
            match.group(0)
            for math_node in root.findall(".//m:oMath", _NS)
            for match in _CODE_IN_MATH_RE.finditer("".join(node.text or "" for node in math_node.findall(".//m:t", _NS)))
        }
    )
    with ZipFile(path) as archive:
        names = archive.namelist()
        footer_xml = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in names
            if re.fullmatch(r"word/footer\d+\.xml", name)
        )

    return DocxExportQualityReport(
        raw_latex_markers=tuple(raw_latex),
        html_markers=tuple(html_markers),
        plaintext_formula_markers=tuple(formula_plaintext),
        omml_count=len(root.findall(".//m:oMath", _NS)),
        display_omml_count=len(root.findall(".//m:oMathPara", _NS)),
        table_count=len(root.findall(".//w:tbl", _NS)),
        code_listing_count=sum(paragraph_style(paragraph) == "OpenClassCodeCaption" for paragraph in paragraphs),
        code_line_count=len(code_line_paragraphs),
        code_math_count=sum(bool(paragraph.findall(".//m:oMath", _NS)) for paragraph in code_line_paragraphs),
        math_code_markers=tuple(math_code_markers),
        figure_count=len(root.findall(".//w:drawing", _NS)),
        figure_caption_count=sum(paragraph_style(paragraph) == "OpenClassFigureCaption" for paragraph in paragraphs),
        media_count=sum(name.startswith("word/media/") and not name.endswith("/") for name in names),
        duplicate_title_count=len(normalized_titles & normalized_heading_ones),
        page_number_field_count=len(re.findall(r">\s*PAGE\s*<", footer_xml)),
    )


def assert_docx_export_quality(path: Path) -> DocxExportQualityReport:
    report = inspect_docx_export(path)
    if not report.passed:
        problems = []
        if report.raw_latex_markers:
            problems.append(f"raw LaTeX markers: {', '.join(report.raw_latex_markers)}")
        if report.html_markers:
            problems.append(f"HTML markers: {', '.join(report.html_markers)}")
        if report.plaintext_formula_markers:
            problems.append(f"plaintext formula markers: {', '.join(report.plaintext_formula_markers)}")
        if report.math_code_markers:
            problems.append(f"program code inside OMML: {', '.join(report.math_code_markers)}")
        if report.code_math_count:
            problems.append(f"code-line paragraphs containing OMML: {report.code_math_count}")
        if report.duplicate_title_count:
            problems.append(f"duplicate document title / heading: {report.duplicate_title_count}")
        if report.code_listing_count and report.code_line_count < report.code_listing_count:
            problems.append(
                f"code listings without rendered lines: {report.code_listing_count - report.code_line_count}"
            )
        if report.figure_count != report.figure_caption_count:
            problems.append(
                f"figure/caption mismatch: {report.figure_count} figures, "
                f"{report.figure_caption_count} captions"
            )
        if report.figure_count and report.media_count == 0:
            problems.append("figures have no embedded media")
        raise DocxExportQualityError("; ".join(problems))
    return report
