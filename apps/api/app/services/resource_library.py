from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from pypdf import PdfReader
from docx import Document as DocxDocument

from app.models import (
    LibraryChapter,
    ResourceContextChunk,
    ResourceLibraryItem,
    ResourceReferenceContext,
)
from app.services.image_ocr import extract_image_text, extract_pdf_pages_text


_PDF_TEXT_SUMMARY_LIMIT = 140
_PDF_LOCATOR_SEPARATOR = " || "


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


def _pattern_recognition_teaching_points(chapter_title: str, text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    compact_title = re.sub(r"\s+", "", chapter_title)
    if "概论" not in compact_title and "第一章" not in compact_title and "第1章" not in compact_title:
        return []
    if not {"模式识别", "监督", "非监督"} <= set(re.findall(r"模式识别|监督|非监督", compact)):
        return []
    return [
        "先讲“模式”不是单个样本，而是一类对象、过程或事件背后的规律和特征组合。",
        "再讲“模式识别”的任务：从观测对象提取特征，并把对象归入类别，或在没有标签时发现聚类结构。",
        "接着区分监督模式识别和非监督模式识别：前者有已知类别样本训练分类器，后者按相似性自学习聚类。",
        "最后串起典型系统流程：信息获取与预处理 -> 特征提取与选择 -> 分类器设计或聚类分析 -> 分类决策/结果解释。",
    ]


def _statistical_learning_teaching_points(chapter_title: str, text: str) -> list[str]:
    compact = re.sub(r"\s+", "", f"{chapter_title}\n{text}").lower()
    if "统计学习理论" not in compact and "statisticallearning" not in compact:
        return []
    if not any(term in compact for term in ("经验风险", "真实风险", "期望风险", "vc", "推广能力", "一致性")):
        return []
    return [
        "先把本章定位讲清：它关心训练误差小为什么不一定代表测试误差小。",
        "再区分损失函数、真实风险和经验风险，说明经验风险最小化为什么只是可计算替代。",
        "接着用过学习/过拟合说明函数集合太复杂时，训练集会被噪声牵着走。",
        "然后引出一致性、函数集容量与 VC 维，把“复杂度”变成可以讨论的对象。",
        "最后落到推广能力界、SVM 最大间隔和正则化：这些方法本质上都是在控制复杂度。",
    ]


def _humanities_teaching_points(chapter_title: str, text: str) -> list[str]:
    corpus = f"{chapter_title}\n{text}"
    compact = re.sub(r"\s+", "", corpus)
    humanities_markers = (
        "文学",
        "历史",
        "哲学",
        "政治",
        "法律",
        "社会",
        "文化",
        "教育",
        "伦理",
        "艺术",
        "语文",
        "诗歌",
        "小说",
        "制度",
        "思想",
        "观点",
        "论证",
        "叙事",
        "人物",
        "原因",
        "影响",
        "意义",
        "评价",
        "改革",
        "变法",
        "革命",
        "战争",
    )
    technical_markers = ("统计学习理论", "模式识别", "机器学习", "算法", "公式", "定理", "计算机")
    if any(marker in compact for marker in technical_markers):
        return []
    if sum(1 for marker in humanities_markers if marker in compact) < 2:
        return []
    return [
        "先识别材料中的核心观点、事件、人物、制度、文本细节或论证环节，不要只按标题概括。",
        "每个重要内容都要扩讲：交代背景，翻译材料原意，说明因果链、论证链或文本细读链。",
        "历史/政治/法律类材料要讲清原因、过程、结果和影响；文学/哲学类材料要讲清关键词、文本细节和思想关系。",
        "最后设计检查问题，让学生能从材料证据出发分析，而不是只背空泛结论。",
    ]


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

    teaching_points = [
        *_pattern_recognition_teaching_points(chapter.title, compact),
        *_statistical_learning_teaching_points(chapter.title, compact),
        *_humanities_teaching_points(chapter.title, compact),
        f"先说明“{chapter.title}”这一章想解决的核心问题，再接回用户当前问题。",
        f"优先把 {', '.join(_keywords_from_text(compact)[:3]) or chapter.title} 之间的关系讲顺。",
        "先给定义或直觉，再补一个可用于讲课的例子或对比。",
    ]
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


def extract_outline(file_path: Path, original_name: str, mime_type: str) -> tuple[list[LibraryChapter], bool, str | None]:
    name_lower = original_name.lower()
    if mime_type.startswith("image/"):
        generic_title = Path(original_name).stem
        extracted_text = extract_image_text(file_path)
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
        if "csapp" in name_lower or "computer systems" in name_lower:
            return _curated_csapp_outline(), True, None

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
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    outline, extracted, text_content = extract_outline(file_path, original_name, mime_type)
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
        source_path=str(file_path),
    )


def extract_reference_context(
    resource: ResourceLibraryItem,
    chapter_id: str,
    *,
    user_query: str,
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
    elif suffix == ".pdf":
        raw_text = _extract_pdf_chapter_text(file_path, chapter, user_query)
    else:
        raw_text = resource.text_content or f"{chapter.title}\n{chapter.summary}\n{' '.join(chapter.keywords)}"

    return _build_reference_context(resource, chapter, user_query, raw_text)
