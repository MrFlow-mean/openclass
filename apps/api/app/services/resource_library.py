from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from pypdf import PdfReader

from app.models import LibraryChapter, ResourceLibraryItem


def _chapter(title: str, summary: str, keywords: list[str], level: int = 1) -> LibraryChapter:
    return LibraryChapter(title=title, summary=summary, keywords=keywords, level=level)


def _curated_csapp_outline() -> list[LibraryChapter]:
    return [
        _chapter("Computer Systems Tour", "系统总览，建立整本书的坐标系。", ["system", "overview", "csapp"]),
        _chapter("Representing and Manipulating Information", "机器如何表达整数、浮点数和位级运算。", ["bits", "representation", "floating point"]),
        _chapter("Machine-Level Representation", "程序如何变成汇编和机器级行为。", ["assembly", "machine-level", "stack"]),
        _chapter("Processor Architecture", "处理器、流水线与性能。", ["processor", "pipeline", "architecture"]),
        _chapter("Optimizing Program Performance", "性能优化与基准意识。", ["optimization", "performance", "cache"]),
        _chapter("The Memory Hierarchy", "缓存与内存层次。", ["cache", "memory hierarchy", "latency"]),
        _chapter("Linking", "目标文件、静态链接与动态链接。", ["linking", "symbol", "loader"]),
        _chapter("Exceptional Control Flow", "进程、信号和异常控制流。", ["process", "signal", "exception"]),
        _chapter("Virtual Memory", "地址空间与虚拟内存机制。", ["virtual memory", "address space", "page"]),
        _chapter("System-Level I/O", "Unix I/O 与网络编程基础。", ["io", "network", "socket"]),
    ]


def _extract_markdown_outline(text: str) -> list[LibraryChapter]:
    chapters: list[LibraryChapter] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            title = line[level:].strip()
            chapters.append(
                _chapter(
                    title=title,
                    summary=f"来自资料标题“{title}”的章节摘要待进一步展开。",
                    keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                    level=level,
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
    snippet = re.sub(r"\s+", " ", text).strip()[:120] or f"围绕“{title}”补充资料入口。"
    return _chapter(
        title=title,
        summary=f"{summary_prefix}{snippet}",
        keywords=_keywords_from_text(text) or [token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
    )


def extract_outline(file_path: Path, original_name: str, mime_type: str) -> tuple[list[LibraryChapter], bool]:
    name_lower = original_name.lower()
    if "csapp" in name_lower or "computer systems" in name_lower:
        return _curated_csapp_outline(), True

    if mime_type.startswith("image/"):
        generic_title = Path(original_name).stem
        return (
            [
                _chapter(
                    title=generic_title,
                    summary=f"已上传图片资料“{original_name}”，可作为当前课程的视觉参考。",
                    keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", generic_title)[:6]],
                )
            ],
            False,
        )

    if mime_type in {"text/plain", "text/markdown"} or file_path.suffix.lower() in {".md", ".txt"}:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        outline = _extract_markdown_outline(text)
        if outline:
            return outline, True
        return (
            [
                _generic_chapter_from_text(
                    Path(original_name).stem,
                    text,
                    summary_prefix="从文本资料中抽取到的内容摘要：",
                )
            ],
            True,
        )

    if file_path.suffix.lower() == ".pdf":
        reader = PdfReader(str(file_path))
        if reader.outline:
            chapters: list[LibraryChapter] = []

            def _walk_outline(items: list, level: int = 1) -> None:
                for item in items:
                    if isinstance(item, list):
                        _walk_outline(item, level + 1)
                    else:
                        title = str(getattr(item, "title", item))
                        chapters.append(
                            _chapter(
                                title=title,
                                summary=f"PDF 目录项“{title}”被收录进课程资料库。",
                                keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", title)[:5]],
                                level=level,
                            )
                        )

            _walk_outline(list(reader.outline))
            if chapters:
                return chapters, True
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
            )

    generic_title = Path(original_name).stem
    return (
        [
            _chapter(
                title=generic_title,
                summary=f"当前资料尚未提取出显式目录，先以“{generic_title}”作为入口。",
                keywords=[token.lower() for token in re.findall(r"[A-Za-z\u4e00-\u9fff]+", generic_title)[:5]],
            )
        ],
        False,
    )


def build_resource_item(file_path: Path, original_name: str) -> ResourceLibraryItem:
    mime_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    outline, extracted = extract_outline(file_path, original_name, mime_type)
    concept_index: dict[str, list[str]] = {}
    for chapter in outline:
        for keyword in chapter.keywords:
            concept_index.setdefault(keyword, []).append(chapter.id)

    return ResourceLibraryItem(
        name=original_name,
        mime_type=mime_type,
        resource_type="image" if mime_type.startswith("image/") else "document",
        size_bytes=file_path.stat().st_size,
        outline=outline,
        concept_index=concept_index,
        extracted_text_available=extracted,
        source_path=str(file_path),
    )
