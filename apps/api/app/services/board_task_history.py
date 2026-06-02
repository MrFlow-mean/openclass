from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.models import (
    BoardTaskRequirementSheet,
    BoardTaskRunStatus,
    new_id,
    now_iso,
)


ACTIVE_BOARD_TASK_STATUSES: set[BoardTaskRunStatus] = {"collecting", "ready", "awaiting_confirmation"}


@dataclass(frozen=True)
class BoardTaskHistoryStamp:
    run_id: str | None = None
    version_id: str | None = None
    phase: BoardTaskRunStatus | None = None


@dataclass
class BoardTaskHistorySnapshot:
    run_id: str | None = None
    status: BoardTaskRunStatus | None = None
    latest_version_id: str | None = None
    latest_version_number: int = 0
    latest_sheet_json: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "BoardTaskHistorySnapshot":
        if not raw:
            return cls()
        status = raw.get("status")
        if status not in ACTIVE_BOARD_TASK_STATUSES:
            status = None
        return cls(
            run_id=raw.get("run_id") if isinstance(raw.get("run_id"), str) else None,
            status=status,
            latest_version_id=raw.get("latest_version_id") if isinstance(raw.get("latest_version_id"), str) else None,
            latest_version_number=int(raw.get("latest_version_number") or 0),
            latest_sheet_json=raw.get("latest_sheet_json") if isinstance(raw.get("latest_sheet_json"), str) else None,
        )


class BoardTaskHistoryStore:
    def create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_task_runs (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                status TEXT NOT NULL,
                active_version_id TEXT,
                consumed_commit_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_board_task_runs_owner_lesson
                ON board_task_runs(owner_user_id, lesson_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS board_task_versions (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES board_task_runs(id) ON DELETE CASCADE,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                sheet_json TEXT NOT NULL,
                change_kind TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, version_number)
            );

            CREATE INDEX IF NOT EXISTS idx_board_task_versions_lesson
                ON board_task_versions(owner_user_id, lesson_id, created_at);

            CREATE TABLE IF NOT EXISTS board_task_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES board_task_runs(id) ON DELETE CASCADE,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_version_id TEXT,
                to_version_id TEXT,
                change_summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_board_task_events_lesson
                ON board_task_events(owner_user_id, lesson_id, created_at);
            """
        )

    def load_state(
        self,
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        lesson_id: str,
    ) -> dict[str, Any] | None:
        run_row = conn.execute(
            """
            SELECT *
            FROM board_task_runs
            WHERE owner_user_id = ?
              AND lesson_id = ?
              AND status IN ('collecting', 'ready', 'awaiting_confirmation')
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (owner_user_id, lesson_id),
        ).fetchone()
        if run_row is None:
            return None
        version_row = conn.execute(
            """
            SELECT *
            FROM board_task_versions
            WHERE run_id = ?
            ORDER BY version_number DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (run_row["id"],),
        ).fetchone()
        return {
            "run_id": run_row["id"],
            "status": run_row["status"],
            "latest_version_id": version_row["id"] if version_row is not None else None,
            "latest_version_number": version_row["version_number"] if version_row is not None else 0,
            "latest_sheet_json": version_row["sheet_json"] if version_row is not None else None,
        }

    def list_versions(
        self,
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        lesson_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM board_task_versions
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, run_id, version_number, id
            """,
            (owner_user_id, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_events(
        self,
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        lesson_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT *
            FROM board_task_events
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY rowid
            """,
            (owner_user_id, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def apply_operations(self, conn: sqlite3.Connection, operations: list[dict[str, Any]]) -> None:
        for operation in operations:
            operation_type = operation.get("type")
            if operation_type == "insert_board_task_run":
                conn.execute(
                    """
                    INSERT INTO board_task_runs(
                        id, owner_user_id, lesson_id, status, active_version_id,
                        consumed_commit_id, created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"],
                        operation["owner_user_id"],
                        operation["lesson_id"],
                        operation["status"],
                        operation.get("active_version_id"),
                        operation.get("consumed_commit_id"),
                        operation["created_at"],
                        operation["updated_at"],
                        operation.get("archived_at"),
                    ),
                )
                continue
            if operation_type == "insert_board_task_version":
                conn.execute(
                    """
                    INSERT INTO board_task_versions(
                        id, run_id, owner_user_id, lesson_id, version_number, status,
                        sheet_json, change_kind, change_summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"],
                        operation["run_id"],
                        operation["owner_user_id"],
                        operation["lesson_id"],
                        operation["version_number"],
                        operation["status"],
                        operation["sheet_json"],
                        operation["change_kind"],
                        operation["change_summary"],
                        operation["created_at"],
                    ),
                )
                continue
            if operation_type == "update_board_task_run":
                conn.execute(
                    """
                    UPDATE board_task_runs
                    SET status = ?,
                        active_version_id = COALESCE(?, active_version_id),
                        consumed_commit_id = COALESCE(?, consumed_commit_id),
                        updated_at = ?,
                        archived_at = COALESCE(?, archived_at)
                    WHERE id = ?
                    """,
                    (
                        operation["status"],
                        operation.get("active_version_id"),
                        operation.get("consumed_commit_id"),
                        operation["updated_at"],
                        operation.get("archived_at"),
                        operation["id"],
                    ),
                )
                continue
            if operation_type == "insert_board_task_event":
                conn.execute(
                    """
                    INSERT INTO board_task_events(
                        id, run_id, owner_user_id, lesson_id, event_type, from_version_id,
                        to_version_id, change_summary, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"],
                        operation["run_id"],
                        operation["owner_user_id"],
                        operation["lesson_id"],
                        operation["event_type"],
                        operation.get("from_version_id"),
                        operation.get("to_version_id"),
                        operation["change_summary"],
                        operation["metadata_json"],
                        operation["created_at"],
                    ),
                )
                continue
            raise ValueError(f"Unknown board task history operation {operation_type}")


def _canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sheet_summary(sheet: BoardTaskRequirementSheet, fallback: str) -> str:
    for value in [sheet.question_or_topic, sheet.clarification_question, fallback]:
        compact = " ".join((value or "").split())
        if compact:
            return compact[:240]
    return fallback


@dataclass
class BoardTaskHistoryRecorder:
    owner_user_id: str
    lesson_id: str
    snapshot: BoardTaskHistorySnapshot
    operations: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_store_state(
        cls,
        *,
        owner_user_id: str,
        lesson_id: str,
        state: dict[str, Any] | None,
    ) -> "BoardTaskHistoryRecorder":
        return cls(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            snapshot=BoardTaskHistorySnapshot.from_mapping(state),
        )

    def current_stamp(self) -> BoardTaskHistoryStamp:
        return BoardTaskHistoryStamp(
            run_id=self.snapshot.run_id,
            version_id=self.snapshot.latest_version_id,
            phase=self.snapshot.status,
        )

    def record_update(
        self,
        *,
        sheet: BoardTaskRequirementSheet,
        status: BoardTaskRunStatus | None = None,
        change_summary: str | None = None,
    ) -> BoardTaskHistoryStamp:
        phase = status or ("ready" if sheet.progress >= 100 else "collecting")
        sheet_json = _canonical_json(sheet)
        if (
            self.snapshot.run_id
            and self.snapshot.status in ACTIVE_BOARD_TASK_STATUSES
            and self.snapshot.latest_sheet_json == sheet_json
            and self.snapshot.status == phase
        ):
            return self.current_stamp()

        created_run = self._ensure_mutable_run()
        version_number = self.snapshot.latest_version_number + 1
        version_id = new_id("btver")
        created_at = now_iso()
        if phase == "awaiting_confirmation":
            change_kind = "awaiting_confirmation"
        elif phase == "ready":
            change_kind = "ready"
        elif version_number == 1:
            change_kind = "created"
        else:
            change_kind = "updated"
        summary = change_summary or _sheet_summary(sheet, "Board task sheet changed.")
        self.operations.append(
            {
                "type": "insert_board_task_version",
                "id": version_id,
                "run_id": self.snapshot.run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "version_number": version_number,
                "status": phase,
                "sheet_json": sheet_json,
                "change_kind": change_kind,
                "change_summary": summary,
                "created_at": created_at,
            }
        )
        self.operations.append(
            {
                "type": "update_board_task_run",
                "id": self.snapshot.run_id,
                "status": phase,
                "active_version_id": version_id,
                "updated_at": created_at,
            }
        )
        if created_run and change_kind != "created":
            self._append_event(
                event_type="created",
                from_version_id=None,
                to_version_id=None,
                change_summary="Board task run created.",
                created_at=created_at,
            )
        self._append_event(
            event_type=change_kind,
            from_version_id=self.snapshot.latest_version_id,
            to_version_id=version_id,
            change_summary=summary,
            created_at=created_at,
        )
        self.snapshot.status = phase
        self.snapshot.latest_version_id = version_id
        self.snapshot.latest_version_number = version_number
        self.snapshot.latest_sheet_json = sheet_json
        return self.current_stamp()

    def consume(self, *, commit_id: str, change_summary: str | None = None) -> BoardTaskHistoryStamp:
        return self._finish(
            status="consumed",
            event_type="consumed",
            change_summary=change_summary or "Board task was consumed by an execution commit.",
            metadata={"commit_id": commit_id},
            consumed_commit_id=commit_id,
        )

    def not_executed(self, *, reason: str) -> BoardTaskHistoryStamp:
        return self._finish(
            status="not_executed",
            event_type="not_executed",
            change_summary=reason,
            metadata={"reason": reason},
        )

    def execution_failed(
        self,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> BoardTaskHistoryStamp:
        if not self.snapshot.run_id:
            return self.current_stamp()
        created_at = now_iso()
        active_status = self.snapshot.status if self.snapshot.status in ACTIVE_BOARD_TASK_STATUSES else "ready"
        self.operations.append(
            {
                "type": "update_board_task_run",
                "id": self.snapshot.run_id,
                "status": active_status,
                "updated_at": created_at,
            }
        )
        self._append_event(
            event_type="execution_failed",
            from_version_id=self.snapshot.latest_version_id,
            to_version_id=self.snapshot.latest_version_id,
            change_summary=reason,
            metadata=metadata or {},
            created_at=created_at,
        )
        self.snapshot.status = active_status
        return self.current_stamp()

    def _finish(
        self,
        *,
        status: BoardTaskRunStatus,
        event_type: str,
        change_summary: str,
        metadata: dict[str, Any] | None = None,
        consumed_commit_id: str | None = None,
    ) -> BoardTaskHistoryStamp:
        if not self.snapshot.run_id:
            return self.current_stamp()
        created_at = now_iso()
        self.operations.append(
            {
                "type": "update_board_task_run",
                "id": self.snapshot.run_id,
                "status": status,
                "consumed_commit_id": consumed_commit_id,
                "updated_at": created_at,
                "archived_at": created_at if status in {"not_executed", "archived"} else None,
            }
        )
        self._append_event(
            event_type=event_type,
            from_version_id=self.snapshot.latest_version_id,
            to_version_id=self.snapshot.latest_version_id,
            change_summary=change_summary,
            metadata=metadata or {},
            created_at=created_at,
        )
        self.snapshot.status = status
        return BoardTaskHistoryStamp(
            run_id=self.snapshot.run_id,
            version_id=self.snapshot.latest_version_id,
            phase=status,
        )

    def _ensure_mutable_run(self) -> bool:
        if self.snapshot.run_id and self.snapshot.status in ACTIVE_BOARD_TASK_STATUSES:
            return False
        run_id = new_id("btrun")
        created_at = now_iso()
        self.operations.append(
            {
                "type": "insert_board_task_run",
                "id": run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "status": "collecting",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        self.snapshot = BoardTaskHistorySnapshot(run_id=run_id, status="collecting")
        return True

    def _append_event(
        self,
        *,
        event_type: str,
        from_version_id: str | None,
        to_version_id: str | None,
        change_summary: str,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        self.operations.append(
            {
                "type": "insert_board_task_event",
                "id": new_id("btevt"),
                "run_id": self.snapshot.run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "event_type": event_type,
                "from_version_id": from_version_id,
                "to_version_id": to_version_id,
                "change_summary": change_summary,
                "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                "created_at": created_at or now_iso(),
            }
        )
