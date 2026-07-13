from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.models import SourceChunk, SourceIngestionRecord, SourceStructure
from app.services.native_source_index import NativeSourceIndex
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


def test_rebuild_keeps_deterministic_chunk_ids_and_text_hashes(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    document_path = tmp_path / "stable.md"
    document_path.write_text(
        "# Stable index\n\n" + ("Concurrent workers coordinate durable queue processing.\n\n" * 120),
        encoding="utf-8",
    )
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source = SourceIngestionRecord(
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Stable source",
        file_name=document_path.name,
        mime_type="text/markdown",
        status="ready",
        metadata={"local_source_path": str(document_path)},
    )
    source_store.save_source(source)
    indexer = SourceStructureIndexer(store=structure_store)

    indexer.rebuild_structure(source)
    first = structure_store.get_structure_view(source=source, chunk_limit=100)
    indexer.rebuild_structure(source)
    second = structure_store.get_structure_view(source=source, chunk_limit=100)

    first_identity = [(chunk.id, chunk.metadata.get("text_hash")) for chunk in first.chunks]
    second_identity = [(chunk.id, chunk.metadata.get("text_hash")) for chunk in second.chunks]
    assert first_identity == second_identity
    assert len(first_identity) > 1
    assert all(chunk_id.startswith("sourcechunk_") for chunk_id, _ in first_identity)
    assert all(isinstance(text_hash, str) and len(text_hash) == 64 for _, text_hash in first_identity)

    with sqlite3.connect(database_path) as conn:
        embedding_rows = conn.execute(
            "SELECT chunk_id, text_hash FROM source_chunk_embeddings ORDER BY chunk_id"
        ).fetchall()
        chunk_hash_rows = conn.execute(
            "SELECT id, text_hash FROM source_chunks ORDER BY id"
        ).fetchall()
        fts_count = conn.execute("SELECT COUNT(*) FROM source_chunks_fts").fetchone()[0]
    assert sorted(embedding_rows) == sorted(first_identity)
    assert sorted(chunk_hash_rows) == sorted(first_identity)
    assert fts_count == len(first_identity)


def test_hybrid_search_uses_fts_embeddings_and_source_filters(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    selected = _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Selected source",
        chunks=[
            "Concurrent worker scheduling coordinates durable task queues and dependencies.",
            "A separate paragraph describes archival storage and recovery policies.",
        ],
    )
    excluded = _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Excluded source",
        chunks=["Concurrent worker scheduling concurrent worker scheduling exact duplicate."],
    )
    _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_2",
        package_id="pkg_other",
        title="Other owner",
        chunks=["Concurrent worker scheduling must not cross ownership boundaries."],
    )

    evidence = structure_store.chunk_evidence_search(
        owner_user_id="user_1",
        package_id="pkg_1",
        query="concurrent worker scheduling",
        limit=4,
        token_budget=2000,
        source_ingestion_ids=[selected.id],
    )

    assert evidence
    assert {item.source_ingestion_id for item in evidence} == {selected.id}
    assert excluded.id not in {item.source_ingestion_id for item in evidence}
    assert evidence[0].metadata["native_index_mode"] == "hybrid"
    assert "fts5_bm25" in evidence[0].metadata["match_modes"]
    assert evidence[0].metadata["semantic_score"] > 0
    assert evidence[0].relevance_score == evidence[0].metadata["hybrid_score"]

    text_evidence = structure_store.chunk_evidence_search(
        owner_user_id="user_1",
        package_id="pkg_1",
        query="concurrent worker scheduling",
        limit=4,
        token_budget=2000,
        source_ingestion_ids=[selected.id],
        search_mode="text",
    )
    assert text_evidence
    assert text_evidence[0].metadata["native_index_mode"] == "text"
    assert text_evidence[0].metadata["semantic_score"] == 0
    assert "local_embedding" not in text_evidence[0].metadata["match_modes"]


def test_native_index_provider_is_pluggable_and_persisted(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    native_index = NativeSourceIndex(embedding_provider=_TinyEmbeddingProvider())
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path, native_index=native_index)
    source = _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Provider source",
        chunks=["target representation", "unrelated material"],
    )

    with sqlite3.connect(database_path) as conn:
        rows = conn.execute(
            """
            SELECT provider, model, dimensions
            FROM source_chunk_embeddings
            WHERE source_ingestion_id = ?
            """,
            (source.id,),
        ).fetchall()
    assert rows
    assert set(rows) == {("test_local", "tiny-v1", 3)}

    semantic_evidence = structure_store.chunk_evidence_search(
        owner_user_id="user_1",
        package_id="pkg_1",
        query="target query",
        limit=2,
        token_budget=1000,
        source_ingestion_ids=[source.id],
        search_mode="semantic",
    )
    assert semantic_evidence
    assert semantic_evidence[0].expanded_text == "target representation"
    assert semantic_evidence[0].metadata["native_index_mode"] == "semantic"
    assert semantic_evidence[0].metadata["keyword_score"] == 0
    assert semantic_evidence[0].metadata["match_modes"] == ["local_embedding"]
    assert semantic_evidence[0].relevance_score == semantic_evidence[0].metadata["semantic_score"]


def test_rebuild_and_delete_update_fts_and_embeddings_transactionally(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source = _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Replaceable source",
        chunks=["old searchable content"],
    )
    old_chunk_id = _chunk_ids(database_path)[0]
    replacement = SourceChunk(
        id="sourcechunk_replacement",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id=source.id,
        order_index=0,
        text="new searchable content",
        end_offset=len("new searchable content"),
        token_count=5,
    )
    structure_store.save_structure_bundle(
        structure=_linear_structure(source),
        chapters=[],
        chunks=[replacement],
    )

    with sqlite3.connect(database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_chunks WHERE id = ?", (old_chunk_id,)).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings WHERE chunk_id = ?", (old_chunk_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunks_fts WHERE chunk_id = ?", (old_chunk_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings WHERE chunk_id = ?", (replacement.id,)
        ).fetchone()[0] == 1

    structure_store.delete_for_source(
        owner_user_id="user_1",
        package_id="pkg_1",
        source_id=source.id,
    )
    with sqlite3.connect(database_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunks_fts WHERE source_ingestion_id = ?", (source.id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings WHERE source_ingestion_id = ?", (source.id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunks WHERE source_ingestion_id = ?", (source.id,)
        ).fetchone()[0] == 0


def test_failed_embedding_update_rolls_back_the_previous_native_index(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    native_index = NativeSourceIndex(embedding_provider=_FailingEmbeddingProvider())
    structure_store = SourceStructureStore(database_path, native_index=native_index)
    source = _save_linear_source(
        source_store=source_store,
        structure_store=structure_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Transactional source",
        chunks=["preserved native index content"],
    )
    old_chunk_id = _chunk_ids(database_path)[0]
    replacement = SourceChunk(
        id="sourcechunk_failing_replacement",
        owner_user_id="user_1",
        package_id="pkg_1",
        source_ingestion_id=source.id,
        text="trigger embedding failure",
        end_offset=len("trigger embedding failure"),
        token_count=5,
    )

    with pytest.raises(RuntimeError, match="embedding failure"):
        structure_store.save_structure_bundle(
            structure=_linear_structure(source),
            chapters=[],
            chunks=[replacement],
        )

    with sqlite3.connect(database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_chunks WHERE id = ?", (old_chunk_id,)).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings WHERE chunk_id = ?", (old_chunk_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunks_fts WHERE chunk_id = ?", (old_chunk_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunks WHERE id = ?", (replacement.id,)
        ).fetchone()[0] == 0


def test_native_index_backfills_preexisting_chunks_without_text_hash(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database_path)
    first_store = SourceStructureStore(database_path)
    source = _save_linear_source(
        source_store=source_store,
        structure_store=first_store,
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Legacy source",
        chunks=["legacy chunk content"],
    )
    chunk_id = _chunk_ids(database_path)[0]
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            "UPDATE source_chunks SET text_hash = '', metadata_json = '{}' WHERE id = ?",
            (chunk_id,),
        )
        conn.execute("DELETE FROM source_chunk_embeddings WHERE chunk_id = ?", (chunk_id,))
        conn.execute("DELETE FROM source_chunks_fts WHERE chunk_id = ?", (chunk_id,))
        conn.commit()

    migrated_store = SourceStructureStore(database_path)
    migrated_store.get_structure(owner_user_id="user_1", package_id="pkg_1", source_id=source.id)

    with sqlite3.connect(database_path) as conn:
        metadata = json.loads(
            conn.execute("SELECT metadata_json FROM source_chunks WHERE id = ?", (chunk_id,)).fetchone()[0]
        )
        embedding_count = conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()[0]
        fts_count = conn.execute(
            "SELECT COUNT(*) FROM source_chunks_fts WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()[0]
    assert len(metadata["text_hash"]) == 64
    assert embedding_count == 1
    assert fts_count == 1


class _TinyEmbeddingProvider:
    provider = "test_local"
    model = "tiny-v1"
    dimensions = 3

    def embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        if "target" in lowered:
            return [1.0, 0.0, 0.0]
        if "unrelated" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class _FailingEmbeddingProvider:
    provider = "test_local"
    model = "failing-v1"
    dimensions = 3

    def embed(self, text: str) -> list[float]:
        if "trigger" in text:
            raise RuntimeError("embedding failure")
        return [1.0, 0.0, 0.0]


def _save_linear_source(
    *,
    source_store: SourceEvidenceStore,
    structure_store: SourceStructureStore,
    owner_user_id: str,
    package_id: str,
    title: str,
    chunks: list[str],
) -> SourceIngestionRecord:
    source = SourceIngestionRecord(
        owner_user_id=owner_user_id,
        package_id=package_id,
        title=title,
        file_name=f"{title}.txt",
        mime_type="text/plain",
        status="ready",
    )
    source_store.save_source(source)
    source_chunks: list[SourceChunk] = []
    offset = 0
    for index, text in enumerate(chunks):
        source_chunks.append(
            SourceChunk(
                id=f"sourcechunk_{source.id}_{index}",
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_ingestion_id=source.id,
                order_index=index,
                text=text,
                start_offset=offset,
                end_offset=offset + len(text),
                token_count=max(1, len(text) // 4),
            )
        )
        offset += len(text)
    structure_store.save_structure_bundle(
        structure=_linear_structure(source),
        chapters=[],
        chunks=source_chunks,
    )
    return source


def _linear_structure(source: SourceIngestionRecord) -> SourceStructure:
    return SourceStructure(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="linear_only",
        strategy="linear_text",
    )


def _chunk_ids(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as conn:
        return [str(row[0]) for row in conn.execute("SELECT id FROM source_chunks ORDER BY order_index").fetchall()]
