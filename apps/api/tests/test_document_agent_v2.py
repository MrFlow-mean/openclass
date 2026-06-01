import sqlite3

from reportlab.pdfgen import canvas

from app.models import ResourceLibraryItem
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.document_indexer import enqueue_resource_index, index_resource_now
from app.services.document_locator import locate_document_evidence
from app.services.image_ocr import PdfOcrPageResult
from app.services.resource_service import build_queued_resource


def _write_pdf(path, pages: list[list[str]]) -> None:
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 760
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 24
        pdf.showPage()
    pdf.save()


def _seed_resource(db_path, source_path, filename: str) -> ResourceLibraryItem:
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    resource = build_queued_resource(source_path, filename)
    workspace.packages[0].resources.append(resource)
    store.save(workspace)
    enqueue_resource_index(db_path, resource.id)
    return resource


def _load_resource(db_path, resource_id: str) -> ResourceLibraryItem:
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = store.load()
    return next(resource for package in workspace.packages for resource in package.resources if resource.id == resource_id)


def test_document_indexer_builds_pdf_blocks_and_locator_uses_toc_page_anchor(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    pdf_path = tmp_path / "ml.pdf"
    _write_pdf(
        pdf_path,
        [
            ["Cover"],
            ["Contents", "1.1 Target Topic ................ 3"],
            ["Intro actual page / printed page 1", "1"],
            ["Bridge actual page / printed page 2", "2"],
            ["1.1 Target Topic", "The target body evidence is here.", "3"],
        ],
    )
    resource = _seed_resource(db_path, pdf_path, "ml.pdf")

    index_resource_now(db_path, resource.id)
    indexed = _load_resource(db_path, resource.id)
    evidence = locate_document_evidence(db_path, resources=[indexed], query="呈现 1.1 Target Topic")

    assert indexed.index_status == "ready"
    assert indexed.page_count == 5
    assert indexed.indexed_block_count > 0
    assert evidence
    assert evidence[0].page_range == "5"
    assert "target body evidence" in evidence[0].excerpt
    assert any("目录候选" in line for line in evidence[0].trace)


def test_document_indexer_locates_short_pdf_without_toc(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    pdf_path = tmp_path / "short.pdf"
    _write_pdf(
        pdf_path,
        [
            ["Short document"],
            ["The important gradient descent paragraph appears here."],
            ["More supporting body."],
        ],
    )
    resource = _seed_resource(db_path, pdf_path, "short.pdf")

    index_resource_now(db_path, resource.id)
    indexed = _load_resource(db_path, resource.id)
    evidence = locate_document_evidence(db_path, resources=[indexed], query="找到 gradient descent 的内容")

    assert indexed.index_status == "ready"
    assert evidence
    assert evidence[0].page_range == "2"


def test_document_indexer_uses_ocr_for_scanned_pdf(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    pdf_path = tmp_path / "scan.pdf"
    _write_pdf(pdf_path, [[], []])
    resource = _seed_resource(db_path, pdf_path, "scan.pdf")

    def fake_ocr(path, *, page_start=1, page_end=None, max_pages=80, page_timeout=90):
        return [PdfOcrPageResult(page_number=1, text="OCR recovered scan evidence.", status="text")]

    monkeypatch.setattr("app.services.document_indexer.extract_pdf_page_texts", fake_ocr)

    index_resource_now(db_path, resource.id)
    indexed = _load_resource(db_path, resource.id)
    evidence = locate_document_evidence(db_path, resources=[indexed], query="scan evidence")

    assert indexed.index_status == "ready"
    assert indexed.page_count == 1
    assert evidence[0].text_source == "ocr"


def test_clear_legacy_resources_dry_run_and_apply_backup(tmp_path) -> None:
    from scripts.clear_legacy_resources import clear_resources

    db_path = tmp_path / "openclass.sqlite3"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    source = upload_dir / "old.txt"
    source.write_text("old resource", encoding="utf-8")
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    workspace.packages[0].resources.append(build_queued_resource(source, "old.txt"))
    store.save(workspace)

    dry_run = clear_resources(db_path, upload_dir, apply=False)
    assert dry_run["resource_count"] == 1
    assert source.exists()

    applied = clear_resources(db_path, upload_dir, apply=True)
    assert applied["database_backup"]
    assert applied["upload_backup"]
    assert not source.exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0


def test_legacy_ready_resource_without_v2_blocks_is_queued_on_load(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    source = tmp_path / "legacy.txt"
    source.write_text("legacy source body", encoding="utf-8")
    store = SqliteCourseStore(db_path, legacy_json_path=None)
    workspace = build_initial_workspace_state()
    legacy = ResourceLibraryItem(
        name="legacy.txt",
        mime_type="text/plain",
        resource_type="document",
        size_bytes=source.stat().st_size,
        source_path=str(source),
        index_status="ready",
        indexed_block_count=0,
        page_count=0,
    )
    workspace.packages[0].resources.append(legacy)
    store.save(workspace)

    reloaded = store.load()
    resource = reloaded.packages[0].resources[0]

    assert resource.index_status == "queued"
    assert "重建 v2" in resource.index_message
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM resource_index_jobs WHERE resource_id = ?",
            (resource.id,),
        ).fetchone()
    assert row[0] == "queued"
