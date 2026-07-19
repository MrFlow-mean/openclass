from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from app.services.board_asset_store import BoardAssetStore
from app.services.course_store import SqliteCourseStore
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionJobStore
from app.services.source_structure_store import SourceStructureStore


def _assert_connection_closes(context: AbstractContextManager[sqlite3.Connection]) -> None:
    with context as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1").fetchone()


def test_shared_sqlite_stores_close_connections_after_each_operation(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    stores = [
        SqliteCourseStore(database_path, legacy_json_path=None),
        BoardAssetStore(database_path, tmp_path / "board-assets"),
        SourceStructureStore(database_path),
        SourceEvidenceStore(database_path),
        SourceIngestionJobStore(database_path),
    ]

    for store in stores:
        _assert_connection_closes(store._connect())
