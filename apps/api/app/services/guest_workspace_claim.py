from __future__ import annotations

import sqlite3

from app.services.board_asset_identity import (
    rewrite_board_asset_html,
    rewrite_board_asset_json,
    rewrite_board_asset_markdown,
    stable_board_asset_id,
)


CLAIMABLE_OWNER_TABLES = (
    "course_packages",
    "board_assets",
    "source_notebooks",
    "source_ingestions",
    "evidence_bundles",
    "source_ingestion_jobs",
    "source_structures",
    "source_chapters",
    "source_chunks",
    "source_visuals",
    "source_chunk_embeddings",
    "source_chunks_fts",
    "learning_requirement_runs",
    "learning_requirement_versions",
    "learning_requirement_events",
    "board_task_runs",
    "board_task_versions",
    "board_task_events",
    "board_document_chunks",
    "research_transformations",
    "research_speaker_profiles",
    "research_episode_profiles",
    "research_notes",
    "research_notes_fts",
    "research_chat_threads",
    "research_artifacts",
)


def claim_guest_workspace(
    conn: sqlite3.Connection,
    *,
    guest_user_id: str,
    user_id: str,
) -> None:
    """Move one guest workspace into an account using the caller's transaction."""

    _rekey_claimed_board_assets(
        conn,
        guest_user_id=guest_user_id,
        user_id=user_id,
    )
    _move_owner_scoped_rows(
        conn,
        guest_user_id=guest_user_id,
        user_id=user_id,
    )
    guest_setting_key = _workspace_setting_key(guest_user_id)
    user_setting_key = _workspace_setting_key(user_id)
    guest_active_row = conn.execute(
        "SELECT value FROM workspace_settings WHERE key = ?",
        (guest_setting_key,),
    ).fetchone()
    if guest_active_row is not None:
        conn.execute(
            "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
            (user_setting_key, guest_active_row["value"]),
        )
        conn.execute("DELETE FROM workspace_settings WHERE key = ?", (guest_setting_key,))
    conn.execute("DELETE FROM auth_guest_sessions WHERE guest_user_id = ?", (guest_user_id,))


def _workspace_setting_key(owner_user_id: str) -> str:
    return f"active_package_id:{owner_user_id}"


def _rekey_claimed_board_assets(
    conn: sqlite3.Connection,
    *,
    guest_user_id: str,
    user_id: str,
) -> None:
    if not _table_has_column(conn, "board_assets", "owner_user_id"):
        return
    rows = conn.execute(
        "SELECT * FROM board_assets WHERE owner_user_id = ? ORDER BY id",
        (guest_user_id,),
    ).fetchall()
    for row in rows:
        old_asset_id = str(row["id"])
        content_hash = str(row["content_hash"])
        existing = conn.execute(
            """
            SELECT id
            FROM board_assets
            WHERE owner_user_id = ? AND content_hash = ?
            """,
            (user_id, content_hash),
        ).fetchone()
        new_asset_id = (
            str(existing["id"])
            if existing is not None
            else stable_board_asset_id(owner_user_id=user_id, content_hash=content_hash)
        )
        _rewrite_claimed_board_asset_references(
            conn,
            guest_user_id=guest_user_id,
            old_asset_id=old_asset_id,
            new_asset_id=new_asset_id,
        )
        if existing is not None:
            conn.execute(
                "DELETE FROM board_assets WHERE id = ? AND owner_user_id = ?",
                (old_asset_id, guest_user_id),
            )
            continue
        file_name = str(row["file_name"] or "").replace(old_asset_id, new_asset_id)
        conn.execute(
            """
            UPDATE board_assets
            SET id = ?, owner_user_id = ?, file_name = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (new_asset_id, user_id, file_name, old_asset_id, guest_user_id),
        )


def _rewrite_claimed_board_asset_references(
    conn: sqlite3.Connection,
    *,
    guest_user_id: str,
    old_asset_id: str,
    new_asset_id: str,
) -> None:
    lesson_rows = conn.execute(
        """
        SELECT lessons.id, lessons.board_content_json, lessons.board_content_html,
               lessons.board_content_text
        FROM lessons
        JOIN course_packages ON course_packages.id = lessons.package_id
        WHERE course_packages.owner_user_id = ?
        """,
        (guest_user_id,),
    ).fetchall()
    for row in lesson_rows:
        conn.execute(
            """
            UPDATE lessons
            SET board_content_json = ?, board_content_html = ?, board_content_text = ?
            WHERE id = ?
            """,
            (
                rewrite_board_asset_json(
                    str(row["board_content_json"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_html(
                    str(row["board_content_html"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_markdown(
                    str(row["board_content_text"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                row["id"],
            ),
        )

    commit_rows = conn.execute(
        """
        SELECT lesson_commits.id, lesson_commits.operations_json,
               lesson_commits.snapshot_content_json, lesson_commits.snapshot_content_html,
               lesson_commits.snapshot_content_text, lesson_commits.metadata_json
        FROM lesson_commits
        JOIN lessons ON lessons.id = lesson_commits.lesson_id
        JOIN course_packages ON course_packages.id = lessons.package_id
        WHERE course_packages.owner_user_id = ?
        """,
        (guest_user_id,),
    ).fetchall()
    for row in commit_rows:
        conn.execute(
            """
            UPDATE lesson_commits
            SET operations_json = ?, snapshot_content_json = ?,
                snapshot_content_html = ?, snapshot_content_text = ?, metadata_json = ?
            WHERE id = ?
            """,
            (
                rewrite_board_asset_json(
                    str(row["operations_json"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_json(
                    str(row["snapshot_content_json"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_html(
                    str(row["snapshot_content_html"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_markdown(
                    str(row["snapshot_content_text"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                rewrite_board_asset_json(
                    str(row["metadata_json"] or ""),
                    old_asset_id=old_asset_id,
                    new_asset_id=new_asset_id,
                ),
                row["id"],
            ),
        )


def _move_owner_scoped_rows(
    conn: sqlite3.Connection,
    *,
    guest_user_id: str,
    user_id: str,
) -> None:
    for table in CLAIMABLE_OWNER_TABLES:
        if not _table_has_column(conn, table, "owner_user_id"):
            continue
        quoted_table = '"' + table.replace('"', '""') + '"'
        conn.execute(
            f"UPDATE {quoted_table} SET owner_user_id = ? WHERE owner_user_id = ?",
            (user_id, guest_user_id),
        )


def _table_has_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> bool:
    quoted_table = '"' + table.replace('"', '""') + '"'
    return any(
        str(row["name"]) == column
        for row in conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
    )
