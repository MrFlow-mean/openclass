from __future__ import annotations

import json
import re
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
    now_iso,
)
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


class SourceStructureStore:
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
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_source_chunks_source
                    ON source_chunks(owner_user_id, package_id, source_ingestion_id, order_index);
                CREATE INDEX IF NOT EXISTS idx_source_chunks_chapter
                    ON source_chunks(owner_user_id, package_id, chapter_id, order_index);
                """
            )
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
        with self._lock:
            with self._connect() as conn:
                with conn:
                    for table in ("source_chunks", "source_chapters", "source_structures"):
                        conn.execute(
                            f"""
                            DELETE FROM {table}
                            WHERE owner_user_id = ? AND package_id = ? AND source_ingestion_id = ?
                            """,
                            (owner_user_id, package_id, source_id),
                        )

    def save_structure_bundle(
        self,
        *,
        structure: SourceStructure,
        chapters: list[SourceChapter],
        chunks: list[SourceChunk],
    ) -> SourceStructure:
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
                            metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                _dumps(chunk.metadata),
                            )
                            for chunk in chunks
                        ],
                    )
        return structure

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
        return SourceStructureView(
            source=self.attach_summary(source),
            structure=self._structure_from_row(structure_row) if structure_row else None,
            chapters=[self._chapter_from_row(row) for row in chapter_rows],
            chunks=[self._chunk_from_row(row) for row in chunk_rows],
        )

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
                WHERE owner_user_id = ? AND package_id = ? AND chapter_id = ?
                ORDER BY order_index
                """,
                (owner_user_id, package_id, chapter.id),
            ).fetchall()
            chunks = [self._chunk_from_row(row) for row in chunk_rows]
            if not chunks:
                continue
            chunk_ids: list[str] = []
            text_parts: list[str] = []
            chunk_tokens = 0
            for chunk in chunks:
                if used_tokens and used_tokens + chunk.token_count > token_budget:
                    break
                chunk_ids.append(chunk.id)
                text_parts.append(chunk.text)
                chunk_tokens += chunk.token_count
                used_tokens += chunk.token_count
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
    ) -> list[RetrievalEvidence]:
        terms = _search_terms(query)
        if not terms:
            return []
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
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
                        AND source_ingestions.status = 'ready'
                        AND source_structures.status IN ('ready', 'linear_only')
                    ORDER BY source_chunks.order_index ASC
                    LIMIT 800
                    """,
                    (owner_user_id, package_id),
                ).fetchall()

        scored_rows: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            score = _chunk_search_score(str(row["text"] or ""), terms)
            if score > 0:
                scored_rows.append((score, row))
        scored_rows.sort(key=lambda item: (-item[0], item[1]["order_index"]))

        evidence: list[RetrievalEvidence] = []
        used_tokens = 0
        for score, row in scored_rows:
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
                    relevance_score=score,
                    reason="Open Notebook 不可用或未命中时，命中 OpenClass 本地结构索引正文片段。",
                    token_count=token_count,
                    metadata={
                        "retrieval_mode": "local_chunk_search",
                        "source_locator": chunk.source_locator,
                    },
                )
            )
            if len(evidence) >= limit or used_tokens >= token_budget:
                break
        return evidence

    def _delete_index_rows(self, conn: sqlite3.Connection, structure: SourceStructure) -> None:
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


def _compact_text(text: str, limit: int) -> str:
    compacted = " ".join(text.split())
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    lowered = query.lower()
    for token in re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*|\d+(?:\.\d+)+", lowered):
        if len(token) >= 2:
            terms.append(token)
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        if len(sequence) <= 6:
            terms.append(sequence)
        else:
            terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    seen: set[str] = set()
    return [term for term in terms if not (term in seen or seen.add(term))]


def _chunk_search_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    score = 0.0
    matched = 0
    for term in terms:
        count = lowered.count(term.lower())
        if count <= 0:
            continue
        matched += 1
        score += min(count, 3) * (1.0 + min(len(term), 12) / 12)
    if matched == 0:
        return 0.0
    return score + matched / max(len(terms), 1)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _chapter_page_range(chapter: SourceChapter) -> str:
    if chapter.page_start is None:
        return ""
    if chapter.page_end is None or chapter.page_end == chapter.page_start:
        return f"p. {chapter.page_start}"
    return f"pp. {chapter.page_start}-{chapter.page_end}"


def _chunk_page_range(chunk: SourceChunk) -> str:
    if chunk.page_start is None:
        return ""
    if chunk.page_end is None or chunk.page_end == chunk.page_start:
        return f"p. {chunk.page_start}"
    return f"pp. {chunk.page_start}-{chunk.page_end}"


source_structure_store = SourceStructureStore()
