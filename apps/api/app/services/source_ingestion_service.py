from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.models import CoursePackage, SourceIngestionRecord, now_iso
from app.services.open_notebook_adapter import (
    OpenNotebookAdapter,
    OpenNotebookAdapterError,
    open_notebook_adapter,
)
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store


SUPPORTED_FILE_MIME_PREFIXES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument",
    "application/msword",
    "text/",
)


class SourceIngestionError(RuntimeError):
    pass


class SourceIngestionService:
    def __init__(
        self,
        *,
        adapter: OpenNotebookAdapter = open_notebook_adapter,
        store: SourceEvidenceStore = source_evidence_store,
    ) -> None:
        self.adapter = adapter
        self.store = store

    def list_sources(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionRecord]:
        records = self.store.list_sources(owner_user_id=owner_user_id, package_id=package_id)
        refreshed: list[SourceIngestionRecord] = []
        for record in records:
            if record.status in {"queued", "fetching", "parsing", "indexing"} and record.open_notebook_command_id:
                refreshed.append(self.refresh_source(record))
            else:
                refreshed.append(record)
        return refreshed

    def add_url_source(
        self,
        *,
        owner_user_id: str,
        package: CoursePackage,
        source_uri: str,
        title: str = "",
    ) -> SourceIngestionRecord:
        normalized_uri = _validate_public_url(source_uri)
        notebook_id = self._ensure_notebook(owner_user_id=owner_user_id, package=package)
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
            status="queued",
            open_notebook_notebook_id=notebook_id,
            metadata={"adapter": "open_notebook"},
        )
        try:
            result = self.adapter.add_url_source(
                notebook_id=notebook_id,
                source_uri=normalized_uri,
                title=display_title,
            )
            record = record.model_copy(
                update={
                    "status": _status_from_open_notebook(result.status),
                    "open_notebook_source_id": result.source_id,
                    "open_notebook_command_id": result.command_id,
                    "metadata": {"adapter": "open_notebook", "open_notebook_response": result.raw or {}},
                }
            )
        except OpenNotebookAdapterError as exc:
            record = record.model_copy(update={"status": "failed", "error": str(exc)})
        return self.store.save_source(record)

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
        if not _supported_mime(mime_type, file_name):
            raise SourceIngestionError("Only PDF, Office, TXT, and Markdown files are supported in V1.")
        notebook_id = self._ensure_notebook(owner_user_id=owner_user_id, package=package)
        display_title = title.strip() or file_name
        record = SourceIngestionRecord(
            owner_user_id=owner_user_id,
            package_id=package.id,
            title=display_title,
            source_type="local_file",
            source_uri=None,
            file_name=file_name,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=len(content),
            status="queued",
            open_notebook_notebook_id=notebook_id,
            metadata={"adapter": "open_notebook"},
        )
        try:
            result = self.adapter.upload_file_source(
                notebook_id=notebook_id,
                file_name=file_name,
                content=content,
                mime_type=mime_type,
                title=display_title,
            )
            record = record.model_copy(
                update={
                    "status": _status_from_open_notebook(result.status),
                    "open_notebook_source_id": result.source_id,
                    "open_notebook_command_id": result.command_id,
                    "metadata": {"adapter": "open_notebook", "open_notebook_response": result.raw or {}},
                }
            )
        except OpenNotebookAdapterError as exc:
            record = record.model_copy(update={"status": "failed", "error": str(exc)})
        return self.store.save_source(record)

    def refresh_source(self, record: SourceIngestionRecord) -> SourceIngestionRecord:
        if not record.open_notebook_command_id:
            return record
        try:
            command = self.adapter.get_command(record.open_notebook_command_id)
        except OpenNotebookAdapterError as exc:
            return self.store.save_source(record.model_copy(update={"status": "failed", "error": str(exc)}))
        status = _status_from_open_notebook(_command_status(command))
        error = _command_error(command)
        source_id = _command_source_id(command) or record.open_notebook_source_id
        updated = record.model_copy(
            update={
                "status": status,
                "error": error if status == "failed" else "",
                "open_notebook_source_id": source_id,
                "metadata": {**record.metadata, "last_command": command, "refreshed_at": now_iso()},
            }
        )
        return self.store.save_source(updated)

    def _ensure_notebook(self, *, owner_user_id: str, package: CoursePackage) -> str:
        existing = self.store.get_notebook_id(owner_user_id=owner_user_id, package_id=package.id)
        if existing:
            return existing
        try:
            notebook_id = self.adapter.create_notebook(
                title=f"OpenClass - {package.title}",
                description=f"Sources imported for OpenClass package {package.id}.",
            )
        except OpenNotebookAdapterError as exc:
            raise SourceIngestionError(str(exc)) from exc
        self.store.upsert_notebook(
            owner_user_id=owner_user_id,
            package_id=package.id,
            notebook_id=notebook_id,
            title=package.title,
        )
        return notebook_id


def _supported_mime(mime_type: str, file_name: str) -> bool:
    lowered = file_name.lower()
    if lowered.endswith((".pdf", ".docx", ".doc", ".txt", ".md", ".markdown")):
        return True
    return any((mime_type or "").startswith(prefix) for prefix in SUPPORTED_FILE_MIME_PREFIXES)


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


def _status_from_open_notebook(raw_status: str) -> str:
    normalized = (raw_status or "").strip().lower()
    if normalized in {"ready", "completed", "complete", "success", "succeeded", "done"}:
        return "ready"
    if normalized in {"failed", "error", "errored"}:
        return "failed"
    if normalized in {"fetching", "downloading"}:
        return "fetching"
    if normalized in {"parsing", "processing"}:
        return "parsing"
    if normalized in {"indexing", "embedding", "vectorizing"}:
        return "indexing"
    return "queued"


def _command_status(command: dict[str, object]) -> str:
    for key in ("status", "state", "phase"):
        value = command.get(key)
        if isinstance(value, str):
            return value
    nested = command.get("data")
    return _command_status(nested) if isinstance(nested, dict) else ""


def _command_error(command: dict[str, object]) -> str:
    for key in ("error", "message", "detail"):
        value = command.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = command.get("data")
    return _command_error(nested) if isinstance(nested, dict) else ""


def _command_source_id(command: dict[str, object]) -> str:
    for key in ("source_id", "id", "record_id"):
        value = command.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = command.get("data")
    return _command_source_id(nested) if isinstance(nested, dict) else ""


source_ingestion_service = SourceIngestionService()
