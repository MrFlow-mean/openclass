from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from app.models import SourceIngestionJob, now_iso
from app.services import workspace_state


WriteResult = TypeVar("WriteResult")


class SourceIngestionCoordinator:
    """Bound source parsing and serialize source writes for one SQLite database."""

    def __init__(
        self,
        *,
        processing_capacity: int = 2,
        large_source_bytes: int = 64 * 1024 * 1024,
        lock_retry_delays: tuple[float, ...] = (0.05, 0.15, 0.5, 1.0, 2.0),
    ) -> None:
        if processing_capacity < 1:
            raise ValueError("processing_capacity must be positive")
        self.processing_capacity = processing_capacity
        self.large_source_bytes = large_source_bytes
        self.lock_retry_delays = lock_retry_delays
        self._processing_available = processing_capacity
        self._processing_waiters: deque[tuple[object, int]] = deque()
        self._processing_condition = threading.Condition()
        self._write_locks: dict[str, threading.RLock] = {}
        self._write_locks_guard = threading.Lock()

    def processing_weight(self, *, size_bytes: int, source_type: str) -> int:
        if size_bytes >= self.large_source_bytes or source_type in {"audio_file", "video_file"}:
            return self.processing_capacity
        return 1

    @contextmanager
    def processing_slot(self, *, weight: int = 1) -> Iterator[None]:
        normalized_weight = max(1, min(self.processing_capacity, weight))
        ticket = object()
        with self._processing_condition:
            self._processing_waiters.append((ticket, normalized_weight))
            while (
                self._processing_waiters[0][0] is not ticket
                or self._processing_available < normalized_weight
            ):
                self._processing_condition.wait()
            self._processing_waiters.popleft()
            self._processing_available -= normalized_weight
        try:
            yield
        finally:
            with self._processing_condition:
                self._processing_available += normalized_weight
                self._processing_condition.notify_all()

    def run_write(self, path: Path, operation: Callable[[], WriteResult]) -> WriteResult:
        write_lock = self._write_lock(path)
        with write_lock:
            for attempt in range(len(self.lock_retry_delays) + 1):
                try:
                    return operation()
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt >= len(self.lock_retry_delays):
                        raise
                    time.sleep(self.lock_retry_delays[attempt])
        raise RuntimeError("unreachable source write retry state")

    def _write_lock(self, path: Path) -> threading.RLock:
        key = str(path.resolve())
        with self._write_locks_guard:
            return self._write_locks.setdefault(key, threading.RLock())


source_ingestion_coordinator = SourceIngestionCoordinator()


class SourceIngestionJobStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        coordinator: SourceIngestionCoordinator = source_ingestion_coordinator,
    ) -> None:
        self._path = path
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def path(self) -> Path:
        return self._path or workspace_state.get_store().path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            self._initialize_connection(conn, path)
            with conn:
                yield conn
        finally:
            conn.close()

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

        def save_job() -> SourceIngestionJob:
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

        return self.coordinator.run_write(self.path, save_job)

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
        def delete_jobs() -> None:
            with self._lock, self._connect() as conn, conn:
                conn.execute(
                    "DELETE FROM source_ingestion_jobs WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?",
                    (owner_user_id, package_id, source_id),
                )

        self.coordinator.run_write(self.path, delete_jobs)

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
