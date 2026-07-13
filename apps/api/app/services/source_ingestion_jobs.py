from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.models import SourceIngestionJob, now_iso
from app.services import workspace_state


class SourceIngestionJobStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def path(self) -> Path:
        return self._path or workspace_state.get_store().path

    def _connect(self) -> sqlite3.Connection:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        self._initialize_connection(conn, path)
        return conn

    def _initialize_connection(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            key = str(path)
            if key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_uri TEXT,
                    adapter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    phase_history_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_ingestion_jobs_scope
                    ON source_ingestion_jobs(owner_user_id, package_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_source_ingestion_jobs_source
                    ON source_ingestion_jobs(owner_user_id, package_id, source_ingestion_id, updated_at);
                """
            )
            self._initialized_paths.add(key)

    def save(
        self,
        job: SourceIngestionJob,
        *,
        owner_user_id: str,
        package_id: str,
    ) -> SourceIngestionJob:
        job = job.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO source_ingestion_jobs(
                    id, owner_user_id, package_id, source_ingestion_id, source_type, source_uri,
                    adapter, status, progress, error, phase_history_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    error = excluded.error,
                    phase_history_json = excluded.phase_history_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job.id,
                    owner_user_id,
                    package_id,
                    job.resource_id or "",
                    job.source_type,
                    job.source_uri,
                    job.adapter,
                    job.status,
                    job.progress,
                    job.error,
                    json.dumps(job.phase_history, ensure_ascii=False),
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job

    def list(self, *, owner_user_id: str, package_id: str) -> list[SourceIngestionJob]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM source_ingestion_jobs
                WHERE owner_user_id = ? AND package_id = ?
                ORDER BY updated_at DESC
                """,
                (owner_user_id, package_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def latest_for_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceIngestionJob | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM source_ingestion_jobs
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (owner_user_id, package_id, source_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def delete_for_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> None:
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                "DELETE FROM source_ingestion_jobs WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                (owner_user_id, package_id, source_id),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> SourceIngestionJob:
        try:
            phases = json.loads(row["phase_history_json"] or "[]")
        except json.JSONDecodeError:
            phases = []
        return SourceIngestionJob(
            id=row["id"],
            resource_id=row["source_ingestion_id"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            adapter=row["adapter"],
            status=row["status"],
            progress=row["progress"],
            error=row["error"],
            phase_history=phases,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


source_ingestion_job_store = SourceIngestionJobStore()
