from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.models import ResourceLibraryItem, ResourceSegment
from app.services.resource_library import build_resource_segments


class ResourceSegmentStore:
    def __init__(self) -> None:
        self.fts_available = False

    def create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resource_segments (
                resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                chapter_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                heading_path_json TEXT NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                page_range TEXT,
                before_segment_id TEXT,
                after_segment_id TEXT,
                PRIMARY KEY (resource_id, segment_id)
            );

            CREATE INDEX IF NOT EXISTS idx_resource_segments_resource
                ON resource_segments(resource_id, order_index);

            CREATE INDEX IF NOT EXISTS idx_resource_segments_chapter
                ON resource_segments(resource_id, chapter_id, order_index);

            CREATE INDEX IF NOT EXISTS idx_resource_segments_hash
                ON resource_segments(text_hash);
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS resource_segments_fts USING fts5(
                    resource_id UNINDEXED,
                    chapter_id UNINDEXED,
                    segment_id UNINDEXED,
                    heading_path,
                    text,
                    keywords,
                    tokenize = 'unicode61'
                )
                """
            )
        except sqlite3.OperationalError:
            self.fts_available = False
        else:
            self.fts_available = True

    def read_segments(self, conn: sqlite3.Connection, resource_id: str) -> list[ResourceSegment]:
        rows = conn.execute(
            """
            SELECT *
            FROM resource_segments
            WHERE resource_id = ?
            ORDER BY order_index, segment_id
            """,
            (resource_id,),
        ).fetchall()
        return [
            ResourceSegment(
                segment_id=row["segment_id"],
                resource_id=row["resource_id"],
                chapter_id=row["chapter_id"],
                heading_path=_loads(row["heading_path_json"], []),
                order_index=row["order_index"],
                text=row["text"],
                text_hash=row["text_hash"],
                keywords=_loads(row["keywords_json"], []),
                page_range=row["page_range"],
                before_segment_id=row["before_segment_id"],
                after_segment_id=row["after_segment_id"],
            )
            for row in rows
        ]

    def ensure_segments(self, resource: ResourceLibraryItem) -> list[ResourceSegment]:
        if resource.segments:
            return resource.segments
        return build_resource_segments(resource)

    def replace_segments(
        self,
        conn: sqlite3.Connection,
        resource: ResourceLibraryItem,
    ) -> list[ResourceSegment]:
        conn.execute("DELETE FROM resource_segments WHERE resource_id = ?", (resource.id,))
        self.delete_for_resource(conn, resource.id)
        segments = resource.segments or build_resource_segments(resource)
        for segment in segments:
            conn.execute(
                """
                INSERT INTO resource_segments(
                    resource_id, chapter_id, segment_id, order_index, heading_path_json,
                    text, text_hash, keywords_json, page_range, before_segment_id, after_segment_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource.id,
                    segment.chapter_id,
                    segment.segment_id,
                    segment.order_index,
                    _dumps(segment.heading_path),
                    segment.text,
                    segment.text_hash,
                    _dumps(segment.keywords),
                    segment.page_range,
                    segment.before_segment_id,
                    segment.after_segment_id,
                ),
            )
            self._insert_fts(conn, segment)
        return segments

    def delete_for_owner(self, conn: sqlite3.Connection, owner_user_id: str | None) -> None:
        if not self.fts_available:
            return
        if owner_user_id is None:
            resource_ids = [row["id"] for row in conn.execute("SELECT id FROM resources").fetchall()]
        else:
            resource_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT resources.id
                    FROM resources
                    JOIN course_packages ON course_packages.id = resources.package_id
                    WHERE course_packages.owner_user_id = ?
                    """,
                    (owner_user_id,),
                ).fetchall()
            ]
        for resource_id in resource_ids:
            self.delete_for_resource(conn, resource_id)

    def delete_for_resource(self, conn: sqlite3.Connection, resource_id: str) -> None:
        if not self.fts_available:
            return
        try:
            conn.execute("DELETE FROM resource_segments_fts WHERE resource_id = ?", (resource_id,))
        except sqlite3.OperationalError:
            self.fts_available = False

    def _insert_fts(self, conn: sqlite3.Connection, segment: ResourceSegment) -> None:
        if not self.fts_available:
            return
        try:
            conn.execute(
                """
                INSERT INTO resource_segments_fts(
                    resource_id, chapter_id, segment_id, heading_path, text, keywords
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.resource_id,
                    segment.chapter_id,
                    segment.segment_id,
                    " / ".join(segment.heading_path),
                    segment.text,
                    " ".join(segment.keywords),
                ),
            )
        except sqlite3.OperationalError:
            self.fts_available = False


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)
