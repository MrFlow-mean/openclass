from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.research_models import (
    ResearchArtifact,
    ResearchChatMessage,
    ResearchChatThread,
    ResearchCitation,
    ResearchNote,
)
from app.models import now_iso
from app.services import workspace_state


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class ResearchStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        return workspace_state.get_store().path

    def _connect(self) -> sqlite3.Connection:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        self._initialize_connection(conn, path)
        return conn

    def _initialize_connection(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            path_key = str(path)
            if path_key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_notes (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_notes_owner_package
                    ON research_notes(owner_user_id, package_id, updated_at);

                CREATE TABLE IF NOT EXISTS research_chat_threads (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    context_mode TEXT NOT NULL,
                    source_ingestion_ids_json TEXT NOT NULL DEFAULT '[]',
                    note_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_threads_owner_package
                    ON research_chat_threads(owner_user_id, package_id, updated_at);

                CREATE TABLE IF NOT EXISTS research_chat_messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(thread_id) REFERENCES research_chat_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_research_messages_thread
                    ON research_chat_messages(thread_id, created_at);

                CREATE TABLE IF NOT EXISTS research_artifacts (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    audio_path TEXT,
                    source_ingestion_ids_json TEXT NOT NULL DEFAULT '[]',
                    note_ids_json TEXT NOT NULL DEFAULT '[]',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_artifacts_owner_package
                    ON research_artifacts(owner_user_id, package_id, updated_at);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS research_notes_fts USING fts5(
                        note_id UNINDEXED,
                        owner_user_id UNINDEXED,
                        package_id UNINDEXED,
                        title,
                        content,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError:
                pass
            self._initialized_paths.add(path_key)

    def save_note(self, note: ResearchNote) -> ResearchNote:
        note = note.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_notes(
                    id, owner_user_id, package_id, title, content, tags_json, citations_json,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    tags_json = excluded.tags_json,
                    citations_json = excluded.citations_json,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    note.id,
                    note.owner_user_id,
                    note.package_id,
                    note.title,
                    note.content,
                    _dumps(note.tags),
                    _dumps([item.model_dump(mode="json") for item in note.citations]),
                    note.created_at,
                    note.updated_at,
                    _dumps(note.metadata),
                ),
            )
            try:
                conn.execute("DELETE FROM research_notes_fts WHERE note_id = ?", (note.id,))
                conn.execute(
                    "INSERT INTO research_notes_fts(note_id, owner_user_id, package_id, title, content) VALUES (?, ?, ?, ?, ?)",
                    (note.id, note.owner_user_id, note.package_id, note.title, note.content),
                )
            except sqlite3.OperationalError:
                pass
        return note

    def list_notes(self, *, owner_user_id: str, package_id: str) -> list[ResearchNote]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_notes WHERE owner_user_id = ? AND package_id = ? ORDER BY updated_at DESC",
                (owner_user_id, package_id),
            ).fetchall()
        return [self._note_from_row(row) for row in rows]

    def get_note(self, *, owner_user_id: str, package_id: str, note_id: str) -> ResearchNote | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_notes WHERE id = ? AND owner_user_id = ? AND package_id = ?",
                (note_id, owner_user_id, package_id),
            ).fetchone()
        return self._note_from_row(row) if row else None

    def delete_note(self, *, owner_user_id: str, package_id: str, note_id: str) -> ResearchNote | None:
        note = self.get_note(owner_user_id=owner_user_id, package_id=package_id, note_id=note_id)
        if note is None:
            return None
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                "DELETE FROM research_notes WHERE id = ? AND owner_user_id = ? AND package_id = ?",
                (note_id, owner_user_id, package_id),
            )
            try:
                conn.execute("DELETE FROM research_notes_fts WHERE note_id = ?", (note_id,))
            except sqlite3.OperationalError:
                pass
        return note

    def search_notes(self, *, owner_user_id: str, package_id: str, query: str, limit: int) -> list[tuple[float, ResearchNote]]:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            return []
        with self._lock, self._connect() as conn:
            try:
                terms = [term for term in normalized.replace('"', " ").split() if term]
                match_query = " OR ".join(f'"{term}"' for term in terms)
                rows = conn.execute(
                    """
                    SELECT research_notes.*, bm25(research_notes_fts) AS rank
                    FROM research_notes_fts
                    JOIN research_notes ON research_notes.id = research_notes_fts.note_id
                    WHERE research_notes_fts MATCH ?
                        AND research_notes_fts.owner_user_id = ?
                        AND research_notes_fts.package_id = ?
                    ORDER BY rank ASC
                    LIMIT ?
                    """,
                    (match_query, owner_user_id, package_id, limit),
                ).fetchall()
                return [(1.0 / (1.0 + abs(float(row["rank"] or 0.0))), self._note_from_row(row)) for row in rows]
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT * FROM research_notes
                    WHERE owner_user_id = ? AND package_id = ? AND (title LIKE ? OR content LIKE ?)
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (owner_user_id, package_id, f"%{normalized}%", f"%{normalized}%", limit),
                ).fetchall()
                return [(1.0, self._note_from_row(row)) for row in rows]

    def save_thread(self, thread: ResearchChatThread) -> ResearchChatThread:
        thread = thread.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_chat_threads(
                    id, owner_user_id, package_id, title, context_mode, source_ingestion_ids_json,
                    note_ids_json, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    context_mode = excluded.context_mode,
                    source_ingestion_ids_json = excluded.source_ingestion_ids_json,
                    note_ids_json = excluded.note_ids_json,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    thread.id,
                    thread.owner_user_id,
                    thread.package_id,
                    thread.title,
                    thread.context_mode,
                    _dumps(thread.source_ingestion_ids),
                    _dumps(thread.note_ids),
                    thread.created_at,
                    thread.updated_at,
                    _dumps(thread.metadata),
                ),
            )
        return thread

    def list_threads(self, *, owner_user_id: str, package_id: str) -> list[ResearchChatThread]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_chat_threads WHERE owner_user_id = ? AND package_id = ? ORDER BY updated_at DESC",
                (owner_user_id, package_id),
            ).fetchall()
        return [self._thread_from_row(row) for row in rows]

    def get_thread(self, *, owner_user_id: str, package_id: str, thread_id: str) -> ResearchChatThread | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_chat_threads WHERE id = ? AND owner_user_id = ? AND package_id = ?",
                (thread_id, owner_user_id, package_id),
            ).fetchone()
        return self._thread_from_row(row) if row else None

    def delete_thread(self, *, owner_user_id: str, package_id: str, thread_id: str) -> ResearchChatThread | None:
        thread = self.get_thread(owner_user_id=owner_user_id, package_id=package_id, thread_id=thread_id)
        if thread is None:
            return None
        with self._lock, self._connect() as conn, conn:
            conn.execute("DELETE FROM research_chat_threads WHERE id = ?", (thread_id,))
        return thread

    def save_message(self, message: ResearchChatMessage) -> ResearchChatMessage:
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_chat_messages(id, thread_id, role, content, citations_json, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.thread_id,
                    message.role,
                    message.content,
                    _dumps([item.model_dump(mode="json") for item in message.citations]),
                    message.created_at,
                    _dumps(message.metadata),
                ),
            )
        return message

    def list_messages(self, *, thread_id: str) -> list[ResearchChatMessage]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_chat_messages WHERE thread_id = ? ORDER BY created_at ASC",
                (thread_id,),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def save_artifact(self, artifact: ResearchArtifact, *, audio_path: str | None = None) -> ResearchArtifact:
        artifact = artifact.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            current = conn.execute("SELECT audio_path FROM research_artifacts WHERE id = ?", (artifact.id,)).fetchone()
            stored_audio_path = audio_path if audio_path is not None else (str(current["audio_path"]) if current and current["audio_path"] else None)
            conn.execute(
                """
                INSERT INTO research_artifacts(
                    id, owner_user_id, package_id, kind, status, title, content, transcript, audio_path,
                    source_ingestion_ids_json, note_ids_json, citations_json, error, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    title = excluded.title,
                    content = excluded.content,
                    transcript = excluded.transcript,
                    audio_path = excluded.audio_path,
                    source_ingestion_ids_json = excluded.source_ingestion_ids_json,
                    note_ids_json = excluded.note_ids_json,
                    citations_json = excluded.citations_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    artifact.id,
                    artifact.owner_user_id,
                    artifact.package_id,
                    artifact.kind,
                    artifact.status,
                    artifact.title,
                    artifact.content,
                    artifact.transcript,
                    stored_audio_path,
                    _dumps(artifact.source_ingestion_ids),
                    _dumps(artifact.note_ids),
                    _dumps([item.model_dump(mode="json") for item in artifact.citations]),
                    artifact.error,
                    artifact.created_at,
                    artifact.updated_at,
                    _dumps(artifact.metadata),
                ),
            )
        return artifact

    def list_artifacts(self, *, owner_user_id: str, package_id: str) -> list[ResearchArtifact]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_artifacts WHERE owner_user_id = ? AND package_id = ? ORDER BY updated_at DESC",
                (owner_user_id, package_id),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def list_pending_artifacts(self) -> list[ResearchArtifact]:
        """Return persisted work that must be resumed after a service restart."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_artifacts
                WHERE status IN ('queued', 'generating')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, *, owner_user_id: str, package_id: str, artifact_id: str) -> ResearchArtifact | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_artifacts WHERE id = ? AND owner_user_id = ? AND package_id = ?",
                (artifact_id, owner_user_id, package_id),
            ).fetchone()
        return self._artifact_from_row(row) if row else None

    def get_artifact_audio_path(self, *, owner_user_id: str, package_id: str, artifact_id: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT audio_path FROM research_artifacts WHERE id = ? AND owner_user_id = ? AND package_id = ?",
                (artifact_id, owner_user_id, package_id),
            ).fetchone()
        return str(row["audio_path"]) if row and row["audio_path"] else None

    def delete_artifact(self, *, owner_user_id: str, package_id: str, artifact_id: str) -> ResearchArtifact | None:
        artifact = self.get_artifact(owner_user_id=owner_user_id, package_id=package_id, artifact_id=artifact_id)
        if artifact is None:
            return None
        with self._lock, self._connect() as conn, conn:
            conn.execute("DELETE FROM research_artifacts WHERE id = ?", (artifact_id,))
        return artifact

    @staticmethod
    def _citations(raw: str | None) -> list[ResearchCitation]:
        return [ResearchCitation.model_validate(item) for item in _loads(raw, []) if isinstance(item, dict)]

    def _note_from_row(self, row: sqlite3.Row) -> ResearchNote:
        return ResearchNote(
            id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            title=row["title"], content=row["content"], tags=_loads(row["tags_json"], []),
            citations=self._citations(row["citations_json"]), created_at=row["created_at"],
            updated_at=row["updated_at"], metadata=_loads(row["metadata_json"], {}),
        )

    def _thread_from_row(self, row: sqlite3.Row) -> ResearchChatThread:
        return ResearchChatThread(
            id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            title=row["title"], context_mode=row["context_mode"],
            source_ingestion_ids=_loads(row["source_ingestion_ids_json"], []),
            note_ids=_loads(row["note_ids_json"], []), created_at=row["created_at"],
            updated_at=row["updated_at"], metadata=_loads(row["metadata_json"], {}),
        )

    def _message_from_row(self, row: sqlite3.Row) -> ResearchChatMessage:
        return ResearchChatMessage(
            id=row["id"], thread_id=row["thread_id"], role=row["role"], content=row["content"],
            citations=self._citations(row["citations_json"]), created_at=row["created_at"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def _artifact_from_row(self, row: sqlite3.Row) -> ResearchArtifact:
        audio_url = f"/api/packages/{row['package_id']}/research/artifacts/{row['id']}/audio" if row["audio_path"] else None
        return ResearchArtifact(
            id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            kind=row["kind"], status=row["status"], title=row["title"], content=row["content"],
            transcript=row["transcript"], audio_url=audio_url,
            source_ingestion_ids=_loads(row["source_ingestion_ids_json"], []),
            note_ids=_loads(row["note_ids_json"], []), citations=self._citations(row["citations_json"]),
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
            metadata=_loads(row["metadata_json"], {}),
        )


research_store = ResearchStore()
