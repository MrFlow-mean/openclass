from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models import MediaPackageManifest, TimedTranscriptSegment, now_iso
from app.services import workspace_state
from app.services.source_ingestion_jobs import (
    SourceIngestionCoordinator,
    source_ingestion_coordinator,
)


class MediaPackageStore:
    """Persist typed media artifacts beside the existing source index."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        coordinator: SourceIngestionCoordinator = source_ingestion_coordinator,
    ) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()
        self.coordinator = coordinator

    @property
    def path(self) -> Path:
        return self._path or workspace_state.get_store().path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            self._initialize(conn)
            yield conn
        finally:
            conn.close()

    def _initialize(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            path_key = str(self.path)
            if path_key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_package_manifests (
                    source_ingestion_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_media_package_owner
                    ON media_package_manifests(owner_user_id, package_id, updated_at);

                CREATE TABLE IF NOT EXISTS media_transcript_segments (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    order_index INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    language TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_media_transcript_source_version
                    ON media_transcript_segments(source_ingestion_id, version, order_index);

                CREATE TABLE IF NOT EXISTS media_ingestion_leases (
                    source_ingestion_id TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    leased_until REAL NOT NULL,
                    heartbeat_at REAL NOT NULL
                );
                """
            )
            self._initialized_paths.add(path_key)

    def get_manifest(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> MediaPackageManifest | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT manifest_json FROM media_package_manifests
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                """,
                (owner_user_id, package_id, source_id),
            ).fetchone()
        if row is None:
            return None
        return MediaPackageManifest.model_validate(json.loads(row["manifest_json"]))

    def save_manifest(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        manifest: MediaPackageManifest,
    ) -> MediaPackageManifest:
        updated = manifest.model_copy(update={"updated_at": now_iso()})

        def save() -> MediaPackageManifest:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO media_package_manifests(
                            source_ingestion_id, owner_user_id, package_id, manifest_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(source_ingestion_id) DO UPDATE SET
                            manifest_json = excluded.manifest_json,
                            updated_at = excluded.updated_at
                        """,
                        (
                            source_id,
                            owner_user_id,
                            package_id,
                            json.dumps(updated.model_dump(mode="json"), ensure_ascii=False),
                            updated.updated_at,
                        ),
                    )
            return updated

        return self.coordinator.run_write(self.path, save)

    def replace_transcript_version(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        version: int,
        segments: list[TimedTranscriptSegment],
    ) -> None:
        ordered = sorted(segments, key=lambda item: (item.start_ms, item.order_index, item.id))

        def save() -> None:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        "DELETE FROM media_transcript_segments WHERE source_ingestion_id = ? AND version = ?",
                        (source_id, version),
                    )
                    conn.executemany(
                        """
                        INSERT INTO media_transcript_segments(
                            id, owner_user_id, package_id, source_ingestion_id, version,
                            order_index, start_ms, end_ms, text, language, source_kind,
                            provider, model, confidence, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                item.id,
                                owner_user_id,
                                package_id,
                                source_id,
                                version,
                                index,
                                item.start_ms,
                                item.end_ms,
                                item.text,
                                item.language,
                                item.source_kind,
                                item.provider,
                                item.model,
                                item.confidence,
                                json.dumps(item.metadata, ensure_ascii=False),
                            )
                            for index, item in enumerate(ordered)
                        ],
                    )

        self.coordinator.run_write(self.path, save)

    def list_transcript(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
        version: int,
    ) -> list[TimedTranscriptSegment]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM media_transcript_segments
                WHERE owner_user_id = ? AND package_id = ?
                  AND source_ingestion_id = ? AND version = ?
                ORDER BY order_index
                """,
                (owner_user_id, package_id, source_id, version),
            ).fetchall()
        return [
            TimedTranscriptSegment(
                id=row["id"],
                source_ingestion_id=row["source_ingestion_id"],
                version=row["version"],
                order_index=row["order_index"],
                start_ms=row["start_ms"],
                end_ms=row["end_ms"],
                text=row["text"],
                language=row["language"],
                source_kind=row["source_kind"],
                provider=row["provider"],
                model=row["model"],
                confidence=row["confidence"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row in rows
        ]

    def claim_source(self, *, source_id: str, worker_id: str, lease_seconds: int = 90) -> bool:
        now = time.time()

        def claim() -> bool:
            with self._connect() as conn, conn:
                cursor = conn.execute(
                    """
                    INSERT INTO media_ingestion_leases(
                        source_ingestion_id, worker_id, leased_until, heartbeat_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_ingestion_id) DO UPDATE SET
                        worker_id = excluded.worker_id,
                        leased_until = excluded.leased_until,
                        heartbeat_at = excluded.heartbeat_at
                    WHERE media_ingestion_leases.leased_until < ?
                       OR media_ingestion_leases.worker_id = excluded.worker_id
                    """,
                    (source_id, worker_id, now + lease_seconds, now, now),
                )
                return cursor.rowcount > 0

        return self.coordinator.run_write(self.path, claim)

    def renew_lease(self, *, source_id: str, worker_id: str, lease_seconds: int = 90) -> bool:
        now = time.time()

        def renew() -> bool:
            with self._connect() as conn, conn:
                cursor = conn.execute(
                    """
                    UPDATE media_ingestion_leases
                    SET leased_until = ?, heartbeat_at = ?
                    WHERE source_ingestion_id = ? AND worker_id = ?
                    """,
                    (now + lease_seconds, now, source_id, worker_id),
                )
                return cursor.rowcount > 0

        return self.coordinator.run_write(self.path, renew)

    def release_lease(self, *, source_id: str, worker_id: str) -> None:
        def release() -> None:
            with self._connect() as conn, conn:
                conn.execute(
                    "DELETE FROM media_ingestion_leases WHERE source_ingestion_id = ? AND worker_id = ?",
                    (source_id, worker_id),
                )

        self.coordinator.run_write(self.path, release)

    def delete_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> None:
        def delete() -> None:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        "DELETE FROM media_transcript_segments WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                        (owner_user_id, package_id, source_id),
                    )
                    conn.execute(
                        "DELETE FROM media_package_manifests WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                        (owner_user_id, package_id, source_id),
                    )
                    conn.execute(
                        "DELETE FROM media_ingestion_leases WHERE source_ingestion_id = ?",
                        (source_id,),
                    )

        self.coordinator.run_write(self.path, delete)


media_package_store = MediaPackageStore()
