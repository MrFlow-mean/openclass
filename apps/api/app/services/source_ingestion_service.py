from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.models import CoursePackage, SourceIngestionJob, SourceIngestionRecord
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store
from app.services.source_ingestion_jobs import SourceIngestionJobStore, source_ingestion_job_store
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore, source_structure_store
from app.services.source_visual_extraction import CURRENT_SOURCE_VISUAL_INDEX_VERSION
from app.services.source_url_snapshot import SourceUrlSnapshotError, fetch_url_source_snapshot
from app.services.youtube_transcript_adapter import (
    YouTubeTranscriptAdapter,
    YouTubeTranscriptAdapterError,
    is_youtube_url,
    youtube_transcript_adapter,
)
from app.services import workspace_state
from app.services.media_transcription_adapter import MediaTranscriptionError, media_transcription_provider


SUPPORTED_FILE_MIME_PREFIXES = (
    "application/pdf",
    "application/epub+zip",
    "application/vnd.openxmlformats-officedocument",
    "text/",
    "audio/",
    "video/",
    "image/",
)
MAX_SOURCE_UPLOAD_BYTES = 100 * 1024 * 1024


class SourceIngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceImportAutomationOutcome:
    artifact_ids: list[str]
    errors: list[str]


SourceImportAutomationRunner = Callable[..., SourceImportAutomationOutcome]


class SourceIngestionService:
    def __init__(
        self,
        *,
        youtube_adapter: YouTubeTranscriptAdapter = youtube_transcript_adapter,
        store: SourceEvidenceStore = source_evidence_store,
        job_store: SourceIngestionJobStore = source_ingestion_job_store,
        structure_indexer: SourceStructureIndexer | None = None,
        structure_store: SourceStructureStore | None = None,
        import_automation_runner: SourceImportAutomationRunner | None = None,
    ) -> None:
        self.youtube_adapter = youtube_adapter
        self.store = store
        self.job_store = job_store
        self.structure_store = structure_store or _structure_store_for_source_store(store)
        self.structure_indexer = structure_indexer or SourceStructureIndexer(store=self.structure_store)
        self.import_automation_runner = import_automation_runner or _run_research_import_automations

    def list_sources(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionRecord]:
        records = self.store.list_sources(owner_user_id=owner_user_id, package_id=package_id)
        return [self.structure_store.attach_summary(record) for record in records]

    def add_url_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        normalized_uri = _validate_public_url(source_uri)
        if is_youtube_url(normalized_uri):
            return self._save_youtube_transcript_source(
                owner_user_id=owner_user_id,
                package=package,
                source_uri=normalized_uri,
                title=title,
            )
        display_title = title.strip() or normalized_uri
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="web_url",
            source_uri=normalized_uri,
            file_name="",
            mime_type="text/html",
            size_bytes=0,
            status="fetching",
            metadata={"adapter": "openclass_native_url"},
        )
        job = self._start_job(record, adapter="openclass_native_url", phase="fetching", progress=10)
        try:
            snapshot_metadata = fetch_url_source_snapshot(record, normalized_uri)
        except SourceUrlSnapshotError as exc:
            failed = self.store.save_source(self._failed_record(record, str(exc), phase="fetch_url"))
            self._finish_job(job, record=failed, status="failed", progress=100, error=str(exc), phase="failed")
            return failed
        local_path = Path(str(snapshot_metadata.get("local_source_path") or ""))
        size_bytes = local_path.stat().st_size if local_path.is_file() else 0
        ready = record.model_copy(
            update={
                "status": "ready",
                "size_bytes": size_bytes,
                "metadata": {
                    **record.metadata,
                    **snapshot_metadata,
                    "requested_source_uri": normalized_uri,
                    "content_hash": _file_content_hash(local_path),
                    "native_index_version": 1,
                    "source_visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
                },
            }
        )
        return self._save_and_index(ready, job)

    def add_file_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        file_name: str,
        content: bytes,
        mime_type: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        if not file_name.strip():
            raise SourceIngestionError("File name is required.")
        if len(content) > MAX_SOURCE_UPLOAD_BYTES:
            raise SourceIngestionError(
                f"Uploaded source exceeds the {MAX_SOURCE_UPLOAD_BYTES}-byte size limit."
            )
        if not _supported_mime(mime_type, file_name):
            raise SourceIngestionError("This file type is not supported by the native source importer.")
        display_title = title.strip() or file_name
        source_type = _source_type_for_upload(mime_type=mime_type, file_name=file_name)
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type=source_type,
            source_uri=None,
            file_name=file_name,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(content),
            status="parsing",
            metadata={"adapter": "openclass_native"},
        )
        file_metadata = _save_local_source_file(record, content)
        if source_type in {"audio_file", "video_file"}:
            original_path = Path(file_metadata["local_source_path"])
            try:
                transcript = media_transcription_provider.transcribe(original_path, mime_type=mime_type)
            except MediaTranscriptionError as exc:
                failed = self.store.save_source(self._failed_record(record.model_copy(update={"metadata": {**record.metadata, **file_metadata}}), str(exc), phase="transcription"))
                job = self._start_job(failed, adapter="openclass_native_media", phase="transcription", progress=100)
                self._finish_job(job, record=failed, status="failed", progress=100, phase="failed", error=str(exc))
                return failed
            transcript_record = record.model_copy(
                update={"file_name": f"{Path(file_name).stem}-transcript.txt", "mime_type": "text/plain"}
            )
            transcript_metadata = _save_local_source_text(transcript_record, transcript.text)
            file_metadata = {
                **transcript_metadata,
                "original_source_path": str(original_path),
                "original_mime_type": mime_type,
                "transcription_provider": transcript.provider,
                "transcription_model": transcript.model,
                "transcript_language": transcript.language,
            }
        record = record.model_copy(
            update={
                "status": "ready",
                "metadata": {
                    **record.metadata,
                    **file_metadata,
                    "content_hash": hashlib.sha256(content).hexdigest(),
                    "native_index_version": 1,
                    "source_visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
                },
            }
        )
        job = self._start_job(record, adapter="openclass_native", phase="parsing", progress=25)
        return self._save_and_index(record, job)

    def add_text_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        text: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        content = text.strip()
        if not content:
            raise SourceIngestionError("Pasted text is empty.")
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=title.strip() or _text_title(content),
            source_type="pasted_text",
            file_name="pasted-text.md",
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            status="ready",
            metadata={
                "adapter": "openclass_native_text",
                "native_index_version": 1,
                "source_visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
            },
        )
        metadata = _save_local_source_text(record, content)
        record = record.model_copy(
            update={"metadata": {**record.metadata, **metadata, "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}}
        )
        job = self._start_job(record, adapter="openclass_native_text", phase="parsing", progress=25)
        return self._save_and_index(record, job)

    def remove_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        self.structure_store.delete_for_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        removed = self.store.delete_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        self.job_store.delete_for_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        _delete_local_source_file(record)
        return removed

    def rename_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        title: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        normalized = title.strip()
        if record is None or not normalized:
            return None
        return self.structure_store.attach_summary(self.store.save_source(record.model_copy(update={"title": normalized})))

    def update_source_content(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        content: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        normalized = content.strip()
        if not normalized:
            raise SourceIngestionError("Source content cannot be empty.")

        current_path = source_local_path(record)
        metadata = dict(record.metadata)
        is_native_text = record.source_type == "pasted_text"
        if not is_native_text and "original_source_path" not in metadata and current_path is not None:
            metadata["original_source_path"] = str(current_path)
            metadata["original_mime_type"] = record.mime_type

        editable_record = record.model_copy(
            update={
                "file_name": record.file_name if is_native_text else f"{Path(record.file_name or record.id).stem}-edited.md",
                "mime_type": "text/markdown",
                "size_bytes": len(normalized.encode("utf-8")),
                "status": "ready",
                "error": "",
                "metadata": metadata,
            }
        )
        local_metadata = _save_local_source_text(editable_record, normalized)
        editable_record = editable_record.model_copy(
            update={
                "metadata": {
                    **metadata,
                    **local_metadata,
                    "adapter": "openclass_native_text",
                    "content_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    "content_edited": True,
                    "native_index_version": 1,
                    "source_visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
                }
            }
        )
        job = self._start_job(editable_record, adapter="openclass_native_text", phase="parsing", progress=25)
        return self._save_and_index(editable_record, job, rebuild=True)

    def retry_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionRecord | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        local_path = source_local_path(record)
        if local_path is None:
            raise SourceIngestionError("Source content is unavailable; import the source again.")
        retrying = record.model_copy(update={"status": "ready", "error": ""})
        self.store.save_source(retrying)
        job = self._start_job(retrying, adapter=str(record.metadata.get("adapter") or "openclass_native"), phase="parsing", progress=20)
        return self._save_and_index(retrying, job, rebuild=True)

    def list_jobs(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionJob]:
        return self.job_store.list(owner_user_id=owner_user_id, package_id=package_id)

    def source_content(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> tuple[SourceIngestionRecord, str] | None:
        record = self.store.get_source(owner_user_id=owner_user_id, package_id=package_id, source_id=source_id)
        if record is None:
            return None
        path = source_local_path(record)
        if path is None:
            return record, ""
        if record.mime_type.startswith("text/") and record.mime_type != "text/html":
            return record, path.read_text(encoding="utf-8", errors="replace")
        view = self.structure_store.get_structure_view(source=record, chunk_limit=5000)
        return record, "\n\n".join(chunk.text for chunk in view.chunks)

    def _start_job(
        self,
        record: SourceIngestionRecord,
        *,
        adapter: str,
        phase: str,
        progress: int,
    ) -> SourceIngestionJob:
        return self.job_store.save(
            SourceIngestionJob(
                resource_id=record.id,
                source_type=record.source_type,
                source_uri=record.source_uri,
                adapter=adapter,
                status=record.status,
                progress=progress,
                phase_history=[phase],
            ),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )

    def _finish_job(
        self,
        job: SourceIngestionJob,
        *,
        record: SourceIngestionRecord,
        status: str,
        progress: int,
        phase: str,
        error: str = "",
    ) -> SourceIngestionJob:
        updated = job.model_copy(
            update={
                "status": status,
                "progress": progress,
                "error": error,
                "phase_history": [*job.phase_history, phase],
            }
        )
        return self.job_store.save(
            updated,
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )

    def _save_and_index(
        self,
        record: SourceIngestionRecord,
        job: SourceIngestionJob,
        *,
        rebuild: bool = False,
    ) -> SourceIngestionRecord:
        saved = self.store.save_source(record)
        indexing_job = self.job_store.save(
            job.model_copy(update={"status": "indexing", "progress": 55, "phase_history": [*job.phase_history, "indexing"]}),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )
        try:
            structure = (
                self.structure_indexer.rebuild_structure(saved)
                if rebuild
                else self.structure_indexer.ensure_structure(saved)
            )
        except Exception as exc:
            failed = self.store.save_source(saved.model_copy(update={"status": "failed", "error": str(exc)}))
            self.job_store.save(
                indexing_job.model_copy(
                    update={"status": "failed", "progress": 100, "error": str(exc), "phase_history": [*indexing_job.phase_history, "failed"]}
                ),
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
            )
            return self.structure_store.attach_summary(failed)
        final_status = "ready" if structure.status in {"ready", "linear_only"} else "failed"
        error = structure.error if final_status == "failed" else ""
        automation_outcome = SourceImportAutomationOutcome(artifact_ids=[], errors=[])
        if final_status == "ready":
            transforming_job = self.job_store.save(
                indexing_job.model_copy(
                    update={
                        "status": "indexing",
                        "progress": 85,
                        "phase_history": [*indexing_job.phase_history, "transforming"],
                    }
                ),
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
            )
            indexing_job = transforming_job
            try:
                automation_outcome = self.import_automation_runner(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                )
            except Exception as exc:
                automation_outcome = SourceImportAutomationOutcome(
                    artifact_ids=[],
                    errors=[str(exc)],
                )
            if automation_outcome.errors:
                error = "Automatic import transformation failed: " + "; ".join(automation_outcome.errors)
        final_record = self.store.save_source(
            saved.model_copy(
                update={
                    "status": final_status,
                    "error": error,
                    "metadata": {
                        **saved.metadata,
                        "import_transformation_status": (
                            "failed"
                            if automation_outcome.errors
                            else "ready" if automation_outcome.artifact_ids else "not_configured"
                        ),
                        "import_transformation_artifact_ids": automation_outcome.artifact_ids,
                    },
                }
            )
        )
        self.job_store.save(
            indexing_job.model_copy(
                update={
                    "status": final_status,
                    "progress": 100,
                    "error": error,
                    "phase_history": [*indexing_job.phase_history, final_status],
                }
            ),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
        )
        return self.structure_store.attach_summary(final_record)

    def _failed_record(self, record: SourceIngestionRecord, error: str, *, phase: str) -> SourceIngestionRecord:
        return record.model_copy(
            update={
                "status": "failed",
                "error": error,
                "metadata": {
                    **record.metadata,
                    "error_phase": phase,
                },
            }
        )

    def _save_youtube_transcript_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str,
    ) -> SourceIngestionRecord:
        display_title = title.strip() or source_uri
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="video_url",
            source_uri=source_uri,
            file_name="youtube-transcript.txt",
            mime_type="text/plain",
            size_bytes=0,
            status="fetching",
            metadata={"adapter": "youtube_transcript", "media_provider": "youtube", "media_kind": "video"},
        )
        try:
            transcript = self.youtube_adapter.extract(source_uri, title=title)
        except YouTubeTranscriptAdapterError as exc:
            failed = self.store.save_source(self._failed_record(record, str(exc), phase="youtube_transcript"))
            job = self._start_job(failed, adapter="youtube_transcript", phase="transcription", progress=100)
            self._finish_job(job, record=failed, status="failed", progress=100, phase="failed", error=str(exc))
            return failed
        transcript_file_name = _safe_file_name(f"{transcript.video_id or record.id}-transcript.txt")
        record_for_file = record.model_copy(update={"title": transcript.title, "file_name": transcript_file_name})
        transcript_bytes = transcript.text.encode("utf-8")
        ready = record_for_file.model_copy(
            update={
                "status": "ready",
                "error": "",
                "size_bytes": len(transcript_bytes),
                "metadata": {
                    **record_for_file.metadata,
                    **transcript.metadata,
                    **_save_local_source_text(record_for_file, transcript.text),
                    "content_hash": hashlib.sha256(transcript_bytes).hexdigest(),
                    "native_index_version": 1,
                    "source_visual_index_version": CURRENT_SOURCE_VISUAL_INDEX_VERSION,
                },
            }
        )
        job = self._start_job(ready, adapter="youtube_transcript", phase="parsing", progress=25)
        return self._save_and_index(ready, job)


def _supported_mime(mime_type: str, file_name: str) -> bool:
    lowered = file_name.lower()
    if lowered.endswith(
        (
            ".pdf", ".epub", ".docx", ".pptx", ".xlsx", ".csv",
            ".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml",
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".m4a", ".wav",
            ".ogg", ".mp4", ".mov", ".webm", ".mpeg",
        )
    ):
        return True
    return any((mime_type or "").startswith(prefix) for prefix in SUPPORTED_FILE_MIME_PREFIXES)


def _save_local_source_file(record: SourceIngestionRecord, content: bytes) -> dict[str, str]:
    safe_name = _safe_file_name(record.file_name or record.id)
    source_dir = workspace_state.UPLOAD_DIR / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / f"{record.id}_{safe_name}"
    path.write_bytes(content)
    return {"local_source_path": str(path)}


def _save_local_source_text(record: SourceIngestionRecord, text: str) -> dict[str, str]:
    return _save_local_source_file(record, text.encode("utf-8"))


def source_local_path(record: SourceIngestionRecord) -> Path | None:
    raw_path = record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
    if allowed_root not in path.parents or not path.is_file():
        return None
    return path


def source_download_path(record: SourceIngestionRecord) -> Path | None:
    raw_path = record.metadata.get("original_source_path") or record.metadata.get("local_source_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
    return path if allowed_root in path.parents and path.is_file() else None


def _delete_local_source_file(record: SourceIngestionRecord) -> None:
    paths = [source_local_path(record)]
    raw_original = record.metadata.get("original_source_path")
    if isinstance(raw_original, str) and raw_original.strip():
        candidate = Path(raw_original).expanduser().resolve()
        allowed_root = (workspace_state.UPLOAD_DIR / "sources").resolve()
        if allowed_root in candidate.parents and candidate.is_file():
            paths.append(candidate)
    for path in dict.fromkeys(item for item in paths if item is not None):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _file_content_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_type_for_upload(*, mime_type: str, file_name: str) -> str:
    normalized_mime = (mime_type or "").lower()
    suffix = Path(file_name).suffix.lower()
    if normalized_mime.startswith("audio/") or suffix in {".mp3", ".m4a", ".wav", ".ogg"}:
        return "audio_file"
    if normalized_mime.startswith("video/") or suffix in {".mp4", ".mov", ".webm", ".mpeg"}:
        return "video_file"
    return "local_file"


def _text_title(text: str, limit: int = 80) -> str:
    first_line = next((line.strip("# *\t") for line in text.splitlines() if line.strip()), "")
    return first_line[:limit] or "Pasted text"


def _safe_file_name(file_name: str) -> str:
    name = Path(file_name).name.strip() or "source"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:180]


def _structure_store_for_source_store(store: SourceEvidenceStore) -> SourceStructureStore:
    if getattr(store, "_path", None) is None:
        return source_structure_store
    return SourceStructureStore(store.path)


def _run_research_import_automations(
    *,
    owner_user_id: str,
    package_id: str,
    source_ingestion_id: str,
) -> SourceImportAutomationOutcome:
    from app.services.research_workspace import research_workspace_service

    result = research_workspace_service.run_import_transformations(
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_ingestion_id=source_ingestion_id,
    )
    return SourceImportAutomationOutcome(
        artifact_ids=[artifact.id for artifact in result.artifacts],
        errors=result.errors,
    )


def _validate_public_url(raw_uri: str) -> str:
    uri = raw_uri.strip()
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceIngestionError("Only http and https URLs are supported.")
    hostname = parsed.hostname or ""
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SourceIngestionError("Localhost URLs are not allowed for source ingestion.")
    try:
        for info in socket.getaddrinfo(hostname, None):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local:
                raise SourceIngestionError("Private network URLs are not allowed for source ingestion.")
    except socket.gaierror as exc:
        raise SourceIngestionError("URL hostname could not be resolved.") from exc
    return uri


source_ingestion_service = SourceIngestionService()
