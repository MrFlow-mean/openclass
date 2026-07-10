from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from app.models import SourceChapter, SourceChunk, SourceIngestionRecord, SourceStructure
from app.services import workspace_state
from app.services.pdf_toc_parser import PdfOutlineAnchor, PdfTocNode, extract_pdf_toc
from app.services.source_structure_store import SourceStructureStore, source_structure_store

CHUNK_CHAR_LIMIT = 1800
CHUNK_CHAR_OVERLAP = 160


@dataclass
class PageText:
    page_no: int
    text: str
    start_offset: int = 0
    end_offset: int = 0


@dataclass
class DetectedChapter:
    title: str
    number: str = ""
    level: int = 1
    source_locator: str = ""
    start_offset: int | None = None
    end_offset: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    confidence: float = 0.0
    verified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedSourceDocument:
    text: str
    chapters: list[DetectedChapter] = field(default_factory=list)
    pages: list[PageText] = field(default_factory=list)
    strategy: str = "linear_text"
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceStructureIndexer:
    def __init__(self, *, store: SourceStructureStore = source_structure_store) -> None:
        self.store = store

    def ensure_structure(self, record: SourceIngestionRecord) -> SourceStructure | None:
        current = self.store.get_structure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        if current and current.status in {"ready", "linear_only", "failed"}:
            return current
        return self.rebuild_structure(record)

    def rebuild_structure(self, record: SourceIngestionRecord) -> SourceStructure:
        building = SourceStructure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="building",
            strategy="linear_text",
            metadata={"source_title": record.title, "mime_type": record.mime_type},
        )
        self.store.save_structure_bundle(structure=building, chapters=[], chunks=[])
        try:
            parsed = self._parse_record(record)
            chapters = self._chapters_for_record(record, parsed)
            chunks = self._chunks_for_record(record, parsed, chapters)
            has_verified_toc = any(chapter.anchor_status == "verified" for chapter in chapters)
            status = "ready" if has_verified_toc else "linear_only"
            confidence = max((chapter.confidence for chapter in chapters), default=0.0)
            structure = building.model_copy(
                update={
                    "status": status,
                    "strategy": parsed.strategy,
                    "confidence": confidence,
                    "warnings": parsed.warnings,
                    "metadata": {
                        **building.metadata,
                        **parsed.metadata,
                        "text_length": len(parsed.text),
                        "chapter_node_count": len(chapters),
                        "verified_chapter_count": sum(
                            chapter.anchor_status == "verified" for chapter in chapters
                        ),
                        "unverified_chapter_count": sum(
                            chapter.anchor_status == "unverified" for chapter in chapters
                        ),
                    },
                }
            )
            return self.store.save_structure_bundle(structure=structure, chapters=chapters, chunks=chunks)
        except Exception as exc:  # pragma: no cover - defensive boundary around third-party parsers
            failed = building.model_copy(
                update={
                    "status": "failed",
                    "error": str(exc),
                    "warnings": ["资料结构索引失败，仍可在 Open Notebook 可用时走全文检索。"],
                }
            )
            return self.store.save_structure_bundle(structure=failed, chapters=[], chunks=[])

    def _parse_record(self, record: SourceIngestionRecord) -> ParsedSourceDocument:
        local_path = _local_source_path(record)
        if not local_path:
            is_url_source = record.source_type == "web_url" or bool(record.source_uri)
            warning = (
                "URL 资料 V1 暂不在 OpenClass 本地重建目录，使用 Open Notebook 检索。"
                if is_url_source
                else "未找到资料原文件，旧上传资料需要重新上传后才能尝试建立目录。"
            )
            return ParsedSourceDocument(
                text="",
                strategy="open_notebook_search_only",
                warnings=[warning],
                metadata={"source_type": record.source_type, "missing_local_source_path": not is_url_source},
            )
        suffix = local_path.suffix.lower()
        if suffix == ".epub" or _looks_like_epub(record.mime_type):
            return _parse_epub(local_path)
        if suffix == ".pdf" or record.mime_type == "application/pdf":
            return _parse_pdf(local_path)
        if suffix in {".docx", ".doc"} or "wordprocessingml" in record.mime_type:
            return _parse_docx(local_path)
        return _parse_text_document(local_path, prefer_markdown=suffix in {".md", ".markdown"})

    def _chapters_for_record(self, record: SourceIngestionRecord, parsed: ParsedSourceDocument) -> list[SourceChapter]:
        chapters: list[SourceChapter] = []
        level_stack: list[SourceChapter] = []
        for index, chapter in enumerate(parsed.chapters):
            is_verified = chapter.verified and chapter.start_offset is not None
            end_offset = chapter.end_offset
            if is_verified and end_offset is None:
                next_chapter = next(
                    (
                        candidate
                        for candidate in parsed.chapters[index + 1 :]
                        if candidate.verified and candidate.start_offset is not None
                    ),
                    None,
                )
                end_offset = next_chapter.start_offset if next_chapter else len(parsed.text)
            excerpt = (
                _compact(parsed.text[chapter.start_offset or 0 : end_offset or len(parsed.text)], 360)
                if is_verified
                else ""
            )
            title = _clean_label(chapter.title)
            number = chapter.number or _number_from_title(title)
            level = max(1, chapter.level)
            while level_stack and level_stack[-1].level >= level:
                level_stack.pop()
            parent = level_stack[-1] if level_stack else None
            source_chapter = SourceChapter(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                parent_id=parent.id if parent else None,
                number=number,
                normalized_number=_normalize_chapter_number(number),
                title=title,
                level=level,
                path=[*(parent.path if parent else []), title],
                order_index=index,
                source_locator=chapter.source_locator,
                body_start_offset=chapter.start_offset if is_verified else None,
                body_end_offset=end_offset if is_verified else None,
                page_start=chapter.page_start if is_verified else None,
                page_end=chapter.page_end if is_verified else None,
                anchor_status="verified" if is_verified else "unverified",
                confidence=chapter.confidence,
                excerpt=excerpt,
                metadata=chapter.metadata,
            )
            chapters.append(source_chapter)
            level_stack.append(source_chapter)
        return chapters

    def _chunks_for_record(
        self,
        record: SourceIngestionRecord,
        parsed: ParsedSourceDocument,
        chapters: list[SourceChapter],
    ) -> list[SourceChunk]:
        if not parsed.text.strip():
            return []
        chapter_ranges = [
            (
                chapter.id,
                chapter.body_start_offset,
                chapter.body_end_offset,
                chapter.page_start,
                chapter.page_end,
                chapter.level,
            )
            for chapter in chapters
            if chapter.anchor_status == "verified"
            and chapter.body_start_offset is not None
            and chapter.body_end_offset is not None
        ]
        chunks: list[SourceChunk] = []
        cursor = 0
        order_index = 0
        text_length = len(parsed.text)
        while cursor < text_length:
            end = min(text_length, cursor + CHUNK_CHAR_LIMIT)
            if end < text_length:
                boundary = parsed.text.rfind("\n\n", cursor + CHUNK_CHAR_LIMIT // 2, end)
                if boundary > cursor:
                    end = boundary
            chunk_text = parsed.text[cursor:end].strip()
            if chunk_text:
                chapter_id, page_start, page_end = _chapter_for_chunk(cursor, end, chapter_ranges)
                chunks.append(
                    SourceChunk(
                        owner_user_id=record.owner_user_id,
                        package_id=record.package_id,
                        source_ingestion_id=record.id,
                        chapter_id=chapter_id,
                        order_index=order_index,
                        source_locator=_locator_for_offset(parsed.pages, cursor),
                        text=chunk_text,
                        start_offset=cursor,
                        end_offset=end,
                        page_start=page_start,
                        page_end=page_end,
                        token_count=_estimate_tokens(chunk_text),
                    )
                )
                order_index += 1
            if end >= text_length:
                break
            cursor = max(end - CHUNK_CHAR_OVERLAP, cursor + 1)
        return chunks


def _local_source_path(record: SourceIngestionRecord) -> Path | None:
    raw_path = record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _find_legacy_upload_path(record)
    path = Path(raw_path).expanduser()
    if path.exists() and path.is_file():
        return path
    return _find_legacy_upload_path(record)


def _find_legacy_upload_path(record: SourceIngestionRecord) -> Path | None:
    upload_dir = workspace_state.UPLOAD_DIR
    if not upload_dir.exists():
        return None
    file_name = Path(record.file_name or record.title).name.strip()
    if not file_name:
        return None
    for candidate in upload_dir.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate.name == file_name or candidate.name.endswith(f"_{file_name}"):
            return candidate
    return None


def _looks_like_epub(mime_type: str) -> bool:
    return mime_type == "application/epub+zip" or "epub" in mime_type.lower()


def _parse_text_document(path: Path, *, prefer_markdown: bool) -> ParsedSourceDocument:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    chapters = _headings_from_markdown(text) if prefer_markdown or _looks_like_markdown(text) else []
    return ParsedSourceDocument(
        text=text,
        chapters=chapters,
        strategy="markdown_heading" if chapters else "linear_text",
        metadata={"parser": "text"},
    )


def _parse_docx(path: Path) -> ParsedSourceDocument:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("python-docx is required to parse DOCX source structure.") from exc
    document = Document(str(path))
    parts: list[str] = []
    chapters: list[DetectedChapter] = []
    offset = 0
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = (paragraph.style.name if paragraph.style is not None else "") or ""
        level = _docx_heading_level(style_name)
        if level:
            number = _number_from_title(text)
            chapters.append(
                DetectedChapter(
                    title=text,
                    number=number,
                    level=level,
                    source_locator=f"docx:paragraph:{len(parts)}",
                    start_offset=offset,
                    confidence=0.86,
                    verified=True,
                    metadata={"source": "docx_heading", "style": style_name},
                )
            )
        parts.append(text)
        offset += len(text) + 2
    full_text = "\n\n".join(parts)
    _close_chapter_ranges(chapters, len(full_text))
    return ParsedSourceDocument(
        text=full_text,
        chapters=chapters,
        strategy="docx_heading" if chapters else "linear_text",
        metadata={"parser": "docx"},
    )


def _parse_pdf(path: Path) -> ParsedSourceDocument:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("pypdf is required to parse PDF source structure.") from exc
    reader = PdfReader(str(path))
    pages: list[PageText] = []
    parts: list[str] = []
    offset = 0
    for page_index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        page_text = f"\n\n[Page {page_index + 1}]\n{text.strip()}"
        start = offset
        parts.append(page_text)
        offset += len(page_text)
        pages.append(PageText(page_no=page_index + 1, text=text, start_offset=start, end_offset=offset))
    full_text = "".join(parts).strip()
    outline_chapters = _pdf_outline_chapters(reader, pages, full_text)
    chapters = outline_chapters
    strategy = "pdf_outline" if outline_chapters else "linear_text"
    warnings: list[str] = []
    toc_metadata: dict[str, Any] = {}

    if outline_chapters and not any(chapter.level > 1 for chapter in outline_chapters):
        extraction = extract_pdf_toc(
            path,
            outline=[
                PdfOutlineAnchor(
                    title=chapter.title,
                    page_no=chapter.page_start,
                    level=chapter.level,
                    metadata=chapter.metadata,
                )
                for chapter in outline_chapters
                if chapter.page_start is not None
            ],
            page_count=len(pages),
        )
        warnings.extend(extraction.warnings)
        if extraction.nodes:
            toc_chapters = _chapters_from_pdf_toc(extraction.nodes, pages)
            chapters = _merge_pdf_navigation(outline_chapters, toc_chapters, extraction.nodes)
            strategy = "pdf_merged_toc"
            toc_metadata = {
                "toc_page_start": extraction.toc_page_start,
                "toc_page_end": extraction.toc_page_end,
                "printed_page_offset": extraction.printed_page_offset,
                "printed_page_mapping_support": extraction.mapping_support,
                "ocr_toc_node_count": len(extraction.nodes),
            }
    if not chapters:
        chapters = _pdf_toc_chapters(pages, full_text)
        strategy = "pdf_toc" if chapters else "linear_text"
    _close_pdf_navigation_ranges(chapters, pages, len(full_text))
    return ParsedSourceDocument(
        text=full_text,
        chapters=chapters,
        pages=pages,
        strategy=strategy,
        warnings=warnings,
        metadata={"parser": "pdf", "page_count": len(pages), **toc_metadata},
    )


def _parse_epub(path: Path) -> ParsedSourceDocument:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        spine_items = _epub_spine_items(archive)
        html_names = spine_items or sorted(
            name for name in names if name.lower().endswith((".xhtml", ".html", ".htm"))
        )
        docs: list[tuple[str, str, list[DetectedChapter]]] = []
        parts: list[str] = []
        offset_by_name: dict[str, int] = {}
        offset = 0
        heading_chapters: list[DetectedChapter] = []
        for name in html_names:
            try:
                raw = archive.read(name)
            except KeyError:
                continue
            text, headings = _html_text_and_headings(raw.decode("utf-8", errors="replace"), source_name=name)
            if not text.strip():
                continue
            prefix = f"\n\n[{name}]\n"
            offset_by_name[name] = offset + len(prefix)
            parts.append(prefix + text)
            for heading in headings:
                heading.start_offset = offset + len(prefix) + (heading.start_offset or 0)
                heading.source_locator = f"epub:{name}"
                heading.verified = True
                heading.confidence = 0.82
                heading.metadata = {"source": "epub_heading", "file": name}
                heading_chapters.append(heading)
            docs.append((name, text, headings))
            offset += len(prefix) + len(text)
        full_text = "".join(parts).strip()
        nav_items = _epub_navigation_items(archive, names)
        nav_chapters = _chapters_from_epub_nav(nav_items, docs, offset_by_name)
        if nav_chapters:
            _close_chapter_ranges(nav_chapters, len(full_text))
            return ParsedSourceDocument(
                text=full_text,
                chapters=nav_chapters,
                strategy="epub_navigation",
                metadata={"parser": "epub", "navigation_items": len(nav_chapters)},
            )
        _close_chapter_ranges(heading_chapters, len(full_text))
        return ParsedSourceDocument(
            text=full_text,
            chapters=heading_chapters,
            strategy="epub_heading" if heading_chapters else "linear_text",
            warnings=[] if heading_chapters else ["EPUB 未发现可验证导航目录或标题结构。"],
            metadata={"parser": "epub"},
        )


def _epub_spine_items(archive: zipfile.ZipFile) -> list[str]:
    try:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except Exception:
        return []
    rootfile = ""
    for element in container.iter():
        if element.tag.endswith("rootfile"):
            rootfile = element.attrib.get("full-path", "")
            break
    if not rootfile:
        return []
    base = str(Path(rootfile).parent)
    if base == ".":
        base = ""
    try:
        opf = ElementTree.fromstring(archive.read(rootfile))
    except Exception:
        return []
    manifest: dict[str, str] = {}
    spine_ids: list[str] = []
    for element in opf.iter():
        tag = element.tag.split("}")[-1]
        if tag == "item":
            item_id = element.attrib.get("id", "")
            href = element.attrib.get("href", "")
            if item_id and href:
                manifest[item_id] = f"{base}/{href}".lstrip("/")
        elif tag == "itemref":
            item_id = element.attrib.get("idref", "")
            if item_id:
                spine_ids.append(item_id)
    return [manifest[item_id] for item_id in spine_ids if item_id in manifest]


def _epub_navigation_items(archive: zipfile.ZipFile, names: list[str]) -> list[tuple[str, str, int]]:
    items: list[tuple[str, str, int]] = []
    nav_names = [name for name in names if re.search(r"(^|/)(nav|toc)\.(xhtml|html|htm)$", name, re.I)]
    for name in nav_names:
        try:
            text = archive.read(name).decode("utf-8", errors="replace")
        except Exception:
            continue
        parser = _NavLinkParser()
        parser.feed(text)
        base = str(Path(name).parent)
        if base == ".":
            base = ""
        for order, (href, label) in enumerate(parser.links):
            if not label.strip() or not href.strip():
                continue
            target = f"{base}/{href.split('#', 1)[0]}".lstrip("/")
            if target:
                items.append((target, _clean_label(label), order))
    if items:
        return _dedupe_nav_items(items)
    for name in [entry for entry in names if entry.lower().endswith(".ncx")]:
        try:
            root = ElementTree.fromstring(archive.read(name))
        except Exception:
            continue
        base = str(Path(name).parent)
        if base == ".":
            base = ""
        for order, point in enumerate(root.iter()):
            if not point.tag.endswith("navPoint"):
                continue
            label = ""
            src = ""
            for child in point.iter():
                if child.tag.endswith("text") and child.text:
                    label = child.text
                if child.tag.endswith("content"):
                    src = child.attrib.get("src", "")
            if label and src:
                target = f"{base}/{src.split('#', 1)[0]}".lstrip("/")
                items.append((target, _clean_label(label), order))
    return _dedupe_nav_items(items)


def _chapters_from_epub_nav(
    nav_items: list[tuple[str, str, int]],
    docs: list[tuple[str, str, list[DetectedChapter]]],
    offset_by_name: dict[str, int],
) -> list[DetectedChapter]:
    docs_by_name = {name: text for name, text, _headings in docs}
    chapters: list[DetectedChapter] = []
    for target, label, order in nav_items:
        if target not in docs_by_name:
            continue
        base_offset = offset_by_name.get(target)
        if base_offset is None:
            continue
        text = docs_by_name[target]
        local_index = _find_title_offset(text, label)
        start_offset = base_offset + max(local_index, 0)
        number = _number_from_title(label)
        chapters.append(
            DetectedChapter(
                title=label,
                number=number,
                level=max(1, len(number.split("."))) if number else 1,
                source_locator=f"epub:{target}",
                start_offset=start_offset,
                confidence=0.95 if local_index >= 0 else 0.78,
                verified=True,
                metadata={"source": "epub_navigation", "file": target, "nav_order": order},
            )
        )
    return chapters


class _TextHeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.headings: list[DetectedChapter] = []
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._heading_start = 0

    @property
    def text_offset(self) -> int:
        return sum(len(part) for part in self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "div", "section", "article", "br", "li", "tr"}:
            self.parts.append("\n")
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n")
            self._heading_tag = tag
            self._heading_text = []
            self._heading_start = self.text_offset

    def handle_endtag(self, tag: str) -> None:
        if tag == self._heading_tag:
            text = _clean_label(" ".join(self._heading_text))
            if text:
                level = int(tag[1])
                self.headings.append(
                    DetectedChapter(
                        title=text,
                        number=_number_from_title(text),
                        level=level,
                        start_offset=self._heading_start,
                    )
                )
            self._heading_tag = None
            self._heading_text = []
            self.parts.append("\n")
        elif tag in {"p", "div", "section", "article", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = html.unescape(data)
        if not text.strip():
            return
        if self._heading_tag:
            self._heading_text.append(text.strip())
        self.parts.append(text.strip() + " ")


class _NavLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = {key: value or "" for key, value in attrs}
        self._href = attrs_dict.get("href", "")
        self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text)))
        if tag == "a":
            self._href = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data.strip())


def _html_text_and_headings(content: str, *, source_name: str) -> tuple[str, list[DetectedChapter]]:
    parser = _TextHeadingParser()
    parser.feed(content)
    text = _normalize_text("".join(parser.parts))
    for heading in parser.headings:
        heading.source_locator = f"html:{source_name}"
    return text, parser.headings


def _headings_from_markdown(text: str) -> list[DetectedChapter]:
    chapters: list[DetectedChapter] = []
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, flags=re.M):
        title = _clean_label(match.group(2))
        if not title:
            continue
        chapters.append(
            DetectedChapter(
                title=title,
                number=_number_from_title(title),
                level=len(match.group(1)),
                source_locator=f"markdown:line:{text[: match.start()].count(chr(10)) + 1}",
                start_offset=match.start(),
                confidence=0.88,
                verified=True,
                metadata={"source": "markdown_heading"},
            )
        )
    _close_chapter_ranges(chapters, len(text))
    return chapters


def _pdf_outline_chapters(reader: Any, pages: list[PageText], full_text: str) -> list[DetectedChapter]:
    try:
        outline = reader.outline
    except Exception:
        return []
    flattened: list[tuple[Any, int]] = []

    def visit(items: Any, level: int = 1) -> None:
        if not isinstance(items, list):
            flattened.append((items, level))
            return
        for item in items:
            if isinstance(item, list):
                visit(item, level + 1)
            else:
                flattened.append((item, level))

    visit(outline)
    chapters: list[DetectedChapter] = []
    for item, level in flattened:
        title = _clean_label(getattr(item, "title", "") or str(item))
        if not title:
            continue
        try:
            page_index = reader.get_destination_page_number(item)
        except Exception:
            page_index = None
        page = pages[page_index] if isinstance(page_index, int) and 0 <= page_index < len(pages) else None
        start_offset = page.start_offset if page else _find_title_offset(full_text, title)
        if start_offset is None or start_offset < 0:
            continue
        chapters.append(
            DetectedChapter(
                title=title,
                number=_number_from_title(title),
                level=level,
                source_locator=f"pdf:outline:{page.page_no if page else ''}",
                start_offset=start_offset,
                page_start=page.page_no if page else None,
                confidence=0.93,
                verified=True,
                metadata={"source": "pdf_outline"},
            )
        )
    return chapters


def _chapters_from_pdf_toc(nodes: list[PdfTocNode], pages: list[PageText]) -> list[DetectedChapter]:
    chapters: list[DetectedChapter] = []
    for node in nodes:
        page = (
            pages[node.physical_page - 1]
            if node.physical_page is not None and 1 <= node.physical_page <= len(pages)
            else None
        )
        verified = node.verified and page is not None
        chapters.append(
            DetectedChapter(
                title=node.title,
                number=node.number,
                level=node.level,
                source_locator=f"pdf:toc-page:{node.toc_page}:printed:{node.printed_page}",
                start_offset=page.start_offset if verified and page else None,
                page_start=page.page_no if verified and page else None,
                confidence=node.confidence,
                verified=verified,
                metadata=node.metadata,
            )
        )
    return chapters


def _merge_pdf_navigation(
    outline: list[DetectedChapter],
    toc_chapters: list[DetectedChapter],
    toc_nodes: list[PdfTocNode],
) -> list[DetectedChapter]:
    matched_outline_pairs = {
        (str(node.metadata.get("outline_title") or ""), int(node.metadata.get("outline_page") or 0))
        for node in toc_nodes
        if node.metadata.get("outline_title") and node.metadata.get("outline_page")
    }
    matched_root_pages = {
        chapter.page_start
        for chapter in toc_chapters
        if chapter.level == 1 and chapter.page_start is not None
    }
    unmatched_outline = [
        chapter
        for chapter in outline
        if (chapter.title, chapter.page_start or 0) not in matched_outline_pairs
        and not (chapter.level == 1 and chapter.page_start in matched_root_pages)
    ]
    first_body_page = min(
        (chapter.page_start for chapter in toc_chapters if chapter.page_start is not None),
        default=max((node.toc_page for node in toc_nodes), default=0) + 1,
    )
    prefix = [chapter for chapter in unmatched_outline if (chapter.page_start or 0) < first_body_page]
    suffix = [chapter for chapter in unmatched_outline if (chapter.page_start or 0) >= first_body_page]
    return prefix + toc_chapters + suffix


def _close_pdf_navigation_ranges(
    chapters: list[DetectedChapter],
    pages: list[PageText],
    text_length: int,
) -> None:
    for index, chapter in enumerate(chapters):
        if not chapter.verified or chapter.start_offset is None or chapter.page_start is None:
            continue
        boundary = next(
            (
                candidate
                for candidate in chapters[index + 1 :]
                if candidate.verified
                and candidate.start_offset is not None
                and candidate.page_start is not None
                and candidate.level <= chapter.level
            ),
            None,
        )
        chapter.end_offset = boundary.start_offset if boundary else text_length
        if boundary and boundary.page_start is not None:
            chapter.page_end = max(chapter.page_start + 1, boundary.page_start)
        else:
            chapter.page_end = pages[-1].page_no + 1 if pages else chapter.page_start + 1


def _pdf_toc_chapters(pages: list[PageText], full_text: str) -> list[DetectedChapter]:
    toc_pages = [page for page in pages[:30] if _looks_like_toc_page(page.text)]
    if not toc_pages:
        return []
    body_pages = [page for page in pages if page.page_no > max(toc.page_no for toc in toc_pages)]
    chapters: list[DetectedChapter] = []
    seen: set[tuple[str, int]] = set()
    has_root = False
    for toc_page in toc_pages[:6]:
        for line in toc_page.text.splitlines():
            parsed = _parse_toc_line(line)
            if not parsed:
                continue
            title, printed_page = parsed
            key = (_normalize_for_match(title), printed_page)
            if key in seen:
                continue
            seen.add(key)
            title_offset = -1
            matched_page: PageText | None = None
            for page in body_pages:
                local_offset = _find_title_offset(page.text, title)
                if local_offset >= 0:
                    title_offset = page.start_offset + local_offset
                    matched_page = page
                    break
            number = _number_from_title(title)
            level = max(1, len(number.split("."))) if number else (2 if has_root else 1)
            if level == 1:
                has_root = True
            chapters.append(
                DetectedChapter(
                    title=title,
                    number=number,
                    level=level,
                    source_locator=f"pdf:toc-page:{toc_page.page_no}:printed:{printed_page}",
                    start_offset=title_offset if title_offset >= 0 else None,
                    page_start=matched_page.page_no if matched_page else None,
                    confidence=0.82 if title_offset >= 0 else 0.62,
                    verified=title_offset >= 0,
                    metadata={
                        "source": "pdf_toc",
                        "printed_page": printed_page,
                        "verification": "body_title_match" if title_offset >= 0 else "toc_candidate",
                    },
                )
            )
    return chapters


def _parse_toc_line(line: str) -> tuple[str, int] | None:
    cleaned = _clean_label(line)
    if len(cleaned) < 6 or len(cleaned) > 180:
        return None
    match = re.match(r"^(.+?)(?:\.{2,}\s*|\s{2,})(\d{1,4})$", cleaned)
    if not match:
        return None
    title = _clean_label(match.group(1))
    if not title or re.fullmatch(r"\d+(?:\.\d+)*", title):
        return None
    return title, int(match.group(2))


def _looks_like_toc_page(text: str) -> bool:
    lowered = text.lower()
    toc_heading = "contents" in lowered or "table of contents" in lowered or "目录" in text
    dotted_lines = sum(1 for line in text.splitlines() if _parse_toc_line(line))
    return toc_heading or dotted_lines >= 3


def _docx_heading_level(style_name: str) -> int | None:
    match = re.search(r"(?:heading|标题)\s*(\d+)", style_name, flags=re.I)
    if not match:
        return None
    return max(1, min(6, int(match.group(1))))


def _looks_like_markdown(text: str) -> bool:
    return bool(re.search(r"^#{1,6}\s+\S", text, flags=re.M))


def _close_chapter_ranges(chapters: list[DetectedChapter], text_length: int) -> None:
    chapters.sort(key=lambda chapter: chapter.start_offset if chapter.start_offset is not None else text_length)
    for index, chapter in enumerate(chapters):
        if chapter.start_offset is None:
            continue
        next_chapter = chapters[index + 1] if index + 1 < len(chapters) else None
        if chapter.end_offset is None:
            chapter.end_offset = next_chapter.start_offset if next_chapter else text_length
        if chapter.page_start is not None and chapter.page_end is None:
            chapter.page_end = next_chapter.page_start if next_chapter and next_chapter.page_start else chapter.page_start


def _chapter_for_chunk(
    start: int,
    end: int,
    chapter_ranges: list[tuple[str, int, int, int | None, int | None, int]],
) -> tuple[str | None, int | None, int | None]:
    best: tuple[str | None, int | None, int | None] = (None, None, None)
    best_overlap = 0
    best_level = 0
    for chapter_id, chapter_start, chapter_end, page_start, page_end, level in chapter_ranges:
        overlap = max(0, min(end, chapter_end) - max(start, chapter_start))
        if overlap > best_overlap or (overlap == best_overlap and overlap > 0 and level > best_level):
            best_overlap = overlap
            best_level = level
            best = (chapter_id, page_start, page_end)
    return best


def _locator_for_offset(pages: list[PageText], offset: int) -> str:
    for page in pages:
        if page.start_offset <= offset <= page.end_offset:
            return f"page:{page.page_no}"
    return ""


def _find_title_offset(text: str, title: str) -> int:
    if not text or not title:
        return -1
    normalized_title = _normalize_for_match(title)
    if not normalized_title:
        return -1
    normalized_text = _normalize_for_match(text)
    index = normalized_text.find(normalized_title)
    if index < 0:
        return -1
    return min(index, len(text) - 1)


def _number_from_title(title: str) -> str:
    cleaned = _clean_label(title)
    patterns = [
        r"^(\d+(?:\.\d+){0,8})(?=\s|[.:：、-]|$)",
        r"^chapter\s+(\d+(?:\.\d+){0,8})(?=\s|[.:：、-]|$)",
        r"^第\s*(\d+)\s*[章节]",
    ]
    lowered = cleaned.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.I)
        if match:
            return match.group(1)
    return ""


def _normalize_chapter_number(number: str) -> str:
    parts = [part for part in re.split(r"\.+", number.strip()) if part != ""]
    normalized: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized.append(str(int(part)))
        else:
            normalized.append(part.lower())
    return ".".join(normalized)


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _normalize_text(value: str) -> str:
    lines = [line.strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_label(value)).lower()


def _compact(text: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ascii_chars = sum(1 for char in stripped if ord(char) < 128)
    non_ascii_chars = len(stripped) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def _dedupe_nav_items(items: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str, int]] = []
    for target, label, order in items:
        key = (target, label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((target, label, order))
    return deduped


source_structure_indexer = SourceStructureIndexer()
