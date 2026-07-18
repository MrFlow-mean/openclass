from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Sequence

from app.models import SourceChapter
from app.services.source_codex_models import SourceCatalogImagePage


DEFAULT_SOURCE_CATALOG_IMAGE_LIMIT = 12
_SUPPORTED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def prepare_source_catalog_images(
    *,
    path: Path | None,
    mime_type: str,
    page_count: int,
    candidate_chapters: Sequence[SourceChapter],
    parser_metadata: dict[str, Any],
    max_images: int = DEFAULT_SOURCE_CATALOG_IMAGE_LIMIT,
) -> list[SourceCatalogImagePage]:
    if path is None or not path.is_file() or max_images <= 0:
        return []
    if mime_type == "application/pdf" or path.suffix.casefold() == ".pdf":
        page_numbers = select_source_catalog_image_pages(
            page_count=page_count,
            candidate_chapters=candidate_chapters,
            parser_metadata=parser_metadata,
            max_images=max_images,
        )
        return _render_pdf_pages(path, page_numbers)
    normalized_mime = mime_type.casefold()
    if normalized_mime in _SUPPORTED_IMAGE_MIME_TYPES:
        try:
            content = path.read_bytes()
        except OSError:
            return []
        if not content:
            return []
        return [
            SourceCatalogImagePage(
                page_no=1,
                data_url=_data_url(content, normalized_mime),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ]
    return []


def select_source_catalog_image_pages(
    *,
    page_count: int,
    candidate_chapters: Sequence[SourceChapter],
    parser_metadata: dict[str, Any],
    max_images: int = DEFAULT_SOURCE_CATALOG_IMAGE_LIMIT,
) -> list[int]:
    if page_count <= 0 or max_images <= 0:
        return []
    prioritized: list[int] = []

    def add(page_no: object) -> None:
        if isinstance(page_no, int) and 1 <= page_no <= page_count and page_no not in prioritized:
            prioritized.append(page_no)

    for page_no in range(1, min(page_count, 8) + 1):
        add(page_no)
    toc_start = parser_metadata.get("toc_page_start")
    toc_end = parser_metadata.get("toc_page_end")
    if isinstance(toc_start, int) and isinstance(toc_end, int):
        for page_no in range(toc_start, min(toc_end, toc_start + 5) + 1):
            add(page_no)
    for chapter in candidate_chapters:
        add(chapter.metadata.get("toc_page"))
    for page_no in range(max(1, page_count - 2), page_count + 1):
        add(page_no)
    return prioritized[:max_images]


def _render_pdf_pages(path: Path, page_numbers: Sequence[int]) -> list[SourceCatalogImagePage]:
    if not page_numbers:
        return []
    try:
        import fitz  # type: ignore[import-not-found]

        document = fitz.open(path)
    except Exception:
        return []
    rendered: list[SourceCatalogImagePage] = []
    try:
        for page_no in page_numbers:
            if page_no < 1 or page_no > document.page_count:
                continue
            page = document.load_page(page_no - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
            try:
                content = pixmap.tobytes("jpeg", jpg_quality=68)
                rendered_mime = "image/jpeg"
            except (TypeError, ValueError):
                content = pixmap.tobytes("png")
                rendered_mime = "image/png"
            rendered.append(
                SourceCatalogImagePage(
                    page_no=page_no,
                    data_url=_data_url(content, rendered_mime),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    except Exception:
        return rendered
    finally:
        document.close()
    return rendered


def _data_url(content: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
