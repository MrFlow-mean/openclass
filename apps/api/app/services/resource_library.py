from __future__ import annotations

import html
import hashlib
import mimetypes
import posixpath
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET
from pathlib import Path

from pypdf import PdfReader
from docx import Document as DocxDocument

from app.models import (
    LibraryChapter,
    now_iso,
    ResourceBodyBlock,
    ResourceChapterShard,
    ResourceContextChunk,
    ResourceCopyrightEvidencePacket,
    ResourceCopyrightProbeSection,
    ResourceStructureRegion,
    ResourceTOCEntry,
    ResourceLibraryItem,
    ResourceReferenceContext,
)
from app.services.image_ocr import extract_image_text, extract_pdf_pages_text


_PDF_TEXT_SUMMARY_LIMIT = 140
_PDF_LOCATOR_SEPARATOR = " || "
_BODY_BLOCK_TEXT_LIMIT = 5000
_EPUB_HTML_TEXT_NORMALIZE_LIMIT = 120_000
_EPUB_STORED_TEXT_LIMIT = 200_000
_COPYRIGHT_PROBE_SECTION_LIMIT = 10
_COPYRIGHT_PROBE_TEXT_LIMIT = 800
_COPYRIGHT_PROBE_PACKET_TEXT_LIMIT = 5000
_COPYRIGHT_PROBE_WINDOW_LIMIT = 12_000
_COPYRIGHT_PROBE_PATTERNS = (
    "isbn",
    "copyright",
    "rights",
    "license",
    "licensed",
    "publisher",
    "published by",
    "publication",
    "creative commons",
    "public domain",
    "版权",
    "版权所有",
    "著作权",
    "出版社",
    "出版",
    "版次",
    "印次",
    "许可",
    "授权",
    "cip",
)
_COPYRIGHT_LICENSE_PATTERNS = (
    "license",
    "licensed",
    "creative commons",
    "cc by",
    "cc-by",
    "cc0",
    "public domain",
    "许可",
    "授权",
)


@dataclass(frozen=True)
class IndexedSourceUnit:
    chapter: LibraryChapter
    heading_path: list[str]
    block_text: str
    page_number_scope: str
    body_page_no: int | None
    body_page_idx: int | None
    physical_page_no: int | None
    physical_page_idx: int | None
    source_locator: str | None
    source_location_range: str | None


def _normalize_extracted_text(text: str) -> str:
    cleaned = text.replace("\x00", "").replace("\r\n", "\n")
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"(?<=[0-9])\s+(?=[0-9])", "", cleaned)
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[0-9A-Za-z])", "", cleaned)
    cleaned = re.sub(r"(?<=[0-9A-Za-z])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"\s+([,，.。!?！？；;：:])", r"\1", cleaned)
    cleaned = re.sub(r"([，。！？；：])\s+", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?;:])\s+(?=[\u4e00-\u9fff])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _summary_snippet(text: str, *, limit: int = _PDF_TEXT_SUMMARY_LIMIT) -> str:
    compact = re.sub(r"\s+", " ", _normalize_extracted_text(text)).strip()
    return compact[:limit].strip(" ，,。") if compact else ""


def _stable_text_hash(text: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]


def _page_label(start: int | None, end: int | None) -> str | None:
    if start is None:
        return None
    if end is None or end == start:
        return str(start)
    return f"{start}-{end}"


def _read_pdf_text_window(
    reader: PdfReader,
    *,
    page_start: int,
    page_end: int,
    max_pages: int = 6,
    max_nonempty_pages: int | None = None,
) -> str:
    start_index = max(page_start - 1, 0)
    end_index = min(max(page_end, page_start), len(reader.pages))
    extracted: list[str] = []
    nonempty_pages = 0
    scanned_pages = 0
    for page_index in range(start_index, end_index):
        if scanned_pages >= max_pages:
            break
        scanned_pages += 1
        try:
            text = reader.pages[page_index].extract_text() or ""
        except Exception:
            continue
        text = _normalize_extracted_text(text)
        if not text:
            continue
        extracted.append(text)
        nonempty_pages += 1
        if max_nonempty_pages is not None and nonempty_pages >= max_nonempty_pages:
            break
    return "\n".join(extracted).strip()


def _pdf_locator_hint(
    title: str,
    *,
    source: str,
    toc_page: int | None = None,
    printed_page: int | None = None,
    actual_page: int | None = None,
) -> str:
    parts = [title, f"source={source}"]
    if toc_page is not None:
        parts.append(f"toc_page={toc_page}")
    if printed_page is not None:
        parts.append(f"printed_page={printed_page}")
    if actual_page is not None:
        parts.append(f"actual_page={actual_page}")
    return _PDF_LOCATOR_SEPARATOR.join(parts)


def _resource_source_locator(
    title: str,
    *,
    source: str,
    source_index: int | None = None,
    body_page: int | None = None,
    physical_page: int | None = None,
) -> str:
    parts = [title, f"source={source}"]
    if source_index is not None:
        parts.append(f"source_index={source_index}")
    if body_page is not None:
        parts.append(f"body_page={body_page}")
    if physical_page is not None:
        parts.append(f"physical_page={physical_page}")
    return _PDF_LOCATOR_SEPARATOR.join(parts)


def _pdf_locator_value(locator_hint: str | None, key: str) -> int | None:
    if not locator_hint:
        return None
    for part in locator_hint.split(_PDF_LOCATOR_SEPARATOR):
        name, sep, value = part.partition("=")
        if sep and name.strip() == key:
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def _pdf_locator_source(locator_hint: str | None) -> str | None:
    if not locator_hint:
        return None
    for part in locator_hint.split(_PDF_LOCATOR_SEPARATOR):
        name, sep, value = part.partition("=")
        if sep and name.strip() == "source":
            return value.strip()
    return None


def _locator_title(locator_hint: str | None, fallback: str) -> str:
    if not locator_hint:
        return fallback
    title = locator_hint.split(_PDF_LOCATOR_SEPARATOR, 1)[0].strip()
    return title or fallback


def _chapter(
    title: str,
    summary: str,
    keywords: list[str],
    level: int = 1,
    *,
    locator_hint: str | None = None,
    order_index: int = 0,
    scan_strategy: str = "outline_only",
    page_start: int | None = None,
    page_end: int | None = None,
) -> LibraryChapter:
    page_range = None
    if page_start and page_end and page_end >= page_start:
        page_range = f"{page_start}-{page_end}" if page_end > page_start else str(page_start)
    elif page_start:
        page_range = str(page_start)

    return LibraryChapter(
        title=title,
        summary=summary,
        keywords=keywords,
        level=level,
        locator_hint=locator_hint or title,
        order_index=order_index,
        scan_strategy=scan_strategy,  # type: ignore[arg-type]
        page_start=page_start,
        page_end=page_end,
        page_range=page_range,
    )


def _attach_outline_hierarchy(chapters: list[LibraryChapter]) -> list[LibraryChapter]:
    stack: list[LibraryChapter] = []
    enriched: list[LibraryChapter] = []
    for chapter in chapters:
        while stack and stack[-1].level >= chapter.level:
            stack.pop()
        parent = stack[-1] if stack else None
        path = [*(parent.path if parent else []), chapter.title]
        enriched_chapter = chapter.model_copy(
            update={
                "parent_id": parent.id if parent else None,
                "parent_title": parent.title if parent else None,
                "path": path,
            }
        )
        enriched.append(enriched_chapter)
        stack.append(enriched_chapter)
    return enriched


def _markdown_sections(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or not line.startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        title = line[level:].strip()
        if title:
            headings.append((index, level, title))

    sections: list[dict[str, object]] = []
    for index, (line_number, level, title) in enumerate(headings):
        end = len(lines)
        for next_line_number, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_line_number
                break
        content = "\n".join(lines[line_number + 1 : end]).strip()
        sections.append(
            {
                "title": title,
                "level": level,
                "content": content,
                "order_index": index,
            }
        )
    return sections


def _extract_markdown_outline(text: str) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    for section in _markdown_sections(text):
        title = str(section["title"])
        content = str(section["content"])
        summary_seed = re.sub(r"\s+", " ", content).strip()[:90]
        summary = summary_seed or f"来自资料标题“{title}”的章节摘要待进一步展开。"
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=_keywords_from_text(f"{title}\n{content}") or [token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                level=int(section["level"]),
                locator_hint=title,
                order_index=int(section["order_index"]),
                scan_strategy="heading_section",
            )
        )
    return chapters


def _keywords_from_text(text: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "of",
        "to",
        "in",
        "on",
        "is",
        "are",
        "as",
        "by",
        "be",
        "or",
        "an",
        "at",
        "into",
        "about",
        "lesson",
        "chapter",
        "section",
        "一个",
        "一些",
        "我们",
        "你们",
        "什么",
        "以及",
        "当前",
        "这个",
        "那个",
        "可以",
        "通过",
    }
    counts: dict[str, int] = {}
    for token in re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", text.lower()):
        if token in stopwords or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
    return [token for token, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]]


def _generic_chapter_from_text(title: str, text: str, *, summary_prefix: str) -> LibraryChapter:
    normalized_text = _normalize_extracted_text(text)
    snippet = re.sub(r"\s+", " ", normalized_text[:4000]).strip()[:120] or f"围绕“{title}”补充资料入口。"
    return _chapter(
        title=title,
        summary=f"{summary_prefix}{snippet}",
        keywords=_keywords_from_text(normalized_text) or [token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
        locator_hint=title,
        order_index=0,
        scan_strategy="fulltext_match",
    )


def _ai_generated_outline(original_name: str, text: str) -> list[LibraryChapter]:
    # 资料没有清晰目录时，才让 Directory AI 根据原文生成通用目录，不允许补外部知识。
    normalized_text = _normalize_extracted_text(text)
    if len(normalized_text) < 80:
        return []
    try:
        from app.services.openai_course_ai import openai_course_ai

        generated = openai_course_ai.generate_resource_outline(
            resource_name=original_name,
            extracted_text=normalized_text,
        )
    except Exception:
        return []
    if generated is None:
        return []

    chapters: list[LibraryChapter] = []
    seen_titles: set[str] = set()
    for index, item in enumerate(generated.chapters):
        title = re.sub(r"\s+", " ", item.title).strip()
        summary = re.sub(r"\s+", " ", item.summary).strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        chapters.append(
            _chapter(
                title=title[:80],
                summary=summary[:220] or f"从资料“{original_name}”生成的目录入口。",
                keywords=[
                    keyword[:40]
                    for keyword in (item.keywords or _keywords_from_text(f"{title}\n{summary}"))[:8]
                    if keyword.strip()
                ],
                level=max(1, min(item.level, 4)),
                locator_hint=title,
                order_index=index,
                scan_strategy="fulltext_match",
            )
        )
    return chapters


def _read_text_file(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _docx_items(file_path: Path) -> list[dict[str, object]]:
    source = DocxDocument(file_path)
    items: list[dict[str, object]] = []

    for paragraph in source.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = (paragraph.style.name if paragraph.style else "").lower()
        level = 0
        if "heading 1" in style_name or "title" in style_name:
            level = 1
        elif "heading 2" in style_name:
            level = 2
        elif "heading 3" in style_name:
            level = 3
        items.append({"text": text, "level": level})

    for table in source.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                items.append({"text": " | ".join(cells), "level": 0})

    return items


def _read_docx_text(file_path: Path) -> str:
    return "\n".join(str(item["text"]) for item in _docx_items(file_path))


def _docx_sections(file_path: Path) -> list[dict[str, object]]:
    items = _docx_items(file_path)
    headings = [(index, int(item["level"]), str(item["text"])) for index, item in enumerate(items) if int(item["level"]) > 0]
    sections: list[dict[str, object]] = []

    for index, (item_index, level, title) in enumerate(headings):
        end = len(items)
        for next_item_index, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_item_index
                break
        content = "\n".join(str(item["text"]) for item in items[item_index + 1 : end]).strip()
        sections.append(
            {
                "title": title,
                "level": level,
                "content": content,
                "order_index": index,
            }
        )
    return sections


def _extract_docx_outline(file_path: Path) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    for section in _docx_sections(file_path):
        title = str(section["title"])
        content = str(section["content"])
        summary_seed = re.sub(r"\s+", " ", content).strip()[:90]
        summary = summary_seed or f"来自资料标题“{title}”的章节摘要待进一步展开。"
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=_keywords_from_text(f"{title}\n{content}") or [token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                level=int(section["level"]),
                locator_hint=title,
                order_index=int(section["order_index"]),
                scan_strategy="heading_section",
            )
        )
    return chapters


def _extract_docx_section_text(file_path: Path, chapter: LibraryChapter) -> str:
    sections = _docx_sections(file_path)
    target = next(
        (
            section
            for section in sections
            if str(section["title"]) == (chapter.locator_hint or chapter.title)
        ),
        None,
    )
    if target is None:
        return _read_docx_text(file_path)
    content = str(target["content"]).strip()
    if content:
        return content
    return str(target["title"])


_EPUB_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_EPUB_CHINESE_UNITS = {"十": 10, "百": 100}
_EPUB_NUMBER_PATTERN = r"(?:\d+|[一二三四五六七八九十百〇零两]+)"


def _parse_epub_outline_number(value: str | None) -> int | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)

    total = 0
    current = 0
    seen = False
    for char in cleaned:
        if char in _EPUB_CHINESE_DIGITS:
            current = _EPUB_CHINESE_DIGITS[char]
            seen = True
            continue
        unit = _EPUB_CHINESE_UNITS.get(char)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
        seen = True
    if not seen:
        return None
    return total + current


def _extract_epub_requested_outline_reference(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        rf"第\s*({_EPUB_NUMBER_PATTERN})\s*章(?:\s*第\s*({_EPUB_NUMBER_PATTERN})\s*[节讲部分])?",
        text,
    )
    if match:
        chapter_no = _parse_epub_outline_number(match.group(1))
        section_no = _parse_epub_outline_number(match.group(2)) if match.group(2) else None
        return chapter_no, section_no
    english = re.search(r"\bchapter\s*(\d+)\s*(?:section\s*(\d+))?\b", text, flags=re.IGNORECASE)
    if english:
        return int(english.group(1)), int(english.group(2)) if english.group(2) else None
    dotted = re.search(r"\b(\d+)\.(\d+)\b", text)
    if dotted:
        return int(dotted.group(1)), int(dotted.group(2))
    return None, None


def _epub_title_outline_reference(title: str) -> tuple[int | None, int | None]:
    cleaned = title.strip()
    chapter = re.search(rf"第\s*({_EPUB_NUMBER_PATTERN})\s*章", cleaned)
    if chapter:
        return _parse_epub_outline_number(chapter.group(1)), None
    dotted = re.search(r"^\s*(\d+)\s*[.．]\s*(\d+)", cleaned)
    if dotted:
        return int(dotted.group(1)), int(dotted.group(2))
    english = re.search(r"\bchapter\s*(\d+)\b", cleaned, flags=re.IGNORECASE)
    if english:
        return int(english.group(1)), None
    return None, None


def _epub_is_html_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".xhtml", ".html", ".htm"))


def _decode_epub_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _epub_text_from_html(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|head|nav)\b.*?</\1>", "\n", raw_html)
    cleaned = re.sub(r"(?is)<br\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</?(h[1-6]|p|div|section|article|li|tr|td|th|blockquote)\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", "", cleaned)
    text = html.unescape(cleaned)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _normalize_extracted_text("\n".join(lines)[:_EPUB_HTML_TEXT_NORMALIZE_LIMIT])


def _epub_fragment_text(fragment: str) -> str:
    return _epub_text_from_html(fragment).strip()


def _epub_html_title(raw_html: str, path: str) -> str:
    for match in re.finditer(r"(?is)<h([1-6])\b[^>]*>(.*?)</h\1>", raw_html):
        title = _epub_fragment_text(match.group(2))
        if title:
            return title[:120]
    title_match = re.search(r"(?is)<title\b[^>]*>(.*?)</title>", raw_html)
    if title_match:
        title = _epub_fragment_text(title_match.group(1))
        if title:
            return title[:120]
    return Path(path).stem.replace("_", " ").replace("-", " ").strip() or path


def _epub_rootfile_path(archive: zipfile.ZipFile) -> str | None:
    try:
        container_xml = archive.read("META-INF/container.xml")
    except KeyError:
        return None
    try:
        root = ET.fromstring(container_xml)
    except ET.ParseError:
        return None
    rootfile = root.find(".//{*}rootfile")
    if rootfile is None:
        return None
    full_path = rootfile.attrib.get("full-path")
    return full_path.strip() if full_path else None


def _epub_reading_order_paths(archive: zipfile.ZipFile) -> list[str]:
    rootfile_path = _epub_rootfile_path(archive)
    if not rootfile_path:
        return []
    try:
        opf_root = ET.fromstring(archive.read(rootfile_path))
    except (KeyError, ET.ParseError):
        return []

    base_dir = posixpath.dirname(rootfile_path)
    manifest: dict[str, tuple[str, str, str]] = {}
    for item in opf_root.findall(".//{*}manifest/{*}item"):
        item_id = item.attrib.get("id", "").strip()
        href = item.attrib.get("href", "").strip()
        media_type = item.attrib.get("media-type", "").strip()
        properties = item.attrib.get("properties", "").strip()
        if not item_id or not href:
            continue
        path = posixpath.normpath(posixpath.join(base_dir, href))
        manifest[item_id] = (path, media_type, properties)

    ordered: list[str] = []
    for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
        item_id = itemref.attrib.get("idref", "").strip()
        path, media_type, properties = manifest.get(item_id, ("", "", ""))
        if not path:
            continue
        if "nav" in properties or "toc" in properties:
            continue
        if media_type in {"application/xhtml+xml", "text/html"} or _epub_is_html_path(path):
            ordered.append(path)
    return [path for path in ordered if path in archive.namelist()]


def _epub_html_items(file_path: Path) -> list[dict[str, object]]:
    try:
        with zipfile.ZipFile(file_path) as archive:
            paths = _epub_reading_order_paths(archive)
            if not paths:
                paths = [
                    path
                    for path in archive.namelist()
                    if _epub_is_html_path(path) and not re.search(r"(?:^|/)(?:nav|toc|cover)\.", path, flags=re.IGNORECASE)
                ]
            items: list[dict[str, object]] = []
            for order_index, path in enumerate(paths):
                try:
                    raw_html = _decode_epub_bytes(archive.read(path))
                except KeyError:
                    continue
                text = _epub_text_from_html(raw_html)
                if not text:
                    continue
                items.append(
                    {
                        "path": path,
                        "title": _epub_html_title(raw_html, path),
                        "text": text,
                        "order_index": order_index,
                    }
                )
            return items
    except (zipfile.BadZipFile, OSError):
        return []


def _is_epub_separator_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title).lower()
    return compact in {"封面", "版权", "目录", "目次", "前言", "序", "绪言", "contents", "cover", "titlepage"}


def _looks_like_epub_heading(line: str) -> bool:
    cleaned = line.strip()
    if _looks_like_epub_heading_artifact(cleaned):
        return False
    if len(cleaned) > 90:
        return False
    if _looks_like_reference_heading(cleaned):
        return True
    if re.match(r"^(?:chapter\s+\d+|\d+\s*[.．]\s*\d+)", cleaned, flags=re.IGNORECASE):
        return True
    if _looks_like_numbered_title(cleaned):
        return True
    return False


def _looks_like_epub_heading_artifact(line: str) -> bool:
    cleaned = re.sub(r"\s+", " ", line.strip())
    if not cleaned:
        return True
    compact = re.sub(r"\s+", "", cleaned)
    if compact.isdigit() and len(compact) <= 4:
        return True
    if re.search(r"[{}();#$]|/\*|\*/|//|\\n|%[a-zA-Z]|0x[0-9a-fA-F]+", cleaned):
        return True
    if re.match(
        r"^\d+\s+(?:int|long|char|short|float|double|void|return|if|else|for|while|foreach|subq|mov[lq]?|add[qbl]?|call|retq?|push|pop|jmp|cmp|lea)\b",
        cleaned,
        flags=re.IGNORECASE,
    ):
        return True
    if re.match(r"^\d+\s+[A-Z]+\s+/", cleaned):
        return True
    if re.match(r"^\d+\s+(?:Host|GET|POST|HTTP|Client|Server)\b", cleaned, flags=re.IGNORECASE):
        return True
    return False


def _looks_like_numbered_title(line: str) -> bool:
    if re.search(r"[{}();#$]|/", line):
        return False
    match = re.match(r"^\d+\s+(.+)$", line)
    if not match:
        return False
    words = re.findall(r"[A-Za-z\u4e00-\u9fff]+", match.group(1))
    if len(words) < 2:
        return False
    first_word = words[0]
    return bool(re.match(r"^[A-Z\u4e00-\u9fff]", first_word))


def _epub_sections(file_path: Path) -> list[dict[str, object]]:
    items = _epub_html_items(file_path)
    sections: list[dict[str, object]] = []
    for item in items:
        text = str(item["text"])
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        heading_indexes = [index for index, line in enumerate(lines) if _looks_like_epub_heading(line)]
        if not heading_indexes:
            title = str(item["title"]).strip() or Path(str(item["path"])).stem
            if _is_epub_separator_title(title) and len(lines) > 1:
                title = next((line for line in lines if len(line) <= 80 and not _is_epub_separator_title(line)), title)
            content = "\n".join(lines).strip()
            if content:
                sections.append(
                    {
                        "title": title[:120],
                        "content": content,
                        "level": 1,
                        "order_index": int(item["order_index"]),
                    }
                )
            continue

        for local_index, line_index in enumerate(heading_indexes):
            end = heading_indexes[local_index + 1] if local_index + 1 < len(heading_indexes) else len(lines)
            title = lines[line_index].strip()
            content = "\n".join(lines[line_index + 1 : end]).strip()
            if _is_epub_separator_title(title) and not content:
                continue
            level = _toc_entry_level(title)
            sections.append(
                {
                    "title": title[:120],
                    "content": content or title,
                    "level": level,
                    "order_index": len(sections),
                }
            )
    return sections


def _read_epub_text(file_path: Path) -> str:
    return _normalize_extracted_text("\n\n".join(str(item["text"]) for item in _epub_html_items(file_path)))


def _read_epub_text_from_sections(sections: list[dict[str, object]]) -> str:
    parts: list[str] = []
    remaining = _EPUB_STORED_TEXT_LIMIT
    for section in sections:
        if remaining <= 0:
            break
        title = str(section["title"]).strip()
        content = str(section["content"]).strip()
        chunk = f"{title}\n{content}".strip()
        if not chunk:
            continue
        parts.append(chunk[:remaining])
        remaining -= len(parts[-1])
    return _normalize_extracted_text("\n\n".join(parts))


def _extract_epub_outline_from_sections(sections: list[dict[str, object]]) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    for section in sections:
        title = str(section["title"]).strip()
        if not title or _is_epub_separator_title(title) or _looks_like_epub_heading_artifact(title):
            continue
        content = str(section["content"])
        summary_seed = re.sub(r"\s+", " ", content).strip()[:120]
        summary = summary_seed or f"来自 EPUB 标题“{title}”的章节摘要待进一步展开。"
        order_index = int(section["order_index"])
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=_keywords_from_text(f"{title}\n{content}"),
                level=int(section["level"]),
                locator_hint=_resource_source_locator(title, source="epub_section", source_index=order_index + 1),
                order_index=order_index,
                scan_strategy="heading_section",
            )
        )
    return chapters


def _extract_epub_outline(file_path: Path) -> list[LibraryChapter]:
    return _extract_epub_outline_from_sections(_epub_sections(file_path))


def _epub_section_with_children(sections: list[dict[str, object]], start_index: int) -> str:
    target = sections[start_index]
    target_level = int(target["level"])

    def section_text(section: dict[str, object]) -> str:
        title = str(section["title"]).strip()
        content = str(section["content"]).strip()
        if content.startswith(title):
            return content
        return f"{title}\n{content}".strip()

    parts = [section_text(target)]
    for section in sections[start_index + 1 :]:
        if int(section["level"]) <= target_level:
            break
        parts.append(section_text(section))
    return "\n\n".join(part for part in parts if part.strip())


def _generic_outline_marker_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", line)
        if not 2 <= len(compact) <= 36:
            continue
        if re.match(r"^[【\[\(（].{1,34}[】\]\)）]$", compact):
            count += 1
            continue
        if re.match(r"^(?:第?[一二三四五六七八九十百千万\d]+[章节部分讲课、.．)]|[A-Za-z][.)])", compact):
            count += 1
    return count


def _continuous_explanatory_sentence_count(text: str) -> int:
    segments = re.split(r"[。！？!?；;]\s*", text)
    return sum(1 for segment in segments if len(re.sub(r"\s+", "", segment)) >= 18)


def _body_text_density(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    body_chars = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        line_compact = re.sub(r"\s+", "", stripped)
        if len(line_compact) <= 36 and _generic_outline_marker_count(stripped):
            continue
        body_chars += len(line_compact)
    return body_chars / len(compact)


def _epub_section_body_score(sections: list[dict[str, object]], index: int) -> tuple[int, int]:
    text = _epub_section_with_children(sections, index)
    compact = re.sub(r"\s+", "", text)
    outline_markers = _generic_outline_marker_count(text)
    explanatory_sentences = _continuous_explanatory_sentence_count(text)
    body_density = _body_text_density(text)
    if len(compact) <= 80 and outline_markers >= 2 and explanatory_sentences == 0:
        return (-1, len(compact))
    score = min(len(compact), 2000)
    if outline_markers >= 2 and explanatory_sentences <= 1:
        score -= 200
    if body_density < 0.45:
        score -= 120
    return (score, len(compact))


def _extract_epub_section_text(file_path: Path, chapter: LibraryChapter, user_query: str) -> tuple[str, str]:
    sections = _epub_sections(file_path)
    if not sections:
        return chapter.title, _read_epub_text(file_path)

    requested_chapter_no, requested_section_no = _extract_epub_requested_outline_reference(user_query)
    if requested_chapter_no is not None:
        exact_candidates: list[int] = []
        fallback_candidates: list[int] = []
        for index, section in enumerate(sections):
            chapter_no, section_no = _epub_title_outline_reference(str(section["title"]))
            if chapter_no != requested_chapter_no:
                continue
            if requested_section_no is not None and section_no == requested_section_no:
                exact_candidates.append(index)
            if requested_section_no is None and section_no is None:
                exact_candidates.append(index)
            fallback_candidates.append(index)
        candidates = exact_candidates or fallback_candidates
        if candidates:
            best_index = max(candidates, key=lambda candidate: _epub_section_body_score(sections, candidate))
            section = sections[best_index]
            return str(section["title"]), _epub_section_with_children(sections, best_index)

    target_title = _locator_title(chapter.locator_hint, chapter.title).strip()
    title_candidates = [
        index
        for index, section in enumerate(sections)
        if str(section["title"]).strip() == target_title
    ]
    if title_candidates:
        best_index = max(title_candidates, key=lambda candidate: _epub_section_body_score(sections, candidate))
        section = sections[best_index]
        return str(section["title"]), _epub_section_with_children(sections, best_index)

    first_index = next(
        (
            index
            for index, section in enumerate(sections)
            if int(section["level"]) == 1 and not _is_epub_separator_title(str(section["title"]))
        ),
        0,
    )
    section = sections[first_index]
    return str(section["title"]), _epub_section_with_children(sections, first_index)


def _looks_like_reference_heading(line: str) -> bool:
    cleaned = line.strip()
    return bool(
        re.match(r"^(?:第\s*[0-9一二三四五六七八九十百〇零两]+\s*章|[0-9]+\s*[.．]\s*[0-9]+)", cleaned)
        or re.match(r"^[一二三四五六七八九十]+[、.．]\s*", cleaned)
    )


def _looks_like_page_artifact(line: str) -> bool:
    cleaned = re.sub(r"\s+", "", line.strip())
    if not cleaned:
        return True
    if cleaned.isdigit() and len(cleaned) <= 3:
        return True
    if _looks_like_epub_heading_artifact(line):
        return True
    if re.fullmatch(r"第[0-9一二三四五六七八九十百〇零两]+章(?:概论|绪论)?", cleaned):
        return True
    return False


def _join_reference_lines(lines: list[str]) -> str:
    text = "".join(line.strip() for line in lines if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def _reference_text_passages(text: str) -> list[str]:
    lines = [line.strip() for line in _normalize_extracted_text(text).splitlines() if line.strip()]
    passages: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        passage = _join_reference_lines(current)
        if len(passage) >= 8:
            passages.append(passage)
        current = []

    for line in lines:
        if _looks_like_page_artifact(line):
            continue
        if _looks_like_reference_heading(line):
            flush()
            passages.append(line)
            continue
        if re.match(r"^[•·\-—]", line):
            flush()
            passages.append(line)
            continue

        current.append(line)
        joined = _join_reference_lines(current)
        if re.search(r"[。！？!?]$", line) and len(joined) >= 80:
            flush()
        elif len(joined) >= 260:
            flush()
    flush()

    fallback = [
        segment.strip()
        for segment in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", text)
        if len(segment.strip()) >= 8
    ]
    candidates = passages or fallback
    unique: list[str] = []
    seen: set[str] = set()
    for passage in candidates:
        cleaned = re.sub(r"\s+", " ", passage).strip()
        if len(cleaned) < 8:
            continue
        key = cleaned[:80]
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def _rank_passages(text: str, query: str, *, anchor: str | None = None) -> list[str]:
    paragraphs = _reference_text_passages(text)
    if not paragraphs:
        compact = re.sub(r"\s+", " ", text).strip()
        return [compact] if compact else []

    query_terms = [term for term in _keywords_from_text(f"{query}\n{anchor or ''}") if len(term) >= 2]
    scored: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        score = 0
        lowered = paragraph.lower()
        for term in query_terms:
            if term.lower() in lowered:
                score += 2
        if anchor and anchor.lower() in lowered:
            score += 3
        if score:
            scored.append((score, paragraph))
    if not scored:
        return paragraphs[:4]
    return [paragraph for _, paragraph in sorted(scored, key=lambda item: item[0], reverse=True)[:4]]


def _build_teaching_hint(chapter_title: str, excerpt: str) -> str:
    focus = _keywords_from_text(excerpt)[:3]
    if focus:
        return f"讲解时先用自己的话串起 {', '.join(focus)}，再回到“{chapter_title}”的主线。"
    return f"讲解时先概括这段在“{chapter_title}”里解决了什么问题，再给一个更口语化解释。"


def _child_chapters(resource: ResourceLibraryItem, chapter: LibraryChapter) -> list[LibraryChapter]:
    children = [
        candidate
        for candidate in resource.outline
        if candidate.parent_id == chapter.id and not _looks_like_page_artifact(candidate.title)
    ]
    if children:
        return children[:8]

    descendants: list[LibraryChapter] = []
    started = False
    for candidate in sorted(resource.outline, key=lambda item: item.order_index):
        if candidate.id == chapter.id:
            started = True
            continue
        if not started:
            continue
        if candidate.level <= chapter.level:
            break
        if not _looks_like_page_artifact(candidate.title):
            descendants.append(candidate)
    return descendants[:8]


def _outline_chunk(chapter: LibraryChapter, children: list[LibraryChapter]) -> ResourceContextChunk | None:
    if not children:
        return None
    titles = [child.title.strip() for child in children if child.title.strip()]
    if not titles:
        return None
    outline = " -> ".join(titles[:6])
    return ResourceContextChunk(
        title=f"{chapter.title} / 目录主线",
        excerpt=f"这一章可以按目录顺序来讲：{outline}。",
        teaching_hint="先把目录讲成学习地图，再展开正文里的定义、例子和系统流程。",
    )


def _generic_teaching_points(
    *,
    chapter: LibraryChapter,
    children: list[LibraryChapter],
    text: str,
) -> list[str]:
    keywords = _keywords_from_text(f"{chapter.title}\n{text}")[:5]
    points = [
        f"先说明“{chapter.title}”这一节在资料结构中要解决的核心问题。",
        "把抽取到的关键术语、材料证据或推理步骤组织成一条可复述的学习主线。",
        "优先解释概念之间的关系、适用条件和容易混淆的边界，而不是照搬原文段落。",
        "讲解时配一个最小例子、对比或检查问题，用来验证学习者是否能迁移。",
    ]
    if children:
        child_titles = "、".join(child.title for child in children[:4])
        points.insert(1, f"参考子目录顺序组织讲解：{child_titles}。")
    if keywords:
        points.insert(2, f"围绕 {', '.join(keywords[:3])} 的关系展开，不把关键词拆成孤立卡片。")
    return points


def _build_reference_context(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    query: str,
    raw_text: str,
) -> ResourceReferenceContext | None:
    normalized_text = _normalize_extracted_text(raw_text)
    compact = re.sub(r"\s+", " ", normalized_text).strip()
    if not compact:
        return None
    if not resource.extracted_text_available and len(compact) < 320:
        return None

    children = _child_chapters(resource, chapter)
    passages = _rank_passages(normalized_text[:12000], query, anchor=chapter.title)
    chunks = [
        ResourceContextChunk(
            title=f"{chapter.title} / 参考片段 {index}",
            excerpt=passage[:420],
            teaching_hint=_build_teaching_hint(chapter.title, passage),
        )
        for index, passage in enumerate(passages[:3], start=1)
    ]
    outline = _outline_chunk(chapter, children)
    if outline is not None:
        chunks.insert(0, outline)

    teaching_points = _generic_teaching_points(chapter=chapter, children=children, text=compact)
    unique_points: list[str] = []
    seen_points: set[str] = set()
    for point in teaching_points:
        if point in seen_points:
            continue
        seen_points.add(point)
        unique_points.append(point)

    if children:
        child_titles = "、".join(child.title for child in children[:5])
        summary = f"《{resource.name}》的《{chapter.title}》包含这些讲解入口：{child_titles}。"
    else:
        summary = f"《{resource.name}》的《{chapter.title}》可以作为本次讲解参考。"
    summary = (
        f"{summary}"
        "下面的上下文会优先保留本章结构、关键定义和可用于课堂解释的片段。"
    )
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=chapter.id,
        resource_name=resource.name,
        chapter_title=chapter.title,
        summary=summary,
        teaching_points=unique_points[:6],
        chunks=chunks,
        full_text=normalized_text,
    )


def _extract_markdown_section_text(file_path: Path, chapter: LibraryChapter) -> str:
    text = _read_text_file(file_path)
    sections = _markdown_sections(text)
    target = next(
        (
            section
            for section in sections
            if str(section["title"]) == (chapter.locator_hint or chapter.title)
        ),
        None,
    )
    if target is None:
        return text[:4000]
    content = str(target["content"]).strip()
    if content:
        return content
    return str(target["title"])


def _extract_pdf_chapter_text(file_path: Path, chapter: LibraryChapter, query: str) -> str:
    reader = PdfReader(str(file_path))
    candidate_pages = _pdf_page_candidates(chapter, len(reader.pages))
    best_text = ""
    best_score = -1
    locator_source = _pdf_locator_source(chapter.locator_hint)
    trusted_locator = locator_source == "pdf_outline"
    for page_start in candidate_pages:
        page_end = min(chapter.page_end or page_start + 3, len(reader.pages))
        raw_text = _read_pdf_text_window(
            reader,
            page_start=page_start,
            page_end=page_end,
            max_pages=max(1, min(12, page_end - page_start + 1)),
        )
        if not raw_text:
            raw_text = extract_pdf_pages_text(
                file_path,
                page_start=page_start,
                page_end=page_end,
                max_pages=max(1, min(6, page_end - page_start + 1)),
            ) or ""
        raw_text = _normalize_extracted_text(raw_text)
        if not raw_text:
            continue

        score = _chapter_text_match_score(raw_text, chapter, query)
        if score > best_score:
            best_score = score
            best_text = raw_text
        if score >= 2 or trusted_locator:
            return raw_text

    if best_text and best_score > 0:
        return best_text

    searched_text = _find_pdf_text_by_keywords(reader, chapter, query)
    if searched_text:
        return searched_text

    if best_text and trusted_locator:
        return best_text

    if chapter.page_start or locator_source == "toc_page":
        return ""

    joined = _read_pdf_text_window(reader, page_start=1, page_end=min(3, len(reader.pages)), max_pages=3)
    if joined:
        return joined

    # 只有没有页码定位时，才退回到前几页相关片段，避免把前言错塞进正文章节。
    fallback: list[str] = []
    for page in reader.pages[: min(5, len(reader.pages))]:
        try:
            fallback.append(_normalize_extracted_text(page.extract_text() or ""))
        except Exception:
            continue
    fallback_text = "\n".join(fallback)
    passages = _rank_passages(fallback_text, query, anchor=chapter.title)
    return "\n\n".join(passages)


def _pdf_page_candidates(chapter: LibraryChapter, total_pages: int) -> list[int]:
    raw_candidates = [
        chapter.page_start,
        _pdf_locator_value(chapter.locator_hint, "actual_page"),
        _pdf_locator_value(chapter.locator_hint, "printed_page"),
    ]
    toc_page = _pdf_locator_value(chapter.locator_hint, "toc_page")
    printed_page = _pdf_locator_value(chapter.locator_hint, "printed_page")
    if toc_page and printed_page:
        raw_candidates.extend([toc_page + printed_page, toc_page + printed_page - 1])

    candidates: list[int] = []
    for candidate in raw_candidates:
        if candidate is None or candidate < 1 or candidate > total_pages:
            continue
        for nearby in (candidate, candidate - 2, candidate - 1, candidate + 1, candidate + 2):
            if 1 <= nearby <= total_pages and nearby not in candidates:
                candidates.append(nearby)
    return candidates


def _chapter_text_match_score(text: str, chapter: LibraryChapter, query: str) -> int:
    compact_text = re.sub(r"\s+", "", text).lower()
    compact_title = re.sub(r"\s+", "", chapter.title).lower()
    score = 0
    if compact_title and compact_title in compact_text:
        score += 4
    for path_item in chapter.path:
        compact_path_item = re.sub(r"\s+", "", path_item).lower()
        if compact_path_item and compact_path_item in compact_text:
            score += 2
    for keyword in _keywords_from_text(f"{chapter.title}\n{' '.join(chapter.keywords)}\n{query}")[:8]:
        if re.sub(r"\s+", "", keyword.lower()) in compact_text:
            score += 1
    return score


def _find_pdf_text_by_keywords(reader: PdfReader, chapter: LibraryChapter, query: str) -> str:
    scored_pages: list[tuple[int, int, str]] = []
    for page_index, page in enumerate(reader.pages):
        try:
            text = _normalize_extracted_text(page.extract_text() or "")
        except Exception:
            continue
        if not text:
            continue
        score = _chapter_text_match_score(text, chapter, query)
        if score:
            scored_pages.append((score, -page_index, text))
    if not scored_pages:
        return ""
    scored_pages.sort(reverse=True)
    return scored_pages[0][2]


def _outline_entries_to_chapters(
    entries: list[tuple[str, int, int | None]],
    total_pages: int,
    *,
    reader: PdfReader | None = None,
) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    for index, (title, level, page_start) in enumerate(entries):
        page_end = None
        if page_start:
            page_end = total_pages
            for _, candidate_level, candidate_page in entries[index + 1 :]:
                if not candidate_page or candidate_level > level:
                    continue
                if candidate_page <= page_start:
                    # Some PDFs place a section label and its first subsection on the
                    # same page. Keep scanning until we find the next real page break.
                    continue
                page_end = max(page_start, candidate_page - 1)
                break
        page_label = None
        if page_start:
            page_label = str(page_start) if page_end == page_start or not page_end else f"{page_start}-{page_end}"
        summary = (
            f"PDF 页 {page_label} 已按目录定位；引用时将读取该页范围正文。"
            if page_label
            else f"PDF 目录项“{title}”被收录进课程资料库。"
        )
        if reader is not None and page_start:
            window_text = _read_pdf_text_window(
                reader,
                page_start=page_start,
                page_end=page_end or page_start,
                max_pages=6,
                max_nonempty_pages=1,
            )
            snippet = _summary_snippet(window_text)
            if snippet:
                summary = f"PDF 页 {page_label} 内容摘要：{snippet}"
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                level=level,
                locator_hint=_pdf_locator_hint(title, source="pdf_outline", actual_page=page_start) if page_start else title,
                order_index=index,
                scan_strategy="page_window" if page_start else "outline_only",
                page_start=page_start,
                page_end=page_end,
            )
        )
    return chapters


def _toc_entry_level(title: str) -> int:
    cleaned = title.strip()
    if re.match(r"^(?:第\s*[0-9一二三四五六七八九十百〇零两]+\s*章|chapter\s+\d+)\b", cleaned, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\s*[.．]\s*\d+", cleaned):
        return 2
    return 1


def _parse_toc_entries(text: str) -> list[tuple[str, int, int]]:
    entries: list[tuple[str, int, int]] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" .·•\t")
        if not line or line in {"目录", "目 录", "contents", "Contents"}:
            continue
        line = re.sub(r"[.．·•…]{2,}", " ", line)
        match = re.search(r"(?P<title>.+?)\s+(?P<page>\d{1,4})$", line)
        if not match:
            continue
        title = match.group("title").strip(" .·•…")
        if len(title) < 2 or not re.search(r"(?:第\s*[0-9一二三四五六七八九十百〇零两]+\s*章|\d+\s*[.．]\s*\d+|chapter\s+\d+)", title, re.IGNORECASE):
            continue
        page_number = int(match.group("page"))
        entries.append((title, _toc_entry_level(title), page_number))
    return entries


def _looks_like_toc_page(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return "目录" in compact or "contents" in compact or len(_parse_toc_entries(text)) >= 2


def _extract_pdf_toc_text_pages(reader: PdfReader, file_path: Path, *, max_pages: int = 20) -> list[tuple[int, str]]:
    toc_pages: list[tuple[int, str]] = []
    in_toc = False
    for page_number in range(1, min(max_pages, len(reader.pages)) + 1):
        text = _read_pdf_text_window(reader, page_start=page_number, page_end=page_number, max_pages=1)
        if not text and page_number <= 12:
            text = extract_pdf_pages_text(file_path, page_start=page_number, page_end=page_number, max_pages=1) or ""
            text = _normalize_extracted_text(text)
        if not text:
            if in_toc:
                break
            continue
        entry_count = len(_parse_toc_entries(text))
        if _looks_like_toc_page(text) or (in_toc and entry_count):
            toc_pages.append((page_number, text))
            in_toc = True
            continue
        if in_toc:
            break
    return toc_pages


def _resolve_toc_entry_actual_page(
    reader: PdfReader,
    *,
    title: str,
    toc_page: int,
    printed_page: int,
) -> int | None:
    candidates = [printed_page, toc_page + printed_page, toc_page + printed_page - 1]
    for candidate in candidates:
        if candidate < 1 or candidate > len(reader.pages):
            continue
        window_text = _read_pdf_text_window(
            reader,
            page_start=candidate,
            page_end=min(candidate + 1, len(reader.pages)),
            max_pages=2,
        )
        if not window_text:
            continue
        pseudo_chapter = LibraryChapter(title=title, summary="", keywords=_keywords_from_text(title))
        if _chapter_text_match_score(window_text, pseudo_chapter, title) > 0:
            return candidate
    return next((candidate for candidate in candidates if 1 <= candidate <= len(reader.pages)), None)


def _toc_entries_to_chapters(reader: PdfReader, toc_pages: list[tuple[int, str]]) -> list[LibraryChapter]:
    raw_entries: list[tuple[str, int, int, int]] = []
    for toc_page, toc_text in toc_pages:
        for title, level, printed_page in _parse_toc_entries(toc_text):
            raw_entries.append((title, level, printed_page, toc_page))
    if not raw_entries:
        return []

    chapters: list[LibraryChapter] = []
    for index, (title, level, printed_page, toc_page) in enumerate(raw_entries):
        actual_page = _resolve_toc_entry_actual_page(
            reader,
            title=title,
            toc_page=toc_page,
            printed_page=printed_page,
        )
        page_end = None
        next_entry = next(
            (
                candidate
                for candidate in raw_entries[index + 1 :]
                if candidate[1] <= level and candidate[2] > printed_page
            ),
            None,
        )
        if actual_page and next_entry:
            next_actual_page = _resolve_toc_entry_actual_page(
                reader,
                title=next_entry[0],
                toc_page=next_entry[3],
                printed_page=next_entry[2],
            )
            if next_actual_page and next_actual_page > actual_page:
                page_end = next_actual_page - 1
        elif actual_page:
            page_end = min(actual_page + 3, len(reader.pages))

        page_label = str(actual_page) if actual_page and (not page_end or page_end == actual_page) else (
            f"{actual_page}-{page_end}" if actual_page and page_end else None
        )
        summary = (
            f"PDF 目录页 {toc_page} 标注页码 {printed_page}；引用时会尝试实际页、目录页偏移和全文检索定位正文。"
        )
        if page_label:
            summary = f"PDF 页 {page_label} 已由目录页 {toc_page} 的页码 {printed_page} 定位；引用时会再次校验正文。"
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=_keywords_from_text(title),
                level=level,
                locator_hint=_pdf_locator_hint(
                    title,
                    source="toc_page",
                    toc_page=toc_page,
                    printed_page=printed_page,
                    actual_page=actual_page,
                ),
                order_index=index,
                scan_strategy="page_window" if actual_page else "fulltext_match",
                page_start=actual_page,
                page_end=page_end,
            )
        )
    return chapters


def _infer_body_start_page(outline: list[LibraryChapter]) -> tuple[int | None, list[str]]:
    candidates: list[int] = []
    evidence: list[str] = []
    for chapter in outline:
        locator_source = _pdf_locator_source(chapter.locator_hint)
        printed_page = _pdf_locator_value(chapter.locator_hint, "printed_page")
        actual_page = chapter.page_start or _pdf_locator_value(chapter.locator_hint, "actual_page")
        if locator_source == "toc_page" and printed_page and actual_page:
            candidate = actual_page - printed_page + 1
            if candidate >= 1:
                candidates.append(candidate)
                evidence.append(f"目录项“{chapter.title}”标注正文页 {printed_page}，映射到物理页 {actual_page}。")
    if candidates:
        body_start = sorted(candidates)[0]
        return body_start, evidence[:6]

    page_starts = [chapter.page_start for chapter in outline if chapter.page_start]
    if page_starts:
        body_start = min(page_starts)
        return body_start, [f"资料目录最早正文候选页为物理页 {body_start}。"]
    return None, ["资料没有可靠页码线索，正文逻辑路径以抽取到的章节顺序建立。"]


def _chapter_body_text_for_index(
    *,
    file_path: Path,
    mime_type: str,
    chapter: LibraryChapter,
    text_content: str | None,
) -> str:
    suffix = file_path.suffix.lower()
    try:
        if text_content and file_path.suffix.lower() not in {".pdf"}:
            if mime_type in {"text/plain", "text/markdown"} or suffix in {".md", ".txt"}:
                return (
                    _extract_markdown_section_text(file_path, chapter)
                    if chapter.scan_strategy == "heading_section" and file_path.exists()
                    else text_content
                )
            if suffix == ".docx" and file_path.exists():
                return (
                    _extract_docx_section_text(file_path, chapter)
                    if chapter.scan_strategy == "heading_section"
                    else text_content
                )
            if suffix == ".epub" and file_path.exists():
                return _text_window_for_chapter(text_content, chapter)
            return text_content

        if suffix == ".pdf" and file_path.exists() and chapter.page_start:
            raw_text = _extract_pdf_chapter_text(file_path, chapter, chapter.title)
            if raw_text:
                return raw_text
    except Exception:
        return ""
    return ""


def _text_window_for_chapter(text_content: str, chapter: LibraryChapter) -> str:
    title = chapter.title.strip()
    if not text_content:
        return f"{title}\n{chapter.summary}\n{' '.join(chapter.keywords)}"
    if not title:
        return text_content[: _BODY_BLOCK_TEXT_LIMIT * 2]
    start = text_content.casefold().find(title.casefold())
    if start < 0:
        return f"{title}\n{chapter.summary}\n{' '.join(chapter.keywords)}"
    return text_content[start : start + _BODY_BLOCK_TEXT_LIMIT * 2]


def _source_location_range(
    *,
    body_page_no: int | None,
    physical_page_no: int | None,
    source_index: int | None,
) -> str | None:
    if physical_page_no is not None and body_page_no is not None:
        return f"正文第 {body_page_no} 页 / 全文第 {physical_page_no} 页"
    if body_page_no is not None:
        return f"正文逻辑页 {body_page_no}"
    if physical_page_no is not None:
        return f"全文第 {physical_page_no} 页"
    if source_index is not None:
        return f"源顺序单元 {source_index}"
    return None


def _source_locator_for_chapter(
    *,
    file_path: Path,
    chapter: LibraryChapter,
    chapter_index: int,
    body_page_no: int | None,
    physical_page_no: int | None,
) -> str | None:
    locator_source = _pdf_locator_source(chapter.locator_hint)
    source_index = _pdf_locator_value(chapter.locator_hint, "source_index") or chapter_index + 1
    if locator_source:
        return _resource_source_locator(
            _locator_title(chapter.locator_hint, chapter.title),
            source=locator_source,
            source_index=source_index,
            body_page=body_page_no,
            physical_page=physical_page_no,
        )
    suffix = file_path.suffix.lower().lstrip(".") or "resource"
    return _resource_source_locator(
        chapter.title,
        source=f"{suffix}_section",
        source_index=chapter_index + 1,
        body_page=body_page_no,
        physical_page=physical_page_no,
    )


def _build_indexed_source_units(
    *,
    file_path: Path,
    mime_type: str,
    outline: list[LibraryChapter],
    text_content: str | None,
    body_start_page: int | None,
) -> list[IndexedSourceUnit]:
    units: list[IndexedSourceUnit] = []
    for chapter_index, chapter in enumerate(outline):
        locator_source = _pdf_locator_source(chapter.locator_hint)
        printed_page = _pdf_locator_value(chapter.locator_hint, "printed_page")
        actual_page = chapter.page_start or _pdf_locator_value(chapter.locator_hint, "actual_page")
        if locator_source == "toc_page" and printed_page:
            body_page_no = printed_page
            page_scope = "body"
        elif actual_page and body_start_page:
            body_page_no = actual_page - body_start_page + 1
            page_scope = "body" if body_page_no >= 1 else "physical"
        else:
            body_page_no = chapter_index + 1
            page_scope = "body"

        physical_page_no = actual_page
        if body_start_page and body_page_no and page_scope == "body":
            physical_page_no = body_start_page + body_page_no - 1

        heading_path = chapter.path or [chapter.title]
        raw_text = _chapter_body_text_for_index(
            file_path=file_path,
            mime_type=mime_type,
            chapter=chapter,
            text_content=text_content,
        )
        block_text = _normalize_extracted_text(raw_text)[:_BODY_BLOCK_TEXT_LIMIT]
        if not block_text:
            block_text = _normalize_extracted_text(f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}")

        source_locator = _source_locator_for_chapter(
            file_path=file_path,
            chapter=chapter,
            chapter_index=chapter_index,
            body_page_no=body_page_no if page_scope == "body" else None,
            physical_page_no=physical_page_no,
        )
        source_index = _pdf_locator_value(source_locator, "source_index")
        units.append(
            IndexedSourceUnit(
                chapter=chapter,
                heading_path=heading_path,
                block_text=block_text,
                page_number_scope=page_scope,
                body_page_no=body_page_no if page_scope == "body" else None,
                body_page_idx=body_page_no - 1 if body_page_no and page_scope == "body" else None,
                physical_page_no=physical_page_no,
                physical_page_idx=physical_page_no - 1 if physical_page_no else None,
                source_locator=source_locator,
                source_location_range=_source_location_range(
                    body_page_no=body_page_no if page_scope == "body" else None,
                    physical_page_no=physical_page_no,
                    source_index=source_index,
                ),
            )
        )
    return units


def _build_resource_structure_regions(
    *,
    outline: list[LibraryChapter],
    body_start_page: int | None,
    body_start_evidence: list[str],
    text_content: str | None,
) -> list[ResourceStructureRegion]:
    max_page = max((chapter.page_end or chapter.page_start or 0 for chapter in outline), default=0)
    toc_pages = sorted(
        {
            value
            for chapter in outline
            for value in [_pdf_locator_value(chapter.locator_hint, "toc_page")]
            if value is not None
        }
    )
    if max_page <= 0 and text_content:
        max_page = 1
    if max_page <= 0:
        return [
            ResourceStructureRegion(
                role="unknown",
                label="未识别结构",
                confidence=0.25,
                evidence=["资料没有页码或章节正文可用于结构分区。"],
            )
        ]

    regions: list[ResourceStructureRegion] = []
    if body_start_page and body_start_page > 1:
        regions.append(
            ResourceStructureRegion(
                role="cover",
                label="封面 / 起始材料",
                physical_page_start=1,
                physical_page_end=1,
                confidence=0.55,
                evidence=["正文第一页之前的第一页按通用资料结构标记为起始材料。"],
            )
        )
        toc_start = min(toc_pages) if toc_pages else None
        toc_end = max(toc_pages) if toc_pages else None
        if toc_start and toc_start > 2:
            regions.append(
                ResourceStructureRegion(
                    role="front_matter",
                    label="前言 / 正文前材料",
                    physical_page_start=2,
                    physical_page_end=toc_start - 1,
                    confidence=0.58,
                    evidence=["正文第一页之前、目录之前的页面按通用结构归为前置材料。"],
                )
            )
        elif not toc_start and body_start_page > 2:
            regions.append(
                ResourceStructureRegion(
                    role="front_matter",
                    label="前言 / 正文前材料",
                    physical_page_start=2,
                    physical_page_end=body_start_page - 1,
                    confidence=0.5,
                    evidence=["未识别独立目录页，正文第一页之前的页面归为前置材料。"],
                )
            )
        if toc_start and toc_end:
            regions.append(
                ResourceStructureRegion(
                    role="toc",
                    label="目录",
                    physical_page_start=toc_start,
                    physical_page_end=toc_end,
                    confidence=0.82,
                    evidence=[f"PDF 目录项来自物理页 {toc_start}-{toc_end}。"],
                )
            )
            if toc_end + 1 < body_start_page:
                regions.append(
                    ResourceStructureRegion(
                        role="front_matter",
                        label="目录后正文前材料",
                        physical_page_start=toc_end + 1,
                        physical_page_end=body_start_page - 1,
                        confidence=0.48,
                        evidence=["目录结束后到正文开始前仍存在前置材料。"],
                    )
                )

    body_start = body_start_page or 1
    regions.append(
        ResourceStructureRegion(
            role="body",
            label="正文",
            physical_page_start=body_start,
            physical_page_end=max_page,
            body_page_start=1,
            body_page_end=max_page - body_start + 1 if max_page >= body_start else None,
            confidence=0.78 if body_start_page else 0.46,
            evidence=body_start_evidence,
        )
    )
    return regions


def _build_resource_indexes(
    *,
    file_path: Path,
    mime_type: str,
    outline: list[LibraryChapter],
    text_content: str | None,
) -> tuple[
    list[ResourceStructureRegion],
    list[ResourceBodyBlock],
    list[ResourceTOCEntry],
    list[ResourceChapterShard],
    list[str],
]:
    body_start_page, body_start_evidence = _infer_body_start_page(outline)
    structure_regions = _build_resource_structure_regions(
        outline=outline,
        body_start_page=body_start_page,
        body_start_evidence=body_start_evidence,
        text_content=text_content,
    )
    warnings: list[str] = []
    if body_start_page is None:
        warnings.append("未识别可靠正文第一页，正文逻辑页码按章节顺序建立。")

    body_blocks: list[ResourceBodyBlock] = []
    toc_entries: list[ResourceTOCEntry] = []
    chapter_shards: list[ResourceChapterShard] = []
    source_units = _build_indexed_source_units(
        file_path=file_path,
        mime_type=mime_type,
        outline=outline,
        text_content=text_content,
        body_start_page=body_start_page,
    )

    for chapter_index, unit in enumerate(source_units):
        chapter = unit.chapter
        locator_source = _pdf_locator_source(chapter.locator_hint)
        printed_page = _pdf_locator_value(chapter.locator_hint, "printed_page")
        block = ResourceBodyBlock(
            chapter_id=chapter.id,
            physical_page_no=unit.physical_page_no,
            physical_page_idx=unit.physical_page_idx,
            body_page_no=unit.body_page_no,
            body_page_idx=unit.body_page_idx,
            source_locator=unit.source_locator,
            source_location_range=unit.source_location_range,
            block_order=chapter_index,
            heading_path=unit.heading_path,
            text=unit.block_text,
            text_hash=_stable_text_hash(unit.block_text),
        )
        body_blocks.append(block)

        toc_entries.append(
            ResourceTOCEntry(
                chapter_id=chapter.id,
                title=chapter.title,
                level=chapter.level,
                heading_path=unit.heading_path,
                printed_page_label=str(printed_page) if printed_page else _page_label(chapter.page_start, chapter.page_end),
                page_number_scope=unit.page_number_scope,  # type: ignore[arg-type]
                body_page_no=unit.body_page_no,
                physical_page_no=unit.physical_page_no,
                confidence=0.84 if locator_source == "toc_page" and printed_page and unit.physical_page_no else 0.58,
                evidence=[
                    "目录页码默认按正文页码解释。"
                    if locator_source == "toc_page" and printed_page
                    else "按章节正文逻辑页码或章节顺序建立目录入口。"
                ],
            )
        )

        page_end = chapter.page_end
        body_page_end = unit.body_page_no
        if page_end and body_start_page and unit.body_page_no is not None:
            body_page_end = max(unit.body_page_no, page_end - body_start_page + 1)
        chapter_shards.append(
            ResourceChapterShard(
                chapter_id=chapter.id,
                title=chapter.title,
                heading_path=unit.heading_path,
                body_page_start=unit.body_page_no,
                body_page_end=body_page_end,
                physical_page_start=unit.physical_page_no,
                physical_page_end=page_end,
                source_locator=unit.source_locator,
                source_location_range=unit.source_location_range,
                block_ids=[block.id],
                summary=chapter.summary,
                keywords=chapter.keywords,
                text_hash=_stable_text_hash(f"{chapter.title}\n{chapter.summary}\n{unit.block_text}"),
            )
        )

    return structure_regions, body_blocks, toc_entries, chapter_shards, warnings


def _resource_index_stats(body_blocks: list[ResourceBodyBlock]) -> tuple[int, int, str]:
    indexed_block_count = len([block for block in body_blocks if block.text.strip()])
    physical_pages = [block.physical_page_no for block in body_blocks if block.physical_page_no is not None]
    body_pages = [block.body_page_no for block in body_blocks if block.body_page_no is not None]
    page_count = max(physical_pages or body_pages or [0])
    if physical_pages:
        message = f"已完成索引：{page_count} 页，{indexed_block_count} 个正文块。"
    else:
        message = f"已完成索引：{page_count} 个正文逻辑单元，{indexed_block_count} 个正文块。"
    return page_count, indexed_block_count, message


def build_copyright_evidence_packet(
    file_path: Path,
    original_name: str,
    mime_type: str,
    *,
    structure_regions: list[ResourceStructureRegion],
    text_content: str | None,
) -> ResourceCopyrightEvidencePacket:
    suffix = file_path.suffix.lower()
    base_packet = ResourceCopyrightEvidencePacket(
        title_candidates=[_metadata_phrase(Path(original_name).stem)],
        source_markers=[f"filename:{original_name}"],
        metadata_sources=["filename"],
    )
    if mime_type.startswith("image/"):
        return _trim_copyright_packet(
            _merge_copyright_packets(
                base_packet,
                _copyright_probe_from_image_summary(original_name, text_content),
            )
        )
    if suffix == ".pdf":
        return _trim_copyright_packet(
            _merge_copyright_packets(
                base_packet,
                _copyright_probe_from_pdf(file_path, structure_regions),
            )
        )
    if suffix == ".epub":
        return _trim_copyright_packet(
            _merge_copyright_packets(
                base_packet,
                _copyright_probe_from_epub(file_path),
            )
        )
    if suffix == ".docx":
        return _trim_copyright_packet(
            _merge_copyright_packets(
                base_packet,
                _copyright_probe_from_docx(file_path),
            )
        )
    if mime_type in {"text/plain", "text/markdown"} or suffix in {".md", ".txt"}:
        return _trim_copyright_packet(
            _merge_copyright_packets(
                base_packet,
                _copyright_probe_from_text(original_name, text_content or _read_text_file(file_path)),
            )
        )
    return _trim_copyright_packet(base_packet)


def _copyright_probe_from_pdf(
    file_path: Path,
    structure_regions: list[ResourceStructureRegion],
) -> ResourceCopyrightEvidencePacket:
    packet = ResourceCopyrightEvidencePacket()
    try:
        reader = PdfReader(str(file_path))
    except Exception:
        return packet

    metadata_texts: list[str] = []
    try:
        metadata = reader.metadata or {}
    except Exception:
        metadata = {}
    for key, source_name in (
        ("/Title", "pdf_metadata:title"),
        ("/Author", "pdf_metadata:author"),
        ("/Subject", "pdf_metadata:subject"),
        ("/Creator", "pdf_metadata:creator"),
        ("/Producer", "pdf_metadata:producer"),
    ):
        value = str(metadata.get(key) or "").strip() if metadata else ""
        if not value:
            continue
        metadata_texts.append(f"{key.lstrip('/')} {value}")
        if key == "/Title":
            _append_candidate(packet.title_candidates, value)
        elif key == "/Author":
            _append_candidate(packet.author_candidates, value)
        _append_candidate(packet.metadata_sources, source_name)
    if metadata_texts:
        _add_probe_section(
            packet,
            role="metadata",
            label="PDF metadata",
            source_location="pdf:metadata",
            text="\n".join(metadata_texts),
            confidence=0.72,
            evidence=["PDF document metadata"],
            allow_header=True,
        )
        _collect_copyright_candidates(packet, "\n".join(metadata_texts))

    total_pages = len(reader.pages)
    selected_pages: list[int] = []
    selected_pages.extend(range(1, min(total_pages, 6) + 1))
    for region in structure_regions:
        if region.role not in {"cover", "front_matter", "toc"}:
            continue
        if region.physical_page_start is None:
            continue
        end = region.physical_page_end or region.physical_page_start
        selected_pages.extend(range(region.physical_page_start, min(end, region.physical_page_start + 5) + 1))
    if total_pages > 2:
        selected_pages.extend(range(max(1, total_pages - 1), total_pages + 1))

    seen_pages: set[int] = set()
    for page_no in selected_pages:
        if page_no in seen_pages or page_no < 1 or page_no > total_pages:
            continue
        seen_pages.add(page_no)
        try:
            raw_text = reader.pages[page_no - 1].extract_text() or ""
        except Exception:
            continue
        role = _pdf_probe_role(page_no, total_pages, structure_regions)
        allow_header = role in {"cover", "title_page"} and page_no <= 2
        if _add_probe_section(
            packet,
            role=role,
            label=f"PDF page {page_no}",
            source_location=f"pdf:page:{page_no}",
            text=raw_text,
            confidence=0.62 if role != "unknown" else 0.42,
            evidence=["Selected from generic PDF front matter, table of contents, or back matter probes."],
            allow_header=allow_header,
        ):
            _collect_copyright_candidates(packet, raw_text)

    return packet


def _copyright_probe_from_epub(file_path: Path) -> ResourceCopyrightEvidencePacket:
    packet = ResourceCopyrightEvidencePacket()
    try:
        with zipfile.ZipFile(file_path) as archive:
            rootfile_path = _epub_rootfile_path(archive)
            opf_root = None
            if rootfile_path:
                try:
                    opf_root = ET.fromstring(archive.read(rootfile_path))
                except (KeyError, ET.ParseError):
                    opf_root = None
            if opf_root is not None:
                metadata_text = _epub_metadata_text(opf_root)
                if metadata_text:
                    _add_probe_section(
                        packet,
                        role="metadata",
                        label="EPUB OPF metadata",
                        source_location=f"epub:{rootfile_path}:metadata",
                        text=metadata_text,
                        confidence=0.82,
                        evidence=["EPUB package metadata"],
                        allow_header=True,
                    )
                    _collect_epub_metadata_candidates(packet, opf_root)
                    _collect_copyright_candidates(packet, metadata_text)
                    _append_candidate(packet.metadata_sources, "epub_opf_metadata")
            for path, label, order_index in _epub_probe_paths(archive, rootfile_path, opf_root):
                try:
                    raw_html = _decode_epub_bytes(archive.read(path))
                except KeyError:
                    continue
                text = _epub_text_from_html(raw_html)
                if not text:
                    continue
                role = _epub_probe_role(path, label, order_index)
                allow_header = role in {"cover", "title_page", "front_matter"} and order_index <= 3
                if _add_probe_section(
                    packet,
                    role=role,
                    label=label,
                    source_location=f"epub:{path}",
                    text=text,
                    confidence=0.66,
                    evidence=["Selected from generic EPUB metadata, navigation, cover, or front matter probes."],
                    allow_header=allow_header,
                ):
                    _collect_copyright_candidates(packet, text)
    except (zipfile.BadZipFile, OSError):
        return packet
    return packet


def _copyright_probe_from_docx(file_path: Path) -> ResourceCopyrightEvidencePacket:
    packet = ResourceCopyrightEvidencePacket()
    try:
        document = DocxDocument(file_path)
    except Exception:
        return packet
    properties = document.core_properties
    metadata_parts: list[str] = []
    for label, value in (
        ("title", properties.title),
        ("author", properties.author),
        ("subject", properties.subject),
        ("keywords", properties.keywords),
        ("category", properties.category),
        ("comments", properties.comments),
    ):
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        metadata_parts.append(f"{label}: {cleaned}")
        if label == "title":
            _append_candidate(packet.title_candidates, cleaned)
        elif label == "author":
            _append_candidate(packet.author_candidates, cleaned)
        _append_candidate(packet.metadata_sources, f"docx_core_properties:{label}")
    if metadata_parts:
        metadata_text = "\n".join(metadata_parts)
        _add_probe_section(
            packet,
            role="metadata",
            label="DOCX core properties",
            source_location="docx:core_properties",
            text=metadata_text,
            confidence=0.78,
            evidence=["DOCX core properties"],
            allow_header=True,
        )
        _collect_copyright_candidates(packet, metadata_text)

    first_items = [str(item["text"]) for item in _docx_items(file_path)[:24]]
    start_text = "\n".join(first_items)[:_COPYRIGHT_PROBE_WINDOW_LIMIT]
    if start_text and _contains_copyright_probe_signal(start_text):
        if _add_probe_section(
            packet,
            role="document_start",
            label="DOCX document start",
            source_location="docx:start",
            text=start_text,
            confidence=0.58,
            evidence=["First document window contains generic copyright or license markers."],
        ):
            _collect_copyright_candidates(packet, start_text)
    return packet


def _copyright_probe_from_text(original_name: str, text: str) -> ResourceCopyrightEvidencePacket:
    packet = ResourceCopyrightEvidencePacket()
    window = text[:_COPYRIGHT_PROBE_WINDOW_LIMIT]
    heading_match = re.search(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", window, flags=re.MULTILINE)
    if heading_match:
        _append_candidate(packet.title_candidates, heading_match.group(1))
        _append_candidate(packet.metadata_sources, "document_start_heading")
    if _contains_copyright_probe_signal(window):
        if _add_probe_section(
            packet,
            role="document_start",
            label=f"{Path(original_name).suffix.upper() or 'Text'} document start",
            source_location="text:start",
            text=window,
            confidence=0.54,
            evidence=["First text window contains generic copyright or license markers."],
        ):
            _collect_copyright_candidates(packet, window)
    return packet


def _copyright_probe_from_image_summary(original_name: str, text_content: str | None) -> ResourceCopyrightEvidencePacket:
    packet = ResourceCopyrightEvidencePacket(metadata_sources=["filename"])
    text = (text_content or "")[:_COPYRIGHT_PROBE_WINDOW_LIMIT]
    if not text or not _contains_copyright_probe_signal(text):
        return packet
    if _add_probe_section(
        packet,
        role="ocr_summary",
        label=f"Image OCR summary for {original_name}",
        source_location="image:ocr_summary",
        text=text,
        confidence=0.5,
        evidence=["Existing image OCR summary contains generic copyright or license markers."],
    ):
        _collect_copyright_candidates(packet, text)
    return packet


def _add_probe_section(
    packet: ResourceCopyrightEvidencePacket,
    *,
    role: str,
    label: str,
    source_location: str,
    text: str,
    confidence: float,
    evidence: list[str],
    allow_header: bool = False,
) -> bool:
    if len(packet.probe_sections) >= _COPYRIGHT_PROBE_SECTION_LIMIT:
        return False
    excerpt = _copyright_excerpt(text, allow_header=allow_header)
    if not excerpt:
        return False
    packet.probe_sections.append(
        ResourceCopyrightProbeSection(
            role=role,  # type: ignore[arg-type]
            label=label[:120],
            source_location=source_location,
            text_excerpt=excerpt,
            confidence=confidence,
            evidence=evidence,
        )
    )
    return True


def _copyright_excerpt(text: str, *, allow_header: bool = False) -> str:
    normalized = _normalize_extracted_text(text or "")
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    selected: list[str] = []
    for index, line in enumerate(lines):
        if not _line_has_copyright_probe_signal(line):
            continue
        if index > 0 and len(lines[index - 1]) <= 160 and len(selected) < 8:
            selected.append(lines[index - 1])
        selected.append(line)
        if index + 1 < len(lines) and len(lines[index + 1]) <= 220:
            selected.append(lines[index + 1])
        if len("\n".join(selected)) >= _COPYRIGHT_PROBE_TEXT_LIMIT:
            break
    if not selected and allow_header:
        selected = [line for line in lines[:8] if len(line) <= 140][:4]
    return _dedupe_preserve_order(selected)[:_COPYRIGHT_PROBE_TEXT_LIMIT].strip()


def _contains_copyright_probe_signal(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _COPYRIGHT_PROBE_PATTERNS)


def _line_has_copyright_probe_signal(line: str) -> bool:
    lowered = line.lower()
    return any(pattern in lowered for pattern in _COPYRIGHT_PROBE_PATTERNS)


def _collect_copyright_candidates(packet: ResourceCopyrightEvidencePacket, text: str) -> None:
    for value in _isbn_candidates(text):
        _append_candidate(packet.isbn_candidates, value)
    for value in _line_candidates(text, ("publisher", "published by", "出版社", "出版者")):
        _append_candidate(packet.publisher_candidates, value)
    for value in _line_candidates(text, ("copyright", "rights", "版权所有", "著作权", "cip")):
        _append_candidate(packet.rights_candidates, value)
    for value in _line_candidates(text, _COPYRIGHT_LICENSE_PATTERNS):
        _append_candidate(packet.license_candidates, value)
    for value in _line_candidates(text, ("author", "作者")):
        _append_candidate(packet.author_candidates, _strip_metadata_label(value))


def _line_candidates(text: str, patterns: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for line in _normalize_extracted_text(text).splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned or len(cleaned) > 240:
            continue
        lowered = cleaned.lower()
        if any(pattern in lowered for pattern in patterns):
            candidates.append(cleaned)
        if len(candidates) >= 6:
            break
    return candidates


def _append_candidate(values: list[str], value: str | None, *, limit: int = 8) -> None:
    cleaned = _metadata_phrase(value or "")
    if not cleaned:
        return
    key = cleaned.lower()
    if any(existing.lower() == key for existing in values):
        return
    values.append(cleaned)
    del values[limit:]


def _dedupe_preserve_order(values: list[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return "\n".join(output)


def _metadata_phrase(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip(" .,:;()[]{}")
    cleaned = re.sub(r"(?i)^(/?[a-z_ -]{2,24}|[\u4e00-\u9fff]{2,12})\s*[:：]\s*", "", cleaned)
    return cleaned[:160].strip()


def _strip_metadata_label(value: str) -> str:
    return re.sub(r"(?i)^(author|by|作者)\s*[:：]?\s*", "", value).strip()


def _isbn_candidates(text: str) -> list[str]:
    matches: list[str] = []
    for match in re.finditer(
        r"(?i)\bISBN(?:-1[03])?\s*[:：]?\s*([0-9][0-9Xx][0-9Xx\- ]{7,20}[0-9Xx])",
        text or "",
    ):
        value = re.sub(r"[^0-9Xx]", "", match.group(1)).upper()
        if len(value) in {10, 13} and value not in matches:
            matches.append(value)
    return matches[:4]


def _merge_copyright_packets(
    first: ResourceCopyrightEvidencePacket,
    second: ResourceCopyrightEvidencePacket,
) -> ResourceCopyrightEvidencePacket:
    merged = first.model_copy(deep=True)
    for field_name in (
        "title_candidates",
        "author_candidates",
        "publisher_candidates",
        "isbn_candidates",
        "rights_candidates",
        "license_candidates",
        "source_markers",
        "metadata_sources",
    ):
        values = getattr(merged, field_name)
        for value in getattr(second, field_name):
            _append_candidate(values, value)
    merged.probe_sections.extend(second.probe_sections)
    return merged


def _trim_copyright_packet(packet: ResourceCopyrightEvidencePacket) -> ResourceCopyrightEvidencePacket:
    packet.probe_sections = packet.probe_sections[:_COPYRIGHT_PROBE_SECTION_LIMIT]
    used = 0
    trimmed_sections: list[ResourceCopyrightProbeSection] = []
    for section in packet.probe_sections:
        remaining = _COPYRIGHT_PROBE_PACKET_TEXT_LIMIT - used
        if remaining <= 0:
            break
        excerpt = section.text_excerpt[: min(_COPYRIGHT_PROBE_TEXT_LIMIT, remaining)].strip()
        if not excerpt:
            continue
        used += len(excerpt)
        trimmed_sections.append(section.model_copy(update={"text_excerpt": excerpt}))
    packet.probe_sections = trimmed_sections
    return packet


def _pdf_probe_role(
    page_no: int,
    total_pages: int,
    structure_regions: list[ResourceStructureRegion],
) -> str:
    for region in structure_regions:
        start = region.physical_page_start
        end = region.physical_page_end or start
        if start is not None and end is not None and start <= page_no <= end:
            if region.role == "cover":
                return "cover"
            if region.role == "front_matter":
                return "front_matter"
            if region.role == "toc":
                return "toc"
    if page_no == 1:
        return "cover"
    if page_no <= 6:
        return "front_matter"
    if total_pages and page_no >= max(1, total_pages - 1):
        return "back_matter"
    return "unknown"


def _epub_metadata_text(opf_root: ET.Element) -> str:
    parts: list[str] = []
    for node in opf_root.findall(".//{*}metadata/{*}*"):
        name = _xml_local_name(node.tag).lower()
        if name not in {"title", "creator", "publisher", "identifier", "rights", "language", "date"}:
            continue
        value = " ".join((node.text or "").split())
        if value:
            parts.append(f"{name}: {value}")
    return "\n".join(parts)


def _collect_epub_metadata_candidates(packet: ResourceCopyrightEvidencePacket, opf_root: ET.Element) -> None:
    for node in opf_root.findall(".//{*}metadata/{*}*"):
        name = _xml_local_name(node.tag).lower()
        value = " ".join((node.text or "").split())
        if not value:
            continue
        if name == "title":
            _append_candidate(packet.title_candidates, value)
        elif name == "creator":
            _append_candidate(packet.author_candidates, value)
        elif name == "publisher":
            _append_candidate(packet.publisher_candidates, value)
        elif name == "identifier":
            for isbn in _isbn_candidates(value):
                _append_candidate(packet.isbn_candidates, isbn)
            if "isbn" in value.lower():
                _append_candidate(packet.source_markers, "epub_identifier:isbn")
        elif name == "rights":
            _append_candidate(packet.rights_candidates, value)


def _epub_probe_paths(
    archive: zipfile.ZipFile,
    rootfile_path: str | None,
    opf_root: ET.Element | None,
) -> list[tuple[str, str, int]]:
    if opf_root is None or rootfile_path is None:
        html_paths = [path for path in archive.namelist() if _epub_is_html_path(path)]
        return [(path, Path(path).stem, index) for index, path in enumerate(html_paths[:6])]

    base_dir = posixpath.dirname(rootfile_path)
    manifest: dict[str, tuple[str, str, str]] = {}
    for item in opf_root.findall(".//{*}manifest/{*}item"):
        item_id = item.attrib.get("id", "").strip()
        href = item.attrib.get("href", "").strip()
        if not item_id or not href:
            continue
        path = posixpath.normpath(posixpath.join(base_dir, href))
        properties = item.attrib.get("properties", "").strip()
        media_type = item.attrib.get("media-type", "").strip()
        manifest[item_id] = (path, properties, media_type)

    ordered: list[tuple[str, str, int]] = []
    for order_index, itemref in enumerate(opf_root.findall(".//{*}spine/{*}itemref")):
        item_id = itemref.attrib.get("idref", "").strip()
        path, properties, media_type = manifest.get(item_id, ("", "", ""))
        if not path or not (media_type in {"application/xhtml+xml", "text/html"} or _epub_is_html_path(path)):
            continue
        ordered.append((path, " ".join(filter(None, [item_id, properties])), order_index))

    manifest_only = [
        (path, " ".join(filter(None, [item_id, properties])), len(ordered) + index)
        for index, (item_id, (path, properties, media_type)) in enumerate(manifest.items())
        if path not in {entry[0] for entry in ordered}
        and (media_type in {"application/xhtml+xml", "text/html"} or _epub_is_html_path(path))
    ]
    selected: list[tuple[str, str, int]] = []
    for path, label, order_index in [*ordered, *manifest_only]:
        marker = f"{path} {label}".lower()
        is_probe_path = any(
            token in marker
            for token in (
                "cover",
                "title",
                "copyright",
                "rights",
                "license",
                "front",
                "preface",
                "foreword",
                "nav",
                "toc",
            )
        )
        if is_probe_path or order_index <= 3:
            selected.append((path, label or Path(path).stem, order_index))
        if len(selected) >= 8:
            break
    return [(path, label, index) for path, label, index in selected if path in archive.namelist()]


def _epub_probe_role(path: str, label: str, order_index: int) -> str:
    marker = f"{path} {label}".lower()
    if "cover" in marker:
        return "cover"
    if "copyright" in marker or "rights" in marker or "license" in marker:
        return "copyright_page"
    if "title" in marker:
        return "title_page"
    if "nav" in marker or "toc" in marker:
        return "toc"
    if "front" in marker or "preface" in marker or "foreword" in marker:
        return "front_matter"
    return "unknown"


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def extract_outline(file_path: Path, original_name: str, mime_type: str) -> tuple[list[LibraryChapter], bool, str | None]:
    # 资料解析入口：按图片、文本、DOCX、EPUB、PDF 等格式提取目录和可检索文本。
    if mime_type.startswith("image/"):
        generic_title = Path(original_name).stem
        extracted_text = extract_image_text(file_path)
        if extracted_text:
            ai_outline = _ai_generated_outline(original_name, extracted_text)
            if ai_outline:
                return ai_outline, True, extracted_text
            return (
                [
                    _generic_chapter_from_text(
                        generic_title,
                        extracted_text,
                        summary_prefix="从图片中识别到的文字摘要：",
                    )
                ],
                True,
                extracted_text,
            )
        return (
            [
                _chapter(
                    title=generic_title,
                    summary=f"已上传图片资料“{original_name}”，可作为当前课程的视觉参考。",
                    keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", generic_title)[:6]],
                    locator_hint=generic_title,
                    order_index=0,
                )
            ],
            False,
            None,
        )

    if mime_type in {"text/plain", "text/markdown"} or file_path.suffix.lower() in {".md", ".txt"}:
        text = _read_text_file(file_path)
        outline = _extract_markdown_outline(text)
        if outline:
            return outline, True, text
        ai_outline = _ai_generated_outline(original_name, text)
        if ai_outline:
            return ai_outline, True, text
        return (
            [
                _generic_chapter_from_text(
                    Path(original_name).stem,
                    text,
                    summary_prefix="从文本资料中抽取到的内容摘要：",
                )
            ],
            True,
            text,
        )

    if file_path.suffix.lower() == ".docx":
        text = _read_docx_text(file_path)
        outline = _extract_docx_outline(file_path)
        if outline:
            return outline, True, text
        ai_outline = _ai_generated_outline(original_name, text)
        if ai_outline:
            return ai_outline, True, text
        return (
            [
                _generic_chapter_from_text(
                    Path(original_name).stem,
                    text,
                    summary_prefix="从 Word 资料中抽取到的内容摘要：",
                )
            ],
            True,
            text,
        )

    if file_path.suffix.lower() == ".epub":
        sections = _epub_sections(file_path)
        text = _read_epub_text_from_sections(sections)
        outline = _extract_epub_outline_from_sections(sections)
        if outline:
            return outline, True, text if text else None
        if text:
            ai_outline = _ai_generated_outline(original_name, text)
            if ai_outline:
                return ai_outline, True, text
            return (
                [
                    _generic_chapter_from_text(
                        Path(original_name).stem,
                        text,
                        summary_prefix="从 EPUB 资料中抽取到的内容摘要：",
                    )
                ],
                True,
                text,
            )

    if file_path.suffix.lower() == ".pdf":
        reader = PdfReader(str(file_path))
        if reader.outline:
            entries: list[tuple[str, int, int | None]] = []

            def _walk_outline(items: list, level: int = 1) -> None:
                for item in items:
                    if isinstance(item, list):
                        _walk_outline(item, level + 1)
                        continue
                    title = str(getattr(item, "title", item))
                    page_start = None
                    try:
                        page_start = reader.get_destination_page_number(item) + 1
                    except Exception:
                        page_start = None
                    entries.append((title, level, page_start))

            _walk_outline(list(reader.outline))
            chapters = _outline_entries_to_chapters(entries, len(reader.pages), reader=reader)
            if chapters:
                return chapters, True, None

        toc_pages = _extract_pdf_toc_text_pages(reader, file_path)
        toc_chapters = _toc_entries_to_chapters(reader, toc_pages)
        if toc_chapters:
            return toc_chapters, True, None
        extracted_text = []
        for page in reader.pages[:2]:
            try:
                extracted_text.append(page.extract_text() or "")
            except Exception:
                continue
        joined_text = "\n".join(extracted_text).strip()
        if joined_text:
            ai_outline = _ai_generated_outline(original_name, joined_text)
            if ai_outline:
                return ai_outline, True, None
            return (
                [
                    _generic_chapter_from_text(
                        Path(original_name).stem,
                        joined_text,
                        summary_prefix="从 PDF 前几页抽取到的内容摘要：",
                    )
                ],
                True,
                None,
            )
    generic_title = Path(original_name).stem
    return (
        [
            _chapter(
                title=generic_title,
                summary=f"当前资料尚未提取出显式目录，先以“{generic_title}”作为入口。",
                keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", generic_title)[:5]],
                locator_hint=generic_title,
                order_index=0,
            )
        ],
        False,
        None,
    )


def build_resource_item(file_path: Path, original_name: str) -> ResourceLibraryItem:
    # 上传资料会被整理成 ResourceLibraryItem：文件信息、目录、关键词和可引用文本都在这里汇总。
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    outline, extracted, text_content = extract_outline(file_path, original_name, mime_type)
    outline = _attach_outline_hierarchy(outline)
    concept_index: dict[str, list[str]] = {}
    for chapter in outline:
        path_keywords = _keywords_from_text(" ".join(chapter.path))
        for keyword in [*chapter.keywords, *path_keywords]:
            concept_index.setdefault(keyword, []).append(chapter.id)
    structure_regions, body_blocks, toc_entries, chapter_shards, parse_warnings = _build_resource_indexes(
        file_path=file_path,
        mime_type=mime_type,
        outline=outline,
        text_content=text_content,
    )
    copyright_probe = build_copyright_evidence_packet(
        file_path,
        original_name,
        mime_type,
        structure_regions=structure_regions,
        text_content=text_content,
    )
    page_count, indexed_block_count, index_message = _resource_index_stats(body_blocks)

    return ResourceLibraryItem(
        name=original_name,
        mime_type=mime_type,
        resource_type="image" if mime_type.startswith("image/") else "document",
        size_bytes=file_path.stat().st_size,
        outline=outline,
        concept_index=concept_index,
        extracted_text_available=extracted,
        text_content=text_content,
        source_path=str(file_path),
        index_status="ready",
        index_message=index_message,
        index_updated_at=now_iso(),
        page_count=page_count,
        indexed_block_count=indexed_block_count,
        structure_regions=structure_regions,
        body_blocks=body_blocks,
        toc_entries=toc_entries,
        chapter_shards=chapter_shards,
        parse_warnings=parse_warnings,
        copyright_probe=copyright_probe,
    )


def reindex_resource_item(resource: ResourceLibraryItem) -> ResourceLibraryItem:
    if not resource.source_path:
        raise ValueError("resource cannot be reindexed without a source_path")
    source_path = Path(resource.source_path)
    if not source_path.exists():
        raise FileNotFoundError(resource.source_path)
    rebuilt = build_resource_item(source_path, resource.name)
    return rebuilt.model_copy(
        update={
            "id": resource.id,
            "uploaded_at": resource.uploaded_at,
            "scope_lesson_id": resource.scope_lesson_id,
            "source_path": resource.source_path,
            "copyright_audit": resource.copyright_audit,
        }
    )


def extract_reference_context(
    resource: ResourceLibraryItem,
    chapter_id: str,
    *,
    user_query: str,
) -> ResourceReferenceContext | None:
    # 选中资料章节后，从原文件或已抽取文本里拿到本轮可以交给 AI 的引用片段。
    chapter = next((candidate for candidate in resource.outline if candidate.id == chapter_id), None)
    if chapter is None:
        return None

    if resource.text_content and resource.resource_type == "image":
        return _build_reference_context(
            resource,
            chapter,
            user_query,
            raw_text=resource.text_content,
        )

    if not resource.source_path:
        return _build_reference_context(
            resource,
            chapter,
            user_query,
            raw_text=resource.text_content or f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}",
        )

    file_path = Path(resource.source_path)
    if not file_path.exists():
        if resource.text_content:
            return _build_reference_context(
                resource,
                chapter,
                user_query,
                raw_text=resource.text_content,
            )
        return None

    suffix = file_path.suffix.lower()
    raw_text = ""
    if resource.mime_type in {"text/plain", "text/markdown"} or suffix in {".md", ".txt"}:
        if chapter.scan_strategy == "heading_section":
            raw_text = _extract_markdown_section_text(file_path, chapter)
        else:
            raw_text = _read_text_file(file_path)
    elif suffix == ".docx":
        if chapter.scan_strategy == "heading_section":
            raw_text = _extract_docx_section_text(file_path, chapter)
        else:
            raw_text = _read_docx_text(file_path)
    elif suffix == ".epub":
        chapter_title, raw_text = _extract_epub_section_text(file_path, chapter, user_query)
        if chapter_title and chapter_title != chapter.title:
            chapter = chapter.model_copy(
                update={
                    "title": chapter_title,
                    "summary": _summary_snippet(raw_text, limit=180)
                    or f"EPUB 章节“{chapter_title}”可作为本次讲解参考。",
                    "keywords": _keywords_from_text(f"{chapter_title}\n{raw_text}"),
                    "locator_hint": chapter_title,
                }
            )
    elif suffix == ".pdf":
        raw_text = _extract_pdf_chapter_text(file_path, chapter, user_query)
    else:
        raw_text = resource.text_content or f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}"

    return _build_reference_context(resource, chapter, user_query, raw_text)
