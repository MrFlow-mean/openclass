from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.models import (
    EvidenceBundle,
    SourceChapter,
    SourceChunk,
    SourceIngestionJob,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisual,
)
from app.research_models import ResearchNote
from app.services.auth_service import AuthService
from app.services.auth_store import _CLAIMABLE_OWNER_TABLES
from app.services.board_asset_identity import stable_board_asset_id
from app.services.board_asset_store import BoardAssetStore
from app.services.course_store import SqliteCourseStore
from app.services.docx_exporter import export_docx
from app.services.html_document_export import standalone_html
from app.services.lesson_factory import create_empty_lesson
from app.services.research_store import ResearchStore
from app.services.rich_document import rebuild_document_from_content_json
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_ingestion_jobs import SourceIngestionJobStore
from app.services.source_structure_store import SourceStructureStore


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _seed_guest_board(
    *,
    database_path: Path,
    asset_store: BoardAssetStore,
    course_store: SqliteCourseStore,
    guest_user_id: str,
) -> tuple[str, str, str, str]:
    workspace = course_store.load_for_user(guest_user_id)
    package = workspace.packages[0]
    lesson = create_empty_lesson("访客板书")
    asset = asset_store.put_bytes(
        owner_user_id=guest_user_id,
        lesson_id=lesson.id,
        content=_PNG,
        mime_type="image/png",
        file_name="guest-chart.png",
        source_visual_id="visual_guest",
    )
    asset_url = asset.content_url
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"blockId": "paragraph_before"},
                "content": [{"type": "text", "text": f"检索迁移锚点 {asset.id}"}],
            },
            {
                "type": "resourceVisualBlock",
                "attrs": {
                    "assetId": asset.id,
                    "originalSrc": asset_url,
                    "caption": "访客图表",
                    "originalAlt": "访客图表",
                },
            },
        ],
    }
    lesson.board_document = rebuild_document_from_content_json(lesson.board_document, content_json)
    lesson.history_graph.commits[0].snapshot = lesson.board_document.model_copy(deep=True)
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    course_store.save_for_user(guest_user_id, workspace)

    operations_json = json.dumps(
        [{"op": "attach_asset", "asset_url": asset_url}],
        ensure_ascii=False,
    )
    metadata_json = json.dumps(
        {
            "board_asset_ids": [asset.id],
            "content_text": f"元数据正文必须保留 {asset.id}",
        },
        ensure_ascii=False,
    )
    markdown_text = (
        f"检索迁移锚点 {asset.id}\n\n"
        f"![访客 \\] (图表)]({asset_url})\n\n"
        f"普通正文中的裸地址保持原样：{asset_url}"
    )
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE lessons SET board_content_text = ? WHERE id = ?",
            (markdown_text, lesson.id),
        )
        initial = conn.execute(
            "SELECT * FROM lesson_commits WHERE lesson_id = ?",
            (lesson.id,),
        ).fetchone()
        assert initial is not None
        conn.execute(
            """
            UPDATE lesson_commits
            SET operations_json = ?, snapshot_content_text = ?, metadata_json = ?
            WHERE id = ?
            """,
            (operations_json, markdown_text, metadata_json, initial["id"]),
        )
        initial = conn.execute(
            "SELECT * FROM lesson_commits WHERE id = ?",
            (initial["id"],),
        ).fetchone()
        assert initial is not None
        feature_commit_id = "commit_guest_feature"
        conn.execute(
            """
            INSERT INTO lesson_commits(
                id, lesson_id, sort_order, label, message, branch_name, created_at,
                operations_json, snapshot_document_id, snapshot_title, snapshot_content_json,
                snapshot_content_html, snapshot_content_text, snapshot_page_settings_json,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature_commit_id,
                lesson.id,
                1,
                "Feature snapshot",
                "Feature snapshot",
                "feature",
                initial["created_at"],
                operations_json,
                initial["snapshot_document_id"],
                initial["snapshot_title"],
                initial["snapshot_content_json"],
                initial["snapshot_content_html"],
                initial["snapshot_content_text"],
                initial["snapshot_page_settings_json"],
                metadata_json,
            ),
        )
        conn.execute(
            "INSERT INTO lesson_commit_parents(commit_id, parent_id, sort_order) VALUES (?, ?, 0)",
            (feature_commit_id, initial["id"]),
        )
        conn.execute(
            """
            INSERT INTO lesson_branches(lesson_id, name, head_commit_id, base_commit_id, created_at)
            VALUES (?, 'feature', ?, ?, ?)
            """,
            (lesson.id, feature_commit_id, initial["id"], initial["created_at"]),
        )
    return package.id, lesson.id, asset.id, asset.content_hash


def _seed_source_state(
    *,
    database_path: Path,
    owner_user_id: str,
    package_id: str,
    lesson_id: str,
) -> tuple[SourceIngestionRecord, EvidenceBundle]:
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source = source_store.save_source(
        SourceIngestionRecord(
            id="source_guest_claim",
            owner_user_id=owner_user_id,
            package_id=package_id,
            title="访客资料",
            file_name="guest.txt",
            mime_type="text/plain",
            status="ready",
        )
    )
    chapter = SourceChapter(
        id="sourcechapter_guest_claim",
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_ingestion_id=source.id,
        title="资料章节",
        anchor_status="verified",
    )
    chunk = SourceChunk(
        id="sourcechunk_guest_claim",
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_ingestion_id=source.id,
        chapter_id=chapter.id,
        text="访客资料全文检索迁移锚点",
        end_offset=12,
        token_count=6,
    )
    structure = SourceStructure(
        id="structure_guest_claim",
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_ingestion_id=source.id,
        status="ready",
        strategy="linear_text",
        visual_index_status="ready",
        visual_index_version=1,
    )
    visual = SourceVisual(
        id="sourcevisual_guest_claim",
        owner_user_id=owner_user_id,
        package_id=package_id,
        source_ingestion_id=source.id,
        structure_id=structure.id,
        structure_version=1,
        chapter_id=chapter.id,
        caption="资料图表",
        before_chunk_id=chunk.id,
        after_chunk_id=chunk.id,
        anchor_status="verified",
        position_hash="position_guest_claim",
    )
    structure_store.save_structure_bundle(
        structure=structure,
        chapters=[chapter],
        chunks=[chunk],
        visuals=[visual],
    )
    bundle = source_store.save_bundle(
        EvidenceBundle(
            id="bundle_guest_claim",
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            purpose="board_generation",
            status="confirmed",
            confirmed_by_user=True,
        )
    )
    SourceIngestionJobStore(database_path).save(
        SourceIngestionJob(
            id="ingest_guest_claim",
            resource_id=source.id,
            status="ready",
            progress=100,
        ),
        owner_user_id=owner_user_id,
        package_id=package_id,
    )
    ResearchStore(database_path).save_note(
        ResearchNote(
            id="note_guest_claim",
            owner_user_id=owner_user_id,
            package_id=package_id,
            title="访客研究笔记",
            content="研究全文检索迁移锚点",
        )
    )
    return source, bundle


def _asset_resolver(asset_store: BoardAssetStore, owner_user_id: str):
    def resolve(asset_id: str) -> tuple[str, bytes] | None:
        result = asset_store.read_bytes(asset_id, owner_user_id)
        if result is None:
            return None
        record, content = result
        return record.mime_type, content

    return resolve


def test_guest_claim_rekeys_assets_references_sources_and_fts(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    course_store = SqliteCourseStore(database_path, legacy_json_path=None)
    auth = AuthService(database_path)
    asset_store = BoardAssetStore(database_path, tmp_path / "board-assets")
    guest_token, guest = auth.start_guest_session()
    package_id, lesson_id, old_asset_id, content_hash = _seed_guest_board(
        database_path=database_path,
        asset_store=asset_store,
        course_store=course_store,
        guest_user_id=guest.id,
    )
    source, bundle = _seed_source_state(
        database_path=database_path,
        owner_user_id=guest.id,
        package_id=package_id,
        lesson_id=lesson_id,
    )
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            CREATE TABLE course_publications(
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO course_publications(id, owner_user_id, payload) VALUES ('publication_guest', ?, 'audit')",
            (guest.id,),
        )
        conn.execute(
            """
            CREATE TABLE plugin_audit_records(
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO plugin_audit_records(id, owner_user_id, payload) VALUES ('audit_guest', ?, 'keep')",
            (guest.id,),
        )

    _, user = auth.register("claim@example.com", "correct-password", guest_token=guest_token)
    new_asset_id = stable_board_asset_id(owner_user_id=user.id, content_hash=content_hash)

    claimed = course_store.load_for_user(user.id)
    lesson = claimed.packages[0].lessons[0]
    attrs = lesson.board_document.content_json["content"][1]["attrs"]
    assert attrs["assetId"] == new_asset_id
    assert attrs["originalSrc"] == f"/api/board-assets/{new_asset_id}/content"
    assert f"检索迁移锚点 {old_asset_id}" in lesson.board_document.content_text
    assert f"](/api/board-assets/{new_asset_id}/content)" in lesson.board_document.content_text
    assert f"](/api/board-assets/{old_asset_id}/content)" not in lesson.board_document.content_text
    assert f"普通正文中的裸地址保持原样：/api/board-assets/{old_asset_id}/content" in (
        lesson.board_document.content_text
    )
    assert old_asset_id in lesson.board_document.content_html
    assert f'data-board-asset-id="{new_asset_id}"' in lesson.board_document.content_html
    assert f'src="/api/board-assets/{new_asset_id}/content"' in lesson.board_document.content_html
    assert f'data-board-asset-id="{old_asset_id}"' not in lesson.board_document.content_html
    assert f'src="/api/board-assets/{old_asset_id}/content"' not in lesson.board_document.content_html
    assert set(lesson.history_graph.branches) == {"main", "feature"}
    assert {commit.branch_name for commit in lesson.history_graph.commits} == {"main", "feature"}
    for commit in lesson.history_graph.commits:
        assert commit.operations[0].asset_url == f"/api/board-assets/{new_asset_id}/content"
        assert commit.metadata["board_asset_ids"] == [new_asset_id]
        assert old_asset_id in commit.metadata["content_text"]
        assert f"检索迁移锚点 {old_asset_id}" in commit.snapshot.content_text
        assert f"](/api/board-assets/{new_asset_id}/content)" in commit.snapshot.content_text
        assert commit.snapshot.content_json["content"][1]["attrs"]["assetId"] == new_asset_id

    assert asset_store.read_bytes(new_asset_id, user.id) is not None
    assert asset_store.read_bytes(old_asset_id, guest.id) is None
    assert asset_store.read_bytes(new_asset_id, "another_user") is None
    html = standalone_html(
        lesson.board_document,
        asset_resolver=_asset_resolver(asset_store, user.id),
    )
    assert "data:image/png;base64," in html
    docx_path = export_docx(
        lesson.board_document,
        tmp_path / "claimed.docx",
        asset_resolver=_asset_resolver(asset_store, user.id),
    )
    with ZipFile(docx_path) as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())
    assert course_store.search_document_segments(
        "检索迁移锚点",
        owner_user_id=user.id,
    )
    assert course_store.search_document_segments(
        "检索迁移锚点",
        owner_user_id=guest.id,
    ) == []

    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    claimed_sources = source_store.list_sources(owner_user_id=user.id, package_id=package_id)
    assert [item.id for item in claimed_sources] == [source.id]
    assert source_store.list_sources(owner_user_id=guest.id, package_id=package_id) == []
    assert structure_store.get_structure(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source.id,
    ) is not None
    assert structure_store.get_structure(
        owner_user_id=guest.id,
        package_id=package_id,
        source_id=source.id,
    ) is None
    assert [item.id for item in structure_store.list_visuals(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source.id,
    )] == ["sourcevisual_guest_claim"]
    assert structure_store.list_visuals(
        owner_user_id=guest.id,
        package_id=package_id,
        source_id=source.id,
    ) == []
    assert structure_store.chunk_evidence_search(
        owner_user_id=user.id,
        package_id=package_id,
        query="全文检索迁移锚点",
        limit=3,
        token_budget=200,
        source_ingestion_ids=[source.id],
        search_mode="text",
    )
    assert structure_store.chunk_evidence_search(
        owner_user_id=guest.id,
        package_id=package_id,
        query="全文检索迁移锚点",
        limit=3,
        token_budget=200,
        source_ingestion_ids=[source.id],
        search_mode="text",
    ) == []
    assert source_store.get_bundle(owner_user_id=user.id, bundle_id=bundle.id) is not None
    assert source_store.get_bundle(owner_user_id=guest.id, bundle_id=bundle.id) is None
    jobs = SourceIngestionJobStore(database_path)
    assert [item.id for item in jobs.list(owner_user_id=user.id, package_id=package_id)] == ["ingest_guest_claim"]
    assert jobs.list(owner_user_id=guest.id, package_id=package_id) == []
    research = ResearchStore(database_path)
    assert research.search_notes(
        owner_user_id=user.id,
        package_id=package_id,
        query="研究全文检索迁移锚点",
        limit=3,
    )
    assert research.search_notes(
        owner_user_id=guest.id,
        package_id=package_id,
        query="研究全文检索迁移锚点",
        limit=3,
    ) == []

    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        claimable_owner_tables = []
        for table in _CLAIMABLE_OWNER_TABLES:
            quoted = '"' + table.replace('"', '""') + '"'
            if any(
                str(column["name"]) == "owner_user_id"
                for column in conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            ):
                claimable_owner_tables.append(quoted)
        assert claimable_owner_tables
        assert all(
            conn.execute(
                f"SELECT count(*) FROM {table} WHERE owner_user_id = ?",
                (guest.id,),
            ).fetchone()[0]
            == 0
            for table in claimable_owner_tables
        )
        assert conn.execute(
            "SELECT owner_user_id FROM course_publications WHERE id = 'publication_guest'"
        ).fetchone()[0] == guest.id
        assert conn.execute(
            "SELECT owner_user_id FROM plugin_audit_records WHERE id = 'audit_guest'"
        ).fetchone()[0] == guest.id


def test_guest_claim_merges_same_hash_into_target_legacy_asset_id(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    course_store = SqliteCourseStore(database_path, legacy_json_path=None)
    auth = AuthService(database_path)
    _, target_user = auth.register("existing@example.com", "correct-password")
    guest_token, guest = auth.start_guest_session()
    asset_store = BoardAssetStore(database_path, tmp_path / "board-assets")
    _, _, guest_asset_id, content_hash = _seed_guest_board(
        database_path=database_path,
        asset_store=asset_store,
        course_store=course_store,
        guest_user_id=guest.id,
    )
    guest_asset = asset_store.get(guest_asset_id, guest.id)
    assert guest_asset is not None
    legacy_asset_id = "basset_target_legacy_same_hash"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            INSERT INTO board_assets(
                id, owner_user_id, lesson_id, content_hash, mime_type,
                size_bytes, storage_key, file_name, source_visual_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_asset_id,
                target_user.id,
                "legacy_lesson",
                content_hash,
                guest_asset.mime_type,
                guest_asset.size_bytes,
                guest_asset.storage_key,
                "legacy-chart.png",
                "legacy_visual",
                guest_asset.created_at,
            ),
        )

    auth.login("existing@example.com", "correct-password", guest_token=guest_token)

    lesson = course_store.load_for_user(target_user.id).packages[0].lessons[0]
    attrs = lesson.board_document.content_json["content"][1]["attrs"]
    assert attrs["assetId"] == legacy_asset_id
    assert attrs["originalSrc"] == f"/api/board-assets/{legacy_asset_id}/content"
    assert asset_store.read_bytes(legacy_asset_id, target_user.id) is not None
    assert asset_store.read_bytes(guest_asset_id, guest.id) is None
    assert asset_store.put_bytes(
        owner_user_id=target_user.id,
        lesson_id="another_lesson",
        content=_PNG,
        mime_type="image/png",
    ).id == legacy_asset_id
    with sqlite3.connect(database_path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM board_assets WHERE owner_user_id = ? AND content_hash = ?",
            (target_user.id, content_hash),
        ).fetchone()[0] == 1


def test_guest_claim_rolls_back_reference_and_owner_changes_on_asset_id_conflict(tmp_path: Path) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    course_store = SqliteCourseStore(database_path, legacy_json_path=None)
    auth = AuthService(database_path)
    _, target_user = auth.register("rollback@example.com", "correct-password")
    guest_token, guest = auth.start_guest_session()
    asset_store = BoardAssetStore(database_path, tmp_path / "board-assets")
    package_id, _, guest_asset_id, content_hash = _seed_guest_board(
        database_path=database_path,
        asset_store=asset_store,
        course_store=course_store,
        guest_user_id=guest.id,
    )
    conflicting_id = stable_board_asset_id(owner_user_id=target_user.id, content_hash=content_hash)
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            """
            INSERT INTO board_assets(
                id, owner_user_id, lesson_id, content_hash, mime_type,
                size_bytes, storage_key, file_name, source_visual_id, created_at
            ) VALUES (?, ?, 'conflict_lesson', ?, 'image/png', 1, 'ff/conflict.png',
                      'conflict.png', '', '2026-01-01T00:00:00Z')
            """,
            (conflicting_id, target_user.id, "f" * 64),
        )

    with pytest.raises(sqlite3.IntegrityError):
        auth.login("rollback@example.com", "correct-password", guest_token=guest_token)

    assert auth.get_user_by_token(guest_token).id == guest.id
    assert asset_store.get(guest_asset_id, guest.id) is not None
    assert asset_store.get(conflicting_id, target_user.id) is not None
    guest_workspace = course_store.load_for_user(guest.id)
    assert guest_workspace.packages[0].id == package_id
    attrs = guest_workspace.packages[0].lessons[0].board_document.content_json["content"][1]["attrs"]
    assert attrs["assetId"] == guest_asset_id
    with sqlite3.connect(database_path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM course_packages WHERE owner_user_id = ?",
            (guest.id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM course_packages WHERE owner_user_id = ?",
            (target_user.id,),
        ).fetchone()[0] == 0
