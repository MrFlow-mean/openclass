import json
import sqlite3

from app.models import ResourceLibraryItem
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state


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
