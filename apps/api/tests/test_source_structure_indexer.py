from __future__ import annotations

import zipfile
from pathlib import Path
from uuid import uuid4

from reportlab.pdfgen import canvas

from app.models import SourceIngestionRecord
from app.services import pdf_toc_parser
from app.services.image_ocr import OCRLineLayout, OCRPageLayout
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


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
        archive.writestr("OEBPS/ch1.xhtml", "<html><body><h1>1 Foundations</h1><p>Body.</p></body></html>")
        archive.writestr("OEBPS/ch11.xhtml", "<html><body><h2>1.1 Details</h2><p>Details.</p></body></html>")


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
