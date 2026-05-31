import sqlite3

from reportlab.pdfgen import canvas

from app.models import LibraryChapter, ResourceLibraryItem
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
