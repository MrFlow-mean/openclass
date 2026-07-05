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


@dataclass(frozen=True)
class EpubTocEntry:
    title: str
    level: int
    source: str = ""
    page_start: int | None = None


def extract_epub_toc_entries(file_path: Path) -> list[EpubTocEntry]:
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
                    return entries
    except (zipfile.BadZipFile, OSError):
        return []
    return []


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
