from __future__ import annotations

import threading

from app.models import CoursePackage, SourceStructure
from app.services import workspace_state
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionJobStore
from app.services.source_ingestion_service import SourceIngestionService
from app.services.source_structure_store import SourceStructureStore


def test_file_ingestion_exposes_durable_progress_while_indexing(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)
    reached_page_scan = threading.Event()
    allow_completion = threading.Event()

    class BlockingIndexer:
        def ensure_structure(self, record, *, progress_callback=None):
            assert progress_callback is not None
            progress_callback("reading_pages", 47)
            reached_page_scan.set()
            assert allow_completion.wait(timeout=5)
            progress_callback("extracting_visuals", 82)
            return structure_store.save_structure_bundle(
                structure=SourceStructure(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                    status="linear_only",
                    strategy="linear_text",
                ),
                chapters=[],
                chunks=[],
            )

    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    service = SourceIngestionService(
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=BlockingIndexer(),
    )
    package = CoursePackage(id="course_progress", title="Progress", summary="", lessons=[])

    queued = service.queue_file_source(
        owner_user_id="user_progress",
        package=package,
        file_name="source.md",
        content=b"# Source\n\nBody",
        mime_type="text/markdown",
    )
    assert queued.status == "parsing"
    assert queued.ingestion_job is not None
    assert queued.ingestion_job.progress == 15

    worker = threading.Thread(
        target=service.process_file_source,
        kwargs={
            "owner_user_id": "user_progress",
            "package_id": package.id,
            "source_id": queued.id,
        },
    )
    worker.start()
    assert reached_page_scan.wait(timeout=5)

    processing = service.list_sources(owner_user_id="user_progress", package_id=package.id)[0]
    assert processing.status == "parsing"
    assert processing.ingestion_job is not None
    assert processing.ingestion_job.progress == 47
    assert processing.ingestion_job.phase_history[-1] == "reading_pages"

    allow_completion.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    completed = service.list_sources(owner_user_id="user_progress", package_id=package.id)[0]
    assert completed.status == "ready"
    assert completed.ingestion_job is not None
    assert completed.ingestion_job.progress == 100
    assert completed.ingestion_job.phase_history[-1] == "ready"
