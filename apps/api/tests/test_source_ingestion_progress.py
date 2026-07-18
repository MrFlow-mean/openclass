from __future__ import annotations

import sqlite3
import threading

from app.models import CoursePackage, SourceIngestionRecord, SourceStructure
from app.services import workspace_state
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionCoordinator, SourceIngestionJobStore
from app.services.source_ingestion_service import SourceIngestionService
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


def test_source_ingestion_uses_codex_catalog_backend(tmp_path) -> None:
    database = tmp_path / "openclass.sqlite3"

    service = SourceIngestionService(
        store=SourceEvidenceStore(database),
        job_store=SourceIngestionJobStore(database),
        structure_store=SourceStructureStore(database),
    )



def test_retry_detaches_legacy_source_state(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    job_store = SourceIngestionJobStore(database)
    upload_dir = tmp_path / "uploads"
    source_dir = upload_dir / "sources"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "scan.pdf"
    source_path.write_bytes(b"local source bytes")
    received_records = []

    class NativeRetryIndexer:
        def rebuild_structure(self, record, *, progress_callback=None):
            received_records.append(record)
            if progress_callback is not None:
                progress_callback("persisting", 94)
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

    record = source_store.save_source(
        SourceIngestionRecord(
            id="source_remote_failed",
            owner_user_id="user_1",
            package_id="course_1",
            title="Scanned source",
            source_type="local_file",
            file_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=source_path.stat().st_size,
            status="failed",
            error="OpenNotebook unavailable",
            open_notebook_notebook_id="notebook:remote",
            open_notebook_source_id="source:remote",
            open_notebook_command_id="command:remote",
            metadata={
                "local_source_path": str(source_path),
                "adapter": "open_notebook",
                "source_processing_owner": "open_notebook",
                "open_notebook_sync_status": "failed",
                "open_notebook_sync_warning": "unavailable",
            },
        )
    )
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", upload_dir)
    service = SourceIngestionService(
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=NativeRetryIndexer(),
    )

    retried = service.retry_source(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert retried is not None
    assert retried.status == "ready"
    assert retried.open_notebook_notebook_id == ""
    assert retried.open_notebook_source_id == ""
    assert retried.open_notebook_command_id == ""
    assert retried.metadata["adapter"] == "codex_source_catalog"
    assert "source_processing_owner" not in retried.metadata
    assert not any(key.startswith("open_notebook_") for key in retried.metadata)
    assert received_records[0].metadata["adapter"] == "codex_source_catalog"
    assert "source_processing_owner" not in received_records[0].metadata
    assert not any(
        key.startswith("open_notebook_") for key in received_records[0].metadata
    )


def test_source_ingestion_coordinator_allows_bounded_parallel_work() -> None:
    coordinator = SourceIngestionCoordinator(processing_capacity=2)
    release_workers = threading.Event()
    two_workers_entered = threading.Event()
    state_lock = threading.Lock()
    active_workers = 0
    max_active_workers = 0

    def run_worker() -> None:
        nonlocal active_workers, max_active_workers
        with coordinator.processing_slot():
            with state_lock:
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
                if active_workers == 2:
                    two_workers_entered.set()
            assert release_workers.wait(timeout=5)
            with state_lock:
                active_workers -= 1

    workers = [threading.Thread(target=run_worker) for _ in range(3)]
    for worker in workers:
        worker.start()

    assert two_workers_entered.wait(timeout=5)
    with state_lock:
        assert active_workers == 2
    assert coordinator.processing_weight(size_bytes=1, source_type="local_file") == 1
    assert coordinator.processing_weight(
        size_bytes=64 * 1024 * 1024,
        source_type="local_file",
    ) == 2

    release_workers.set()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert max_active_workers == 2


def test_source_structure_write_retries_transient_database_lock(tmp_path, monkeypatch) -> None:
    coordinator = SourceIngestionCoordinator(lock_retry_delays=(0.0,))
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3", coordinator=coordinator)
    structure = SourceStructure(
        owner_user_id="user_retry",
        package_id="course_retry",
        source_ingestion_id="source_retry",
        status="linear_only",
        strategy="linear_text",
    )
    original_save = structure_store._save_structure_bundle
    attempts = 0

    def flaky_save(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_save(**kwargs)

    monkeypatch.setattr(structure_store, "_save_structure_bundle", flaky_save)

    saved = structure_store.save_structure_bundle(
        structure=structure,
        chapters=[],
        chunks=[],
    )

    assert attempts == 2
    assert saved.status == "linear_only"
    assert structure_store.get_structure(
        owner_user_id="user_retry",
        package_id="course_retry",
        source_id="source_retry",
    ) is not None


def test_concurrent_file_ingestion_finishes_without_locked_database(tmp_path, monkeypatch) -> None:
    database = tmp_path / "openclass.sqlite3"
    coordinator = SourceIngestionCoordinator(processing_capacity=2, lock_retry_delays=(0.0, 0.0))
    source_store = SourceEvidenceStore(database, coordinator=coordinator)
    structure_store = SourceStructureStore(database, coordinator=coordinator)
    job_store = SourceIngestionJobStore(database, coordinator=coordinator)
    service = SourceIngestionService(
        store=source_store,
        job_store=job_store,
        structure_store=structure_store,
        structure_indexer=SourceStructureIndexer(
            store=structure_store,
            coordinator=coordinator,
        ),
    )
    package = CoursePackage(id="course_concurrent", title="Concurrent", summary="", lessons=[])
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    queued_sources = [
        service.queue_file_source(
            owner_user_id="user_concurrent",
            package=package,
            file_name=f"source-{index}.md",
            content=f"# Source {index}\n\nConcurrent body {index}".encode(),
            mime_type="text/markdown",
        )
        for index in range(4)
    ]
    start_together = threading.Barrier(len(queued_sources) + 1)
    errors: list[Exception] = []

    def process(source_id: str) -> None:
        try:
            start_together.wait(timeout=5)
            service.process_file_source(
                owner_user_id="user_concurrent",
                package_id=package.id,
                source_id=source_id,
            )
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=process, args=(source.id,)) for source in queued_sources]
    for worker in workers:
        worker.start()
    start_together.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()

    assert errors == []
    completed = service.list_sources(owner_user_id="user_concurrent", package_id=package.id)
    assert len(completed) == len(queued_sources)
    assert all(source.status == "ready" for source in completed)
    assert all(source.error == "" for source in completed)


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
