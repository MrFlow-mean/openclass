from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.models import SourceCodexRun, SourceCodexTask, now_iso
from app.services import workspace_state
from app.services.source_ingestion_jobs import (
    SourceIngestionCoordinator,
    source_ingestion_coordinator,
)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class SourceCodexStore:
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
        if self._path is not None:
            return self._path
        return workspace_state.get_store().path

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
            conn.execute("PRAGMA synchronous = NORMAL")
            self._initialize_connection(conn, path)
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize_connection(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            path_key = str(path)
            if path_key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_codex_runs (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    pipeline_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_manifest_hash TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    coordinator_thread_id TEXT NOT NULL,
                    coordinator_turn_id TEXT NOT NULL,
                    worker_count INTEGER NOT NULL,
                    completed_worker_count INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    published_structure_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_codex_runs_source
                    ON source_codex_runs(owner_user_id, package_id, source_ingestion_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS source_codex_tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    shard_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    input_hash TEXT NOT NULL,
                    output_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    error TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_codex_tasks_run
                    ON source_codex_tasks(run_id, role, shard_id);
                """
            )
            self._initialized_paths.add(path_key)

    def save_run(self, run: SourceCodexRun) -> SourceCodexRun:
        saved = run.model_copy(update={"updated_at": now_iso()})
        self.coordinator.run_write(self.path, lambda: self._save_run(saved))
        return saved

    def _save_run(self, run: SourceCodexRun) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_codex_runs(
                    id, owner_user_id, package_id, source_ingestion_id, content_hash,
                    pipeline_version, status, model, input_manifest_hash, output_hash,
                    coordinator_thread_id, coordinator_turn_id, worker_count,
                    completed_worker_count, error, published_structure_id, created_at,
                    updated_at, finished_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    model = excluded.model,
                    input_manifest_hash = excluded.input_manifest_hash,
                    output_hash = excluded.output_hash,
                    coordinator_thread_id = excluded.coordinator_thread_id,
                    coordinator_turn_id = excluded.coordinator_turn_id,
                    worker_count = excluded.worker_count,
                    completed_worker_count = excluded.completed_worker_count,
                    error = excluded.error,
                    published_structure_id = excluded.published_structure_id,
                    updated_at = excluded.updated_at,
                    finished_at = excluded.finished_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    run.id,
                    run.owner_user_id,
                    run.package_id,
                    run.source_ingestion_id,
                    run.content_hash,
                    run.pipeline_version,
                    run.status,
                    run.model,
                    run.input_manifest_hash,
                    run.output_hash,
                    run.coordinator_thread_id,
                    run.coordinator_turn_id,
                    run.worker_count,
                    run.completed_worker_count,
                    run.error,
                    run.published_structure_id,
                    run.created_at,
                    run.updated_at,
                    run.finished_at,
                    _dumps(run.metadata),
                ),
            )

    def save_tasks(self, tasks: list[SourceCodexTask]) -> list[SourceCodexTask]:
        if not tasks:
            return []
        stamp = now_iso()
        saved = [task.model_copy(update={"updated_at": stamp}) for task in tasks]

        def save_all() -> None:
            with self._lock, self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO source_codex_tasks(
                        id, run_id, owner_user_id, package_id, source_ingestion_id,
                        role, shard_id, attempt, status, page_start, page_end, input_hash,
                        output_hash, model, thread_id, turn_id, error, accepted,
                        created_at, updated_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        input_hash = excluded.input_hash,
                        output_hash = excluded.output_hash,
                        model = excluded.model,
                        thread_id = excluded.thread_id,
                        turn_id = excluded.turn_id,
                        error = excluded.error,
                        accepted = excluded.accepted,
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """,
                    [
                        (
                            task.id,
                            task.run_id,
                            task.owner_user_id,
                            task.package_id,
                            task.source_ingestion_id,
                            task.role,
                            task.shard_id,
                            task.attempt,
                            task.status,
                            task.page_start,
                            task.page_end,
                            task.input_hash,
                            task.output_hash,
                            task.model,
                            task.thread_id,
                            task.turn_id,
                            task.error,
                            int(task.accepted),
                            task.created_at,
                            task.updated_at,
                            _dumps(task.metadata),
                        )
                        for task in saved
                    ],
                )

        self.coordinator.run_write(self.path, save_all)
        return saved

    @staticmethod
    def complete_publish_on_connection(
        conn: sqlite3.Connection,
        *,
        run: SourceCodexRun,
        structure_id: str,
        finished_at: str,
    ) -> None:
        """Mark one accepted Codex run ready inside the structure transaction."""

        cursor = conn.execute(
            """
            UPDATE source_codex_runs
            SET status = 'ready', published_structure_id = ?, error = '',
                updated_at = ?, finished_at = ?
            WHERE id = ? AND owner_user_id = ? AND package_id = ?
              AND source_ingestion_id = ?
            """,
            (
                structure_id,
                finished_at,
                finished_at,
                run.id,
                run.owner_user_id,
                run.package_id,
                run.source_ingestion_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Codex source run disappeared before atomic publication")

    def latest_run(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> SourceCodexRun | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM source_codex_runs
                WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (owner_user_id, package_id, source_id),
            ).fetchone()
        return _run_from_row(row) if row else None

    def tasks_for_run(self, run_id: str) -> list[SourceCodexTask]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM source_codex_tasks
                WHERE run_id = ?
                ORDER BY role, shard_id, attempt
                """,
                (run_id,),
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def delete_for_source(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_id: str,
    ) -> None:
        def delete() -> None:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    DELETE FROM source_codex_tasks
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (owner_user_id, package_id, source_id),
                )
                conn.execute(
                    """
                    DELETE FROM source_codex_runs
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (owner_user_id, package_id, source_id),
                )

        self.coordinator.run_write(self.path, delete)


def _run_from_row(row: sqlite3.Row) -> SourceCodexRun:
    return SourceCodexRun(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        package_id=str(row["package_id"]),
        source_ingestion_id=str(row["source_ingestion_id"]),
        content_hash=str(row["content_hash"]),
        pipeline_version=int(row["pipeline_version"]),
        status=str(row["status"]),
        model=str(row["model"]),
        input_manifest_hash=str(row["input_manifest_hash"]),
        output_hash=str(row["output_hash"]),
        coordinator_thread_id=str(row["coordinator_thread_id"]),
        coordinator_turn_id=str(row["coordinator_turn_id"]),
        worker_count=int(row["worker_count"]),
        completed_worker_count=int(row["completed_worker_count"]),
        error=str(row["error"]),
        published_structure_id=str(row["published_structure_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
        metadata=_loads(row["metadata_json"], {}),
    )


def _task_from_row(row: sqlite3.Row) -> SourceCodexTask:
    return SourceCodexTask(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        owner_user_id=str(row["owner_user_id"]),
        package_id=str(row["package_id"]),
        source_ingestion_id=str(row["source_ingestion_id"]),
        role=str(row["role"]),
        shard_id=str(row["shard_id"]),
        attempt=int(row["attempt"]),
        status=str(row["status"]),
        page_start=int(row["page_start"]) if row["page_start"] is not None else None,
        page_end=int(row["page_end"]) if row["page_end"] is not None else None,
        input_hash=str(row["input_hash"]),
        output_hash=str(row["output_hash"]),
        model=str(row["model"]),
        thread_id=str(row["thread_id"]),
        turn_id=str(row["turn_id"]),
        error=str(row["error"]),
        accepted=bool(row["accepted"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        metadata=_loads(row["metadata_json"], {}),
    )


source_codex_store = SourceCodexStore()
