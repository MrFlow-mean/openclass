from __future__ import annotations

import zipfile
from pathlib import Path

from reportlab.pdfgen import canvas

from app.models import BoardTaskRequirementSheet, SourceIngestionRecord
from app.services.resource_resolver import ResourceResolver
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


class _NoSearchAdapter:
    def search(self, **_kwargs):  # pragma: no cover - should not be called by chapter-direct test
        raise AssertionError("Open Notebook search should not run when verified chapter evidence exists.")


def test_source_structure_indexer_uses_epub_navigation_metadata(tmp_path: Path) -> None:
    epub_path = tmp_path / "book.epub"
    _write_epub_with_nav(epub_path)
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name="book.epub", mime_type="application/epub+zip", path=epub_path)
    store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    structure = indexer.rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert structure.strategy == "epub_navigation"
    assert view is not None
    assert [chapter.normalized_number for chapter in view.chapters] == ["1", "1.1"]
    assert view.chapters[1].parent_id == view.chapters[0].id
    assert view.chapters[1].path == ["1 Foundations", "1.1 Details"]
    assert all(chapter.anchor_status == "verified" for chapter in view.chapters)
    assert view.chunks


def test_source_structure_indexer_does_not_generate_fake_toc_without_headings(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("第一段普通正文。\n\n第二段普通正文。", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name="notes.txt", mime_type="text/plain", path=text_path)
    store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    structure = indexer.rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "linear_only"
    assert structure.has_verified_toc is False
    assert view is not None
    assert view.chapters == []
    assert view.chunks


def test_source_structure_indexer_maps_pdf_toc_to_body_heading(tmp_path: Path) -> None:
    pdf_path = tmp_path / "toc.pdf"
    _write_pdf_with_toc(pdf_path)
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name="toc.pdf", mime_type="application/pdf", path=pdf_path)
    store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    structure = indexer.rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert structure.strategy == "pdf_toc"
    assert view is not None
    assert view.chapters[0].normalized_number == "1"
    assert view.chapters[0].page_start == 2


def test_resource_resolver_prefers_verified_chapter_index(tmp_path: Path) -> None:
    markdown_path = tmp_path / "chapter.md"
    markdown_path.write_text("# 7.7.3 Cache Write Policies\n\nThis section body is the exact source.", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    store.upsert_notebook(owner_user_id="user_1", package_id="pkg_1", notebook_id="nb_1", title="资料容器")
    record = _source_record(tmp_path, file_name="chapter.md", mime_type="text/markdown", path=markdown_path)
    store.save_source(record)
    SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="结合资料讲 7.7.3",
        board_task=BoardTaskRequirementSheet(requested_action="explain", question_or_topic="7.7.3", progress=100),
        purpose="board_explain",
    )

    assert bundle is not None
    assert bundle.metadata["retrieval_mode"] == "verified_chapter"
    assert bundle.evidence_items[0].chapter_id
    assert "exact source" in bundle.evidence_items[0].expanded_text


def _source_record(tmp_path: Path, *, file_name: str, mime_type: str, path: Path) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=f"source_{path.stem}",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=file_name,
        source_type="local_file",
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="ready",
        open_notebook_notebook_id="nb_1",
        open_notebook_source_id=f"open_{path.stem}",
        metadata={"local_source_path": str(path), "tmp_root": str(tmp_path)},
    )


def _write_epub_with_nav(path: Path) -> None:
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
              <spine>
                <itemref idref="ch1"/>
                <itemref idref="ch11"/>
              </spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html><body><nav epub:type="toc"><ol>
              <li><a href="ch1.xhtml">1 Foundations</a></li>
              <li><a href="ch11.xhtml">1.1 Details</a></li>
            </ol></nav></body></html>""",
        )
        archive.writestr("OEBPS/ch1.xhtml", "<html><body><h1>1 Foundations</h1><p>Foundations body.</p></body></html>")
        archive.writestr("OEBPS/ch11.xhtml", "<html><body><h2>1.1 Details</h2><p>Details body.</p></body></html>")


def _write_pdf_with_toc(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Contents")
    pdf.drawString(72, 690, "1 Intro ........ 2")
    pdf.showPage()
    pdf.drawString(72, 720, "1 Intro")
    pdf.drawString(72, 690, "Body text for this section.")
    pdf.save()
