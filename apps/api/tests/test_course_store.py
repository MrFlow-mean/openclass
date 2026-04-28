import json
import sqlite3

from app.models import BoardTeachingProgress, ResourceLibraryItem
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson


def test_sqlite_store_round_trips_workspace_without_store_json(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    workspace.packages[0].title = "多人课程工作台"
    workspace.packages[0].lessons[0].board_document.content_text = "数据库保存后的讲义"
    store.save(workspace)

    reloaded = store.load()

    assert db_path.exists()
    assert not (tmp_path / "store.json").exists()
    assert reloaded.packages[0].title == "多人课程工作台"
    assert reloaded.packages[0].lessons[0].board_document.content_text == "数据库保存后的讲义"

    with sqlite3.connect(db_path) as conn:
        package_count = conn.execute("SELECT count(*) FROM course_packages").fetchone()[0]
        lesson_count = conn.execute("SELECT count(*) FROM lessons").fetchone()[0]
        commit_count = conn.execute("SELECT count(*) FROM lesson_commits").fetchone()[0]
    assert package_count == 1
    assert lesson_count == 3
    assert commit_count == 3


def test_sqlite_store_imports_and_archives_legacy_store_json(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    legacy_path = tmp_path / "store.json"
    workspace = build_initial_workspace_state()
    workspace.packages[0].title = "旧 JSON 课程包"
    legacy_path.write_text(
        json.dumps(workspace.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    store = SqliteCourseStore(db_path, legacy_json_path=legacy_path)
    loaded = store.load()

    assert loaded.packages[0].title == "旧 JSON 课程包"
    assert db_path.exists()
    assert not legacy_path.exists()
    assert list(tmp_path.glob("store.migrated-*.json"))

    with sqlite3.connect(db_path) as conn:
        package_title = conn.execute("SELECT title FROM course_packages").fetchone()[0]
    assert package_title == "旧 JSON 课程包"


def test_sqlite_store_round_trips_resource_lesson_scope(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    package = workspace.packages[0]
    lesson = package.lessons[0]
    package.resources.append(
        ResourceLibraryItem(
            name="lesson-only.png",
            mime_type="image/png",
            resource_type="image",
            size_bytes=12,
            scope_lesson_id=lesson.id,
        )
    )
    store.save(workspace)

    reloaded = store.load()

    assert reloaded.packages[0].resources[0].scope_lesson_id == lesson.id


def test_sqlite_store_round_trips_board_teaching_progress(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    lesson = workspace.packages[0].lessons[0]
    lesson.board_teaching_progress = BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash="hash-1",
        current_section_index=1,
        completed_section_indexes=[0, 1],
        waiting_for_continue=True,
    )
    store.save(workspace)

    reloaded = store.load()
    progress = reloaded.packages[0].lessons[0].board_teaching_progress

    assert progress is not None
    assert progress.current_section_index == 1
    assert progress.completed_section_indexes == [0, 1]
    assert progress.waiting_for_continue is True


def test_sqlite_store_keeps_user_workspaces_isolated(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    user_a_workspace = store.load_for_user("user_a")
    user_a_workspace.packages[0].title = "A 的私有课程包"
    store.save_for_user("user_a", user_a_workspace)

    user_b_workspace = store.load_for_user("user_b")
    user_b_workspace.packages[0].title = "B 的私有课程包"
    store.save_for_user("user_b", user_b_workspace)

    reloaded_a = store.load_for_user("user_a")
    reloaded_b = store.load_for_user("user_b")

    assert reloaded_a.packages[0].title == "A 的私有课程包"
    assert reloaded_b.packages[0].title == "B 的私有课程包"
    assert reloaded_a.packages[0].id != reloaded_b.packages[0].id

    with sqlite3.connect(db_path) as conn:
        owner_ids = {
            row[0]
            for row in conn.execute("SELECT DISTINCT owner_user_id FROM course_packages").fetchall()
        }
    assert owner_ids == {"user_a", "user_b"}


def test_sqlite_store_creates_empty_account_workspace(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load_for_user("guest_preview")

    assert len(workspace.packages) == 1
    assert workspace.packages[0].lessons == []
    assert workspace.packages[0].course_graph == []
    assert workspace.packages[0].open_lesson_ids == []
    assert workspace.packages[0].active_lesson_id is None
    assert workspace.packages[0].workspace_tab_order == []


def test_sqlite_store_removes_only_unmodified_starter_lessons_from_account_workspace(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = build_initial_workspace_state()
    user_lesson = create_empty_lesson("在测试1")
    package = workspace.packages[0]
    package.lessons.append(user_lesson)
    package.open_lesson_ids.append(user_lesson.id)
    package.workspace_tab_order.append(user_lesson.id)
    package.active_lesson_id = user_lesson.id
    store.save_for_user("guest_preview", workspace)

    reloaded = store.load_for_user("guest_preview")
    package = reloaded.packages[0]

    assert [lesson.title for lesson in package.lessons] == ["在测试1"]
    assert package.open_lesson_ids == [user_lesson.id]
    assert package.workspace_tab_order == [user_lesson.id]
    assert package.active_lesson_id == user_lesson.id
    assert package.course_graph == []


def test_sqlite_store_claims_legacy_workspace_for_first_user(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    legacy_workspace = store.load()
    legacy_workspace.packages[0].title = "迁移前课程包"
    store.save(legacy_workspace)

    claimed_workspace = store.load_for_user("user_owner")
    second_workspace = store.load_for_user("user_second")

    assert claimed_workspace.packages[0].title == "迁移前课程包"
    assert second_workspace.packages[0].title != "迁移前课程包"

    with sqlite3.connect(db_path) as conn:
        unowned_count = conn.execute(
            "SELECT count(*) FROM course_packages WHERE owner_user_id IS NULL"
        ).fetchone()[0]
        owner_count = conn.execute(
            "SELECT count(*) FROM course_packages WHERE owner_user_id = ?",
            ("user_owner",),
        ).fetchone()[0]
    assert unowned_count == 0
    assert owner_count == len(claimed_workspace.packages)


def test_sqlite_store_does_not_claim_legacy_workspace_for_guest(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    legacy_workspace = store.load()
    legacy_workspace.packages[0].title = "正式账号旧课程包"
    store.save(legacy_workspace)

    guest_workspace = store.load_for_user("guest_preview")
    claimed_workspace = store.load_for_user("user_owner")

    assert guest_workspace.packages[0].title != "正式账号旧课程包"
    assert claimed_workspace.packages[0].title == "正式账号旧课程包"


def test_sqlite_store_assigns_legacy_workspace_to_existing_admin(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    legacy_workspace = store.load()
    legacy_workspace.packages[0].title = "管理员旧课程包"
    store.save(legacy_workspace)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO users(id, role, created_at) VALUES (?, ?, ?)",
            ("user_admin", "admin", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO users(id, role, created_at) VALUES (?, ?, ?)",
            ("user_visitor", "user", "2026-01-02T00:00:00+00:00"),
        )

    visitor_workspace = store.load_for_user("user_visitor")
    admin_workspace = store.load_for_user("user_admin")

    assert visitor_workspace.packages[0].title != "管理员旧课程包"
    assert admin_workspace.packages[0].title == "管理员旧课程包"
