from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, TypeVar

from app.models import now_iso
from app.research_models import ResearchEpisodeProfile, ResearchSpeakerProfile, ResearchTransformation
from app.services import workspace_state


Profile = TypeVar("Profile", ResearchTransformation, ResearchSpeakerProfile, ResearchEpisodeProfile)


class ResearchConfigurationStore:
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
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        self._initialize(conn, path)
        return conn

    def _initialize(self, conn: sqlite3.Connection, path: Path) -> None:
        with self._lock:
            key = str(path)
            if key in self._initialized_paths:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_transformations (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, package_id TEXT NOT NULL,
                    name TEXT NOT NULL, instructions TEXT NOT NULL, output_kind TEXT NOT NULL,
                    run_on_import INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_research_transformations_scope
                    ON research_transformations(owner_user_id, package_id, updated_at);
                CREATE TABLE IF NOT EXISTS research_speaker_profiles (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, package_id TEXT NOT NULL,
                    name TEXT NOT NULL, speakers_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_speaker_profiles_scope
                    ON research_speaker_profiles(owner_user_id, package_id, updated_at);
                CREATE TABLE IF NOT EXISTS research_episode_profiles (
                    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, package_id TEXT NOT NULL,
                    name TEXT NOT NULL, language TEXT NOT NULL, tone TEXT NOT NULL, length TEXT NOT NULL,
                    segment_count INTEGER NOT NULL, instructions TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_episode_profiles_scope
                    ON research_episode_profiles(owner_user_id, package_id, updated_at);
                """
            )
            self._initialized_paths.add(key)

    def save_transformation(self, item: ResearchTransformation) -> ResearchTransformation:
        item = item.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_transformations(id, owner_user_id, package_id, name, instructions, output_kind,
                    run_on_import, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, instructions=excluded.instructions,
                    output_kind=excluded.output_kind, run_on_import=excluded.run_on_import,
                    updated_at=excluded.updated_at, metadata_json=excluded.metadata_json
                """,
                (item.id, item.owner_user_id, item.package_id, item.name, item.instructions, item.output_kind,
                 int(item.run_on_import), item.created_at, item.updated_at, json.dumps(item.metadata, ensure_ascii=False)),
            )
        return item

    def list_transformations(self, *, owner_user_id: str, package_id: str) -> list[ResearchTransformation]:
        return self._list("research_transformations", owner_user_id, package_id, self._transformation)

    def list_import_transformations(
        self,
        *,
        owner_user_id: str,
        package_id: str,
    ) -> list[ResearchTransformation]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_transformations
                WHERE owner_user_id = ? AND package_id = ? AND run_on_import = 1
                ORDER BY created_at, id
                """,
                (owner_user_id, package_id),
            ).fetchall()
        return [self._transformation(row) for row in rows]

    def get_transformation(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchTransformation | None:
        return self._get("research_transformations", owner_user_id, package_id, item_id, self._transformation)

    def delete_transformation(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchTransformation | None:
        return self._delete("research_transformations", owner_user_id, package_id, item_id, self._transformation)

    def save_speaker_profile(self, item: ResearchSpeakerProfile) -> ResearchSpeakerProfile:
        item = item.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_speaker_profiles(id, owner_user_id, package_id, name, speakers_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, speakers_json=excluded.speakers_json, updated_at=excluded.updated_at
                """,
                (item.id, item.owner_user_id, item.package_id, item.name,
                 json.dumps([speaker.model_dump(mode="json") for speaker in item.speakers], ensure_ascii=False),
                 item.created_at, item.updated_at),
            )
        return item

    def list_speaker_profiles(self, *, owner_user_id: str, package_id: str) -> list[ResearchSpeakerProfile]:
        return self._list("research_speaker_profiles", owner_user_id, package_id, self._speaker_profile)

    def get_speaker_profile(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchSpeakerProfile | None:
        return self._get("research_speaker_profiles", owner_user_id, package_id, item_id, self._speaker_profile)

    def delete_speaker_profile(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchSpeakerProfile | None:
        return self._delete("research_speaker_profiles", owner_user_id, package_id, item_id, self._speaker_profile)

    def save_episode_profile(self, item: ResearchEpisodeProfile) -> ResearchEpisodeProfile:
        item = item.model_copy(update={"updated_at": now_iso()})
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                """
                INSERT INTO research_episode_profiles(id, owner_user_id, package_id, name, language, tone, length,
                    segment_count, instructions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, language=excluded.language, tone=excluded.tone,
                    length=excluded.length, segment_count=excluded.segment_count,
                    instructions=excluded.instructions, updated_at=excluded.updated_at
                """,
                (item.id, item.owner_user_id, item.package_id, item.name, item.language, item.tone,
                 item.length, item.segment_count, item.instructions, item.created_at, item.updated_at),
            )
        return item

    def list_episode_profiles(self, *, owner_user_id: str, package_id: str) -> list[ResearchEpisodeProfile]:
        return self._list("research_episode_profiles", owner_user_id, package_id, self._episode_profile)

    def get_episode_profile(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchEpisodeProfile | None:
        return self._get("research_episode_profiles", owner_user_id, package_id, item_id, self._episode_profile)

    def delete_episode_profile(self, *, owner_user_id: str, package_id: str, item_id: str) -> ResearchEpisodeProfile | None:
        return self._delete("research_episode_profiles", owner_user_id, package_id, item_id, self._episode_profile)

    def _list(self, table: str, owner: str, package: str, factory):
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM {table} WHERE owner_user_id = ? AND package_id = ? ORDER BY updated_at DESC", (owner, package)).fetchall()
        return [factory(row) for row in rows]

    def _get(self, table: str, owner: str, package: str, item_id: str, factory):
        with self._lock, self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND owner_user_id = ? AND package_id = ?", (item_id, owner, package)).fetchone()
        return factory(row) if row else None

    def _delete(self, table: str, owner: str, package: str, item_id: str, factory):
        item = self._get(table, owner, package, item_id, factory)
        if item is None:
            return None
        with self._lock, self._connect() as conn, conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ? AND owner_user_id = ? AND package_id = ?", (item_id, owner, package))
        return item

    @staticmethod
    def _transformation(row: sqlite3.Row) -> ResearchTransformation:
        try: metadata: dict[str, Any] = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError: metadata = {}
        return ResearchTransformation(id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            name=row["name"], instructions=row["instructions"], output_kind=row["output_kind"],
            run_on_import=bool(row["run_on_import"]), created_at=row["created_at"], updated_at=row["updated_at"], metadata=metadata)

    @staticmethod
    def _speaker_profile(row: sqlite3.Row) -> ResearchSpeakerProfile:
        return ResearchSpeakerProfile(id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            name=row["name"], speakers=json.loads(row["speakers_json"] or "[]"), created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _episode_profile(row: sqlite3.Row) -> ResearchEpisodeProfile:
        return ResearchEpisodeProfile(id=row["id"], owner_user_id=row["owner_user_id"], package_id=row["package_id"],
            name=row["name"], language=row["language"], tone=row["tone"], length=row["length"],
            segment_count=row["segment_count"], instructions=row["instructions"], created_at=row["created_at"], updated_at=row["updated_at"])


research_configuration_store = ResearchConfigurationStore()
