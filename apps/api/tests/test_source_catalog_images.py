from __future__ import annotations

from app.models import SourceChapter
from app.services.source_catalog_images import (
    prepare_source_catalog_images,
    select_source_catalog_image_pages,
)
from reportlab.pdfgen import canvas


def test_catalog_image_pages_cover_front_toc_and_back_matter() -> None:
    chapters = [
        SourceChapter(
            owner_user_id="user-1",
            package_id="package-1",
            source_ingestion_id="source-1",
            title="Section",
            metadata={"toc_page": 15},
        )
    ]

    selected = select_source_catalog_image_pages(
        page_count=120,
        candidate_chapters=chapters,
        parser_metadata={"toc_page_start": 14, "toc_page_end": 16},
        max_images=14,
    )

    assert selected[:8] == list(range(1, 9))
    assert {14, 15, 16}.issubset(selected)
    assert selected[-3:] == [118, 119, 120]


def test_catalog_images_render_controlled_pdf_pages(tmp_path) -> None:
    path = tmp_path / "book.pdf"
    document = canvas.Canvas(str(path))
    document.drawString(72, 720, "Front cover")
    document.showPage()
    document.drawString(72, 720, "Body")
    document.save()

    rendered = prepare_source_catalog_images(
        path=path,
        mime_type="application/pdf",
        page_count=2,
        candidate_chapters=[],
        parser_metadata={},
    )

    assert [page.page_no for page in rendered] == [1, 2]
    assert all(page.data_url.startswith("data:image/") for page in rendered)
    assert all(len(page.sha256) == 64 for page in rendered)
