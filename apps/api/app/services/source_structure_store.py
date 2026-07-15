from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.models import (
    RetrievalEvidence,
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceStructure,
    SourceStructureView,
    SourceVisualAsset,
    SourceVisualEvidence,
    now_iso,
)
from app.services import workspace_state
from app.services.native_source_index import NativeSearchMode, NativeSourceIndex, source_chunk_text_hash


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class SourceStructureStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        native_index: NativeSourceIndex | None = None,
    ) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._initialized_paths: set[str] = set()
        self.native_index = native_index or NativeSourceIndex()

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
                CREATE TABLE IF NOT EXISTS source_structures (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    has_verified_toc INTEGER NOT NULL,
                    chapter_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    error TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_source_structures_source
                    ON source_structures(owner_user_id, package_id, source_ingestion_id);
                CREATE INDEX IF NOT EXISTS idx_source_structures_status
                    ON source_structures(owner_user_id, package_id, status, updated_at);

                CREATE TABLE IF NOT EXISTS source_chapters (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    parent_id TEXT,
                    number TEXT NOT NULL,
                    normalized_number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    path_json TEXT NOT NULL DEFAULT '[]',
                    order_index INTEGER NOT NULL,
                    source_locator TEXT NOT NULL,
                    body_start_offset INTEGER,
                    body_end_offset INTEGER,
                    page_start INTEGER,
                    page_end INTEGER,
                    anchor_status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    excerpt TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_chapters_source
                    ON source_chapters(owner_user_id, package_id, source_ingestion_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_source_chapters_number
                    ON source_chapters(owner_user_id, package_id, normalized_number, anchor_status);

                CREATE TABLE IF NOT EXISTS source_chunks (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    chapter_id TEXT,
                    order_index INTEGER NOT NULL,
                    source_locator TEXT NOT NULL,
                    text TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    token_count INTEGER NOT NULL,
                    text_hash TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_chunks_source
                    ON source_chunks(owner_user_id, package_id, source_ingestion_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_source_chunks_chapter
                    ON source_chunks(owner_user_id, package_id, chapter_id, order_index);

                CREATE TABLE IF NOT EXISTS source_visual_assets (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    source_ingestion_id TEXT NOT NULL,
                    chapter_id TEXT,
                    kind TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    page_start INTEGER,
                    page_end INTEGER,
                    paragraph_index INTEGER,
                    bbox_json TEXT NOT NULL DEFAULT '[]',
                    caption TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,
                    surrounding_text TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    asset_path TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_visual_assets_source
                    ON source_visual_assets(owner_user_id, package_id, source_ingestion_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_source_visual_assets_chapter
                    ON source_visual_assets(owner_user_id, package_id, chapter_id, order_index);
                """
            )
            chunk_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(source_chunks)").fetchall()
            }
            if "text_hash" not in chunk_columns:
                conn.execute("ALTER TABLE source_chunks ADD COLUMN text_hash TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_source_chunks_text_hash
                ON source_chunks(owner_user_id, package_id, source_ingestion_id, text_hash)
                """
            )
            self.native_index.create_schema(conn)
            self.native_index.backfill(conn)
            self._initialized_paths.add(path_key)

    def attach_summary(self, record: SourceIngestionRecord) -> SourceIngestionRecord:
        structure = self.get_structure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        if structure is None:
            return record
        return record.model_copy(
            update={
                "structure_status": structure.status,
                "structure_strategy": structure.strategy,
                "structure_has_verified_toc": structure.has_verified_toc,
                "structure_error": structure.error,
                "structure_updated_at": structure.updated_at,
            }
        )

    def delete_for_source(self, *, owner_user_id: str, package_id: str, source_id: str) -> None:
        asset_paths: list[str] = []
        with self._lock:
            with self._connect() as conn:
                asset_paths = [
                    str(row["asset_path"])
                    for row in conn.execute(
                        """
                        SELECT asset_path FROM source_visual_assets
                        WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        """,
                        (owner_user_id, package_id, source_id),
                    ).fetchall()
                ]
                with conn:
                    self.native_index.delete_for_source(
                        conn,
                        owner_user_id=owner_user_id,
                        package_id=package_id,
                        source_ingestion_id=source_id,
                    )
                    for table in (
                        "source_visual_assets",
                        "source_chunks",
                        "source_chapters",
                        "source_structures",
                    ):
                        conn.execute(
                            f"""
                            DELETE FROM {table}
                            WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                            """,
                            (owner_user_id, package_id, source_id),
                        )
        _remove_asset_files(asset_paths)

    def save_structure_bundle(
        self,
        *,
        structure: SourceStructure,
        chapters: list[SourceChapter],
        chunks: list[SourceChunk],
        visuals: list[SourceVisualAsset] | None = None,
    ) -> SourceStructure:
        visuals = visuals or []
        chunks = [_chunk_with_text_hash(chunk) for chunk in chunks]
        old_asset_paths: list[str] = []
        stamp = now_iso()
        structure = structure.model_copy(
            update={
                "updated_at": stamp,
                "chapter_count": len(chapters),
                        "chunk_count": len(chunks),
                "has_verified_toc": any(chapter.anchor_status == "verified" for chapter in chapters),
            }
        )
        with self._lock:
            with self._connect() as conn:
                old_asset_paths = [
                    str(row["asset_path"])
                    for row in conn.execute(
                        """
                        SELECT asset_path FROM source_visual_assets
                        WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        """,
                        (
                            structure.owner_user_id,
                            structure.package_id,
                            structure.source_ingestion_id,
                        ),
                    ).fetchall()
                ]
                with conn:
                    self._delete_index_rows(conn, structure)
                    conn.execute(
                        """
                        INSERT INTO source_structures(
                            id, owner_user_id, package_id, source_ingestion_id, status, strategy, has_verified_toc,
                            chapter_count, chunk_count, confidence, error, warnings_json, created_at, updated_at,
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(owner_user_id, package_id, source_ingestion_id) DO UPDATE SET
                            id = excluded.id,
                            status = excluded.status,
                            strategy = excluded.strategy,
                            has_verified_toc = excluded.has_verified_toc,
                            chapter_count = excluded.chapter_count,
                            chunk_count = excluded.chunk_count,
                            confidence = excluded.confidence,
                            error = excluded.error,
                            warnings_json = excluded.warnings_json,
                            updated_at = excluded.updated_at,
                            metadata_json = excluded.metadata_json
                        """,
                        (
                            structure.id,
                            structure.owner_user_id,
                            structure.package_id,
                            structure.source_ingestion_id,
                            structure.status,
                            structure.strategy,
                            int(structure.has_verified_toc),
                            structure.chapter_count,
                            structure.chunk_count,
                            structure.confidence,
                            structure.error,
                            _dumps(structure.warnings),
                            structure.created_at,
                            structure.updated_at,
                            _dumps(structure.metadata),
                        ),
                    )
                    conn.executemany(
                        """
                        INSERT INTO source_chapters(
                            id, owner_user_id, package_id, source_ingestion_id, parent_id, number,
                            normalized_number, title, level, path_json, order_index, source_locator,
                            body_start_offset, body_end_offset, page_start, page_end, anchor_status,
                            confidence, excerpt, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                chapter.id,
                                chapter.owner_user_id,
                                chapter.package_id,
                                chapter.source_ingestion_id,
                                chapter.parent_id,
                                chapter.number,
                                chapter.normalized_number,
                                chapter.title,
                                chapter.level,
                                _dumps(chapter.path),
                                chapter.order_index,
                                chapter.source_locator,
                                chapter.body_start_offset,
                                chapter.body_end_offset,
                                chapter.page_start,
                                chapter.page_end,
                                chapter.anchor_status,
                                chapter.confidence,
                                chapter.excerpt,
                                _dumps(chapter.metadata),
                            )
                            for chapter in chapters
                        ],
                    )
                    conn.executemany(
                        """
                        INSERT INTO source_chunks(
                            id, owner_user_id, package_id, source_ingestion_id, chapter_id, order_index,
                            source_locator, text, start_offset, end_offset, page_start, page_end, token_count,
                            text_hash, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                chunk.id,
                                chunk.owner_user_id,
                                chunk.package_id,
                                chunk.source_ingestion_id,
                                chunk.chapter_id,
                                chunk.order_index,
                                chunk.source_locator,
                                chunk.text,
                                chunk.start_offset,
                                chunk.end_offset,
                                chunk.page_start,
                                chunk.page_end,
                                chunk.token_count,
                                str(chunk.metadata.get("text_hash") or source_chunk_text_hash(chunk.text)),
                                _dumps(chunk.metadata),
                            )
                            for chunk in chunks
                        ],
                    )
                    conn.executemany(
                        """
                        INSERT INTO source_visual_assets(
                            id, owner_user_id, package_id, source_ingestion_id, chapter_id, kind,
                            source_locator, page_start, page_end, paragraph_index, bbox_json, caption,
                            extracted_text, surrounding_text, mime_type, asset_path, order_index,
                            content_hash, confidence, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                visual.id,
                                visual.owner_user_id,
                                visual.package_id,
                                visual.source_ingestion_id,
                                visual.chapter_id,
                                visual.kind,
                                visual.source_locator,
                                visual.page_start,
                                visual.page_end,
                                visual.paragraph_index,
                                _dumps(visual.bbox),
                                visual.caption,
                                visual.extracted_text,
                                visual.surrounding_text,
                                visual.mime_type,
                                visual.asset_path,
                                visual.order_index,
                                visual.content_hash,
                                visual.confidence,
                                _dumps(visual.metadata),
                            )
                            for visual in visuals
                        ],
                    )
                    self.native_index.index_chunks(conn, chunks)
        retained_paths = {visual.asset_path for visual in visuals if visual.asset_path}
        _remove_asset_files(path for path in old_asset_paths if path not in retained_paths)
        return structure

    def record_rebuild_failure(self, *, structure: SourceStructure, error: str) -> SourceStructure:
        """Expose a failed reparse without replacing the last usable index."""
        warning = "资料重新解析失败，已保留上一次可用的目录和正文索引。"
        warnings = list(dict.fromkeys([*structure.warnings, warning]))
        with self._lock:
            with self._connect() as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE source_structures
                        SET error = ?, warnings_json = ?
                        WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                        """,
                        (
                            error,
                            _dumps(warnings),
                            structure.owner_user_id,
                            structure.package_id,
                            structure.source_ingestion_id,
                        ),
                    )
        return structure.model_copy(update={"error": error, "warnings": warnings})

    def get_structure(self, *, owner_user_id: str, package_id: str, source_id: str) -> SourceStructure | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM source_structures
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (owner_user_id, package_id, source_id),
                ).fetchone()
        return self._structure_from_row(row) if row else None

    def get_structure_view(
        self,
        *,
        source: SourceIngestionRecord,
        chunk_limit: int = 20,
    ) -> SourceStructureView:
        with self._lock:
            with self._connect() as conn:
                structure_row = conn.execute(
                    """
                    SELECT *
                    FROM source_structures
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    """,
                    (source.owner_user_id, source.package_id, source.id),
                ).fetchone()
                chapter_rows = conn.execute(
                    """
                    SELECT *
                    FROM source_chapters
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    ORDER BY order_index
                    """,
                    (source.owner_user_id, source.package_id, source.id),
                ).fetchall()
                chunk_rows = conn.execute(
                    """
                    SELECT *
                    FROM source_chunks
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    ORDER BY order_index
                    LIMIT ?
                    """,
                    (source.owner_user_id, source.package_id, source.id, chunk_limit),
                ).fetchall()
                visual_rows = conn.execute(
                    """
                    SELECT * FROM source_visual_assets
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    ORDER BY order_index
                    """,
                    (source.owner_user_id, source.package_id, source.id),
                ).fetchall()
        return SourceStructureView(
            source=self.attach_summary(source),
            structure=self._structure_from_row(structure_row) if structure_row else None,
            chapters=[self._chapter_from_row(row) for row in chapter_rows],
            chunks=[self._chunk_from_row(row) for row in chunk_rows],
            visuals=[self._visual_from_row(row) for row in visual_rows],
        )

    def visual_evidence_for_scope(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        source_ingestion_id: str,
        chapter_id: str | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> list[SourceVisualEvidence]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM source_visual_assets
                    WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                    ORDER BY order_index
                    """,
                    (owner_user_id, package_id, source_ingestion_id),
                ).fetchall()
        assets = [self._visual_from_row(row) for row in rows]
        selected = [
            asset
            for asset in assets
            if (not chapter_id or asset.chapter_id == chapter_id)
            and _visual_in_page_range(asset, page_start=page_start, page_end=page_end)
        ]
        return [
            SourceVisualEvidence(
                visual_id=asset.id,
                source_ingestion_id=asset.source_ingestion_id,
                source_chapter_id=asset.chapter_id or "",
                kind=asset.kind,
                source_locator=asset.source_locator,
                page_start=asset.page_start,
                page_end=asset.page_end,
                paragraph_index=asset.paragraph_index,
                bbox=asset.bbox,
                caption=asset.caption,
                extracted_text=asset.extracted_text,
                surrounding_text=asset.surrounding_text,
                mime_type=asset.mime_type,
                content_hash=asset.content_hash,
                confidence=asset.confidence,
            )
            for asset in selected
        ]

    def visual_asset_paths(
        self,
        *,
        owner_user_id: str,
        visual_ids: list[str],
    ) -> list[Path]:
        if not visual_ids:
            return []
        placeholders = ", ".join("?" for _ in visual_ids)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT asset_path FROM source_visual_assets
                    WHERE owner_user_id = ? AND id IN ({placeholders})
                    ORDER BY order_index
                    """,
                    [owner_user_id, *visual_ids],
                ).fetchall()
        return [Path(str(row["asset_path"])) for row in rows if str(row["asset_path"] or "")]

    def chapter_evidence_by_number(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        normalized_number: str,
        limit: int,
        token_budget: int,
    ) -> list[RetrievalEvidence]:
        with self._lock:
            with self._connect() as conn:
                chapter_rows = conn.execute(
                    """
                    SELECT source_chapters.*, source_ingestions.title AS source_title,
                        source_ingestions.source_uri AS source_uri,
                        source_ingestions.open_notebook_source_id AS open_notebook_source_id
                    FROM source_chapters
                    JOIN source_ingestions
                        ON source_ingestions.owner_user_id = source_chapters.owner_user_id
                        AND source_ingestions.package_id = source_chapters.package_id
                        AND source_ingestions.id = source_chapters.source_ingestion_id
                    JOIN source_structures
                        ON source_structures.owner_user_id = source_chapters.owner_user_id
                        AND source_structures.package_id = source_chapters.package_id
                        AND source_structures.source_ingestion_id = source_chapters.source_ingestion_id
                    WHERE source_chapters.owner_user_id = ?
                        AND source_chapters.package_id = ?
                        AND source_chapters.normalized_number = ?
                        AND source_chapters.anchor_status = 'verified'
                        AND source_structures.status = 'ready'
                        AND source_ingestions.status = 'ready'
                    ORDER BY source_chapters.confidence DESC, source_chapters.order_index ASC
                    LIMIT ?
                    """,
                    (owner_user_id, package_id, normalized_number, limit),
                ).fetchall()
                return self._chapter_evidence_from_rows(
                    conn,
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    chapter_rows=chapter_rows,
                    limit=limit,
                    token_budget=token_budget,
                )

    def chapter_evidence_by_id(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        chapter_id: str,
        limit: int,
        token_budget: int,
    ) -> list[RetrievalEvidence]:
        with self._lock:
            with self._connect() as conn:
                chapter_rows = conn.execute(
                    """
                    SELECT source_chapters.*, source_ingestions.title AS source_title,
                        source_ingestions.source_uri AS source_uri,
                        source_ingestions.open_notebook_source_id AS open_notebook_source_id
                    FROM source_chapters
                    JOIN source_ingestions
                        ON source_ingestions.owner_user_id = source_chapters.owner_user_id
                        AND source_ingestions.package_id = source_chapters.package_id
                        AND source_ingestions.id = source_chapters.source_ingestion_id
                    JOIN source_structures
                        ON source_structures.owner_user_id = source_chapters.owner_user_id
                        AND source_structures.package_id = source_chapters.package_id
                        AND source_structures.source_ingestion_id = source_chapters.source_ingestion_id
                    WHERE source_chapters.owner_user_id = ?
                        AND source_chapters.package_id = ?
                        AND source_chapters.id = ?
                        AND source_chapters.anchor_status = 'verified'
                        AND source_structures.status = 'ready'
                        AND source_ingestions.status = 'ready'
                    ORDER BY source_chapters.confidence DESC, source_chapters.order_index ASC
                    LIMIT ?
                    """,
                    (owner_user_id, package_id, chapter_id, limit),
                ).fetchall()
                return self._chapter_evidence_from_rows(
                    conn,
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    chapter_rows=chapter_rows,
                    limit=limit,
                    token_budget=token_budget,
                )

    def _chapter_evidence_from_rows(
        self,
        conn: sqlite3.Connection,
        *,
        owner_user_id: str,
        package_id: str,
        chapter_rows: list[sqlite3.Row],
        limit: int,
        token_budget: int,
    ) -> list[RetrievalEvidence]:
        evidence: list[RetrievalEvidence] = []
        used_tokens = 0
        for chapter_row in chapter_rows:
            chapter = self._chapter_from_row(chapter_row)
            chunk_rows = conn.execute(
                """
                SELECT *
                FROM source_chunks
                WHERE owner_user_id = ?
                    AND package_id = ?
                    AND source_ingestion_id = ?
                    AND (
                        chapter_id = ?
                        OR (
                            ? IS NOT NULL
                            AND ? IS NOT NULL
                            AND end_offset > ?
                            AND start_offset < ?
                        )
                    )
                ORDER BY order_index
                """,
                (
                    owner_user_id,
                    package_id,
                    chapter.source_ingestion_id,
                    chapter.id,
                    chapter.body_start_offset,
                    chapter.body_end_offset,
                    chapter.body_start_offset,
                    chapter.body_end_offset,
                ),
            ).fetchall()
            chunks = [self._chunk_from_row(row) for row in chunk_rows]
            if not chunks:
                continue
            chunk_ids: list[str] = []
            text_parts: list[str] = []
            chunk_tokens = 0
            for chunk in chunks:
                chunk_text = _chunk_text_for_chapter(chunk, chapter)
                if not chunk_text:
                    continue
                chunk_token_count = _estimate_tokens(chunk_text)
                if used_tokens and used_tokens + chunk_token_count > token_budget:
                    break
                chunk_ids.append(chunk.id)
                text_parts.append(chunk_text)
                chunk_tokens += chunk_token_count
                used_tokens += chunk_token_count
            if not text_parts:
                break
            expanded_text = "\n\n".join(text_parts).strip()
            evidence.append(
                RetrievalEvidence(
                    source_ingestion_id=chapter.source_ingestion_id,
                    open_notebook_source_id=str(chapter_row["open_notebook_source_id"] or ""),
                    source_title=str(chapter_row["source_title"] or ""),
                    source_uri=chapter_row["source_uri"],
                    chapter_id=chapter.id,
                    section_path=chapter.path or [chapter.title],
                    page_range=_chapter_page_range(chapter),
                    chunk_ids=chunk_ids,
                    excerpt=chapter.excerpt or _compact_text(expanded_text, 360),
                    expanded_text=expanded_text,
                    relevance_score=chapter.confidence,
                    reason="命中已验证目录节点并抽取对应正文范围。",
                    token_count=chunk_tokens,
                    metadata={
                        "retrieval_mode": "verified_chapter",
                        "chapter_number": chapter.normalized_number,
                        "source_locator": chapter.source_locator,
                    },
                )
            )
            if len(evidence) >= limit or used_tokens >= token_budget:
                break
        return evidence

    def chunk_evidence_search(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        limit: int,
        token_budget: int,
        source_ingestion_ids: list[str] | tuple[str, ...] | None = None,
        search_mode: NativeSearchMode = "hybrid",
    ) -> list[RetrievalEvidence]:
        if not query.strip() or limit <= 0:
            return []
        requested_source_ids = tuple(
            dict.fromkeys(source_id for source_id in source_ingestion_ids or [] if source_id)
        )
        with self._lock:
            with self._connect() as conn:
                active_source_params: list[object] = [owner_user_id, package_id]
                requested_filter = ""
                if requested_source_ids:
                    placeholders = ", ".join("?" for _ in requested_source_ids)
                    requested_filter = f"AND source_ingestions.id IN ({placeholders})"
                    active_source_params.extend(requested_source_ids)
                active_source_rows = conn.execute(
                    f"""
                    SELECT source_ingestions.id
                    FROM source_ingestions
                    JOIN source_structures
                        ON source_structures.owner_user_id = source_ingestions.owner_user_id
                        AND source_structures.package_id = source_ingestions.package_id
                        AND source_structures.source_ingestion_id = source_ingestions.id
                    WHERE source_ingestions.owner_user_id = ?
                        AND source_ingestions.package_id = ?
                        AND source_ingestions.status = 'ready'
                        AND source_structures.status IN ('ready', 'linear_only')
                        {requested_filter}
                    """,
                    active_source_params,
                ).fetchall()
                active_source_ids = tuple(str(row["id"]) for row in active_source_rows)
                matches = self.native_index.search(
                    conn,
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    query=query,
                    source_ingestion_ids=active_source_ids,
                    limit=max(limit * 8, 32),
                    search_mode=search_mode,
                )
                if not matches:
                    return []
                chunk_ids = [match.chunk_id for match in matches]
                placeholders = ", ".join("?" for _ in chunk_ids)
                rows = conn.execute(
                    f"""
                    SELECT source_chunks.*, source_ingestions.title AS source_title,
                        source_ingestions.source_uri AS source_uri,
                        source_ingestions.open_notebook_source_id AS open_notebook_source_id,
                        source_chapters.title AS chapter_title,
                        source_chapters.path_json AS chapter_path_json
                    FROM source_chunks
                    JOIN source_ingestions
                        ON source_ingestions.owner_user_id = source_chunks.owner_user_id
                        AND source_ingestions.package_id = source_chunks.package_id
                        AND source_ingestions.id = source_chunks.source_ingestion_id
                    JOIN source_structures
                        ON source_structures.owner_user_id = source_chunks.owner_user_id
                        AND source_structures.package_id = source_chunks.package_id
                        AND source_structures.source_ingestion_id = source_chunks.source_ingestion_id
                    LEFT JOIN source_chapters
                        ON source_chapters.owner_user_id = source_chunks.owner_user_id
                        AND source_chapters.package_id = source_chunks.package_id
                        AND source_chapters.id = source_chunks.chapter_id
                    WHERE source_chunks.owner_user_id = ?
                        AND source_chunks.package_id = ?
                        AND source_chunks.id IN ({placeholders})
                    """,
                    [owner_user_id, package_id, *chunk_ids],
                ).fetchall()
        rows_by_id = {str(row["id"]): row for row in rows}

        evidence: list[RetrievalEvidence] = []
        used_tokens = 0
        for match in matches:
            row = rows_by_id.get(match.chunk_id)
            if row is None:
                continue
            chunk = self._chunk_from_row(row)
            token_count = chunk.token_count or _estimate_tokens(chunk.text)
            if used_tokens and used_tokens + token_count > token_budget:
                break
            used_tokens += token_count
            chapter_path = _loads(row["chapter_path_json"], [])
            if not chapter_path and row["chapter_title"]:
                chapter_path = [str(row["chapter_title"])]
            evidence.append(
                RetrievalEvidence(
                    source_ingestion_id=chunk.source_ingestion_id,
                    open_notebook_source_id=str(row["open_notebook_source_id"] or ""),
                    source_title=str(row["source_title"] or ""),
                    source_uri=row["source_uri"],
                    section_path=chapter_path,
                    page_range=_chunk_page_range(chunk),
                    chunk_ids=[chunk.id],
                    excerpt=_compact_text(chunk.text, 360),
                    expanded_text=chunk.text,
                    relevance_score=match.hybrid_score,
                    reason=_native_index_reason(search_mode),
                    token_count=token_count,
                    metadata={
                        "retrieval_mode": "local_chunk_search",
                        "native_index_mode": search_mode,
                        "keyword_score": match.keyword_score,
                        "semantic_score": match.semantic_score,
                        "hybrid_score": match.hybrid_score,
                        "match_modes": list(match.match_modes),
                        "source_locator": chunk.source_locator,
                    },
                )
            )
            if len(evidence) >= limit or used_tokens >= token_budget:
                break
        return evidence

    def _delete_index_rows(self, conn: sqlite3.Connection, structure: SourceStructure) -> None:
        self.native_index.delete_for_source(
            conn,
            owner_user_id=structure.owner_user_id,
            package_id=structure.package_id,
            source_ingestion_id=structure.source_ingestion_id,
        )
        conn.execute(
            """
            DELETE FROM source_visual_assets
            WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
            """,
            (structure.owner_user_id, structure.package_id, structure.source_ingestion_id),
        )
        conn.execute(
            """
            DELETE FROM source_chunks
            WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
            """,
            (structure.owner_user_id, structure.package_id, structure.source_ingestion_id),
        )
        conn.execute(
            """
            DELETE FROM source_chapters
            WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
            """,
            (structure.owner_user_id, structure.package_id, structure.source_ingestion_id),
        )

    def _structure_from_row(self, row: sqlite3.Row) -> SourceStructure:
        return SourceStructure(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            source_ingestion_id=row["source_ingestion_id"],
            status=row["status"],
            strategy=row["strategy"],
            has_verified_toc=bool(row["has_verified_toc"]),
            chapter_count=row["chapter_count"],
            chunk_count=row["chunk_count"],
            confidence=row["confidence"],
            error=row["error"],
            warnings=_loads(row["warnings_json"], []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def _chapter_from_row(self, row: sqlite3.Row) -> SourceChapter:
        return SourceChapter(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            source_ingestion_id=row["source_ingestion_id"],
            parent_id=row["parent_id"],
            number=row["number"],
            normalized_number=row["normalized_number"],
            title=row["title"],
            level=row["level"],
            path=_loads(row["path_json"], []),
            order_index=row["order_index"],
            source_locator=row["source_locator"],
            body_start_offset=row["body_start_offset"],
            body_end_offset=row["body_end_offset"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            anchor_status=row["anchor_status"],
            confidence=row["confidence"],
            excerpt=row["excerpt"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def _chunk_from_row(self, row: sqlite3.Row) -> SourceChunk:
        return SourceChunk(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            source_ingestion_id=row["source_ingestion_id"],
            chapter_id=row["chapter_id"],
            order_index=row["order_index"],
            source_locator=row["source_locator"],
            text=row["text"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            token_count=row["token_count"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def _visual_from_row(self, row: sqlite3.Row) -> SourceVisualAsset:
        return SourceVisualAsset(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            package_id=row["package_id"],
            source_ingestion_id=row["source_ingestion_id"],
            chapter_id=row["chapter_id"],
            kind=row["kind"],
            source_locator=row["source_locator"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            paragraph_index=row["paragraph_index"],
            bbox=_loads(row["bbox_json"], []),
            caption=row["caption"],
            extracted_text=row["extracted_text"],
            surrounding_text=row["surrounding_text"],
            mime_type=row["mime_type"],
            asset_path=row["asset_path"],
            order_index=row["order_index"],
            content_hash=row["content_hash"],
            confidence=row["confidence"],
            metadata=_loads(row["metadata_json"], {}),
        )


def _compact_text(text: str, limit: int) -> str:
    compacted = " ".join(text.split())
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"


def _chunk_with_text_hash(chunk: SourceChunk) -> SourceChunk:
    text_hash = source_chunk_text_hash(chunk.text)
    if chunk.metadata.get("text_hash") == text_hash:
        return chunk
    return chunk.model_copy(update={"metadata": {**chunk.metadata, "text_hash": text_hash}})


def _native_index_reason(search_mode: NativeSearchMode) -> str:
    if search_mode == "text":
        return "命中 OpenClass 原生全文索引。"
    if search_mode == "semantic":
        return "命中 OpenClass 原生语义索引。"
    return "命中 OpenClass 原生全文与语义混合索引。"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _chapter_page_range(chapter: SourceChapter) -> str:
    if chapter.page_start is None:
        return ""
    display_end = max(chapter.page_start, (chapter.page_end or chapter.page_start + 1) - 1)
    if display_end == chapter.page_start:
        return f"p. {chapter.page_start}"
    return f"pp. {chapter.page_start}-{display_end}"


def _chunk_page_range(chunk: SourceChunk) -> str:
    if chunk.page_start is None:
        return ""
    display_end = max(chunk.page_start, (chunk.page_end or chunk.page_start + 1) - 1)
    if display_end == chunk.page_start:
        return f"p. {chunk.page_start}"
    return f"pp. {chunk.page_start}-{display_end}"


def _chunk_text_for_chapter(chunk: SourceChunk, chapter: SourceChapter) -> str:
    if chapter.body_start_offset is None or chapter.body_end_offset is None:
        return chunk.text.strip()
    overlap_start = max(chunk.start_offset, chapter.body_start_offset)
    overlap_end = min(chunk.end_offset, chapter.body_end_offset)
    if overlap_end <= overlap_start:
        return ""
    start_index = max(0, overlap_start - chunk.start_offset)
    end_index = max(start_index, overlap_end - chunk.start_offset)
    return chunk.text[start_index:end_index].strip()


def _visual_in_page_range(
    asset: SourceVisualAsset,
    *,
    page_start: int | None,
    page_end: int | None,
) -> bool:
    if page_start is None and page_end is None:
        return True
    if asset.page_start is None:
        return False
    lower = page_start if page_start is not None else page_end
    upper = page_end if page_end is not None else page_start
    assert lower is not None and upper is not None
    asset_end = asset.page_end if asset.page_end is not None else asset.page_start
    return asset.page_start <= upper and asset_end >= lower


def _remove_asset_files(paths) -> None:
    parents: set[Path] = set()
    for raw_path in paths:
        path = Path(str(raw_path))
        try:
            if path.is_file():
                path.unlink()
            parents.add(path.parent)
        except OSError:
            continue
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        try:
            parent.rmdir()
        except OSError:
            pass


source_structure_store = SourceStructureStore()
