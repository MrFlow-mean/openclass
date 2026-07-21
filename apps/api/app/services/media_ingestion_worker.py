from __future__ import annotations

import threading
import os
import shutil
import time

from app.services.source_ingestion_service import (
    media_ingestion_enabled,
    source_ingestion_service,
)
from app.services import workspace_state


class MediaIngestionWorker:
    """Recover persisted video jobs without relying on an HTTP request lifetime."""

    def __init__(self, *, poll_seconds: float = 2.0) -> None:
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not media_ingestion_enabled() or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="openclass-media-ingestion",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._cleanup_expired_failures()
            records = source_ingestion_service.store.list_pending_media_sources(limit=10)
            for record in records:
                if self._stop.is_set():
                    return
                try:
                    source_ingestion_service.process_media_url_source(
                        owner_user_id=record.owner_user_id,
                        package_id=record.package_id,
                        source_id=record.id,
                    )
                except Exception:
                    # The source service persists expected pipeline failures. An expired
                    # lease makes an interrupted unexpected failure recoverable.
                    continue
            self._stop.wait(self.poll_seconds)

    def _cleanup_expired_failures(self) -> None:
        root = workspace_state.UPLOAD_DIR / "media-temp"
        if not root.is_dir():
            return
        retention_hours = max(1, int(os.getenv("OPENCLASS_MEDIA_FAILED_CACHE_HOURS") or 24))
        cutoff = time.time() - retention_hours * 3600
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue


media_ingestion_worker = MediaIngestionWorker()
