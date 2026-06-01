from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.models import now_iso


@dataclass(frozen=True)
class DocumentPageRecord:
    resource_id: str
    page_number: int
    text: str
    text_source: str = "source_file"
    printed_page: int | None = None
    text_hash: str = ""


@dataclass(frozen=True)
class DocumentBlockRecord:
    resource_id: str
    block_id: str
    order_index: int
    text: str
    text_hash: str
    keywords: list[str]
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    printed_page_start: int | None = None
    printed_page_end: int | None = None
    block_type: str = "paragraph"
    text_source: str = "source_file"
    confidence: float = 1.0


class DocumentIndexStore:
    def __init__(self) -> None:
        self.fts_available = False

    def create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resource_index_jobs (
                resource_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                queued_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_resource_index_jobs_status
                ON resource_index_jobs(status, queued_at);

            CREATE TABLE IF NOT EXISTS resource_document_pages (
                resource_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                printed_page INTEGER,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                text_source TEXT NOT NULL,
                PRIMARY KEY (resource_id, page_number)
            );

            CREATE INDEX IF NOT EXISTS idx_resource_document_pages_resource
                ON resource_document_pages(resource_id, page_number);

            CREATE INDEX IF NOT EXISTS idx_resource_document_pages_printed
                ON resource_document_pages(resource_id, printed_page);

            CREATE TABLE IF NOT EXISTS resource_document_blocks (
                resource_id TEXT NOT NULL,
                block_id TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                page_start INTEGER,
                page_end INTEGER,
                printed_page_start INTEGER,
                printed_page_end INTEGER,
                heading_path_json TEXT NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                block_type TEXT NOT NULL,
                text_source TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1,
                PRIMARY KEY (resource_id, block_id)
            );

            CREATE INDEX IF NOT EXISTS idx_resource_document_blocks_resource
                ON resource_document_blocks(resource_id, order_index);

            CREATE INDEX IF NOT EXISTS idx_resource_document_blocks_pages
                ON resource_document_blocks(resource_id, page_start, page_end);

            CREATE INDEX IF NOT EXISTS idx_resource_document_blocks_hash
                ON resource_document_blocks(text_hash);
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS resource_document_blocks_fts USING fts5(
                    resource_id UNINDEXED,
                    block_id UNINDEXED,
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

    def enqueue(self, conn: sqlite3.Connection, resource_id: str) -> None:
        now = now_iso()
        conn.execute(
            """
            INSERT INTO resource_index_jobs(resource_id, status, attempts, last_error, queued_at)
            VALUES (?, 'queued', 0, NULL, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                status = 'queued',
                last_error = NULL,
                queued_at = excluded.queued_at,
                started_at = NULL,
                finished_at = NULL
            """,
            (resource_id, now),
        )

    def delete_resource(self, conn: sqlite3.Connection, resource_id: str) -> None:
        conn.execute("DELETE FROM resource_index_jobs WHERE resource_id = ?", (resource_id,))
        self.delete_resource_index(conn, resource_id)

    def delete_resource_index(self, conn: sqlite3.Connection, resource_id: str) -> None:
        conn.execute("DELETE FROM resource_document_pages WHERE resource_id = ?", (resource_id,))
        conn.execute("DELETE FROM resource_document_blocks WHERE resource_id = ?", (resource_id,))
        if self.fts_available:
            try:
                conn.execute("DELETE FROM resource_document_blocks_fts WHERE resource_id = ?", (resource_id,))
            except sqlite3.OperationalError:
                self.fts_available = False

    def replace_index(
        self,
        conn: sqlite3.Connection,
        *,
        resource_id: str,
        pages: list[DocumentPageRecord],
        blocks: list[DocumentBlockRecord],
    ) -> None:
        self.delete_resource_index(conn, resource_id)
        for page in pages:
            conn.execute(
                """
                INSERT INTO resource_document_pages(
                    resource_id, page_number, printed_page, text, text_hash, text_source
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    page.resource_id,
                    page.page_number,
                    page.printed_page,
                    page.text,
                    page.text_hash,
                    page.text_source,
                ),
            )
        for block in blocks:
            conn.execute(
                """
                INSERT INTO resource_document_blocks(
                    resource_id, block_id, order_index, page_start, page_end,
                    printed_page_start, printed_page_end, heading_path_json, text,
                    text_hash, keywords_json, block_type, text_source, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block.resource_id,
                    block.block_id,
                    block.order_index,
                    block.page_start,
                    block.page_end,
                    block.printed_page_start,
                    block.printed_page_end,
                    _dumps(block.heading_path),
                    block.text,
                    block.text_hash,
                    _dumps(block.keywords),
                    block.block_type,
                    block.text_source,
                    block.confidence,
                ),
            )
            self._insert_fts(conn, block)

    def claim_next_job(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            """
            SELECT resource_id
            FROM resource_index_jobs
            WHERE status = 'queued'
            ORDER BY queued_at
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        now = now_iso()
        conn.execute(
            """
            UPDATE resource_index_jobs
            SET status = 'processing',
                attempts = attempts + 1,
                started_at = ?
            WHERE resource_id = ?
            """,
            (now, row["resource_id"]),
        )
        return str(row["resource_id"])

    def finish_job(self, conn: sqlite3.Connection, resource_id: str, *, status: str, error: str | None = None) -> None:
        conn.execute(
            """
            UPDATE resource_index_jobs
            SET status = ?,
                last_error = ?,
                finished_at = ?
            WHERE resource_id = ?
            """,
            (status, error, now_iso(), resource_id),
        )

    def read_pages(self, conn: sqlite3.Connection, resource_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM resource_document_pages
            WHERE resource_id = ?
            ORDER BY page_number
            """,
            (resource_id,),
        ).fetchall()

    def read_blocks(self, conn: sqlite3.Connection, resource_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT *
            FROM resource_document_blocks
            WHERE resource_id = ?
            ORDER BY order_index, block_id
            """,
            (resource_id,),
        ).fetchall()

    def search_blocks(self, conn: sqlite3.Connection, query: str, *, limit: int = 8) -> list[sqlite3.Row]:
        normalized = _normalize_fts_query(query)
        if not normalized or not self.fts_available:
            return []
        try:
            return conn.execute(
                """
                SELECT b.*
                FROM resource_document_blocks_fts AS f
                JOIN resource_document_blocks AS b
                  ON b.resource_id = f.resource_id AND b.block_id = f.block_id
                WHERE resource_document_blocks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (normalized, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            self.fts_available = False
            return []

    def _insert_fts(self, conn: sqlite3.Connection, block: DocumentBlockRecord) -> None:
        if not self.fts_available:
            return
        try:
            conn.execute(
                """
                INSERT INTO resource_document_blocks_fts(
                    resource_id, block_id, heading_path, text, keywords
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    block.resource_id,
                    block.block_id,
                    " / ".join(block.heading_path),
                    block.text,
                    " ".join(block.keywords),
                ),
            )
        except sqlite3.OperationalError:
            self.fts_available = False


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


def _normalize_fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", query)
    return " OR ".join(dict.fromkeys(terms[:8]))
