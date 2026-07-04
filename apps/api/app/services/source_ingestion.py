from __future__ import annotations

from app.models import (
    ResourceLibraryItem,
    ResourceSourceType,
    SourceIngestionJob,
    SourceIngestionStatus,
    now_iso,
)


def create_source_ingestion_job(
    *,
    source_type: ResourceSourceType,
    source_uri: str | None,
    adapter: str,
    resource_id: str | None = None,
    status: SourceIngestionStatus = "queued",
    progress: int = 0,
    error: str = "",
    phase_history: list[str] | None = None,
) -> SourceIngestionJob:
    return SourceIngestionJob(
        resource_id=resource_id,
        source_type=source_type,
        source_uri=source_uri,
        adapter=adapter,
        status=status,
        progress=progress,
        error=error,
        phase_history=list(phase_history or [status]),
        updated_at=now_iso(),
    )


def apply_ingestion_state(
    resource: ResourceLibraryItem,
    *,
    source_type: ResourceSourceType,
    source_uri: str | None,
    adapter: str,
    status: SourceIngestionStatus,
    progress: int,
    error: str = "",
    phase_history: list[str] | None = None,
) -> ResourceLibraryItem:
    job = create_source_ingestion_job(
        resource_id=resource.id,
        source_type=source_type,
        source_uri=source_uri,
        adapter=adapter,
        status=status,
        progress=progress,
        error=error,
        phase_history=phase_history,
    )
    resource.source_type = source_type
    resource.source_uri = source_uri
    resource.ingestion_status = status
    resource.ingestion_error = error
    resource.ingestion_progress = progress
    resource.ingestion_adapter = adapter
    resource.ingestion_job = job
    return resource


def mark_local_file_ready(resource: ResourceLibraryItem, source_uri: str | None) -> ResourceLibraryItem:
    return apply_ingestion_state(
        resource,
        source_type="local_file",
        source_uri=source_uri,
        adapter=resource.parser_provider or "native",
        status="ready",
        progress=100,
        phase_history=["queued", "parsing", "indexing", "ready"],
    )
