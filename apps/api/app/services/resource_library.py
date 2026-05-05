from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from docx import Document as DocxDocument

from app.models import (
    OCRChunkLocator,
    LibraryChapter,
    ResourceContextChunk,
    ResourceLibraryItem,
    ResourceOCRChunk,
    ResourceReferenceContext,
)
from app.services.image_ocr import extract_image_ocr_result, extract_image_text
from app.services.openai_course_ai import openai_course_ai


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


def _curated_csapp_outline() -> list[LibraryChapter]:
    return [
        _chapter("Computer Systems Tour", "系统总览，建立整本书的坐标系。", ["system", "overview", "csapp", "系统总览"], order_index=0),
        _chapter(
            "Representing and Manipulating Information",
            "机器如何表达整数、浮点数和位级运算。",
            ["bits", "representation", "floating point", "信息表示", "浮点数"],
            order_index=1,
        ),
        _chapter(
            "Machine-Level Representation",
            "程序如何变成汇编和机器级行为。",
            ["assembly", "machine-level", "stack", "汇编", "机器级表示"],
            order_index=2,
        ),
        _chapter(
            "Processor Architecture",
            "处理器、流水线与性能。",
            ["processor", "pipeline", "architecture", "处理器", "流水线"],
            order_index=3,
        ),
        _chapter(
            "Optimizing Program Performance",
            "性能优化与基准意识。",
            ["optimization", "performance", "cache", "性能优化"],
            order_index=4,
        ),
        _chapter(
            "The Memory Hierarchy",
            "缓存与内存层次。",
            ["cache", "memory hierarchy", "latency", "缓存", "内存层次"],
            order_index=5,
        ),
        _chapter("Linking", "目标文件、静态链接与动态链接。", ["linking", "symbol", "loader", "链接"], order_index=6),
        _chapter(
            "Exceptional Control Flow",
            "进程、信号和异常控制流。",
            ["process", "signal", "exception", "异常控制流", "进程"],
            order_index=7,
        ),
        _chapter(
            "Virtual Memory",
            "地址空间与虚拟内存机制。",
            ["virtual memory", "address space", "page", "虚拟内存", "地址空间", "页表"],
            order_index=8,
        ),
        _chapter(
            "System-Level I/O",
            "Unix I/O 与网络编程基础。",
            ["io", "network", "socket", "输入输出", "网络编程"],
            order_index=9,
        ),
    ]


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


def _outline_from_ai(resource_name: str, mime_type: str, text: str) -> list[LibraryChapter]:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) < 40:
        return []
    ai_outline = openai_course_ai.generate_resource_outline(
        resource_name=resource_name,
        mime_type=mime_type,
        extracted_text=compact,
    )
    if ai_outline is None or not ai_outline.chapters:
        return []
    chapters: list[LibraryChapter] = []
    for index, chapter in enumerate(ai_outline.chapters):
        title = chapter.title.strip()
        if not title:
            continue
        level = min(max(int(chapter.level or 1), 1), 3)
        summary = chapter.summary.strip() or f"来自资料“{resource_name}”的目录章节。"
        keywords = [item.strip().lower() for item in chapter.keywords if item.strip()]
        chapters.append(
            _chapter(
                title=title,
                summary=summary,
                keywords=keywords or _keywords_from_text(f"{title}\n{summary}")[:6],
                level=level,
                locator_hint=title,
                order_index=index,
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


def _term_embedding(terms: list[str]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for term in terms:
        normalized = term.strip().lower()
        if not normalized:
            continue
        weights[normalized] = weights.get(normalized, 0.0) + 1.0
    return weights


def _normalize_ocr_line(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": str(item.get("text") or "").strip(),
        "x": float(item.get("x") or 0.0),
        "y": float(item.get("y") or 0.0),
        "width": float(item.get("width") or 0.0),
        "height": float(item.get("height") or 0.0),
        "page": int(item["page"]) if item.get("page") not in (None, "") else None,
    }


def _build_image_ocr_chunks(lines: list[dict[str, Any]]) -> list[ResourceOCRChunk]:
    normalized = [_normalize_ocr_line(item) for item in lines if str(item.get("text") or "").strip()]
    if not normalized:
        return []
    normalized.sort(key=lambda line: (line["page"] or 1, -line["y"], line["x"]))
    chunks: list[ResourceOCRChunk] = []
    current_group: list[dict[str, Any]] = []
    group_index = 0
    last_page = normalized[0]["page"] or 1
    last_y = normalized[0]["y"]
    for line in normalized:
        page = line["page"] or 1
        if (
            current_group
            and (page != last_page or abs(float(line["y"]) - float(last_y)) > 0.06 or len(current_group) >= 3)
        ):
            chunk = _ocr_group_to_chunk(current_group, order_index=group_index)
            if chunk is not None:
                chunks.append(chunk)
                group_index += 1
            current_group = []
        current_group.append(line)
        last_page = page
        last_y = line["y"]
    if current_group:
        chunk = _ocr_group_to_chunk(current_group, order_index=group_index)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def _ocr_group_to_chunk(group: list[dict[str, Any]], *, order_index: int) -> ResourceOCRChunk | None:
    text = " ".join(str(item["text"]).strip() for item in group if str(item["text"]).strip()).strip()
    if not text:
        return None
    xs = [float(item["x"]) for item in group]
    ys = [float(item["y"]) for item in group]
    widths = [float(item["width"]) for item in group]
    heights = [float(item["height"]) for item in group]
    min_x = min(xs) if xs else 0.0
    max_x = max((x + w) for x, w in zip(xs, widths, strict=False)) if xs else min_x
    max_y = max((y + h / 2) for y, h in zip(ys, heights, strict=False)) if ys else 0.0
    min_y = min((y - h / 2) for y, h in zip(ys, heights, strict=False)) if ys else 0.0
    locator = OCRChunkLocator(
        x=min(max(min_x, 0.0), 1.0),
        y=min(max((min_y + max_y) / 2, 0.0), 1.0),
        width=min(max(max_x - min_x, 0.0), 1.0),
        height=min(max(max_y - min_y, 0.0), 1.0),
        page=group[0].get("page"),
    )
    terms = _keywords_from_text(text)
    return ResourceOCRChunk(
        text=text,
        summary=text[:120],
        terms=terms,
        embedding=_term_embedding(terms),
        locator=locator,
        order_index=order_index,
    )


def _generic_chapter_from_text(title: str, text: str, *, summary_prefix: str) -> LibraryChapter:
    snippet = re.sub(r"\s+", " ", text[:4000]).strip()[:120] or f"围绕“{title}”补充资料入口。"
    return _chapter(
        title=title,
        summary=f"{summary_prefix}{snippet}",
        keywords=_keywords_from_text(text) or [token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
        locator_hint=title,
        order_index=0,
        scan_strategy="fulltext_match",
    )


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


def _rank_passages(text: str, query: str, *, anchor: str | None = None) -> list[str]:
    paragraphs = [segment.strip() for segment in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", text) if segment.strip()]
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
        return paragraphs[:3]
    return [paragraph for _, paragraph in sorted(scored, key=lambda item: item[0], reverse=True)[:3]]


def _build_teaching_hint(chapter_title: str, excerpt: str) -> str:
    focus = _keywords_from_text(excerpt)[:3]
    if focus:
        return f"讲解时先用自己的话串起 {', '.join(focus)}，再回到“{chapter_title}”的主线。"
    return f"讲解时先概括这段在“{chapter_title}”里解决了什么问题，再给一个更口语化解释。"


def _build_reference_context(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    query: str,
    raw_text: str,
    *,
    preferred_chunk_id: str | None = None,
) -> ResourceReferenceContext | None:
    compact = re.sub(r"\s+", " ", raw_text).strip()
    if not compact:
        return None

    chunks: list[ResourceContextChunk]
    if resource.resource_type == "image" and resource.ocr_chunks:
        chunks = _image_reference_chunks(
            chapter_title=chapter.title,
            query=query,
            ocr_chunks=resource.ocr_chunks,
            preferred_chunk_id=preferred_chunk_id,
        )
    else:
        passages = _rank_passages(compact[:12000], query, anchor=chapter.title)
        chunks = [
            ResourceContextChunk(
                title=f"{chapter.title} / 参考片段 {index}",
                excerpt=passage[:240],
                teaching_hint=_build_teaching_hint(chapter.title, passage),
            )
            for index, passage in enumerate(passages[:3], start=1)
        ]
    teaching_points = [
        f"先说明“{chapter.title}”这一章想解决的核心问题，再接回用户当前问题。",
        f"不要照搬原文，优先把 {', '.join(_keywords_from_text(compact)[:3]) or chapter.title} 之间的关系讲顺。",
        "先给定义或直觉，再补一个可用于讲课的例子或对比。",
    ]
    summary = (
        f"《{resource.name}》的《{chapter.title}》可以作为本次板书参考。"
        "这份上下文已经被压缩成可讲解的要点，不是原文照搬。"
    )
    return ResourceReferenceContext(
        resource_id=resource.id,
        chapter_id=chapter.id,
        resource_name=resource.name,
        chapter_title=chapter.title,
        summary=summary,
        teaching_points=teaching_points,
        chunks=chunks,
        full_text=raw_text.strip(),
    )


def _image_reference_chunks(
    *,
    chapter_title: str,
    query: str,
    ocr_chunks: list[ResourceOCRChunk],
    preferred_chunk_id: str | None,
) -> list[ResourceContextChunk]:
    ordered = ocr_chunks
    if preferred_chunk_id:
        preferred = [chunk for chunk in ocr_chunks if chunk.id == preferred_chunk_id]
        others = [chunk for chunk in ocr_chunks if chunk.id != preferred_chunk_id]
        ordered = preferred + others
    query_terms = set(_keywords_from_text(query))
    scored: list[tuple[float, ResourceOCRChunk]] = []
    for chunk in ordered:
        lowered = chunk.text.lower()
        score = 0.0
        for term in query_terms:
            if term in lowered:
                score += 1.5
        for term in chunk.terms:
            if term and term in query:
                score += 0.6
        if preferred_chunk_id and chunk.id == preferred_chunk_id:
            score += 5.0
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    top_chunks = [chunk for _, chunk in scored[:3]] or ordered[:3]
    return [
        ResourceContextChunk(
            title=f"{chapter_title} / 图片片段 {index}",
            excerpt=chunk.text[:240],
            teaching_hint=_build_teaching_hint(chapter_title, chunk.text),
            locator=chunk.locator,
        )
        for index, chunk in enumerate(top_chunks, start=1)
    ]


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
    start = max((chapter.page_start or 1) - 1, 0)
    end_exclusive = chapter.page_end or ((chapter.page_start or 1) + 2)
    end_exclusive = min(end_exclusive, len(reader.pages))
    extracted: list[str] = []
    for page_index in range(start, max(start + 1, end_exclusive)):
        try:
            extracted.append(reader.pages[page_index].extract_text() or "")
        except Exception:
            continue

    joined = "\n".join(extracted).strip()
    if joined:
        return joined

    # 如果 PDF 无法按页定位，就退回到前几页的相关片段。
    fallback: list[str] = []
    for page in reader.pages[: min(5, len(reader.pages))]:
        try:
            fallback.append(page.extract_text() or "")
        except Exception:
            continue
    fallback_text = "\n".join(fallback)
    passages = _rank_passages(fallback_text, query, anchor=chapter.title)
    return "\n\n".join(passages)


def _outline_entries_to_chapters(entries: list[tuple[str, int, int | None]], total_pages: int) -> list[LibraryChapter]:
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
        chapters.append(
            _chapter(
                title=title,
                summary=f"PDF 目录项“{title}”被收录进课程资料库。",
                keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                level=level,
                locator_hint=title,
                order_index=index,
                scan_strategy="page_window" if page_start else "outline_only",
                page_start=page_start,
                page_end=page_end,
            )
        )
    return chapters


def extract_outline(
    file_path: Path, original_name: str, mime_type: str
) -> tuple[list[LibraryChapter], bool, str | None, list[ResourceOCRChunk]]:
    name_lower = original_name.lower()
    if mime_type.startswith("image/"):
        generic_title = Path(original_name).stem
        ocr_result = extract_image_ocr_result(file_path)
        extracted_text = (
            str(ocr_result.get("text") or "").strip()
            if isinstance(ocr_result, dict)
            else extract_image_text(file_path)
        )
        ocr_chunks = (
            _build_image_ocr_chunks(list(ocr_result.get("lines") or []))
            if isinstance(ocr_result, dict)
            else []
        )
        if extracted_text:
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
                ocr_chunks,
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
            ocr_chunks,
        )

    if mime_type in {"text/plain", "text/markdown"} or file_path.suffix.lower() in {".md", ".txt"}:
        text = _read_text_file(file_path)
        outline = _extract_markdown_outline(text)
        if outline:
            return outline, True, text, []
        ai_outline = _outline_from_ai(original_name, mime_type, text)
        if ai_outline:
            return ai_outline, True, text, []
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
            [],
        )

    if file_path.suffix.lower() == ".docx":
        text = _read_docx_text(file_path)
        outline = _extract_docx_outline(file_path)
        if outline:
            return outline, True, text, []
        ai_outline = _outline_from_ai(original_name, mime_type, text)
        if ai_outline:
            return ai_outline, True, text, []
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
            [],
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
            chapters = _outline_entries_to_chapters(entries, len(reader.pages))
            if chapters:
                return chapters, True, None, []
        extracted_text = []
        for page in reader.pages[:2]:
            try:
                extracted_text.append(page.extract_text() or "")
            except Exception:
                continue
        joined_text = "\n".join(extracted_text).strip()
        if joined_text:
            ai_outline = _outline_from_ai(original_name, mime_type, joined_text)
            if ai_outline:
                return ai_outline, True, joined_text, []
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
                [],
            )
        if "csapp" in name_lower or "computer systems" in name_lower:
            return _curated_csapp_outline(), True, None, []

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
        [],
    )


def build_resource_item(file_path: Path, original_name: str) -> ResourceLibraryItem:
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    outline, extracted, text_content, ocr_chunks = extract_outline(file_path, original_name, mime_type)
    outline = _attach_outline_hierarchy(outline)
    concept_index: dict[str, list[str]] = {}
    for chapter in outline:
        path_keywords = _keywords_from_text(" ".join(chapter.path))
        for keyword in [*chapter.keywords, *path_keywords]:
            concept_index.setdefault(keyword, []).append(chapter.id)

    return ResourceLibraryItem(
        name=original_name,
        mime_type=mime_type,
        resource_type="image" if mime_type.startswith("image/") else "document",
        size_bytes=file_path.stat().st_size,
        outline=outline,
        concept_index=concept_index,
        extracted_text_available=extracted,
        text_content=text_content,
        ocr_chunks=ocr_chunks,
        source_path=str(file_path),
    )


def extract_reference_context(
    resource: ResourceLibraryItem,
    chapter_id: str,
    *,
    user_query: str,
    preferred_chunk_id: str | None = None,
) -> ResourceReferenceContext | None:
    chapter = next((candidate for candidate in resource.outline if candidate.id == chapter_id), None)
    if chapter is None:
        return None

    if resource.text_content and resource.resource_type == "image":
        return _build_reference_context(
            resource,
            chapter,
            user_query,
            raw_text=resource.text_content,
            preferred_chunk_id=preferred_chunk_id,
        )

    if not resource.source_path:
        return _build_reference_context(
            resource,
            chapter,
            user_query,
            raw_text=resource.text_content or f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}",
            preferred_chunk_id=preferred_chunk_id,
        )

    file_path = Path(resource.source_path)
    if not file_path.exists():
        if resource.text_content:
            return _build_reference_context(
                resource,
                chapter,
                user_query,
                raw_text=resource.text_content,
                preferred_chunk_id=preferred_chunk_id,
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
    elif suffix == ".pdf":
        raw_text = _extract_pdf_chapter_text(file_path, chapter, user_query)
    else:
        raw_text = resource.text_content or f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}"

    return _build_reference_context(
        resource,
        chapter,
        user_query,
        raw_text,
        preferred_chunk_id=preferred_chunk_id,
    )
