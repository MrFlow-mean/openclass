from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.models import (
    LearningClarificationStatus,
    LearningRequirementRunStatus,
    LearningRequirementSheet,
    new_id,
    now_iso,
)


ACTIVE_RUN_STATUSES: set[LearningRequirementRunStatus] = {"collecting", "ready", "frozen"}


@dataclass(frozen=True)
class RequirementHistoryStamp:
    run_id: str | None = None
    version_id: str | None = None
    phase: LearningRequirementRunStatus | None = None


@dataclass
class RequirementHistorySnapshot:
    run_id: str | None = None
    status: LearningRequirementRunStatus | None = None
    latest_version_id: str | None = None
    latest_version_number: int = 0
    latest_sheet_json: str | None = None
    latest_clarification_json: str | None = None
    frozen_version_id: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "RequirementHistorySnapshot":
        if not raw:
            return cls()
        status = raw.get("status")
        if status not in ACTIVE_RUN_STATUSES:
            status = None
        return cls(
            run_id=raw.get("run_id") if isinstance(raw.get("run_id"), str) else None,
            status=status,
            latest_version_id=(
                raw.get("latest_version_id") if isinstance(raw.get("latest_version_id"), str) else None
            ),
            latest_version_number=int(raw.get("latest_version_number") or 0),
            latest_sheet_json=raw.get("latest_sheet_json") if isinstance(raw.get("latest_sheet_json"), str) else None,
            latest_clarification_json=(
                raw.get("latest_clarification_json")
                if isinstance(raw.get("latest_clarification_json"), str)
                else None
            ),
            frozen_version_id=raw.get("frozen_version_id") if isinstance(raw.get("frozen_version_id"), str) else None,
        )


class LearningRequirementHistoryStore:
    def create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS learning_requirement_runs (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                status TEXT NOT NULL,
                active_version_id TEXT,
                frozen_version_id TEXT,
                consumed_commit_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                archived_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_learning_requirement_runs_owner_lesson
                ON learning_requirement_runs(owner_user_id, lesson_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS learning_requirement_versions (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES learning_requirement_runs(id) ON DELETE CASCADE,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                sheet_json TEXT NOT NULL,
                clarification_json TEXT NOT NULL,
                change_kind TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, version_number)
            );

            CREATE INDEX IF NOT EXISTS idx_learning_requirement_versions_lesson
                ON learning_requirement_versions(owner_user_id, lesson_id, created_at);

            CREATE TABLE IF NOT EXISTS learning_requirement_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES learning_requirement_runs(id) ON DELETE CASCADE,
                owner_user_id TEXT NOT NULL,
                lesson_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_version_id TEXT,
                to_version_id TEXT,
                change_summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_learning_requirement_events_lesson
                ON learning_requirement_events(owner_user_id, lesson_id, created_at);
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
            FROM learning_requirement_runs
            WHERE owner_user_id = ?
              AND lesson_id = ?
              AND status IN ('collecting', 'ready', 'frozen')
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
            FROM learning_requirement_versions
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
            "latest_clarification_json": version_row["clarification_json"] if version_row is not None else None,
            "frozen_version_id": run_row["frozen_version_id"],
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
            FROM learning_requirement_versions
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
            FROM learning_requirement_events
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY rowid
            """,
            (owner_user_id, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def apply_operations(
        self,
        conn: sqlite3.Connection,
        operations: list[dict[str, Any]],
    ) -> None:
        for operation in operations:
            operation_type = operation.get("type")
            if operation_type == "insert_requirement_run":
                conn.execute(
                    """
                    INSERT INTO learning_requirement_runs(
                        id, owner_user_id, lesson_id, status, active_version_id,
                        frozen_version_id, consumed_commit_id, created_at, updated_at,
                        completed_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"],
                        operation["owner_user_id"],
                        operation["lesson_id"],
                        operation["status"],
                        operation.get("active_version_id"),
                        operation.get("frozen_version_id"),
                        operation.get("consumed_commit_id"),
                        operation["created_at"],
                        operation["updated_at"],
                        operation.get("completed_at"),
                        operation.get("archived_at"),
                    ),
                )
                continue
            if operation_type == "insert_requirement_version":
                conn.execute(
                    """
                    INSERT INTO learning_requirement_versions(
                        id, run_id, owner_user_id, lesson_id, version_number, status,
                        sheet_json, clarification_json, change_kind, change_summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation["id"],
                        operation["run_id"],
                        operation["owner_user_id"],
                        operation["lesson_id"],
                        operation["version_number"],
                        operation["status"],
                        operation["sheet_json"],
                        operation["clarification_json"],
                        operation["change_kind"],
                        operation["change_summary"],
                        operation["created_at"],
                    ),
                )
                continue
            if operation_type == "update_requirement_run":
                existing = conn.execute(
                    "SELECT * FROM learning_requirement_runs WHERE id = ?",
                    (operation["id"],),
                ).fetchone()
                if existing is None:
                    continue
                conn.execute(
                    """
                    UPDATE learning_requirement_runs
                    SET status = ?,
                        active_version_id = COALESCE(?, active_version_id),
                        frozen_version_id = COALESCE(?, frozen_version_id),
                        consumed_commit_id = COALESCE(?, consumed_commit_id),
                        updated_at = ?,
                        completed_at = COALESCE(?, completed_at),
                        archived_at = COALESCE(?, archived_at)
                    WHERE id = ?
                    """,
                    (
                        operation["status"],
                        operation.get("active_version_id"),
                        operation.get("frozen_version_id"),
                        operation.get("consumed_commit_id"),
                        operation["updated_at"],
                        operation.get("completed_at"),
                        operation.get("archived_at"),
                        operation["id"],
                    ),
                )
                continue
            if operation_type == "insert_requirement_event":
                conn.execute(
                    """
                    INSERT INTO learning_requirement_events(
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
            raise ValueError(f"Unknown learning requirement history operation {operation_type}")


def _canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _change_summary(clarification: LearningClarificationStatus, fallback: str) -> str:
    for value in [clarification.summary, clarification.reason, clarification.next_question, fallback]:
        compact = " ".join((value or "").split())
        if compact:
            return compact[:240]
    return fallback


@dataclass
class LearningRequirementHistoryRecorder:
    owner_user_id: str
    lesson_id: str
    snapshot: RequirementHistorySnapshot
    operations: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_store_state(
        cls,
        *,
        owner_user_id: str,
        lesson_id: str,
        state: dict[str, Any] | None,
    ) -> "LearningRequirementHistoryRecorder":
        return cls(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            snapshot=RequirementHistorySnapshot.from_mapping(state),
        )

    def current_stamp(self) -> RequirementHistoryStamp:
        return RequirementHistoryStamp(
            run_id=self.snapshot.run_id,
            version_id=self.snapshot.latest_version_id,
            phase=self.snapshot.status,
        )

    def record_update(
        self,
        *,
        requirements: LearningRequirementSheet,
        clarification: LearningClarificationStatus,
        change_summary: str | None = None,
    ) -> RequirementHistoryStamp:
        sheet_json = _canonical_json(requirements)
        clarification_json = _canonical_json(clarification)
        if (
            self.snapshot.run_id
            and self.snapshot.status in {"collecting", "ready"}
            and self.snapshot.latest_sheet_json == sheet_json
            and self.snapshot.latest_clarification_json == clarification_json
        ):
            return self.current_stamp()

        created_run = self._ensure_mutable_run()
        phase: LearningRequirementRunStatus = "ready" if clarification.ready_for_board else "collecting"
        version_number = self.snapshot.latest_version_number + 1
        if clarification.ready_for_board:
            change_kind = "completed"
        elif version_number == 1:
            change_kind = "created"
        else:
            change_kind = "updated"
        version_id = new_id("reqver")
        created_at = now_iso()
        summary = change_summary or _change_summary(clarification, "Learning requirement sheet changed.")
        self.operations.append(
            {
                "type": "insert_requirement_version",
                "id": version_id,
                "run_id": self.snapshot.run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "version_number": version_number,
                "status": phase,
                "sheet_json": sheet_json,
                "clarification_json": clarification_json,
                "change_kind": change_kind,
                "change_summary": summary,
                "created_at": created_at,
            }
        )
        self.operations.append(
            {
                "type": "update_requirement_run",
                "id": self.snapshot.run_id,
                "status": phase,
                "active_version_id": version_id,
                "updated_at": created_at,
                "completed_at": created_at if phase == "ready" else None,
            }
        )
        if created_run and change_kind != "created":
            self._append_event(
                event_type="created",
                from_version_id=None,
                to_version_id=None,
                change_summary="Learning requirement run created.",
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
        self.snapshot.latest_clarification_json = clarification_json
        return self.current_stamp()

    def freeze(
        self,
        *,
        requirements: LearningRequirementSheet,
        clarification: LearningClarificationStatus,
        forced: bool,
        change_summary: str | None = None,
    ) -> RequirementHistoryStamp:
        if self.snapshot.run_id and self.snapshot.status == "frozen" and self.snapshot.frozen_version_id:
            return RequirementHistoryStamp(
                run_id=self.snapshot.run_id,
                version_id=self.snapshot.frozen_version_id,
                phase="frozen",
            )
        created_run = self._ensure_mutable_run()
        sheet_json = _canonical_json(requirements)
        clarification_json = _canonical_json(clarification)
        version_number = self.snapshot.latest_version_number + 1
        version_id = new_id("reqver")
        created_at = now_iso()
        change_kind = "forced_frozen" if forced else "frozen"
        summary = change_summary or _change_summary(clarification, "Learning requirement sheet frozen.")
        self.operations.append(
            {
                "type": "insert_requirement_version",
                "id": version_id,
                "run_id": self.snapshot.run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "version_number": version_number,
                "status": "frozen",
                "sheet_json": sheet_json,
                "clarification_json": clarification_json,
                "change_kind": change_kind,
                "change_summary": summary,
                "created_at": created_at,
            }
        )
        if created_run:
            self._append_event(
                event_type="created",
                from_version_id=None,
                to_version_id=None,
                change_summary="Learning requirement run created.",
                created_at=created_at,
            )
        self.operations.append(
            {
                "type": "update_requirement_run",
                "id": self.snapshot.run_id,
                "status": "frozen",
                "active_version_id": version_id,
                "frozen_version_id": version_id,
                "updated_at": created_at,
                "completed_at": created_at if not forced else None,
            }
        )
        self._append_event(
            event_type=change_kind,
            from_version_id=self.snapshot.latest_version_id,
            to_version_id=version_id,
            change_summary=summary,
            metadata={"forced": forced},
            created_at=created_at,
        )
        self.snapshot.status = "frozen"
        self.snapshot.latest_version_id = version_id
        self.snapshot.latest_version_number = version_number
        self.snapshot.latest_sheet_json = sheet_json
        self.snapshot.latest_clarification_json = clarification_json
        self.snapshot.frozen_version_id = version_id
        return RequirementHistoryStamp(run_id=self.snapshot.run_id, version_id=version_id, phase="frozen")

    def consume(self, *, commit_id: str, change_summary: str | None = None) -> RequirementHistoryStamp:
        if not self.snapshot.run_id:
            return self.current_stamp()
        frozen_version_id = self.snapshot.frozen_version_id or self.snapshot.latest_version_id
        created_at = now_iso()
        summary = change_summary or "Frozen learning requirement sheet was consumed by board generation."
        self.operations.append(
            {
                "type": "update_requirement_run",
                "id": self.snapshot.run_id,
                "status": "consumed",
                "consumed_commit_id": commit_id,
                "updated_at": created_at,
            }
        )
        self._append_event(
            event_type="consumed",
            from_version_id=frozen_version_id,
            to_version_id=frozen_version_id,
            change_summary=summary,
            metadata={"commit_id": commit_id},
            created_at=created_at,
        )
        self.snapshot.status = "consumed"
        return RequirementHistoryStamp(run_id=self.snapshot.run_id, version_id=frozen_version_id, phase="consumed")

    def generation_failed(self, *, reason: str, metadata: dict[str, Any] | None = None) -> RequirementHistoryStamp:
        if not self.snapshot.run_id:
            return self.current_stamp()
        frozen_version_id = self.snapshot.frozen_version_id or self.snapshot.latest_version_id
        created_at = now_iso()
        event_metadata = {"reason": reason}
        if metadata:
            event_metadata.update(metadata)
        self._append_event(
            event_type="generation_failed",
            from_version_id=frozen_version_id,
            to_version_id=frozen_version_id,
            change_summary=reason[:240],
            metadata=event_metadata,
            created_at=created_at,
        )
        return RequirementHistoryStamp(run_id=self.snapshot.run_id, version_id=frozen_version_id, phase=self.snapshot.status)

    def _ensure_mutable_run(self) -> bool:
        if self.snapshot.run_id and self.snapshot.status in {"collecting", "ready"}:
            return False
        run_id = new_id("reqrun")
        created_at = now_iso()
        self.operations.append(
            {
                "type": "insert_requirement_run",
                "id": run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "status": "collecting",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        self.snapshot = RequirementHistorySnapshot(run_id=run_id, status="collecting")
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
                "type": "insert_requirement_event",
                "id": new_id("reqevt"),
                "run_id": self.snapshot.run_id,
                "owner_user_id": self.owner_user_id,
                "lesson_id": self.lesson_id,
                "event_type": event_type,
                "from_version_id": from_version_id,
                "to_version_id": to_version_id,
                "change_summary": change_summary,
                "metadata_json": _canonical_json(metadata or {}),
                "created_at": created_at or now_iso(),
            }
        )
