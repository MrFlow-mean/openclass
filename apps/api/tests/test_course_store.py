import json
import sqlite3

from app.models import BoardTeachingProgress, ResourceActivityEvent, ResourceLibraryItem
from app.services import resource_segment_store
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.resource_embedding import ResourceEmbeddingSpec, SegmentEmbeddingRecord
from app.services.resource_library import build_resource_item


def _append_lesson(workspace, title: str = "测试页面"):
    lesson = create_empty_lesson(title)
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return lesson


def test_initial_workspace_has_no_subject_demo_lessons() -> None:
    workspace = build_initial_workspace_state()

    assert len(workspace.packages) == 1
    assert workspace.packages[0].lessons == []
    assert workspace.packages[0].course_graph == []
    assert workspace.packages[0].open_lesson_ids == []
    assert workspace.packages[0].active_lesson_id is None
    assert workspace.packages[0].workspace_tab_order == []


def test_sqlite_store_round_trips_workspace_without_store_json(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    lesson = _append_lesson(workspace)
    workspace.packages[0].title = "多人课程工作台"
    lesson.board_document.content_text = "数据库保存后的讲义"
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
    assert lesson_count == 1
    assert commit_count == 1


def test_sqlite_store_indexes_and_searches_board_document_segments(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = build_initial_workspace_state()
    lesson = _append_lesson(workspace, "可检索页面")
    lesson.board_document = build_document(
        title="结构化文档",
        content_text="## 检索标题\n\nThis paragraph has a retrieval anchor.\n\n$$\nE=mc^2\n$$",
        content_json={
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "检索标题"}],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "This paragraph has a retrieval anchor and "},
                        {"type": "inlineMath", "attrs": {"latex": "x^2+y^2"}},
                        {"type": "text", "text": "."},
                    ],
                },
                {"type": "blockMath", "attrs": {"latex": "E=mc^2"}},
            ],
        },
    )
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    store.save_for_user("user_a", workspace)

    text_results = store.search_document_segments("retrieval anchor", owner_user_id="user_a")
    formula_results = store.search_document_segments("", owner_user_id="user_a", kind="formula")
    other_user_results = store.search_document_segments("retrieval anchor", owner_user_id="user_b")

    assert [result.lesson_id for result in text_results] == [lesson.id]
    assert text_results[0].heading_path == ["检索标题"]
    assert "x^2+y^2" in text_results[0].text
    assert [result.text for result in formula_results] == ["E=mc^2"]
    assert other_user_results == []

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT kind, text
            FROM board_document_segments
            WHERE lesson_id = ?
            ORDER BY order_index
            """,
            (lesson.id,),
        ).fetchall()
    assert rows == [
        ("heading", "检索标题"),
        ("paragraph", "This paragraph has a retrieval anchor and x^2+y^2 ."),
        ("formula", "E=mc^2"),
    ]


def test_sqlite_store_indexes_and_round_trips_resource_segments(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 定积分\n这一节先说明面积问题。\n\n牛顿莱布尼茨公式连接原函数与定积分，是正文里的目标片段。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")
    package.resources.append(resource)
    store.save(workspace)

    reloaded = store.load()
    reloaded_resource = reloaded.packages[0].resources[0]

    assert reloaded_resource.segments
    assert any("牛顿莱布尼茨公式" in segment.text for segment in reloaded_resource.segments)

    with sqlite3.connect(db_path) as conn:
        segment_rows = conn.execute(
            """
            SELECT segment_id, text, heading_path_json
            FROM resource_segments
            WHERE resource_id = ?
            ORDER BY order_index
            """,
            (resource.id,),
        ).fetchall()
        has_fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resource_segments_fts'"
        ).fetchone()
        fts_rows = (
            conn.execute(
                """
                SELECT segment_id
                FROM resource_segments_fts
                WHERE resource_id = ?
                """,
                (resource.id,),
            ).fetchall()
            if has_fts
            else []
        )

    assert segment_rows
    assert any("牛顿莱布尼茨公式" in row[1] for row in segment_rows)
    assert json.loads(segment_rows[0][2]) == ["定积分"]
    if fts_rows:
        assert [row[0] for row in fts_rows] == [row[0] for row in segment_rows]


def test_sqlite_store_persists_resource_segment_embeddings(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    def fake_embed_segments(segments):
        return {
            segment.segment_id: SegmentEmbeddingRecord(
                resource_id=segment.resource_id,
                segment_id=segment.segment_id,
                text_hash=segment.text_hash,
                provider="openai",
                model="test-embedding",
                dimensions=2,
                embedding=[1.0, 0.25],
            )
            for segment in segments
        }

    monkeypatch.setattr(resource_segment_store.resource_embedding_service, "embed_segments", fake_embed_segments)
    monkeypatch.setattr(
        resource_segment_store.resource_embedding_service,
        "current_spec",
        lambda: ResourceEmbeddingSpec(provider="openai", model="test-embedding", dimensions=2),
    )

    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    resource_path = tmp_path / "resource.md"
    resource_path.write_text("# 主题\n这里是可向量化的资料片段。", encoding="utf-8")
    resource = build_resource_item(resource_path, "resource.md")
    package.resources.append(resource)
    store.save(workspace)

    reloaded = store.load()
    segment = reloaded.packages[0].resources[0].segments[0]

    assert segment.embedding == [1.0, 0.25]
    assert segment.embedding_model == "test-embedding"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT model, dimensions, embedding_json
            FROM resource_segment_embeddings
            WHERE resource_id = ?
            """,
            (resource.id,),
        ).fetchone()

    assert row[0] == "test-embedding"
    assert row[1] == 2
    assert json.loads(row[2]) == [1.0, 0.25]


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
    lesson = _append_lesson(workspace)
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


def test_sqlite_store_round_trips_resource_activity_events(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    package = workspace.packages[0]
    lesson = _append_lesson(workspace)
    package.resource_events.extend(
        [
            ResourceActivityEvent(
                id="event_upload",
                action="uploaded",
                resource_id="resource_1",
                resource_name="lesson.png",
                mime_type="image/png",
                resource_type="image",
                size_bytes=12,
                occurred_at="2026-06-01T00:00:00+00:00",
                scope_lesson_id=lesson.id,
            ),
            ResourceActivityEvent(
                id="event_delete",
                action="deleted",
                resource_id="resource_1",
                resource_name="lesson.png",
                mime_type="image/png",
                resource_type="image",
                size_bytes=12,
                occurred_at="2026-06-01T00:01:00+00:00",
                scope_lesson_id=lesson.id,
            ),
        ]
    )
    store.save(workspace)

    reloaded = store.load()
    events = reloaded.packages[0].resource_events

    assert [event.action for event in events] == ["uploaded", "deleted"]
    assert events[0].resource_name == "lesson.png"
    assert events[1].scope_lesson_id == lesson.id

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT action, resource_name
            FROM resource_events
            ORDER BY sort_order
            """
        ).fetchall()
    assert rows == [("uploaded", "lesson.png"), ("deleted", "lesson.png")]


def test_sqlite_store_round_trips_board_teaching_progress(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = store.load()
    lesson = _append_lesson(workspace)
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


def test_sqlite_store_preserves_user_lessons_in_account_workspace(tmp_path) -> None:
    db_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(db_path, legacy_json_path=None)

    workspace = build_initial_workspace_state()
    user_lesson = _append_lesson(workspace, "用户页面")
    store.save_for_user("guest_preview", workspace)

    reloaded = store.load_for_user("guest_preview")
    package = reloaded.packages[0]

    assert [lesson.title for lesson in package.lessons] == ["用户页面"]
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
