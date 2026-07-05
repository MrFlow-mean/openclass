from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


_EPUB_NUMBER_PATTERN = r"(?:\d+|[一二三四五六七八九十百〇零两]+)"
_PARSER_ARTIFACT_TITLE_PATTERN = re.compile(
    r"^(?:text|image|img|table|equation|formula|figure|page)\d{3,}$",
    re.IGNORECASE,
)
_TOC_MARKER_PATTERN = re.compile(
    rf"第\s*{_EPUB_NUMBER_PATTERN}\s*章|\d{{1,2}}\s*[.．,，:：]\s*\d{{1,2}}(?:\s*[.．,，:：]\s*\d{{1,2}})?"
)


@dataclass(frozen=True)
class EpubTocEntry:
    title: str
    level: int
    source: str = ""
    page_start: int | None = None


def extract_epub_toc_entries(file_path: Path) -> list[EpubTocEntry]:
    native_entries: list[EpubTocEntry] = []
    page_entries: list[EpubTocEntry] = []
    try:
        with zipfile.ZipFile(file_path) as archive:
            for path in _toc_paths(archive):
                try:
                    raw_text = _decode_bytes(archive.read(path))
                except KeyError:
                    continue
                raw_entries = _ncx_entries(raw_text) if path.lower().endswith(".ncx") else _nav_entries(raw_text)
                entries = _clean_entries(raw_entries)
                if entries:
                    native_entries = entries
                    break
            page_entries = _document_toc_entries(archive)
    except (zipfile.BadZipFile, OSError):
        return []
    return _prefer_richer_toc(native_entries, page_entries)


def _prefer_richer_toc(native_entries: list[EpubTocEntry], page_entries: list[EpubTocEntry]) -> list[EpubTocEntry]:
    if not page_entries:
        return native_entries
    if not native_entries:
        return page_entries
    native_numbered = sum(1 for entry in native_entries if _entry_number(entry.title))
    page_numbered = sum(1 for entry in page_entries if _entry_number(entry.title))
    if len(page_entries) >= len(native_entries) + 3 or page_numbered >= native_numbered + 3:
        return page_entries
    return native_entries


def _clean_entries(raw_entries: list[dict[str, object]]) -> list[EpubTocEntry]:
    entries: list[EpubTocEntry] = []
    seen_labels: set[str] = set()
    seen_chapter = False
    for raw_entry in raw_entries:
        title, page_start = _clean_label(str(raw_entry.get("label") or ""))
        if not title or _is_separator_title(title) or _is_parser_artifact_title(title):
            continue
        key = title.lower()
        if key in seen_labels:
            continue
        level = _entry_level(title, int(raw_entry.get("level") or 1), seen_chapter=seen_chapter)
        seen_chapter = seen_chapter or level == 1
        seen_labels.add(key)
        entries.append(
            EpubTocEntry(
                title=title,
                level=level,
                source=str(raw_entry.get("source") or ""),
                page_start=page_start,
            )
        )
    return entries


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _rootfile_path(archive: zipfile.ZipFile) -> str | None:
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


def _opf_context(archive: zipfile.ZipFile) -> tuple[ET.Element, dict[str, dict[str, str]]] | None:
    rootfile_path = _rootfile_path(archive)
    if not rootfile_path:
        return None
    try:
        opf_root = ET.fromstring(archive.read(rootfile_path))
    except (KeyError, ET.ParseError):
        return None

    base_dir = posixpath.dirname(rootfile_path)
    manifest: dict[str, dict[str, str]] = {}
    for item in opf_root.findall(".//{*}manifest/{*}item"):
        item_id = item.attrib.get("id", "").strip()
        href = item.attrib.get("href", "").strip()
        if not item_id or not href:
            continue
        manifest[item_id] = {
            "path": posixpath.normpath(posixpath.join(base_dir, href)),
            "media_type": item.attrib.get("media-type", "").strip(),
            "properties": item.attrib.get("properties", "").strip(),
        }
    return opf_root, manifest


def _reading_order_paths(archive: zipfile.ZipFile) -> list[str]:
    context = _opf_context(archive)
    if context is None:
        return [
            path
            for path in archive.namelist()
            if _is_html_path(path) and not re.search(r"(?:^|/)(?:nav|toc|cover)\.", path, flags=re.IGNORECASE)
        ]
    opf_root, manifest = context
    ordered: list[str] = []
    for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
        item_id = itemref.attrib.get("idref", "").strip()
        item = manifest.get(item_id, {})
        path = item.get("path", "")
        media_type = item.get("media_type", "")
        properties = item.get("properties", "")
        if not path or "nav" in properties or "toc" in properties:
            continue
        if media_type in {"application/xhtml+xml", "text/html"} or _is_html_path(path):
            ordered.append(path)
    return [path for path in ordered if path in archive.namelist()]


def _toc_paths(archive: zipfile.ZipFile) -> list[str]:
    context = _opf_context(archive)
    if context is None:
        return []
    opf_root, manifest = context
    paths: list[str] = []

    spine = opf_root.find(".//{*}spine")
    spine_toc_id = spine.attrib.get("toc", "").strip() if spine is not None else ""
    if spine_toc_id and spine_toc_id in manifest:
        paths.append(manifest[spine_toc_id]["path"])

    for item in manifest.values():
        path = item.get("path", "")
        media_type = item.get("media_type", "")
        properties = item.get("properties", "")
        filename = posixpath.basename(path).lower()
        if media_type == "application/x-dtbncx+xml" or "nav" in properties or filename in {"toc.ncx", "nav.xhtml", "nav.html"}:
            paths.append(path)

    unique_paths = list(dict.fromkeys(path for path in paths if path in archive.namelist()))
    if unique_paths:
        return unique_paths
    return [
        path
        for path in archive.namelist()
        if re.search(r"(?:^|/)(?:toc\.ncx|nav\.(?:xhtml|html|htm))$", path, flags=re.IGNORECASE)
    ]


def _document_toc_entries(archive: zipfile.ZipFile) -> list[EpubTocEntry]:
    toc_pages: list[str] = []
    seen_toc = False
    for path in _reading_order_paths(archive)[:80]:
        try:
            text = _html_text(_decode_bytes(archive.read(path)))
        except KeyError:
            continue
        marker_count = len(_TOC_MARKER_PATTERN.findall(_normalize_toc_text(text)))
        toc_shape_count = _toc_line_shape_count(text)
        compact_prefix = re.sub(r"\s+", "", text[:160]).lower()
        starts_toc = "目录" in compact_prefix or "contents" in compact_prefix
        is_toc_page = starts_toc or toc_shape_count >= 4 or (seen_toc and toc_shape_count >= 2 and marker_count >= 2)
        if is_toc_page:
            toc_pages.append(text)
            seen_toc = True
            continue
        if seen_toc and marker_count < 2:
            break
    if not toc_pages:
        return []
    return _parse_document_toc_text("\n".join(toc_pages))


def _toc_line_shape_count(text: str) -> int:
    count = 0
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if re.search(r"[.．·•…]{2,}.*\d{1,4}\s*$", line):
            count += 1
        elif re.match(
            rf"^(?:第\s*{_EPUB_NUMBER_PATTERN}\s*章|\d{{1,2}}(?:[.．]\d{{1,2}}){{1,3}})\b.+\s+\d{{1,4}}$",
            line,
        ):
            count += 1
    return count


def _html_text(raw_html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|head|nav)\b.*?</\1>", "\n", raw_html)
    cleaned = re.sub(r"(?is)<br\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</?(h[1-6]|p|div|section|article|li|tr|td|th|blockquote)\b[^>]*>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", "", cleaned)
    text = html.unescape(cleaned)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _normalize_toc_text(text: str) -> str:
    normalized = html.unescape(text).replace("\xa0", " ")
    normalized = re.sub(r"[\u2000-\u200b]+", " ", normalized)
    normalized = normalized.replace("．", ".").replace("，", ",").replace("：", ":")
    normalized = re.sub(r"(?<=\d)\s*[.](?=\d[,:.]\d)", " ", normalized)
    normalized = re.sub(r"(?<=[A-Za-z\u4e00-\u9fff）)])([0-9]{1,3})[.](?=[0-9][,:][0-9])", r"\1 ", normalized)
    normalized = re.sub(r"(?<=[A-Za-z\u4e00-\u9fff）)])([0-9]{1,3})(?=[0-9][.,:][0-9])", r"\1 ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized


def _parse_document_toc_text(text: str) -> list[EpubTocEntry]:
    normalized = _normalize_toc_text(text)
    matches = list(_TOC_MARKER_PATTERN.finditer(normalized))
    entries: list[EpubTocEntry] = []
    seen_titles: set[str] = set()
    current_chapter_number: str | None = None
    current_chapter_page_start: int | None = None
    for index, match in enumerate(matches):
        marker = _normalize_document_marker(
            match.group(0),
            normalized[max(0, match.start() - 12) : match.start()],
            current_chapter_number,
        )
        if not marker:
            continue
        chapter_number = _chapter_number_from_marker(marker)
        if chapter_number is not None:
            current_chapter_number = str(chapter_number)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        tail = normalized[match.end() : end]
        chunks = _toc_tail_chunks(tail)
        if not chunks:
            continue
        main_title, page_start = chunks[0]
        if chapter_number is not None:
            current_chapter_page_start = page_start
        elif _page_before_current_chapter(page_start, current_chapter_page_start):
            continue
        full_title = _compose_toc_title(marker, main_title)
        if _add_document_entry(entries, seen_titles, full_title, page_start):
            parent_number = _entry_number(full_title)
            if parent_number is not None and "." in parent_number and len(parent_number.split(".")) <= 2:
                for child_index, (child_title, child_page) in enumerate(chunks[1:4], start=1):
                    if _page_before_current_chapter(child_page, current_chapter_page_start):
                        continue
                    if not _looks_like_child_toc_title(child_title):
                        continue
                    child_number = f"{parent_number}.{child_index}"
                    _add_document_entry(entries, seen_titles, _compose_toc_title(child_number, child_title), child_page)
    return entries


def _page_before_current_chapter(page_start: int | None, current_chapter_page_start: int | None) -> bool:
    return page_start is not None and current_chapter_page_start is not None and page_start < current_chapter_page_start


def _normalize_document_marker(marker: str, before_marker: str, current_chapter_number: str | None) -> str:
    normalized = _normalize_marker(marker)
    if not normalized or normalized.startswith("第 ") or current_chapter_number is None:
        return normalized
    prefix_match = re.search(r"(?<![.\d])(\d{1,2})\s*$", before_marker)
    if prefix_match and prefix_match.group(1) == current_chapter_number and not normalized.startswith(f"{current_chapter_number}."):
        return f"{current_chapter_number}.{normalized}"
    return normalized


def _normalize_marker(marker: str) -> str:
    cleaned = re.sub(r"\s+", "", marker).replace("．", ".").replace(",", ".").replace(":", ".")
    chapter = re.match(rf"第({_EPUB_NUMBER_PATTERN})章", cleaned)
    if chapter:
        return f"第 {chapter.group(1)} 章"
    dotted = re.match(r"^(\d+(?:\.\d+){1,2})$", cleaned)
    return dotted.group(1) if dotted else ""


def _chapter_number_from_marker(marker: str) -> int | None:
    chapter = re.match(rf"^第\s*({_EPUB_NUMBER_PATTERN})\s*章$", marker)
    if not chapter:
        return None
    return _parse_chapter_number(chapter.group(1))


def _toc_tail_chunks(tail: str) -> list[tuple[str, int | None]]:
    compact_tail = re.sub(r"\s+", " ", tail).strip()
    if not compact_tail:
        return []
    chunks: list[tuple[str, int | None]] = []
    cursor = 0
    chunk_pattern = re.compile(
        r"(?P<title>.{2,90}?)(?:[.·•…\s]*)(?P<page>\d{1,4})(?=(?:\s|[A-Za-z\u4e00-\u9fff]|$))"
    )
    for match in chunk_pattern.finditer(compact_tail):
        raw_title = compact_tail[cursor : match.start()] + match.group("title")
        title = _clean_chunk_title(raw_title)
        if title:
            chunks.append((title, _page_number(match.group("page"))))
        cursor = match.end()
    remainder = _clean_chunk_title(compact_tail[cursor:])
    if remainder:
        chunks.append((remainder, None))
    if chunks:
        return chunks
    fallback, page_start = _clean_label(compact_tail)
    return [(fallback, page_start)] if fallback else []


def _clean_chunk_title(title: str) -> str:
    cleaned = re.sub(r"[•·.。…]+", " ", title)
    cleaned = re.sub(r"[_^~\"“”'‘’]+", "", cleaned)
    cleaned = cleaned.strip(" \t\n\r-—,，;；:：/|")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if _is_separator_title(cleaned) or _is_parser_artifact_title(cleaned):
        return ""
    if len(re.sub(r"\s+", "", cleaned)) < 2:
        return ""
    return cleaned[:120]


def _page_number(raw: str) -> int | None:
    digits = re.sub(r"\D+", "", raw)
    return int(digits) if digits else None


def _compose_toc_title(marker: str, title: str) -> str:
    if marker.startswith("第 "):
        return re.sub(r"\s+", " ", f"{marker} {title}").strip()
    if title.startswith(marker):
        return title
    return re.sub(r"\s+", " ", f"{marker} {title}").strip()


def _add_document_entry(
    entries: list[EpubTocEntry],
    seen_titles: set[str],
    title: str,
    page_start: int | None,
) -> bool:
    title = re.sub(r"\s+", " ", title).strip()
    if not title or _is_separator_title(title):
        return False
    key = title.lower()
    if key in seen_titles:
        return False
    seen_titles.add(key)
    number = _entry_number(title)
    level = _entry_level(title, 1, seen_chapter=any(entry.level == 1 for entry in entries))
    entries.append(EpubTocEntry(title=title, level=level, source="document_toc", page_start=page_start))
    return bool(number)


def _entry_number(title: str) -> str | None:
    chapter = re.match(rf"^第\s*({_EPUB_NUMBER_PATTERN})\s*章", title)
    if chapter:
        return str(_parse_chapter_number(chapter.group(1)) or chapter.group(1))
    dotted = re.match(r"^(\d+(?:[.．]\d+){1,3})", title)
    return dotted.group(1).replace("．", ".") if dotted else None


def _parse_chapter_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100}
    total = 0
    current = 0
    seen = False
    for char in value:
        if char in digits:
            current = digits[char]
            seen = True
            continue
        unit = units.get(char)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
        seen = True
    return total + current if seen else None


def _looks_like_child_toc_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title)
    if not 2 <= len(compact) <= 45:
        return False
    if _entry_number(title) is not None:
        return False
    return not re.search(r"[{}=%$#]|\\b(?:int|void|return|malloc)\\b", title)


def _is_html_path(path: str) -> bool:
    return path.lower().endswith((".xhtml", ".html", ".htm"))


def _clean_label(label: str) -> tuple[str, int | None]:
    raw = html.unescape(label).replace("\xa0", " ")
    raw = re.sub(r"[\u2000-\u200b]+", " ", raw).strip()
    page_start: int | None = None
    page_match = re.search(
        r"(?:\s{2,}|[.．·•…]{2,}\s*)[.．·•…!！]*\s*(?P<page>[0-9ivxlcdmIVXLCDM一二三四五六七八九十百〇零两】JIil|]{1,8})\.?\s*$",
        raw,
    )
    if page_match and len(raw[: page_match.start()].strip()) >= 2:
        page_digits = re.sub(r"\D+", "", page_match.group("page"))
        page_start = int(page_digits) if page_digits else None
        raw = raw[: page_match.start()]

    raw = re.sub(r"[.．·•…]{2,}$", "", raw).strip(" .．·•…")
    title = re.sub(r"\s+", " ", raw).strip()
    title = re.sub(r"^(\d+)[,，](\d+)", r"\1.\2", title)
    title = re.sub(r"^[.．](\d+)[,，](\d+)", r"\1.\2", title)
    title = re.sub(r"^第\s*([0-9一二三四五六七八九十百〇零两]+)\s*章\s*", r"第 \1 章 ", title)

    dotted = re.match(r"^(\d+(?:\s*[.．]\s*\d+)+)\s*(.*)$", title)
    if dotted:
        number = re.sub(r"\s+", "", dotted.group(1)).replace("．", ".")
        rest = dotted.group(2).strip()
        title = f"{number} {rest}".strip()

    return re.sub(r"\s+", " ", title).strip(), page_start


def _entry_level(title: str, fallback_level: int, *, seen_chapter: bool) -> int:
    if re.match(rf"^第\s*{_EPUB_NUMBER_PATTERN}\s*章\b", title):
        return 1
    if re.match(r"^chapter\s+\d+\b", title, flags=re.IGNORECASE):
        return 1
    dotted = re.match(r"^(\d+(?:[.．]\d+)+)", title)
    if dotted:
        return max(2, min(dotted.group(1).replace("．", ".").count(".") + 1, 4))
    if fallback_level <= 1 and seen_chapter:
        return 2
    return max(1, min(fallback_level, 4))


def _ncx_entries(raw_xml: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return []
    entries: list[dict[str, object]] = []

    def walk(nav_point: ET.Element, depth: int) -> None:
        label_node = nav_point.find("./{*}navLabel/{*}text")
        content_node = nav_point.find("./{*}content")
        label = "".join(label_node.itertext()).strip() if label_node is not None else ""
        source = content_node.attrib.get("src", "").strip() if content_node is not None else ""
        if label:
            entries.append({"label": label, "source": source, "level": depth})
        for child in nav_point.findall("./{*}navPoint"):
            walk(child, depth + 1)

    nav_map = root.find(".//{*}navMap")
    if nav_map is None:
        return []
    for nav_point in nav_map.findall("./{*}navPoint"):
        walk(nav_point, 1)
    return entries


def _nav_entries(raw_html: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(raw_html)
    except ET.ParseError:
        return []

    def local_name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1].lower()

    toc_navs = [
        element
        for element in root.iter()
        if local_name(element) == "nav"
        and any(key.rsplit("}", 1)[-1] == "type" and "toc" in value.lower() for key, value in element.attrib.items())
    ]
    search_roots = toc_navs or [root]
    entries: list[dict[str, object]] = []
    for search_root in search_roots:
        for element in search_root.iter():
            if local_name(element) not in {"a", "span"}:
                continue
            label = " ".join(text.strip() for text in element.itertext() if text.strip())
            if not label:
                continue
            entries.append({"label": label, "source": element.attrib.get("href", "").strip(), "level": 1})
    return entries


def _is_separator_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title).lower()
    return compact in {"封面", "版权", "目录", "目次", "前言", "序", "绪言", "contents", "cover", "titlepage"}


def _is_parser_artifact_title(title: str) -> bool:
    compact = re.sub(r"[\s_-]+", "", title).strip("：:").lower()
    return bool(_PARSER_ARTIFACT_TITLE_PATTERN.fullmatch(compact))
