from __future__ import annotations

import base64
import zipfile
from pathlib import Path
from uuid import uuid4

from reportlab.pdfgen import canvas

from app.models import SourceChapter, SourceChunk, SourceIngestionRecord, SourceStructure
from app.services import pdf_toc_parser
from app.services.image_ocr import OCRLineLayout, OCRPageLayout
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import (
    CURRENT_SOURCE_STRUCTURE_INDEX_VERSION,
    PageText,
    ParsedSourceDocument,
    SourceStructureIndexer,
    _chapter_for_chunk,
    _pdf_outline_chapters,
)
from app.services.source_structure_store import SourceStructureStore
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION


def _source_record(path: Path, *, mime_type: str) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=f"source_{path.stem}",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=path.name,
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(path)},
    )


def test_epub_navigation_builds_verified_stable_chapter_identity(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.epub"
    _write_epub(epub_path)
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(epub_path, mime_type="application/epub+zip")
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    first = indexer.rebuild_structure(record)
    first_view = structure_store.get_structure_view(source=record)
    indexer.rebuild_structure(record)
    second_view = structure_store.get_structure_view(source=record)

    assert first.status == "ready"
    assert first.strategy == "epub_navigation"
    assert [chapter.normalized_number for chapter in first_view.chapters] == ["1", "1.1"]
    assert all(chapter.anchor_status == "verified" for chapter in first_view.chapters)
    assert {tuple(chapter.path): chapter.id for chapter in first_view.chapters} == {
        tuple(chapter.path): chapter.id for chapter in second_view.chapters
    }
    assert len(first_view.visuals) == 1
    assert first_view.visuals[0].kind == "image"
    assert first_view.visuals[0].caption == "A grounded figure"
    assert first_view.visuals[0].chapter_id == first_view.chapters[0].id
    assert first_view.visuals[0].content_hash == second_view.visuals[0].content_hash
    assert first_view.visuals[0].asset_path
    assert "asset_path" not in first_view.visuals[0].model_dump(mode="json")


def test_plain_text_without_headings_uses_linear_chunks_without_fake_toc(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(text_path, mime_type="text/plain")
    source_store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "linear_only"
    assert structure.has_verified_toc is False
    assert view.chapters == []
    assert view.chunks


def test_failed_rebuild_keeps_last_usable_structure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "stable.md"
    path.write_text("# 1 Foundations\n\nBody.", encoding="utf-8")
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(path, mime_type="text/markdown")
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)
    indexer.rebuild_structure(record)
    before = structure_store.get_structure_view(source=record)
    monkeypatch.setattr(
        indexer,
        "_parse_record",
        lambda _record: (_ for _ in ()).throw(RuntimeError("parse failed")),
    )

    recovered = indexer.rebuild_structure(record)
    after = structure_store.get_structure_view(source=record)

    assert recovered.status == "ready"
    assert recovered.error == "parse failed"
    assert after.chapters == before.chapters
    assert after.chunks == before.chunks


def test_missing_local_source_and_url_remain_distinguishable(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    missing_name = f"missing-{uuid4().hex}.txt"
    missing = SourceIngestionRecord(
        id="source_missing",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=missing_name,
        source_type="local_file",
        file_name=missing_name,
        mime_type="text/plain",
        status="ready",
    )
    url = SourceIngestionRecord(
        id="source_url",
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Example",
        source_type="web_url",
        source_uri="https://example.com/article",
        mime_type="text/html",
        status="ready",
    )
    indexer = SourceStructureIndexer(store=store)

    missing_structure = indexer.rebuild_structure(missing)
    url_structure = indexer.rebuild_structure(url)

    assert missing_structure.metadata["missing_local_source_path"] is True
    assert missing_structure.strategy == "linear_text"
    assert url_structure.strategy == "linear_text"


def test_pdf_outline_merges_with_structured_toc_rows(tmp_path: Path, monkeypatch) -> None:
    pdf_path = tmp_path / "merged.pdf"
    _write_pdf_with_outline(pdf_path)
    monkeypatch.setattr(
        pdf_toc_parser,
        "extract_pdf_pages_layout",
        lambda *_args, **_kwargs: [
            _toc_layout_page(
                1,
                [
                    ("1 Intro", 1, 0.90, 0.20),
                    ("1.1 Details", 1, 0.84, 0.24),
                    ("1.2 More", 2, 0.78, 0.24),
                    ("2 Next", 3, 0.72, 0.20),
                ],
            )
        ],
    )
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(pdf_path, mime_type="application/pdf")
    source_store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.strategy == "pdf_merged_toc"
    assert [chapter.normalized_number for chapter in view.chapters] == ["", "1", "1.1", "1.2", "2"]
    assert all(chapter.anchor_status == "verified" for chapter in view.chapters)
    assert any(visual.kind == "diagram" and visual.page_start == 2 for visual in view.visuals)

    visual_paths = [Path(visual.asset_path) for visual in view.visuals if visual.asset_path]
    structure_store.delete_for_source(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert structure_store.get_structure_view(source=record).visuals == []
    assert all(not path.exists() for path in visual_paths)


def test_unresolved_pdf_outline_destination_is_not_verified_from_toc_text() -> None:
    class OutlineItem:
        title = "6.6.2 Binary heap implementation"

    class Reader:
        outline = [OutlineItem()]

        @staticmethod
        def get_destination_page_number(_item: object) -> int:
            raise ValueError("broken destination")

    pages = [
        PageText(
            page_no=1,
            text="Contents 6.6.2 Binary heap implementation",
            start_offset=0,
            end_offset=43,
        )
    ]

    chapters = _pdf_outline_chapters(
        Reader(),
        pages,
        "Contents 6.6.2 Binary heap implementation",
    )

    assert len(chapters) == 1
    assert chapters[0].verified is False
    assert chapters[0].start_offset is None
    assert chapters[0].page_start is None
    assert chapters[0].metadata["verification"] == "destination_unresolved"


def test_chunk_uses_physical_pages_instead_of_chapter_page_metadata(tmp_path: Path) -> None:
    path = tmp_path / "physical-pages.txt"
    path.write_text("A" * 200, encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    parsed = ParsedSourceDocument(
        text="A" * 200,
        pages=[
            PageText(page_no=1, text="A" * 100, start_offset=0, end_offset=100),
            PageText(page_no=2, text="A" * 100, start_offset=100, end_offset=200),
        ],
    )
    malformed_chapter = SourceChapter(
        id="chapter_broad",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        title="Broad chapter",
        body_start_offset=0,
        body_end_offset=200,
        page_start=None,
        page_end=None,
        anchor_status="verified",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    chunks = SourceStructureIndexer(store=store)._chunks_for_record(
        record,
        parsed,
        [malformed_chapter],
    )

    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 3

    SourceEvidenceStore(tmp_path / "openclass.sqlite3").save_source(record)
    store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="ready",
        ),
        chapters=[malformed_chapter],
        chunks=chunks,
    )
    evidence = store.page_range_evidence(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        page_start=2,
        page_end=3,
        token_budget=10_000,
    )
    assert evidence
    assert evidence[0].expanded_text == "A" * 200


def test_chunk_prefers_nearest_containing_chapter_over_broad_range() -> None:
    chapter_id, _page_start, _page_end = _chapter_for_chunk(
        450,
        550,
        [
            ("chapter_wrong_broad", 0, 1_000, None, None, 3),
            ("chapter_correct_nearby", 400, 600, 5, 7, 2),
        ],
    )

    assert chapter_id == "chapter_correct_nearby"


def test_legacy_structure_version_is_lazily_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_text("Current body", encoding="utf-8")
    record = _source_record(path, mime_type="text/plain")
    database = tmp_path / "openclass.sqlite3"
    SourceEvidenceStore(database).save_source(record)
    store = SourceStructureStore(database)
    store.save_structure_bundle(
        structure=SourceStructure(
            id="structure_legacy_text",
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="linear_only",
            visual_index_version=CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            metadata={},
        ),
        chapters=[],
        chunks=[
            SourceChunk(
                id="chunk_legacy_text",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                text="Stale body",
                start_offset=0,
                end_offset=10,
            )
        ],
    )

    upgraded = SourceStructureIndexer(store=store).ensure_structure(record)
    view = store.get_structure_view(source=record)

    assert upgraded is not None
    assert (
        upgraded.metadata["structure_index_version"]
        == CURRENT_SOURCE_STRUCTURE_INDEX_VERSION
    )
    assert [chunk.text for chunk in view.chunks] == ["Current body"]


def _write_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
                <item id="ch11" href="ch11.xhtml" media-type="application/xhtml+xml"/>
                <item id="figure" href="images/figure.png" media-type="image/png"/>
              </manifest>
              <spine><itemref idref="ch1"/><itemref idref="ch11"/></spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html><body><nav epub:type="toc"><ol>
              <li><a href="ch1.xhtml">1 Foundations</a></li>
              <li><a href="ch11.xhtml">1.1 Details</a></li>
            </ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/ch1.xhtml",
            '<html><body><h1>1 Foundations</h1><p>Body.</p><figure><img src="images/figure.png" alt="A grounded figure"/><figcaption>A grounded figure</figcaption></figure></body></html>',
        )
        archive.writestr("OEBPS/ch11.xhtml", "<html><body><h2>1.1 Details</h2><p>Details.</p></body></html>")
        archive.writestr(
            "OEBPS/images/figure.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        )


def _write_pdf_with_outline(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.bookmarkPage("toc")
    pdf.addOutlineEntry("Contents", "toc", level=0)
    pdf.drawString(72, 720, "Contents")
    pdf.showPage()
    pdf.bookmarkPage("chapter-1")
    pdf.addOutlineEntry("1 Intro", "chapter-1", level=0)
    pdf.drawString(72, 720, "1 Intro")
    pdf.drawString(72, 690, "1.1 Details")
    pdf.rect(72, 420, 260, 180, stroke=1, fill=0)
    pdf.line(82, 440, 300, 570)
    pdf.showPage()
    pdf.drawString(72, 720, "1.2 More")
    pdf.showPage()
    pdf.bookmarkPage("chapter-2")
    pdf.addOutlineEntry("2 Next", "chapter-2", level=0)
    pdf.drawString(72, 720, "2 Next")
    pdf.save()


def _toc_layout_page(
    page_no: int,
    rows: list[tuple[str, int, float, float]],
) -> OCRPageLayout:
    lines: list[OCRLineLayout] = []
    for title, printed_page, y, x in rows:
        lines.append(OCRLineLayout(text=title, x=x, y=y, width=0.30, height=0.02))
        lines.append(OCRLineLayout(text=str(printed_page), x=0.84, y=y, width=0.03, height=0.02))
    return OCRPageLayout(page_no=page_no, lines=lines)
