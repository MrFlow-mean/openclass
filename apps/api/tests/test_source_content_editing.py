from __future__ import annotations

from pathlib import Path

import pytest

from app.models import CoursePackage
from app.services import workspace_state
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionJobStore
from app.services.source_ingestion_service import (
    SourceImportAutomationOutcome,
    SourceIngestionError,
    SourceIngestionService,
    source_download_path,
)
from app.services.source_structure_store import SourceStructureStore


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SourceIngestionService:
    database_path = tmp_path / "openclass.sqlite3"
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    return SourceIngestionService(
        source_backend="native",
        store=SourceEvidenceStore(database_path),
        job_store=SourceIngestionJobStore(database_path),
        structure_store=SourceStructureStore(database_path),
        import_automation_runner=lambda **_kwargs: SourceImportAutomationOutcome(artifact_ids=[], errors=[]),
    )


def test_editing_extracted_content_reindexes_and_preserves_original_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, monkeypatch)
    package = CoursePackage(title="Research workspace", summary="", lessons=[])
    original = b"<h1>Original</h1><p>Original searchable evidence.</p>"
    source = service.add_file_source(
        owner_user_id="user_edit",
        package=package,
        file_name="source.html",
        content=original,
        mime_type="text/html",
    )

    updated = service.update_source_content(
        owner_user_id="user_edit",
        package_id=package.id,
        source_id=source.id,
        content="# Revised\n\nReplacement searchable evidence.",
    )

    assert updated is not None
    assert updated.status == "ready"
    assert updated.mime_type == "text/markdown"
    assert updated.metadata["content_edited"] is True
    content_result = service.source_content(
        owner_user_id="user_edit",
        package_id=package.id,
        source_id=source.id,
    )
    assert content_result is not None
    assert "Replacement searchable evidence" in content_result[1]
    evidence = service.structure_store.chunk_evidence_search(
        owner_user_id="user_edit",
        package_id=package.id,
        query="replacement searchable evidence",
        limit=5,
        token_budget=2000,
        source_ingestion_ids=[source.id],
    )
    assert evidence
    assert "Replacement searchable evidence" in evidence[0].expanded_text
    original_path = source_download_path(updated)
    assert original_path is not None
    assert original_path.read_bytes() == original


def test_editing_source_content_rejects_empty_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)
    package = CoursePackage(title="Research workspace", summary="", lessons=[])
    source = service.add_text_source(
        owner_user_id="user_edit",
        package=package,
        text="Original text",
    )

    with pytest.raises(SourceIngestionError, match="cannot be empty"):
        service.update_source_content(
            owner_user_id="user_edit",
            package_id=package.id,
            source_id=source.id,
            content="   ",
        )
