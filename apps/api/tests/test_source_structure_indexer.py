from __future__ import annotations

import zipfile
from pathlib import Path
from uuid import uuid4

from reportlab.pdfgen import canvas

from app.models import BoardTaskRequirementSheet, ChatRequest, SelectionRef, SourceIngestionRecord
from app.services.image_ocr import OCRLineLayout, OCRPageLayout
from app.services import pdf_toc_parser
from app.services.resource_resolver import ResourceResolver
from app.services.source_reference_context import source_aware_user_message
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


def test_source_structure_rebuild_keeps_chapter_identity_stable(tmp_path: Path) -> None:
    markdown_path = tmp_path / "stable-chapters.md"
    markdown_path.write_text(
        "# 1 Foundations\n\nIntroductory body.\n\n## 1.1 Details\n\nDetailed body.",
        encoding="utf-8",
    )
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(
        tmp_path,
        file_name=markdown_path.name,
        mime_type="text/markdown",
        path=markdown_path,
    )
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)

    indexer.rebuild_structure(record)
    first_view = structure_store.get_structure_view(source=record)
    indexer.rebuild_structure(record)
    second_view = structure_store.get_structure_view(source=record)

    assert first_view is not None
    assert second_view is not None
    assert {tuple(chapter.path): chapter.id for chapter in first_view.chapters} == {
        tuple(chapter.path): chapter.id for chapter in second_view.chapters
    }
    assert {chunk.chapter_id for chunk in first_view.chunks if chunk.chapter_id} == {
        chunk.chapter_id for chunk in second_view.chunks if chunk.chapter_id
    }


def test_failed_structure_rebuild_keeps_the_last_usable_index(tmp_path: Path, monkeypatch) -> None:
    markdown_path = tmp_path / "preserved-chapters.md"
    markdown_path.write_text("# 1 Foundations\n\nIntroductory body.", encoding="utf-8")
    source_store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(
        tmp_path,
        file_name=markdown_path.name,
        mime_type="text/markdown",
        path=markdown_path,
    )
    source_store.save_source(record)
    indexer = SourceStructureIndexer(store=structure_store)
    indexer.rebuild_structure(record)
    before = structure_store.get_structure_view(source=record)
    monkeypatch.setattr(indexer, "_parse_record", lambda _record: (_ for _ in ()).throw(RuntimeError("parse failed")))

    recovered = indexer.rebuild_structure(record)
    after = structure_store.get_structure_view(source=record)

    assert recovered.status == "ready"
    assert recovered.error == "parse failed"
    assert before.chapters == after.chapters
    assert before.chunks == after.chunks


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


def test_source_structure_indexer_distinguishes_missing_file_from_url(tmp_path: Path) -> None:
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    missing_file = f"missing-{uuid4().hex}.txt"
    file_record = SourceIngestionRecord(
        id="source_missing_file",
        owner_user_id="user_1",
        package_id="pkg_1",
        title=missing_file,
        source_type="local_file",
        file_name=missing_file,
        mime_type="text/plain",
        status="ready",
        open_notebook_notebook_id="nb_1",
        open_notebook_source_id="open_missing",
    )
    url_record = SourceIngestionRecord(
        id="source_url",
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Example",
        source_type="web_url",
        source_uri="https://example.com/article",
        mime_type="text/html",
        status="ready",
        open_notebook_notebook_id="nb_1",
        open_notebook_source_id="open_url",
    )
    indexer = SourceStructureIndexer(store=structure_store)

    missing_file_structure = indexer.rebuild_structure(file_record)
    url_structure = indexer.rebuild_structure(url_record)

    assert missing_file_structure.status == "linear_only"
    assert missing_file_structure.metadata["missing_local_source_path"] is True
    assert "重新导入" in missing_file_structure.warnings[0]
    assert "重新导入" in url_structure.warnings[0]
    assert missing_file_structure.strategy == "linear_text"
    assert url_structure.strategy == "linear_text"


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


def test_source_structure_indexer_merges_shallow_pdf_outline_with_all_toc_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "merged-toc.pdf"
    _write_pdf_with_shallow_outline(pdf_path)
    monkeypatch.setattr(
        pdf_toc_parser,
        "extract_pdf_pages_layout",
        lambda *_args, **_kwargs: [
            _toc_layout_page(
                1,
                [
                    ("1 Intro", 1, 0.90, 0.20),
                    ("1.1 Details", 1, 0.84, 0.24),
                    ("1.2 More details", 2, 0.78, 0.24),
                    ("2 Next", 3, 0.72, 0.20),
                ],
            )
        ],
    )
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name=pdf_path.name, mime_type="application/pdf", path=pdf_path)
    store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.strategy == "pdf_merged_toc"
    assert structure.metadata["ocr_toc_node_count"] == 4
    assert view is not None
    assert [chapter.normalized_number for chapter in view.chapters] == ["", "1", "1.1", "1.2", "2"]
    chapter_one = next(chapter for chapter in view.chapters if chapter.normalized_number == "1")
    subsection_titles = [
        chapter.title for chapter in view.chapters if chapter.parent_id == chapter_one.id
    ]
    assert subsection_titles == ["1.1 Details", "1.2 More details"]
    assert all(chapter.anchor_status == "verified" for chapter in view.chapters)
    assert next(chapter for chapter in view.chapters if chapter.normalized_number == "1.2").page_start == 3


def test_source_structure_indexer_persists_unverified_toc_nodes_without_exposing_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf_path = tmp_path / "candidate-toc.pdf"
    _write_pdf_with_single_outline_anchor(pdf_path)
    monkeypatch.setattr(
        pdf_toc_parser,
        "extract_pdf_pages_layout",
        lambda *_args, **_kwargs: [
            _toc_layout_page(
                1,
                [
                    ("1 Intro", 1, 0.90, 0.20),
                    ("1.1 Candidate", 2, 0.82, 0.24),
                ],
            )
        ],
    )
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name=pdf_path.name, mime_type="application/pdf", path=pdf_path)
    store.save_source(record)

    structure = SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    view = structure_store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert view is not None
    candidate = next(chapter for chapter in view.chapters if chapter.normalized_number == "1.1")
    assert candidate.anchor_status == "unverified"
    assert candidate.body_start_offset is None
    assert structure_store.chapter_evidence_by_number(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        normalized_number="1.1",
        limit=1,
        token_budget=1000,
    ) == []


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


def test_resource_resolver_uses_top_level_order_for_unnumbered_epub_chapters(tmp_path: Path) -> None:
    epub_path = tmp_path / "unnumbered-guide.epub"
    _write_epub_with_unnumbered_nav(epub_path)
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name=epub_path.name, mime_type="application/epub+zip", path=epub_path)
    store.save_source(record)
    SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    numbered_path = tmp_path / "numbered-reference.md"
    numbered_path.write_text("# 1 Numbered topic\n\nNumbered topic body.", encoding="utf-8")
    numbered_record = _source_record(
        tmp_path,
        file_name=numbered_path.name,
        mime_type="text/markdown",
        path=numbered_path,
    )
    store.save_source(numbered_record)
    SourceStructureIndexer(store=structure_store).rebuild_structure(numbered_record)
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="请结合 unnumbered guide 的第 1 章讲解",
        board_task=BoardTaskRequirementSheet(requested_action="explain", question_or_topic="第 1 章", progress=100),
        purpose="board_explain",
    )

    assert bundle is not None
    assert bundle.evidence_items[0].section_path[-1] == "First topic"
    assert bundle.evidence_items[0].metadata["requested_chapter_number"] == "1"
    assert bundle.metadata["source_reference_resolution"]["matched_rules"] == [
        "unnumbered_top_level_ordinal"
    ]


def test_resource_resolver_covers_all_direct_sections_for_a_chapter_scope(tmp_path: Path) -> None:
    markdown_path = tmp_path / "chapter-scope.md"
    markdown_path.write_text(
        "# 4 Whole Chapter\n\nChapter introduction.\n\n"
        "## 4.1 First Section\n\nFirst section body.\n\n"
        "## 4.2 Second Section\n\nSecond section body.\n\n"
        "## 4.3 Third Section\n\nThird section body.",
        encoding="utf-8",
    )
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _source_record(tmp_path, file_name=markdown_path.name, mime_type="text/markdown", path=markdown_path)
    store.save_source(record)
    SourceStructureIndexer(store=structure_store).rebuild_structure(record)
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="结合资料讲第4章",
        board_task=BoardTaskRequirementSheet(requested_action="explain", question_or_topic="第4章", progress=100),
        purpose="board_explain",
    )

    assert bundle is not None
    assert [item.metadata["scope_kind"] for item in bundle.evidence_items] == ["chapter", "chapter", "chapter"]
    assert [item.metadata["scope_chapter_number"] for item in bundle.evidence_items] == ["4", "4", "4"]
    assert [item.section_path[-1] for item in bundle.evidence_items] == [
        "4.1 First Section",
        "4.2 Second Section",
        "4.3 Third Section",
    ]
    assert [item.expanded_text for item in bundle.evidence_items] == [
        "## 4.1 First Section\n\nFirst section body.",
        "## 4.2 Second Section\n\nSecond section body.",
        "## 4.3 Third Section\n\nThird section body.",
    ]
    assert bundle.metadata["source_reference_resolution"]["scope_coverage"] == "all_direct_sections"


def test_resource_resolver_uses_explicit_source_chapter_reference(tmp_path: Path) -> None:
    target_path = tmp_path / "target.md"
    other_path = tmp_path / "other.md"
    target_path.write_text("# 7.7.3 Target Chapter\n\nTarget source body.", encoding="utf-8")
    other_path.write_text("# 7.7.3 Other Chapter\n\nOther source body.", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    store.upsert_notebook(owner_user_id="user_1", package_id="pkg_1", notebook_id="nb_1", title="资料容器")
    target = _source_record(tmp_path, file_name="target.md", mime_type="text/markdown", path=target_path)
    other = _source_record(tmp_path, file_name="other.md", mime_type="text/markdown", path=other_path)
    store.save_source(target)
    store.save_source(other)
    indexer = SourceStructureIndexer(store=structure_store)
    indexer.rebuild_structure(target)
    indexer.rebuild_structure(other)
    target_view = structure_store.get_structure_view(source=target)
    assert target_view is not None
    target_chapter = target_view.chapters[0]
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)

    request = ChatRequest(
        message="请讲解这一章。",
        selection=SelectionRef(
            kind="source",
            excerpt=f"《{target.title}》 · {target_chapter.title}",
            heading_path=target_chapter.path,
            source_ingestion_id=target.id,
            source_title=target.title,
            source_chapter_id=target_chapter.id,
            source_chapter_number=target_chapter.number,
            source_chapter_title=target_chapter.title,
        ),
    )
    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message=source_aware_user_message(request, include_locator=True),
        board_task=BoardTaskRequirementSheet(requested_action="explain", question_or_topic="这一节", progress=100),
        purpose="board_explain",
    )

    assert bundle is not None
    assert bundle.evidence_items[0].chapter_id == target_chapter.id
    assert "Target source body" in bundle.evidence_items[0].expanded_text
    assert "Other source body" not in bundle.evidence_items[0].expanded_text


def test_resource_resolver_rebinds_a_stale_source_selection_within_its_original_source(tmp_path: Path) -> None:
    target_path = tmp_path / "target.md"
    other_path = tmp_path / "other.md"
    target_path.write_text("# 7.7.3 Target Chapter\n\nTarget source body.", encoding="utf-8")
    other_path.write_text("# 7.7.3 Target Chapter\n\nOther source body.", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    target = _source_record(tmp_path, file_name=target_path.name, mime_type="text/markdown", path=target_path)
    other = _source_record(tmp_path, file_name=other_path.name, mime_type="text/markdown", path=other_path)
    store.save_source(target)
    store.save_source(other)
    indexer = SourceStructureIndexer(store=structure_store)
    indexer.rebuild_structure(target)
    indexer.rebuild_structure(other)
    target_chapter = structure_store.get_structure_view(source=target).chapters[0]
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)
    selection = SelectionRef(
        kind="source",
        excerpt=f"《{target.title}》 · {target_chapter.title}",
        heading_path=target_chapter.path,
        source_ingestion_id=target.id,
        source_title=target.title,
        source_chapter_id="sourcechapter_stale_selection",
        source_chapter_number=target_chapter.number,
        source_chapter_title=target_chapter.title,
    )
    request = ChatRequest(message="请讲解这一章。", selection=selection)

    outcome = resolver.preview_for_learning_requirement(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message=source_aware_user_message(request, include_locator=True),
        requirements=None,
        source_ingestion_ids=[target.id],
        source_reference=selection,
    )

    assert outcome.status == "matched"
    assert outcome.evidence_bundle is not None
    assert outcome.evidence_bundle.evidence_items[0].chapter_id == target_chapter.id
    assert "Target source body" in outcome.evidence_bundle.evidence_items[0].expanded_text
    assert "Other source body" not in outcome.evidence_bundle.evidence_items[0].expanded_text
    resolution = outcome.metadata or {}
    assert resolution["matched_rules"] == ["stale_source_chapter_selection_rebound"]
    assert resolution["requested_chapter_id"] == "sourcechapter_stale_selection"
    assert resolution["resolved_chapter_id"] == target_chapter.id
    assert resolution["rebound_anchors"] == ["chapter_number", "chapter_title", "heading_path"]


def test_resource_resolver_does_not_guess_when_a_stale_selection_lacks_strong_anchors(tmp_path: Path) -> None:
    source_path = tmp_path / "target.md"
    source_path.write_text("# 7.7.3 Target Chapter\n\nTarget source body.", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    source = _source_record(tmp_path, file_name=source_path.name, mime_type="text/markdown", path=source_path)
    store.save_source(source)
    SourceStructureIndexer(store=structure_store).rebuild_structure(source)
    target_chapter = structure_store.get_structure_view(source=source).chapters[0]
    resolver = ResourceResolver(adapter=_NoSearchAdapter(), store=store, structure_store=structure_store)
    selection = SelectionRef(
        kind="source",
        excerpt=f"《{source.title}》 · {target_chapter.title}",
        source_ingestion_id=source.id,
        source_title=source.title,
        source_chapter_id="sourcechapter_stale_selection",
        source_chapter_number=target_chapter.number,
    )

    outcome = resolver.preview_for_learning_requirement(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message=source_aware_user_message(
            ChatRequest(message="请讲解这一章。", selection=selection),
            include_locator=True,
        ),
        requirements=None,
        source_ingestion_ids=[source.id],
        source_reference=selection,
    )

    assert outcome.status == "no_match"
    assert outcome.evidence_bundle is None


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


def _write_epub_with_unnumbered_nav(path: Path) -> None:
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
                <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>
                <item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine>
                <itemref idref="first"/>
                <itemref idref="second"/>
              </spine>
            </package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html><body><nav epub:type="toc"><ol>
              <li><a href="first.xhtml">First topic</a></li>
              <li><a href="second.xhtml">Second topic</a></li>
            </ol></nav></body></html>""",
        )
        archive.writestr("OEBPS/first.xhtml", "<html><body><h1>First topic</h1><p>First topic body.</p></body></html>")
        archive.writestr("OEBPS/second.xhtml", "<html><body><h1>Second topic</h1><p>Second topic body.</p></body></html>")


def _write_pdf_with_toc(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "Contents")
    pdf.drawString(72, 690, "1 Intro ........ 2")
    pdf.showPage()
    pdf.drawString(72, 720, "1 Intro")
    pdf.drawString(72, 690, "Body text for this section.")
    pdf.save()


def _write_pdf_with_shallow_outline(path: Path) -> None:
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
    pdf.drawString(72, 720, "1.2 More details")
    pdf.showPage()
    pdf.bookmarkPage("chapter-2")
    pdf.addOutlineEntry("2 Next", "chapter-2", level=0)
    pdf.drawString(72, 720, "2 Next")
    pdf.save()


def _write_pdf_with_single_outline_anchor(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.bookmarkPage("toc")
    pdf.addOutlineEntry("Contents", "toc", level=0)
    pdf.drawString(72, 720, "Contents")
    pdf.showPage()
    pdf.bookmarkPage("chapter-1")
    pdf.addOutlineEntry("1 Intro", "chapter-1", level=0)
    pdf.drawString(72, 720, "1 Intro")
    pdf.showPage()
    pdf.drawString(72, 720, "1.1 Candidate")
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
