from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.models import LibraryChapter, ResourceLibraryItem, ResourceSourceUnit
from app.services.source_ingestion import apply_ingestion_state

_MAX_WEB_BYTES = 2_000_000
_USER_AGENT = "OpenClass Source Hub/1.0"
_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "nav",
    "header",
    "footer",
    "form",
    "button",
    "select",
    "template",
}
_BLOCK_TAGS = {"p", "li", "blockquote", "dd", "dt", "pre"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

HtmlFetcher = Callable[[str], str]


class WebResourceError(ValueError):
    pass


@dataclass(frozen=True)
class _Paragraph:
    text: str
    heading_path: list[str]


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.paragraphs: list[_Paragraph] = []
        self._skip_depth = 0
        self._title_parts: list[str] = []
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._heading_level = 0
        self._heading_stack: list[str] = []
        self._block_tag: str | None = None
        self._block_parts: list[str] = []
        self._block_heading_path: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_parts = []
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
            self._heading_level = int(tag[1])
            self._heading_parts = []
            return
        if tag in _BLOCK_TAGS and self._block_tag is None:
            self._block_tag = tag
            self._block_parts = []
            self._block_heading_path = list(self._heading_stack)
            return
        if tag == "br":
            self._append_text(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self.title = _normalize_space(" ".join(self._title_parts)) or self.title
            self._title_parts = []
            return
        if tag == self._heading_tag:
            heading = _normalize_space(" ".join(self._heading_parts))
            if heading:
                self._heading_stack = self._heading_stack[: max(self._heading_level - 1, 0)]
                self._heading_stack.append(heading)
            self._heading_tag = None
            self._heading_parts = []
            self._heading_level = 0
            return
        if tag == self._block_tag:
            text = _normalize_space(" ".join(self._block_parts))
            if text:
                self.paragraphs.append(_Paragraph(text=text, heading_path=list(self._block_heading_path)))
            self._block_tag = None
            self._block_parts = []
            self._block_heading_path = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        if not data:
            return
        if self._heading_tag is not None:
            self._heading_parts.append(data)
            return
        if self._block_tag is not None:
            self._block_parts.append(data)
            return
        if self._title_parts is not None:
            self._title_parts.append(data)


def build_web_resource_item(url: str, *, title: str | None = None, fetcher: HtmlFetcher | None = None) -> ResourceLibraryItem:
    normalized_url = _validate_url(url)
    html = (fetcher or fetch_web_html)(normalized_url)
    return build_web_resource_item_from_html(normalized_url, html, title=title)


def build_web_resource_item_from_html(url: str, html: str, *, title: str | None = None) -> ResourceLibraryItem:
    normalized_url = _validate_url(url)
    parser = _ReadableHtmlParser()
    parser.feed(html)
    parser.close()

    source_title = _normalize_space(title or parser.title) or normalized_url
    paragraphs = _dedupe_paragraphs(parser.paragraphs)
    if not paragraphs:
        raise WebResourceError("No readable paragraph content was found at this URL.")

    source_units = [
        ResourceSourceUnit(
            content_type="text",
            text=paragraph.text,
            url=normalized_url,
            heading_path=paragraph.heading_path,
            paragraph_index=index,
            source_locator=f"{normalized_url}#p={index + 1}",
            order_index=index,
            metadata={
                "source_type": "web_url",
                "url": normalized_url,
                "heading_path": paragraph.heading_path,
                "paragraph_index": index,
                "source_title": source_title,
            },
        )
        for index, paragraph in enumerate(paragraphs)
    ]
    chapters = _chapters_from_paragraphs(source_title, paragraphs)
    resource = ResourceLibraryItem(
        name=source_title,
        mime_type="text/html",
        resource_type="webpage",
        size_bytes=len(html.encode("utf-8")),
        outline=chapters,
        concept_index=_concept_index(chapters, paragraphs),
        extracted_text_available=True,
        text_content="\n\n".join(paragraph.text for paragraph in paragraphs),
        source_type="web_url",
        source_uri=normalized_url,
        parser_provider="web_url",
        parser_message=f"网页正文已解析为 {len(source_units)} 个段落证据单元。",
        source_units=source_units,
    )
    return apply_ingestion_state(
        resource,
        source_type="web_url",
        source_uri=normalized_url,
        adapter="web_url",
        status="ready",
        progress=100,
        phase_history=["queued", "fetching", "parsing", "indexing", "ready"],
    )


def build_failed_web_resource_item(url: str, *, title: str | None = None, error: str) -> ResourceLibraryItem:
    source_name = _normalize_space(title or url) or "网页资料"
    normalized_url = _normalize_url_or_original(url)
    resource = ResourceLibraryItem(
        name=source_name,
        mime_type="text/html",
        resource_type="webpage",
        size_bytes=0,
        extracted_text_available=False,
        source_type="web_url",
        source_uri=normalized_url,
        parser_provider="web_url",
        parser_message="网页资料解析失败。",
        parse_warnings=[error],
    )
    return apply_ingestion_state(
        resource,
        source_type="web_url",
        source_uri=normalized_url,
        adapter="web_url",
        status="failed",
        progress=100,
        error=error,
        phase_history=["queued", "fetching", "failed"],
    )


def fetch_web_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=12) as response:
            payload = response.read(_MAX_WEB_BYTES + 1)
            if len(payload) > _MAX_WEB_BYTES:
                raise WebResourceError("Webpage is too large to ingest in this version.")
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise WebResourceError(f"Unsupported URL content type: {content_type}.")
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise WebResourceError(f"URL fetch failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise WebResourceError(f"URL fetch failed: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise WebResourceError("URL fetch timed out.") from exc
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _validate_url(url: str) -> str:
    normalized = _normalize_space(url)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebResourceError("Only http and https URLs can be added as web resources.")
    return normalized


def _normalize_url_or_original(url: str) -> str:
    try:
        return _validate_url(url)
    except WebResourceError:
        return _normalize_space(url)


def _dedupe_paragraphs(paragraphs: list[_Paragraph]) -> list[_Paragraph]:
    seen: set[str] = set()
    deduped: list[_Paragraph] = []
    for paragraph in paragraphs:
        key = paragraph.text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(paragraph)
    return deduped


def _chapters_from_paragraphs(source_title: str, paragraphs: list[_Paragraph]) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    chapter_by_title: dict[str, LibraryChapter] = {}
    for paragraph in paragraphs:
        if not paragraph.heading_path:
            continue
        heading = paragraph.heading_path[-1]
        chapter = chapter_by_title.get(heading)
        if chapter is None:
            chapter = LibraryChapter(
                title=heading,
                level=len(paragraph.heading_path),
                summary=_truncate(paragraph.text, 240),
                path=paragraph.heading_path,
                locator_hint=f"heading:{' > '.join(paragraph.heading_path)}",
                order_index=len(chapters),
                scan_strategy="heading_section",
            )
            chapter_by_title[heading] = chapter
            chapters.append(chapter)
    if chapters:
        return chapters
    return [
        LibraryChapter(
            title=source_title,
            level=1,
            summary=_truncate(paragraphs[0].text, 240),
            locator_hint="webpage:body",
            order_index=0,
            scan_strategy="fulltext_match",
        )
    ]


def _concept_index(chapters: list[LibraryChapter], paragraphs: list[_Paragraph]) -> dict[str, list[str]]:
    if not chapters:
        return {}
    default_chapter_id = chapters[0].id
    chapter_by_heading = {chapter.title: chapter.id for chapter in chapters}
    index: dict[str, list[str]] = {}
    for paragraph in paragraphs:
        chapter_id = chapter_by_heading.get(paragraph.heading_path[-1], default_chapter_id) if paragraph.heading_path else default_chapter_id
        for token in _tokens(paragraph.text)[:24]:
            bucket = index.setdefault(token, [])
            if chapter_id not in bucket:
                bucket.append(chapter_id)
    return index


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        if len(token) == 1 and not ("\u4e00" <= token <= "\u9fff"):
            continue
        tokens.append(token)
    return tokens


def _normalize_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int) -> str:
    cleaned = _normalize_space(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
