from __future__ import annotations

import html
import io
import re
from functools import lru_cache
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
InlineFragment = tuple[str, str]
DocxBlock = tuple[str, list[InlineFragment], dict[str, Any]]

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_MATH_SIGNAL_RE = re.compile(
    r"[\\_^=·*/∞→←≤≥≈≠±]"
    r"|\b(?:lim|sin|cos|tan|ln|log|sqrt|exp)\b"
    r"|\d+\s*/\s*\d+"
    r"|[A-Za-z]\s*\([^)]*\)"
    r"|[A-Za-z]\s*\^\s*\{?[-+\w/]+\}?"
)
_MATH_RUN_RE = re.compile(r"[A-Za-z0-9\\_{}^()+\-−*/=·∞→←≤≥≈≠±<>|'\s.]+")
_DELIMITED_MATH_RE = re.compile(r"\\\((.+?)\\\)|\$(?!\d+\$)([^$\n]+?)\$(?!\d)")
_TRAILING_SENTENCE_MARKS_RE = re.compile(r"[\s.,，。；;:：]+$")
_LEADING_SENTENCE_MARKS_RE = re.compile(r"^[\s.,，。；;:：]+")
_LATEX_SYMBOLS = {
    r"\to": "→",
    r"\leftarrow": "←",
    r"\infty": "∞",
    r"\cdot": "·",
    r"\times": "×",
    r"\div": "÷",
    r"\le": "≤",
    r"\ge": "≥",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\approx": "≈",
    r"\ne": "≠",
    r"\neq": "≠",
    r"\pm": "±",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\theta": "θ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\pi": "π",
    r"\sigma": "σ",
    r"\phi": "φ",
    r"\varphi": "φ",
    r"\omega": "ω",
    r"\partial": "∂",
    r"\int": "∫",
    r"\sum": "∑",
}
_LATEX_FUNCTIONS = {"sin", "cos", "tan", "ln", "log", "sqrt", "exp", "lim"}
_SUPERSCRIPT_CHARS = str.maketrans(
    "0123456789+-=()abcdefgijklmnoprstuvwxyz",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ",
)
_SUBSCRIPT_CHARS = str.maketrans("0123456789+-=()aeijoruvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢⱼₒᵣᵤᵥₓ")


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


def _has_math_signal(value: str) -> bool:
    return bool(_MATH_SIGNAL_RE.search(value))


def _normalize_limit_subscript(value: str) -> str:
    return (
        value.replace("→", r"\to ")
        .replace("∞", r"\infty")
        .replace("+\\infty", r"+\infty")
        .replace("-\\infty", r"-\infty")
        .strip()
    )


def _normalize_latex(value: str) -> str:
    latex = _TRAILING_SENTENCE_MARKS_RE.sub("", _LEADING_SENTENCE_MARKS_RE.sub("", value.strip()))
    replacements = [
        ("−", "-"),
        ("→", r"\to "),
        ("←", r"\leftarrow "),
        ("∞", r"\infty"),
        ("·", r"\cdot "),
        ("≤", r"\le "),
        ("≥", r"\ge "),
        ("≈", r"\approx "),
        ("≠", r"\ne "),
        ("±", r"\pm "),
    ]
    for old, new in replacements:
        latex = latex.replace(old, new)

    latex = latex.replace("，", r",\quad ")
    latex = re.sub(
        r"([A-Za-z]')\\frac\{x\}\{([A-Za-z])\}'\(([^()]+)\)",
        r"\\frac{\1(\3)}{\2'(\3)}",
        latex,
    )
    latex = re.sub(
        r"([A-Za-z])\\frac\{x\}\{([A-Za-z])\}\(([^()]+)\)",
        r"\\frac{\1(\3)}{\2(\3)}",
        latex,
    )
    latex = re.sub(
        r"(?<!\\)\blim_\{([^}]+)\}",
        lambda match: rf"\lim_{{{_normalize_limit_subscript(match.group(1))}}}",
        latex,
    )
    latex = re.sub(r"(?<!\\)\b(sin|cos|tan|ln|log|sqrt|exp)\b", r"\\\1", latex)
    latex = re.sub(r"([A-Za-z]'?\([^()]+\))\s*/\s*([A-Za-z]'?\([^()]+\))", r"\\frac{\1}{\2}", latex)
    latex = re.sub(
        r"(\\(?:sin|cos|tan|ln|log|sqrt|exp)\s+[A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)\s*/\s*\(([^()]+)\)",
        r"\\frac{\1}{\2}",
        latex,
    )
    latex = re.sub(
        r"(\\(?:sin|cos|tan|ln|log|sqrt|exp)\s+[A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)\s*/\s*([A-Za-z0-9]+(?:\^\{?[-+\w/]+\}?)?)",
        r"\\frac{\1}{\2}",
        latex,
    )
    latex = re.sub(r"\(([^()]+)\)\s*/\s*\(([^()]+)\)", r"\\frac{\1}{\2}", latex)
    latex = re.sub(r"\(([^()]+)\)\s*/\s*([A-Za-z0-9\\]+(?:\^\{?[-+\w/]+\}?)?)", r"\\frac{\1}{\2}", latex)
    latex = _normalize_top_level_slash_fractions(latex)
    return re.sub(r"\s+", " ", latex).strip()


def _matching_opener(value: str, end: int, opener: str, closer: str) -> int:
    depth = 0
    for index in range(end, -1, -1):
        if value[index] == closer:
            depth += 1
        elif value[index] == opener:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _matching_closer(value: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    for index in range(start, len(value)):
        if value[index] == opener:
            depth += 1
        elif value[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _find_top_level_slash(value: str) -> int:
    brace_depth = 0
    paren_depth = 0
    for index, char in enumerate(value):
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "/" and brace_depth == 0 and paren_depth == 0:
            return index
    return -1


def _latex_operand_start(value: str, slash_index: int) -> int:
    index = slash_index - 1
    while index >= 0 and value[index].isspace():
        index -= 1
    if index < 0:
        return 0

    while index >= 0:
        if value[index] in "})":
            opener, closer = ("{", "}") if value[index] == "}" else ("(", ")")
            start = _matching_opener(value, index, opener, closer)
            if start < 0:
                return index
            index = start - 1
            if index >= 0 and value[index] in {"_", "^"}:
                index -= 1
                continue
            while index >= 0 and value[index].isspace():
                index -= 1
            if index >= 0 and value[index] == "'":
                index -= 1
            while index >= 0 and (value[index].isalpha() or value[index] == "\\"):
                index -= 1
            return index + 1

        while index >= 0 and re.match(r"[A-Za-z0-9'\\.+\-∞]", value[index]):
            index -= 1
        if index >= 0 and value[index] in {"_", "^"}:
            index -= 1
            continue
        return index + 1
    return 0


def _latex_operand_end(value: str, slash_index: int) -> int:
    index = slash_index + 1
    while index < len(value) and value[index].isspace():
        index += 1
    if index < len(value) and value[index] in {"+", "-"}:
        index += 1
    if index >= len(value):
        return len(value)

    if value[index] == "\\":
        command_match = re.match(r"\\[A-Za-z]+", value[index:])
        if command_match:
            index += len(command_match.group(0))
    elif value[index] in "{(":
        closer = "}" if value[index] == "{" else ")"
        end = _matching_closer(value, index, value[index], closer)
        index = len(value) if end < 0 else end + 1
    else:
        token_match = re.match(r"[A-Za-z0-9]+(?:'\([^()]*\)|\([^()]*\)|')?", value[index:])
        if token_match:
            index += len(token_match.group(0))
        else:
            index += 1

    while index < len(value):
        if value[index].isspace():
            break
        if value[index] in {"_", "^"}:
            index += 1
            while index < len(value) and value[index].isspace():
                index += 1
            if index < len(value) and value[index] == "{":
                end = _matching_closer(value, index, "{", "}")
                index = len(value) if end < 0 else end + 1
            elif index < len(value) and value[index] == "\\":
                command_match = re.match(r"\\[A-Za-z]+", value[index:])
                index += len(command_match.group(0)) if command_match else 1
            else:
                token_match = re.match(r"[A-Za-z0-9+\-]+", value[index:])
                index += len(token_match.group(0)) if token_match else 1
            continue
        if value[index] == "/":
            nested_end = _latex_operand_end(value, index)
            index = nested_end
            continue
        break
    return index


def _strip_wrapping_parentheses(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("(") and stripped.endswith(")") and _matching_closer(stripped, 0, "(", ")") == len(stripped) - 1:
        return stripped[1:-1].strip()
    return stripped


def _normalize_top_level_slash_fractions(value: str) -> str:
    current = value
    for _ in range(24):
        slash_index = _find_top_level_slash(current)
        if slash_index < 0:
            return current
        start = _latex_operand_start(current, slash_index)
        end = _latex_operand_end(current, slash_index)
        numerator = current[start:slash_index].strip()
        denominator = current[slash_index + 1 : end].strip()
        if not numerator or not denominator:
            return current
        numerator = _normalize_top_level_slash_fractions(_strip_wrapping_parentheses(numerator))
        denominator = _normalize_top_level_slash_fractions(_strip_wrapping_parentheses(denominator))
        current = f"{current[:start]}\\frac{{{numerator}}}{{{denominator}}}{current[end:]}"
    return current


def _formula_only_latex(text: str) -> str | None:
    compact = _TRAILING_SENTENCE_MARKS_RE.sub("", text.strip())
    delimited = (
        re.match(r"^\\\[(.+?)\\\]$", compact)
        or re.match(r"^\\\((.+?)\\\)$", compact)
        or re.match(r"^\$\$?(.+?)\$\$?$", compact)
    )
    if delimited:
        return _normalize_latex(delimited.group(1))
    if not compact or _CJK_RE.search(compact) or not _has_math_signal(compact):
        return None
    return _normalize_latex(compact)


def _math_segments(text: str) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []

    for match in _DELIMITED_MATH_RE.finditer(text):
        latex = match.group(1) or match.group(2) or ""
        if latex.strip():
            segments.append((match.start(), match.end(), _normalize_latex(latex)))

    for match in _MATH_RUN_RE.finditer(text):
        raw = match.group(0)
        leading_trimmed = _LEADING_SENTENCE_MARKS_RE.sub("", raw)
        leading_offset = len(raw) - len(leading_trimmed)
        candidate = _TRAILING_SENTENCE_MARKS_RE.sub("", leading_trimmed)
        trailing_offset = len(leading_trimmed) - len(candidate)
        if not candidate or _CJK_RE.search(candidate) or not _has_math_signal(candidate):
            continue
        start = match.start() + leading_offset
        end = match.start() + len(raw) - trailing_offset
        if end <= start or any(start < segment_end and end > segment_start for segment_start, segment_end, _ in segments):
            continue
        segments.append((start, end, _normalize_latex(candidate)))

    return sorted(segments, key=lambda segment: segment[0])


def _auto_math_fragments(text: str) -> list[InlineFragment]:
    segments = _math_segments(text)
    if not segments:
        return [("text", text)] if text else []

    fragments: list[InlineFragment] = []
    cursor = 0
    for start, end, latex in segments:
        if start > cursor:
            fragments.append(("text", text[cursor:start]))
        fragments.append(("math", latex))
        cursor = end
    if cursor < len(text):
        fragments.append(("text", text[cursor:]))
    return fragments


def _fragment_text(fragments: list[InlineFragment]) -> str:
    return "".join(text for _, text in fragments)


class _DocxBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[DocxBlock] = []
        self._tag_stack: list[str] = []
        self._buffer: list[str] = []
        self._attrs_stack: list[dict[str, Any]] = []
        self._fragments: list[InlineFragment] = []
        self._ignored_atom_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        node_type = (attr_map.get("data-type") or "").strip()
        if node_type == "block-math":
            self._flush()
            latex = html.unescape((attr_map.get("data-latex") or "").strip())
            if latex:
                self.blocks.append(("math", [("math", latex)], attr_map))
            self._ignored_atom_depth = 1
            return
        if node_type == "page-break":
            self._flush()
            self.blocks.append(("pageBreak", [], attr_map))
            self._ignored_atom_depth = 1
            return
        if node_type == "inline-math":
            self._flush_text()
            latex = html.unescape((attr_map.get("data-latex") or "").strip())
            if latex:
                self._fragments.append(("math", latex))
            self._ignored_atom_depth = 1
            return

        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            self._flush()
            self._tag_stack.append(tag)
            self._attrs_stack.append(attr_map)
        elif tag == "img":
            src = (attr_map.get("src") or "").strip()
            alt = (attr_map.get("alt") or "").strip()
            self._flush()
            self.blocks.append(("img", [("text", alt)], {"src": src, "alt": alt}))
        elif tag == "br":
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_atom_depth:
            self._ignored_atom_depth -= 1
            return
        if tag in {"h1", "h2", "h3", "p", "li", "blockquote"}:
            current = self._tag_stack.pop() if self._tag_stack else tag
            attrs = self._attrs_stack.pop() if self._attrs_stack else {}
            self._flush(current, attrs)

    def handle_data(self, data: str) -> None:
        if self._ignored_atom_depth:
            return
        self._buffer.append(data)

    def _flush_text(self) -> None:
        if not self._buffer:
            return
        text = html.unescape("".join(self._buffer))
        self._buffer = []
        text = re.sub(r"\s+", " ", text)
        if text:
            self._fragments.append(("text", text))

    def _flush(self, tag: str | None = None, attrs: dict[str, Any] | None = None) -> None:
        self._flush_text()
        if not self._fragments:
            return

        fragments = list(self._fragments)
        self._fragments = []
        while fragments and fragments[0][0] == "text" and not fragments[0][1].strip():
            fragments.pop(0)
        while fragments and fragments[-1][0] == "text" and not fragments[-1][1].strip():
            fragments.pop()
        if fragments and fragments[0][0] == "text":
            fragments[0] = ("text", fragments[0][1].lstrip())
        if fragments and fragments[-1][0] == "text":
            fragments[-1] = ("text", fragments[-1][1].rstrip())
        if fragments:
            self.blocks.append((tag or "p", fragments, attrs or {}))


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


def _script_text(value: str, *, subscript: bool = False) -> str:
    parsed = _latex_inline_text(value)
    table = _SUBSCRIPT_CHARS if subscript else _SUPERSCRIPT_CHARS
    return parsed.translate(table)


class _InlineLatexParser:
    def __init__(self, latex: str) -> None:
        self.latex = latex
        self.index = 0

    def parse(self) -> str:
        parts: list[str] = []
        while self.index < len(self.latex):
            char = self.latex[self.index]
            if char == "}":
                break
            if char.isspace():
                if parts and parts[-1] != " ":
                    parts.append(" ")
                self.index += 1
                continue
            atom = self._parse_atom()
            parts.append(self._parse_scripts(atom))
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        text = re.sub(r"\s*([→←])\s*", r"\1", text)
        text = text.replace("± ∞", "±∞")
        return text

    def _parse_atom(self) -> str:
        char = self.latex[self.index]
        if char == "{":
            raw, self.index = _read_braced(self.latex, self.index)
            return _latex_inline_text(raw)
        if self.latex.startswith(r"\frac", self.index):
            next_index = self.index + len(r"\frac")
            numerator_raw, next_index = _read_braced(self.latex, next_index)
            denominator_raw, next_index = _read_braced(self.latex, next_index)
            self.index = next_index
            numerator = _latex_inline_text(numerator_raw)
            denominator = _latex_inline_text(denominator_raw)
            if re.search(r"\s|[+\-=*/]", numerator):
                numerator = f"({numerator})"
            if re.search(r"\s|[+\-=*/]", denominator):
                denominator = f"({denominator})"
            return f"{numerator}/{denominator}"
        if self.latex.startswith(r"\sqrt", self.index):
            next_index = self.index + len(r"\sqrt")
            radicand_raw, self.index = _read_braced(self.latex, next_index)
            return f"√({ _latex_inline_text(radicand_raw) })"
        if self.latex.startswith(r"\lim", self.index):
            self.index += len(r"\lim")
            if self.index < len(self.latex) and self.latex[self.index] == "_":
                self.index += 1
                return f"lim {_latex_inline_text(self._read_script_raw())}"
            return "lim"
        if char == "\\":
            return self._parse_command()
        match = re.match(r"[A-Za-z]+(?:')?", self.latex[self.index :])
        if match:
            token = match.group(0)
            self.index += len(token)
            return token
        match = re.match(r"\d+(?:\.\d+)?", self.latex[self.index :])
        if match:
            token = match.group(0)
            self.index += len(token)
            return token
        self.index += 1
        return char

    def _parse_command(self) -> str:
        if self.latex.startswith(r"\quad", self.index):
            self.index += len(r"\quad")
            return " "
        command_match = re.match(r"\\[A-Za-z]+", self.latex[self.index :])
        if not command_match:
            self.index += 1
            return ""
        command = command_match.group(0)
        self.index += len(command)
        if command in {r"\left", r"\right"}:
            return ""
        if command in _LATEX_SYMBOLS:
            return _LATEX_SYMBOLS[command]
        return command[1:]

    def _parse_scripts(self, base: str) -> str:
        result = base
        while self.index < len(self.latex) and self.latex[self.index] in {"_", "^"}:
            script_kind = self.latex[self.index]
            self.index += 1
            raw = self._read_script_raw()
            result += _script_text(raw, subscript=script_kind == "_")
        return result

    def _read_script_raw(self) -> str:
        while self.index < len(self.latex) and self.latex[self.index].isspace():
            self.index += 1
        if self.index >= len(self.latex):
            return ""
        if self.latex[self.index] == "{":
            raw, self.index = _read_braced(self.latex, self.index)
            return raw
        if self.latex[self.index] == "\\":
            command_match = re.match(r"\\[A-Za-z]+", self.latex[self.index :])
            if command_match:
                raw = command_match.group(0)
                self.index += len(raw)
                return raw
        raw = self.latex[self.index]
        self.index += 1
        return raw


@lru_cache(maxsize=1024)
def _latex_inline_text(latex: str) -> str:
    return _InlineLatexParser(_normalize_latex(latex)).parse()


class _DisplayMathBox:
    def __init__(self, lines: list[str], baseline: int = 0) -> None:
        self.lines = [line.rstrip() for line in lines] or [""]
        self.width = max(1, max(len(line) for line in self.lines))
        self.lines = [line.center(self.width) for line in self.lines]
        self.baseline = min(max(0, baseline), len(self.lines) - 1)


def _combine_display_boxes(boxes: list[_DisplayMathBox], *, gap: str = " ") -> _DisplayMathBox:
    if not boxes:
        return _DisplayMathBox([""])
    baseline = max(box.baseline for box in boxes)
    descent = max(len(box.lines) - box.baseline - 1 for box in boxes)
    height = baseline + descent + 1
    combined: list[str] = []
    for row in range(height):
        pieces: list[str] = []
        for box in boxes:
            source_row = row - (baseline - box.baseline)
            if 0 <= source_row < len(box.lines):
                pieces.append(box.lines[source_row].center(box.width))
            else:
                pieces.append(" " * box.width)
        combined.append(gap.join(pieces).rstrip())
    return _DisplayMathBox(combined, baseline)


class _DisplayLatexParser:
    def __init__(self, latex: str) -> None:
        self.latex = latex
        self.index = 0

    def parse(self) -> _DisplayMathBox:
        boxes: list[_DisplayMathBox] = []
        text_buffer: list[str] = []
        while self.index < len(self.latex):
            char = self.latex[self.index]
            if char == "}":
                break
            if self.latex.startswith(r"\frac", self.index):
                self._flush_text(text_buffer, boxes)
                boxes.append(self._parse_fraction())
                boxes.append(self._parse_scripts_box())
                continue
            if self.latex.startswith(r"\lim", self.index):
                self._flush_text(text_buffer, boxes)
                boxes.append(self._parse_limit())
                continue
            if char == "{":
                raw, self.index = _read_braced(self.latex, self.index)
                self._flush_text(text_buffer, boxes)
                boxes.append(_latex_display_box(raw))
                continue
            if char == "\\":
                text_buffer.append(self._parse_command())
                continue
            text_buffer.append(char)
            self.index += 1
        self._flush_text(text_buffer, boxes)
        return _combine_display_boxes([box for box in boxes if any(line.strip() for line in box.lines)])

    def _flush_text(self, text_buffer: list[str], boxes: list[_DisplayMathBox]) -> None:
        if not text_buffer:
            return
        text = _latex_inline_text("".join(text_buffer))
        if text:
            boxes.append(_DisplayMathBox([text]))
        text_buffer.clear()

    def _parse_fraction(self) -> _DisplayMathBox:
        next_index = self.index + len(r"\frac")
        numerator_raw, next_index = _read_braced(self.latex, next_index)
        denominator_raw, next_index = _read_braced(self.latex, next_index)
        self.index = next_index
        numerator = _latex_display_box(numerator_raw)
        denominator = _latex_display_box(denominator_raw)
        width = max(numerator.width, denominator.width) + 2
        lines = [line.center(width) for line in numerator.lines]
        lines.append("─" * width)
        lines.extend(line.center(width) for line in denominator.lines)
        return _DisplayMathBox(lines, len(numerator.lines))

    def _parse_limit(self) -> _DisplayMathBox:
        self.index += len(r"\lim")
        if self.index < len(self.latex) and self.latex[self.index] == "_":
            self.index += 1
            raw = self._read_script_raw()
            limit = _latex_inline_text(raw)
            width = max(len("lim"), len(limit))
            return _DisplayMathBox(["lim".center(width), limit.center(width)], 0)
        return _DisplayMathBox(["lim"])

    def _parse_scripts_box(self) -> _DisplayMathBox:
        if self.index >= len(self.latex) or self.latex[self.index] not in {"_", "^"}:
            return _DisplayMathBox([""])
        result = ""
        while self.index < len(self.latex) and self.latex[self.index] in {"_", "^"}:
            script_kind = self.latex[self.index]
            self.index += 1
            raw = self._read_script_raw()
            result += _script_text(raw, subscript=script_kind == "_")
        return _DisplayMathBox([result])

    def _read_script_raw(self) -> str:
        while self.index < len(self.latex) and self.latex[self.index].isspace():
            self.index += 1
        if self.index >= len(self.latex):
            return ""
        if self.latex[self.index] == "{":
            raw, self.index = _read_braced(self.latex, self.index)
            return raw
        if self.latex[self.index] == "\\":
            command_match = re.match(r"\\[A-Za-z]+", self.latex[self.index :])
            if command_match:
                raw = command_match.group(0)
                self.index += len(raw)
                return raw
        raw = self.latex[self.index]
        self.index += 1
        return raw

    def _parse_command(self) -> str:
        if self.latex.startswith(r"\quad", self.index):
            self.index += len(r"\quad")
            return "  "
        command_match = re.match(r"\\[A-Za-z]+", self.latex[self.index :])
        if not command_match:
            self.index += 1
            return ""
        command = command_match.group(0)
        self.index += len(command)
        if command in {r"\left", r"\right"}:
            return ""
        return _LATEX_SYMBOLS.get(command, command[1:])


@lru_cache(maxsize=1024)
def _latex_display_box(latex: str) -> _DisplayMathBox:
    return _DisplayLatexParser(_normalize_latex(latex)).parse()


def _latex_display_lines(latex: str) -> list[str]:
    return [line.rstrip() for line in _latex_display_box(latex).lines]


def _m_element(tag: str, text: str | None = None) -> OxmlElement:
    element = OxmlElement(f"m:{tag}")
    if text is not None:
        element.text = text
    return element


def _math_run(text: str) -> OxmlElement:
    run = _m_element("r")
    text_node = _m_element("t", text)
    run.append(text_node)
    return run


def _math_container(tag: str, children: list[OxmlElement]) -> OxmlElement:
    container = _m_element(tag)
    for child in children:
        container.append(child)
    return container


def _math_arg(tag: str, children: list[OxmlElement]) -> OxmlElement:
    return _math_container(tag, children or [_math_run("")])


def _math_fraction(numerator: list[OxmlElement], denominator: list[OxmlElement]) -> OxmlElement:
    fraction = _m_element("f")
    fraction.append(_math_arg("num", numerator))
    fraction.append(_math_arg("den", denominator))
    return fraction


def _math_script(base: list[OxmlElement], subscript: list[OxmlElement] | None, superscript: list[OxmlElement] | None) -> OxmlElement:
    if subscript is not None and superscript is not None:
        node = _m_element("sSubSup")
        node.append(_math_arg("e", base))
        node.append(_math_arg("sub", subscript))
        node.append(_math_arg("sup", superscript))
        return node
    if subscript is not None:
        node = _m_element("sSub")
        node.append(_math_arg("e", base))
        node.append(_math_arg("sub", subscript))
        return node
    node = _m_element("sSup")
    node.append(_math_arg("e", base))
    node.append(_math_arg("sup", superscript or [_math_run("")]))
    return node


def _math_limit_low(base: list[OxmlElement], limit: list[OxmlElement]) -> OxmlElement:
    node = _m_element("limLow")
    node.append(_math_arg("e", base))
    node.append(_math_arg("lim", limit))
    return node


def _matching_delimiter(value: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    for index in range(start, len(value)):
        if value[index] == opener:
            depth += 1
        elif value[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _read_braced(value: str, index: int) -> tuple[str, int]:
    while index < len(value) and value[index].isspace():
        index += 1
    if index >= len(value):
        return "", index
    if value[index] != "{":
        return value[index], index + 1
    end = _matching_delimiter(value, index, "{", "}")
    if end < 0:
        return value[index + 1 :], len(value)
    return value[index + 1 : end], end + 1


def _read_parenthesized(value: str, index: int) -> tuple[str, int]:
    end = _matching_delimiter(value, index, "(", ")")
    if end < 0:
        return value[index], index + 1
    return value[index : end + 1], end + 1


def _read_base(value: str, index: int) -> tuple[list[OxmlElement], int]:
    if index >= len(value):
        return [_math_run("")], index
    if value[index] == "(":
        parenthesized, next_index = _read_parenthesized(value, index)
        return [_math_run(parenthesized)], next_index
    if value[index] == "{":
        braced, next_index = _read_braced(value, index)
        return _latex_to_normalized_math_children(braced), next_index
    if value[index] == "\\":
        command_match = re.match(r"\\[A-Za-z]+", value[index:])
        if command_match:
            command = command_match.group(0)
            return [_math_run(_LATEX_SYMBOLS.get(command, command[1:]))], index + len(command)
    match = re.match(r"[A-Za-z]+(?:'\([^()]*\)|\([^()]*\)|')?", value[index:])
    if match:
        token = match.group(0)
        return [_math_run(token)], index + len(token)
    return [_math_run(value[index])], index + 1


def _read_script(value: str, index: int) -> tuple[list[OxmlElement], int]:
    script, next_index = _read_braced(value, index)
    return _latex_to_normalized_math_children(script), next_index


def _apply_scripts(value: str, index: int, base: list[OxmlElement]) -> tuple[list[OxmlElement], int]:
    subscript: list[OxmlElement] | None = None
    superscript: list[OxmlElement] | None = None
    current = index
    while current < len(value) and value[current] in {"_", "^"}:
        script_kind = value[current]
        script, current = _read_script(value, current + 1)
        if script_kind == "_":
            subscript = script
        else:
            superscript = script
    if subscript is None and superscript is None:
        return base, index
    return [_math_script(base, subscript, superscript)], current


def _latex_to_math_children(latex: str) -> list[OxmlElement]:
    children: list[OxmlElement] = []
    index = 0
    while index < len(latex):
        char = latex[index]
        if char.isspace():
            if not children or (children[-1].tag != qn("m:r") or (children[-1].find(qn("m:t")).text or "") != " "):
                children.append(_math_run(" "))
            index += 1
            continue
        if char == "{":
            braced, index = _read_braced(latex, index)
            children.extend(_latex_to_normalized_math_children(braced))
            continue
        if latex.startswith(r"\frac", index):
            numerator_raw, after_num = _read_braced(latex, index + len(r"\frac"))
            denominator_raw, after_den = _read_braced(latex, after_num)
            fraction = _math_fraction(
                _latex_to_normalized_math_children(numerator_raw),
                _latex_to_normalized_math_children(denominator_raw),
            )
            scripted, index = _apply_scripts(latex, after_den, [fraction])
            children.extend(scripted)
            continue
        if latex.startswith(r"\lim", index):
            next_index = index + len(r"\lim")
            if next_index < len(latex) and latex[next_index] == "_":
                limit_raw, next_index = _read_braced(latex, next_index + 1)
                lim_node = _math_limit_low([_math_run("lim")], _latex_to_normalized_math_children(limit_raw))
                scripted, index = _apply_scripts(latex, next_index, [lim_node])
                children.extend(scripted)
                continue
            children.append(_math_run("lim"))
            index = next_index
            continue
        if char == "\\":
            if latex.startswith(r"\quad", index):
                children.append(_math_run("    "))
                index += len(r"\quad")
                continue
            command_match = re.match(r"\\[A-Za-z]+", latex[index:])
            if command_match:
                command = command_match.group(0)
                text = _LATEX_SYMBOLS.get(command, command[1:])
                node, next_index = _apply_scripts(latex, index + len(command), [_math_run(text)])
                children.extend(node)
                index = next_index
                continue
        base, next_index = _read_base(latex, index)
        scripted, index = _apply_scripts(latex, next_index, base)
        children.extend(scripted)
    return children or [_math_run(latex)]


def _latex_to_normalized_math_children(latex: str) -> list[OxmlElement]:
    return _latex_to_math_children(_normalize_latex(latex))


def _append_omml_math(paragraph, latex: str, *, display: bool = False) -> None:
    math = _m_element("oMath")
    for child in _latex_to_math_children(_normalize_latex(latex)):
        math.append(child)
    if display:
        math_paragraph = _m_element("oMathPara")
        math_paragraph.append(math)
        paragraph._p.append(math_paragraph)
    else:
        paragraph._p.append(math)


def _append_math(paragraph, latex: str, *, display: bool = False) -> None:
    _append_omml_math(paragraph, latex, display=display)
    if display:
        paragraph.paragraph_format.line_spacing = 1


def _append_fragments(
    paragraph,
    fragments: list[InlineFragment],
    *,
    auto_math: bool = True,
    display_math: bool = False,
) -> None:
    for kind, value in fragments:
        if kind == "math":
            _append_math(paragraph, value, display=display_math)
            continue
        split_fragments = _auto_math_fragments(value) if auto_math else [("text", value)]
        for split_kind, split_value in split_fragments:
            if not split_value:
                continue
            if split_kind == "math":
                _append_math(paragraph, split_value, display=display_math)
            else:
                paragraph.add_run(split_value)


def _add_fragment_paragraph(target: DocxDocument, fragments: list[InlineFragment], style: str | None = None):
    paragraph = target.add_paragraph(style=style)
    _append_fragments(paragraph, fragments)
    return paragraph


def export_docx(document: BoardDocument, path: Path) -> Path:
    target = DocxDocument()
    _apply_page_settings(target, document.page_settings)
    target.add_heading(document.title, level=0)

    parser = _DocxBlockParser()
    parser.feed(document.content_html or text_to_html(document.content_text))
    parser._flush()
    blocks = parser.blocks or [("p", [("text", line)], {}) for line in document.content_text.splitlines() if line.strip()]

    for tag, fragments, attrs in blocks:
        text = _fragment_text(fragments)
        if tag == "h1":
            paragraph = target.add_heading("", level=1)
            _append_fragments(paragraph, fragments)
        elif tag == "h2":
            paragraph = target.add_heading("", level=2)
            _append_fragments(paragraph, fragments)
        elif tag == "h3":
            paragraph = target.add_heading("", level=3)
            _append_fragments(paragraph, fragments)
        elif tag == "li":
            _add_fragment_paragraph(target, fragments, style="List Bullet")
        elif tag == "blockquote":
            _add_fragment_paragraph(target, fragments, style="Intense Quote")
        elif tag == "img":
            src = str(attrs.get("src") or "").strip()
            image_bytes = _decode_data_uri(src)
            if image_bytes:
                target.add_picture(io.BytesIO(image_bytes))
            elif text:
                target.add_paragraph(f"[图片] {text}")
        elif tag == "pageBreak":
            target.add_page_break()
        elif tag == "math" or (len(fragments) == 1 and fragments[0][0] == "math"):
            paragraph = target.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _append_fragments(paragraph, fragments, auto_math=False, display_math=True)
        else:
            formula_latex = _formula_only_latex(text)
            if formula_latex:
                paragraph = target.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _append_math(paragraph, formula_latex, display=True)
            else:
                _add_fragment_paragraph(target, fragments)

    path.parent.mkdir(parents=True, exist_ok=True)
    target.save(path)
    return path
