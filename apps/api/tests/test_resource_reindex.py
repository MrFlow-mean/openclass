import sqlite3
import subprocess

import pytest
from reportlab.pdfgen import canvas

from app.services import resource_parser
from app.models import LibraryChapter, ResourceLibraryItem
from app.services.image_ocr import PdfOcrPageResult
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.resource_library import build_resource_item
from app.services.resource_reindex import ResourceReindexOptions, reindex_resources


def _clear_resource_index(db_path, resource_id: str, *, extracted_text_available: bool = False) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM resource_segment_embeddings WHERE resource_id = ?", (resource_id,))
        conn.execute("DELETE FROM resource_segments WHERE resource_id = ?", (resource_id,))
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resource_segments_fts'"
        ).fetchone():
            conn.execute("DELETE FROM resource_segments_fts WHERE resource_id = ?", (resource_id,))
        conn.execute(
            "UPDATE resources SET extracted_text_available = ? WHERE id = ?",
            (int(extracted_text_available), resource_id),
        )


def _seed_markdown_resource(db_path, tmp_path, *, filename: str, body: str):
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / filename
    resource_path.write_text(body, encoding="utf-8")
    resource = build_resource_item(resource_path, filename)
    package.resources.append(resource)
    store.save(workspace)
    return workspace, package, resource


def _write_blank_pdf(path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.showPage()
    pdf.save()


def _write_pdf_pages(path, pages: list[list[str]]) -> None:
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 760
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()


def _seed_legacy_pdf_resource(db_path, tmp_path, *, filename: str = "scan.pdf", pages: int = 3):
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / filename
    pdf = canvas.Canvas(str(resource_path))
    for _ in range(pages):
        pdf.showPage()
    pdf.save()
    resource = ResourceLibraryItem(
        name=filename,
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=resource_path.stat().st_size,
        outline=[
            LibraryChapter(
                title=filename.removesuffix(".pdf"),
                summary="Legacy scanned PDF entry.",
                locator_hint=filename,
                order_index=0,
            )
        ],
        extracted_text_available=False,
        source_path=str(resource_path),
    )
    package.resources.append(resource)
    store.save(workspace)
    _clear_resource_index(db_path, resource.id)
    return resource, resource_path


def test_resource_reindex_dry_run_reports_rebuild_without_writing(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    _, _, resource = _seed_markdown_resource(
        db_path,
        tmp_path,
        filename="notes.md",
        body="# Resource Heading\nTarget evidence from the uploaded source.",
    )
    _clear_resource_index(db_path, resource.id)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path))

    assert report.dry_run is True
    assert report.scanned_count == 1
    assert report.rebuildable_count == 1
    assert report.applied_count == 0
    assert report.resources[0].status == "would_reindex"
    assert report.resources[0].old_segment_count == 0
    assert report.resources[0].new_segment_count > 0

    with sqlite3.connect(db_path) as conn:
        segment_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]
        text_available = conn.execute(
            "SELECT extracted_text_available FROM resources WHERE id = ?",
            (resource.id,),
        ).fetchone()[0]
    assert segment_count == 0
    assert text_available == 0


def test_resource_reindex_apply_rebuilds_segments_fts_and_preserves_chapter_id(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    _, _, resource = _seed_markdown_resource(
        db_path,
        tmp_path,
        filename="source.md",
        body="# Stable Heading\nThe rebuilt index should contain this evidence.",
    )
    old_chapter_id = resource.outline[0].id
    _clear_resource_index(db_path, resource.id)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, apply=True))

    assert report.backup_path
    assert report.resources[0].status == "reindexed"
    assert report.resources[0].applied is True
    assert report.resources[0].new_extracted_text_available is True

    with sqlite3.connect(db_path) as conn:
        chapter_id = conn.execute(
            "SELECT id FROM resource_chapters WHERE resource_id = ? ORDER BY sort_order LIMIT 1",
            (resource.id,),
        ).fetchone()[0]
        segment_rows = conn.execute(
            "SELECT segment_id, text FROM resource_segments WHERE resource_id = ?",
            (resource.id,),
        ).fetchall()
        fts_rows = conn.execute(
            "SELECT segment_id FROM resource_segments_fts WHERE resource_id = ?",
            (resource.id,),
        ).fetchall()

    assert chapter_id == old_chapter_id
    assert any("rebuilt index" in row[1] for row in segment_rows)
    assert [row[0] for row in fts_rows] == [row[0] for row in segment_rows]


def test_resource_reindex_marks_readable_resource_without_text_as_unavailable(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / "blank.pdf"
    _write_blank_pdf(resource_path)
    resource = ResourceLibraryItem(
        name="blank.pdf",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=resource_path.stat().st_size,
        outline=[
            LibraryChapter(
                title="Blank Resource",
                summary="Legacy metadata-only resource.",
                locator_hint="Blank Resource",
                order_index=0,
            )
        ],
        extracted_text_available=True,
        source_path=str(resource_path),
    )
    package.resources.append(resource)
    store.save(workspace)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, apply=True))

    assert report.resources[0].status == "marked_no_text"
    assert report.still_missing_text_count == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT extracted_text_available FROM resources WHERE id = ?",
            (resource.id,),
        ).fetchone()
        segment_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]
    assert row[0] == 0
    assert segment_count == 0


def test_resource_reindex_reports_missing_source_without_mutating_resource(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource = ResourceLibraryItem(
        name="missing.md",
        mime_type="text/markdown",
        resource_type="document",
        size_bytes=12,
        outline=[
            LibraryChapter(
                title="Missing",
                summary="Legacy missing-source resource.",
                locator_hint="Missing",
                order_index=0,
            )
        ],
        extracted_text_available=True,
        source_path=str(tmp_path / "missing.md"),
    )
    package.resources.append(resource)
    store.save(workspace)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, apply=True))

    assert report.resources[0].status == "missing_source"
    assert report.applied_count == 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT extracted_text_available FROM resources WHERE id = ?",
            (resource.id,),
        ).fetchone()
    assert row[0] == 1


def test_resource_reindex_filters_by_resource_id(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    workspace, package, first = _seed_markdown_resource(
        db_path,
        tmp_path,
        filename="first.md",
        body="# First\nFirst resource evidence.",
    )
    second_path = tmp_path / "second.md"
    second_path.write_text("# Second\nSecond resource evidence.", encoding="utf-8")
    second = build_resource_item(second_path, "second.md")
    package.resources.append(second)
    SqliteCourseStore(db_path, legacy_json_path=None).save(workspace)
    _clear_resource_index(db_path, first.id)
    _clear_resource_index(db_path, second.id)

    report = reindex_resources(
        ResourceReindexOptions(
            database_path=db_path,
            apply=True,
            resource_id=first.id,
        )
    )

    assert [item.resource_id for item in report.resources] == [first.id]
    with sqlite3.connect(db_path) as conn:
        first_segments = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (first.id,),
        ).fetchone()[0]
        second_segments = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (second.id,),
        ).fetchone()[0]
    assert first_segments > 0
    assert second_segments == 0


def test_resource_reindex_rebuilds_short_pdf_with_page_ranges(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / "short.pdf"
    _write_pdf_pages(
        resource_path,
        [
            ["Short PDF page one evidence."],
            ["Short PDF page two evidence."],
        ],
    )
    resource = ResourceLibraryItem(
        name="short.pdf",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=resource_path.stat().st_size,
        outline=[
            LibraryChapter(
                title="short",
                summary="Legacy no-outline PDF entry.",
                locator_hint="short",
                order_index=0,
            )
        ],
        extracted_text_available=False,
        source_path=str(resource_path),
    )
    package.resources.append(resource)
    store.save(workspace)
    _clear_resource_index(db_path, resource.id)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, apply=True))

    assert report.resources[0].status == "reindexed"
    assert report.resources[0].new_extracted_text_available is True
    with sqlite3.connect(db_path) as conn:
        segment_rows = conn.execute(
            """
            SELECT text, page_range
            FROM resource_segments
            WHERE resource_id = ?
            ORDER BY order_index
            """,
            (resource.id,),
        ).fetchall()
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments_fts WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]

    assert [row[1] for row in segment_rows] == ["1", "2"]
    assert any("page two evidence" in row[0] for row in segment_rows)
    assert fts_count == len(segment_rows)


def test_resource_reindex_ocr_dry_run_reports_rebuild_without_writing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    resource, _ = _seed_legacy_pdf_resource(db_path, tmp_path)

    def fake_ocr(*args, **kwargs):
        return [
            PdfOcrPageResult(page_number=1, text="OCR dry-run evidence.", status="text"),
            PdfOcrPageResult(page_number=2, text="", status="empty"),
        ]

    monkeypatch.setattr("app.services.resource_reindex.extract_pdf_page_texts", fake_ocr)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, ocr_pdf=True))

    assert report.dry_run is True
    assert report.ocr_attempted_count == 1
    assert report.ocr_text_page_count == 1
    assert report.resources[0].status == "would_reindex"
    assert report.resources[0].reason.startswith("ocr_segments_rebuilt")
    with sqlite3.connect(db_path) as conn:
        segment_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]
        text_available = conn.execute(
            "SELECT extracted_text_available FROM resources WHERE id = ?",
            (resource.id,),
        ).fetchone()[0]
    assert segment_count == 0
    assert text_available == 0


def test_resource_reindex_ocr_apply_writes_page_segments_and_keeps_page_errors(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    resource, _ = _seed_legacy_pdf_resource(db_path, tmp_path)

    def fake_ocr(*args, **kwargs):
        return [
            PdfOcrPageResult(page_number=1, text="OCR page one evidence.", status="text"),
            PdfOcrPageResult(page_number=2, status="error", error="page failed"),
            PdfOcrPageResult(page_number=3, text="OCR page three evidence.", status="text"),
        ]

    monkeypatch.setattr("app.services.resource_reindex.extract_pdf_page_texts", fake_ocr)

    report = reindex_resources(
        ResourceReindexOptions(
            database_path=db_path,
            apply=True,
            ocr_pdf=True,
        )
    )

    assert report.resources[0].status == "reindexed"
    assert report.resources[0].new_extracted_text_available is True
    assert report.resources[0].ocr_error_page_count == 1
    with sqlite3.connect(db_path) as conn:
        segment_rows = conn.execute(
            """
            SELECT text, page_range, text_source
            FROM resource_segments
            WHERE resource_id = ?
            ORDER BY order_index
            """,
            (resource.id,),
        ).fetchall()
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments_fts WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]
        chapter_row = conn.execute(
            "SELECT locator_hint FROM resource_chapters WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()

    assert [row[1] for row in segment_rows] == ["1", "3"]
    assert {row[2] for row in segment_rows} == {"ocr"}
    assert any("page three evidence" in row[0] for row in segment_rows)
    assert fts_count == len(segment_rows)
    assert "source=pdf_ocr" in chapter_row[0]


def test_resource_reindex_ocr_no_text_keeps_resource_unavailable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    resource, _ = _seed_legacy_pdf_resource(db_path, tmp_path)

    def fake_ocr(*args, **kwargs):
        return [
            PdfOcrPageResult(page_number=1, status="empty"),
            PdfOcrPageResult(page_number=2, status="error", error="not readable"),
        ]

    monkeypatch.setattr("app.services.resource_reindex.extract_pdf_page_texts", fake_ocr)

    report = reindex_resources(
        ResourceReindexOptions(
            database_path=db_path,
            apply=True,
            ocr_pdf=True,
        )
    )

    assert report.resources[0].status == "marked_no_text"
    assert report.resources[0].reason.startswith("ocr_no_text")
    assert report.resources[0].ocr_error_page_count == 1
    with sqlite3.connect(db_path) as conn:
        segment_count = conn.execute(
            "SELECT COUNT(*) FROM resource_segments WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()[0]
        text_available = conn.execute(
            "SELECT extracted_text_available FROM resources WHERE id = ?",
            (resource.id,),
        ).fetchone()[0]
    assert segment_count == 0
    assert text_available == 0


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("timeout", "external_parser_timeout"),
        ("exit", "parser exploded"),
        ("empty", "external_parser_empty_output"),
        ("malformed", "external_parser_malformed_json"),
    ],
)
def test_resource_reindex_reports_parser_failure_and_falls_back_to_native(
    tmp_path,
    monkeypatch,
    failure_kind,
    expected_error,
) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    _, _, resource = _seed_markdown_resource(
        db_path,
        tmp_path,
        filename="parser-fallback.md",
        body="# Native Heading\nNative fallback evidence survives parser failure.",
    )
    _clear_resource_index(db_path, resource.id)
    monkeypatch.setenv("OPENCLASS_RESOURCE_PARSER_COMMAND", "mock-parser")

    def fake_run(command, **kwargs):
        if failure_kind == "timeout":
            raise subprocess.TimeoutExpired(command, timeout=1)
        if failure_kind == "exit":
            raise subprocess.CalledProcessError(2, command, stderr="parser exploded")
        if failure_kind == "empty":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="{not-json", stderr="")

    monkeypatch.setattr(resource_parser.subprocess, "run", fake_run)

    report = reindex_resources(ResourceReindexOptions(database_path=db_path, apply=True))

    assert report.parser_error_count == 1
    assert report.resources[0].parser_status == "failed"
    assert report.resources[0].parser_error == expected_error
    assert report.resources[0].new_extracted_text_available is True
    with sqlite3.connect(db_path) as conn:
        segment_text = conn.execute(
            "SELECT text FROM resource_segments WHERE resource_id = ? ORDER BY order_index LIMIT 1",
            (resource.id,),
        ).fetchone()[0]
    assert "Native fallback evidence" in segment_text
