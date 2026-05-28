from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.models import ResourceLibraryItem, ResourceSegment, now_iso
from app.services.resource_embedding import SegmentEmbeddingRecord, resource_embedding_service
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

            CREATE TABLE IF NOT EXISTS resource_segment_embeddings (
                resource_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                text_hash TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (resource_id, segment_id, provider, model)
            );

            CREATE INDEX IF NOT EXISTS idx_resource_segment_embeddings_resource
                ON resource_segment_embeddings(resource_id, provider, model);

            CREATE INDEX IF NOT EXISTS idx_resource_segment_embeddings_hash
                ON resource_segment_embeddings(text_hash);
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
        embedding_rows = conn.execute(
            """
            SELECT *
            FROM resource_segment_embeddings
            WHERE resource_id = ?
            ORDER BY provider, model
            """,
            (resource_id,),
        ).fetchall()
        embeddings_by_segment: dict[str, list[sqlite3.Row]] = {}
        for row in embedding_rows:
            embeddings_by_segment.setdefault(row["segment_id"], []).append(row)
        preferred_spec = resource_embedding_service.current_spec()
        rows = conn.execute(
            """
            SELECT *
            FROM resource_segments
            WHERE resource_id = ?
            ORDER BY order_index, segment_id
            """,
            (resource_id,),
        ).fetchall()
        segments: list[ResourceSegment] = []
        for row in rows:
            embedding_row = _preferred_embedding_row(
                embeddings_by_segment.get(row["segment_id"], []),
                provider=preferred_spec.provider,
                model=preferred_spec.model,
                text_hash=row["text_hash"],
            )
            segments.append(
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
                    embedding=_loads(embedding_row["embedding_json"], []) if embedding_row is not None else [],
                    embedding_provider=embedding_row["provider"] if embedding_row is not None else None,
                    embedding_model=embedding_row["model"] if embedding_row is not None else None,
                )
            )
        return segments

    def ensure_segments(self, resource: ResourceLibraryItem) -> list[ResourceSegment]:
        if resource.segments:
            return resource.segments
        return build_resource_segments(resource)

    def replace_segments(
        self,
        conn: sqlite3.Connection,
        resource: ResourceLibraryItem,
    ) -> list[ResourceSegment]:
        existing_embeddings = self._read_embedding_records(conn, resource.id)
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
        self._replace_embeddings(conn, segments, existing_embeddings)
        return segments

    def delete_for_owner(self, conn: sqlite3.Connection, owner_user_id: str | None) -> None:
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
        conn.execute("DELETE FROM resource_segment_embeddings WHERE resource_id = ?", (resource_id,))
        if not self.fts_available:
            return
        try:
            conn.execute("DELETE FROM resource_segments_fts WHERE resource_id = ?", (resource_id,))
        except sqlite3.OperationalError:
            self.fts_available = False

    def _read_embedding_records(
        self,
        conn: sqlite3.Connection,
        resource_id: str,
    ) -> dict[tuple[str, str, str], SegmentEmbeddingRecord]:
        rows = conn.execute(
            """
            SELECT *
            FROM resource_segment_embeddings
            WHERE resource_id = ?
            """,
            (resource_id,),
        ).fetchall()
        records: dict[tuple[str, str, str], SegmentEmbeddingRecord] = {}
        for row in rows:
            records[(row["segment_id"], row["provider"], row["model"])] = SegmentEmbeddingRecord(
                resource_id=row["resource_id"],
                segment_id=row["segment_id"],
                text_hash=row["text_hash"],
                provider=row["provider"],
                model=row["model"],
                dimensions=row["dimensions"],
                embedding=_loads(row["embedding_json"], []),
            )
        return records

    def _replace_embeddings(
        self,
        conn: sqlite3.Connection,
        segments: list[ResourceSegment],
        existing_embeddings: dict[tuple[str, str, str], SegmentEmbeddingRecord],
    ) -> None:
        segments_by_id = {segment.segment_id: segment for segment in segments}
        reusable: dict[tuple[str, str, str], SegmentEmbeddingRecord] = {}
        for key, record in existing_embeddings.items():
            segment = segments_by_id.get(record.segment_id)
            if segment is None or segment.text_hash != record.text_hash or not record.embedding:
                continue
            reusable[key] = record
            self._insert_embedding(conn, record)

        spec = resource_embedding_service.current_spec()
        missing_segments = [
            segment
            for segment in segments
            if (segment.segment_id, spec.provider, spec.model) not in reusable
        ]
        generated = resource_embedding_service.embed_segments(missing_segments)
        for segment in segments:
            target_record = reusable.get((segment.segment_id, spec.provider, spec.model)) or generated.get(segment.segment_id)
            if target_record is None:
                continue
            if (segment.segment_id, target_record.provider, target_record.model) not in reusable:
                self._insert_embedding(conn, target_record)
            segment.embedding = target_record.embedding
            segment.embedding_provider = target_record.provider
            segment.embedding_model = target_record.model

    def _insert_embedding(self, conn: sqlite3.Connection, record: SegmentEmbeddingRecord) -> None:
        if not record.embedding:
            return
        conn.execute(
            """
            INSERT OR REPLACE INTO resource_segment_embeddings(
                resource_id, segment_id, provider, model, dimensions,
                text_hash, embedding_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.resource_id,
                record.segment_id,
                record.provider,
                record.model,
                record.dimensions,
                record.text_hash,
                _dumps(record.embedding),
                now_iso(),
            ),
        )

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


def _preferred_embedding_row(
    rows: list[sqlite3.Row],
    *,
    provider: str,
    model: str,
    text_hash: str,
) -> sqlite3.Row | None:
    matching_hash_rows = [row for row in rows if row["text_hash"] == text_hash]
    for row in matching_hash_rows:
        if row["provider"] == provider and row["model"] == model:
            return row
    return matching_hash_rows[0] if matching_hash_rows else None
