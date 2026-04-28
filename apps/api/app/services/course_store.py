from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    CourseGraphEdge,
    CoursePackage,
    Lesson,
    LessonHistoryGraph,
    LibraryChapter,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services.lesson_factory import create_lesson


SCHEMA_VERSION = 4


def _active_package_setting_key(owner_user_id: str | None) -> str:
    if owner_user_id:
        return f"active_package_id:{owner_user_id}"
    return "active_package_id"


class SqliteCourseStore:
    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        self.path = path
        self.legacy_json_path = legacy_json_path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def load(self) -> WorkspaceState:
        with self._lock:
            with self._connect() as conn:
                if self._has_any_packages(conn):
                    return self._read_workspace(conn)

            legacy_workspace = self._load_legacy_workspace()
            workspace = legacy_workspace or build_initial_workspace_state()
            self.save(workspace)
            if legacy_workspace is not None:
                self._archive_legacy_json()
            return workspace

    def save(self, workspace: WorkspaceState) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    self._replace_workspace(conn, workspace)

    def load_for_user(self, owner_user_id: str) -> WorkspaceState:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    if self._has_unowned_packages(conn):
                        self._claim_unowned_workspace(
                            conn,
                            self._legacy_workspace_owner_candidate(conn) or owner_user_id,
                        )
                    if not self._has_user_packages(conn, owner_user_id):
                        self._replace_workspace(
                            conn,
                            build_initial_workspace_state(),
                            owner_user_id=owner_user_id,
                        )
                    return self._read_workspace(conn, owner_user_id=owner_user_id)

    def save_for_user(self, owner_user_id: str, workspace: WorkspaceState) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn:
                    self._replace_workspace(conn, workspace, owner_user_id=owner_user_id)

    def _initialize(self) -> None:
        with self._lock:
            with self._connect() as conn:
                self._create_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS course_packages (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                active_lesson_id TEXT
            );

            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                slug TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                board_document_id TEXT NOT NULL,
                board_document_title TEXT NOT NULL,
                board_content_json TEXT NOT NULL,
                board_content_html TEXT NOT NULL,
                board_content_text TEXT NOT NULL,
                board_page_settings_json TEXT NOT NULL,
                board_teaching_guide_json TEXT,
                board_teaching_progress_json TEXT,
                learning_requirements_json TEXT,
                teaching_guide_json TEXT NOT NULL,
                current_branch TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lessons_package
                ON lessons(package_id, sort_order);

            CREATE TABLE IF NOT EXISTS package_open_lessons (
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (package_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS package_tab_order (
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (package_id, lesson_id)
            );

            CREATE TABLE IF NOT EXISTS course_graph_edges (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                source_lesson_id TEXT NOT NULL,
                target_lesson_id TEXT NOT NULL,
                relationship TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lesson_commits (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                label TEXT NOT NULL,
                message TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                operations_json TEXT NOT NULL,
                snapshot_document_id TEXT NOT NULL,
                snapshot_title TEXT NOT NULL,
                snapshot_content_json TEXT NOT NULL,
                snapshot_content_html TEXT NOT NULL,
                snapshot_content_text TEXT NOT NULL,
                snapshot_page_settings_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lesson_commits_lesson
                ON lesson_commits(lesson_id, sort_order);

            CREATE TABLE IF NOT EXISTS lesson_commit_parents (
                commit_id TEXT NOT NULL REFERENCES lesson_commits(id) ON DELETE CASCADE,
                parent_id TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                PRIMARY KEY (commit_id, sort_order)
            );

            CREATE TABLE IF NOT EXISTS lesson_branches (
                lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                head_commit_id TEXT NOT NULL,
                base_commit_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (lesson_id, name)
            );

            CREATE TABLE IF NOT EXISTS resources (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES course_packages(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                scope_lesson_id TEXT,
                concept_index_json TEXT NOT NULL,
                extracted_text_available INTEGER NOT NULL,
                text_content TEXT,
                source_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_resources_package
                ON resources(package_id, sort_order);

            CREATE TABLE IF NOT EXISTS resource_chapters (
                id TEXT NOT NULL,
                resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                level INTEGER NOT NULL,
                page_range TEXT,
                page_start INTEGER,
                page_end INTEGER,
                summary TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                prerequisites_json TEXT NOT NULL,
                parent_id TEXT,
                parent_title TEXT,
                path_json TEXT NOT NULL,
                locator_hint TEXT,
                order_index INTEGER NOT NULL,
                scan_strategy TEXT NOT NULL,
                PRIMARY KEY (resource_id, id)
            );
            """
        )
        self._migrate_schema(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_course_packages_owner
                ON course_packages(owner_user_id, sort_order)
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        lesson_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "board_teaching_progress_json" not in lesson_columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN board_teaching_progress_json TEXT")
        resource_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(resources)").fetchall()
        }
        if "scope_lesson_id" not in resource_columns:
            conn.execute("ALTER TABLE resources ADD COLUMN scope_lesson_id TEXT")
        package_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(course_packages)").fetchall()
        }
        if "owner_user_id" not in package_columns:
            conn.execute("ALTER TABLE course_packages ADD COLUMN owner_user_id TEXT")

    def _has_any_packages(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute("SELECT 1 FROM course_packages LIMIT 1").fetchone()
        return row is not None

    def _has_user_packages(self, conn: sqlite3.Connection, owner_user_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM course_packages WHERE owner_user_id = ? LIMIT 1",
            (owner_user_id,),
        ).fetchone()
        return row is not None

    def _has_unowned_packages(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM course_packages WHERE owner_user_id IS NULL LIMIT 1"
        ).fetchone()
        return row is not None

    def _claim_unowned_workspace(self, conn: sqlite3.Connection, owner_user_id: str) -> None:
        conn.execute(
            "UPDATE course_packages SET owner_user_id = ? WHERE owner_user_id IS NULL",
            (owner_user_id,),
        )
        active_package_id = _setting(conn, "active_package_id")
        if active_package_id:
            conn.execute(
                "INSERT OR REPLACE INTO workspace_settings(key, value) VALUES (?, ?)",
                (_active_package_setting_key(owner_user_id), active_package_id),
            )
            conn.execute("DELETE FROM workspace_settings WHERE key = ?", ("active_package_id",))

    def _legacy_workspace_owner_candidate(self, conn: sqlite3.Connection) -> str | None:
        if not _table_exists(conn, "users"):
            return None
        row = conn.execute(
            """
            SELECT id
            FROM users
            ORDER BY
                CASE role WHEN 'admin' THEN 0 ELSE 1 END,
                created_at,
                id
            LIMIT 1
            """
        ).fetchone()
        return row["id"] if row is not None else None

    def _read_workspace(self, conn: sqlite3.Connection, *, owner_user_id: str | None = None) -> WorkspaceState:
        active_package_id = _setting(conn, _active_package_setting_key(owner_user_id))
        where_clause = ""
        params: tuple[str, ...] = ()
        if owner_user_id is not None:
            where_clause = "WHERE owner_user_id = ?"
            params = (owner_user_id,)
        packages = [
            self._read_package(conn, package_row)
            for package_row in conn.execute(
                f"""
                SELECT * FROM course_packages
                {where_clause}
                ORDER BY sort_order, id
                """,
                params,
            ).fetchall()
        ]
        return WorkspaceState(packages=packages, active_package_id=active_package_id)

    def _read_package(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CoursePackage:
        package_id = row["id"]
        lessons = [
            self._read_lesson(conn, lesson_row)
            for lesson_row in conn.execute(
                """
                SELECT * FROM lessons
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        course_graph = [
            CourseGraphEdge(
                id=edge_row["id"],
                source_lesson_id=edge_row["source_lesson_id"],
                target_lesson_id=edge_row["target_lesson_id"],
                relationship=edge_row["relationship"],
            )
            for edge_row in conn.execute(
                """
                SELECT * FROM course_graph_edges
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        resources = [
            self._read_resource(conn, resource_row)
            for resource_row in conn.execute(
                """
                SELECT * FROM resources
                WHERE package_id = ?
                ORDER BY sort_order, id
                """,
                (package_id,),
            ).fetchall()
        ]
        open_lesson_ids = _ordered_values(conn, "package_open_lessons", package_id)
        workspace_tab_order = _ordered_values(conn, "package_tab_order", package_id)
        return CoursePackage(
            id=package_id,
            title=row["title"],
            summary=row["summary"],
            lessons=lessons,
            course_graph=course_graph,
            resources=resources,
            open_lesson_ids=open_lesson_ids,
            active_lesson_id=row["active_lesson_id"],
            workspace_tab_order=workspace_tab_order,
        )

    def _read_lesson(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Lesson:
        lesson_id = row["id"]
        commits = [
            self._read_commit(conn, commit_row)
            for commit_row in conn.execute(
                """
                SELECT * FROM lesson_commits
                WHERE lesson_id = ?
                ORDER BY sort_order, id
                """,
                (lesson_id,),
            ).fetchall()
        ]
        branches = {
            branch_row["name"]: BranchRef(
                name=branch_row["name"],
                head_commit_id=branch_row["head_commit_id"],
                base_commit_id=branch_row["base_commit_id"],
                created_at=branch_row["created_at"],
            )
            for branch_row in conn.execute(
                """
                SELECT * FROM lesson_branches
                WHERE lesson_id = ?
                ORDER BY name
                """,
                (lesson_id,),
            ).fetchall()
        }
        history_graph = LessonHistoryGraph(
            branches=branches,
            commits=commits,
            current_branch=row["current_branch"],
        )
        return Lesson(
            id=lesson_id,
            title=row["title"],
            slug=row["slug"],
            summary=row["summary"],
            tags=_loads(row["tags_json"], []),
            board_document=_document_from_row(row, "board"),
            board_teaching_guide=_loads_optional(row["board_teaching_guide_json"]),
            board_teaching_progress=_loads_optional(row["board_teaching_progress_json"]),
            learning_requirements=_loads_optional(row["learning_requirements_json"]),
            teaching_guide=_loads(row["teaching_guide_json"], {}),
            history_graph=history_graph,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _read_commit(self, conn: sqlite3.Connection, row: sqlite3.Row) -> CommitRecord:
        parent_ids = [
            parent_row["parent_id"]
            for parent_row in conn.execute(
                """
                SELECT parent_id FROM lesson_commit_parents
                WHERE commit_id = ?
                ORDER BY sort_order
                """,
                (row["id"],),
            ).fetchall()
        ]
        return CommitRecord(
            id=row["id"],
            label=row["label"],
            message=row["message"],
            branch_name=row["branch_name"],
            created_at=row["created_at"],
            parent_ids=parent_ids,
            operations=_loads(row["operations_json"], []),
            snapshot=_document_from_row(row, "snapshot"),
            metadata=_loads(row["metadata_json"], {}),
        )

    def _read_resource(self, conn: sqlite3.Connection, row: sqlite3.Row) -> ResourceLibraryItem:
        chapters = [
            LibraryChapter(
                id=chapter_row["id"],
                title=chapter_row["title"],
                level=chapter_row["level"],
                page_range=chapter_row["page_range"],
                page_start=chapter_row["page_start"],
                page_end=chapter_row["page_end"],
                summary=chapter_row["summary"],
                keywords=_loads(chapter_row["keywords_json"], []),
                prerequisites=_loads(chapter_row["prerequisites_json"], []),
                parent_id=chapter_row["parent_id"],
                parent_title=chapter_row["parent_title"],
                path=_loads(chapter_row["path_json"], []),
                locator_hint=chapter_row["locator_hint"],
                order_index=chapter_row["order_index"],
                scan_strategy=chapter_row["scan_strategy"],
            )
            for chapter_row in conn.execute(
                """
                SELECT * FROM resource_chapters
                WHERE resource_id = ?
                ORDER BY sort_order, id
                """,
                (row["id"],),
            ).fetchall()
        ]
        return ResourceLibraryItem(
            id=row["id"],
            name=row["name"],
            mime_type=row["mime_type"],
            resource_type=row["resource_type"],
            size_bytes=row["size_bytes"],
            uploaded_at=row["uploaded_at"],
            scope_lesson_id=row["scope_lesson_id"],
            outline=chapters,
            concept_index=_loads(row["concept_index_json"], {}),
            extracted_text_available=bool(row["extracted_text_available"]),
            text_content=row["text_content"],
            source_path=row["source_path"],
        )

    def _replace_workspace(
        self,
        conn: sqlite3.Connection,
        workspace: WorkspaceState,
        *,
        owner_user_id: str | None = None,
    ) -> None:
        setting_key = _active_package_setting_key(owner_user_id)
        if owner_user_id is None:
            conn.execute("DELETE FROM workspace_settings")
            conn.execute("DELETE FROM course_packages")
        else:
            conn.execute("DELETE FROM workspace_settings WHERE key = ?", (setting_key,))
            conn.execute("DELETE FROM course_packages WHERE owner_user_id = ?", (owner_user_id,))
        conn.execute(
            "INSERT INTO workspace_settings(key, value) VALUES (?, ?)",
            (setting_key, workspace.active_package_id or ""),
        )
        for package_index, package in enumerate(workspace.packages):
            self._insert_package(conn, package, package_index, owner_user_id=owner_user_id)

    def _insert_package(
        self,
        conn: sqlite3.Connection,
        package: CoursePackage,
        package_index: int,
        *,
        owner_user_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO course_packages(
                id, owner_user_id, title, summary, sort_order, active_lesson_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (package.id, owner_user_id, package.title, package.summary, package_index, package.active_lesson_id),
        )
        for index, lesson_id in enumerate(package.open_lesson_ids):
            conn.execute(
                """
                INSERT INTO package_open_lessons(package_id, lesson_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (package.id, lesson_id, index),
            )
        for index, lesson_id in enumerate(package.workspace_tab_order):
            conn.execute(
                """
                INSERT INTO package_tab_order(package_id, lesson_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (package.id, lesson_id, index),
            )
        for lesson_index, lesson in enumerate(package.lessons):
            self._insert_lesson(conn, package.id, lesson, lesson_index)
        for edge_index, edge in enumerate(package.course_graph):
            conn.execute(
                """
                INSERT INTO course_graph_edges(
                    id, package_id, sort_order, source_lesson_id, target_lesson_id, relationship
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.id,
                    package.id,
                    edge_index,
                    edge.source_lesson_id,
                    edge.target_lesson_id,
                    edge.relationship,
                ),
            )
        for resource_index, resource in enumerate(package.resources):
            self._insert_resource(conn, package.id, resource, resource_index)

    def _insert_lesson(
        self,
        conn: sqlite3.Connection,
        package_id: str,
        lesson: Lesson,
        lesson_index: int,
    ) -> None:
        document = lesson.board_document
        conn.execute(
            """
            INSERT INTO lessons(
                id, package_id, sort_order, title, slug, summary, tags_json,
                board_document_id, board_document_title, board_content_json,
                board_content_html, board_content_text, board_page_settings_json,
                board_teaching_guide_json, board_teaching_progress_json, learning_requirements_json, teaching_guide_json,
                current_branch, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lesson.id,
                package_id,
                lesson_index,
                lesson.title,
                lesson.slug,
                lesson.summary,
                _dumps(lesson.tags),
                document.id,
                document.title,
                _dumps(document.content_json),
                document.content_html,
                document.content_text,
                _dumps(document.page_settings.model_dump(mode="json")),
                _dumps_optional(lesson.board_teaching_guide),
                _dumps_optional(lesson.board_teaching_progress),
                _dumps_optional(lesson.learning_requirements),
                _dumps(lesson.teaching_guide.model_dump(mode="json")),
                lesson.history_graph.current_branch,
                lesson.created_at,
                lesson.updated_at,
            ),
        )
        for commit_index, commit in enumerate(lesson.history_graph.commits):
            self._insert_commit(conn, lesson.id, commit, commit_index)
        for branch in lesson.history_graph.branches.values():
            conn.execute(
                """
                INSERT INTO lesson_branches(
                    lesson_id, name, head_commit_id, base_commit_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (lesson.id, branch.name, branch.head_commit_id, branch.base_commit_id, branch.created_at),
            )

    def _insert_commit(
        self,
        conn: sqlite3.Connection,
        lesson_id: str,
        commit: CommitRecord,
        commit_index: int,
    ) -> None:
        snapshot = commit.snapshot
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
                commit.id,
                lesson_id,
                commit_index,
                commit.label,
                commit.message,
                commit.branch_name,
                commit.created_at,
                _dumps([operation.model_dump(mode="json") for operation in commit.operations]),
                snapshot.id,
                snapshot.title,
                _dumps(snapshot.content_json),
                snapshot.content_html,
                snapshot.content_text,
                _dumps(snapshot.page_settings.model_dump(mode="json")),
                _dumps(commit.metadata),
            ),
        )
        for parent_index, parent_id in enumerate(commit.parent_ids):
            conn.execute(
                """
                INSERT INTO lesson_commit_parents(commit_id, parent_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (commit.id, parent_id, parent_index),
            )

    def _insert_resource(
        self,
        conn: sqlite3.Connection,
        package_id: str,
        resource: ResourceLibraryItem,
        resource_index: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO resources(
                id, package_id, sort_order, name, mime_type, resource_type, size_bytes,
                uploaded_at, scope_lesson_id, concept_index_json, extracted_text_available, text_content, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resource.id,
                package_id,
                resource_index,
                resource.name,
                resource.mime_type,
                resource.resource_type,
                resource.size_bytes,
                resource.uploaded_at,
                resource.scope_lesson_id,
                _dumps(resource.concept_index),
                int(resource.extracted_text_available),
                resource.text_content,
                resource.source_path,
            ),
        )
        for chapter_index, chapter in enumerate(resource.outline):
            conn.execute(
                """
                INSERT INTO resource_chapters(
                    id, resource_id, sort_order, title, level, page_range, page_start, page_end,
                    summary, keywords_json, prerequisites_json, parent_id, parent_title, path_json,
                    locator_hint, order_index, scan_strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter.id,
                    resource.id,
                    chapter_index,
                    chapter.title,
                    chapter.level,
                    chapter.page_range,
                    chapter.page_start,
                    chapter.page_end,
                    chapter.summary,
                    _dumps(chapter.keywords),
                    _dumps(chapter.prerequisites),
                    chapter.parent_id,
                    chapter.parent_title,
                    _dumps(chapter.path),
                    chapter.locator_hint,
                    chapter.order_index,
                    chapter.scan_strategy,
                ),
            )

    def _load_legacy_workspace(self) -> WorkspaceState | None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return None
        raw_text = self.legacy_json_path.read_text(encoding="utf-8")
        try:
            raw_data = json.loads(raw_text)
            if _contains_legacy_blocks(raw_data):
                self._backup_legacy_json(raw_text, "legacy-blocks-backup")
                return build_initial_workspace_state()
            if isinstance(raw_data, dict) and isinstance(raw_data.get("packages"), list):
                return WorkspaceState.model_validate(raw_data)
            package = CoursePackage.model_validate(raw_data)
            return WorkspaceState(packages=[package], active_package_id=package.id)
        except Exception:
            self._backup_legacy_json(raw_text, "invalid-backup")
            return None

    def _backup_legacy_json(self, raw_text: str, suffix: str) -> None:
        if self.legacy_json_path is None:
            return
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.legacy_json_path.with_name(f"{self.legacy_json_path.stem}.{suffix}-{timestamp}.json")
        backup_path.write_text(raw_text, encoding="utf-8")

    def _archive_legacy_json(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive_path = self.legacy_json_path.with_name(f"{self.legacy_json_path.stem}.migrated-{timestamp}.json")
        self.legacy_json_path.replace(archive_path)


def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM workspace_settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] == "":
        return None
    return row["value"]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _ordered_values(conn: sqlite3.Connection, table: str, package_id: str) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT lesson_id FROM {table}
        WHERE package_id = ?
        ORDER BY sort_order, lesson_id
        """,
        (package_id,),
    ).fetchall()
    return [row["lesson_id"] for row in rows]


def _document_from_row(row: sqlite3.Row, prefix: str) -> BoardDocument:
    title_key = "board_document_title" if prefix == "board" else f"{prefix}_title"
    return BoardDocument(
        id=row[f"{prefix}_document_id"],
        title=row[title_key],
        content_json=_loads(row[f"{prefix}_content_json"], {"type": "doc", "content": [{"type": "paragraph"}]}),
        content_html=row[f"{prefix}_content_html"],
        content_text=row[f"{prefix}_content_text"],
        page_settings=_loads(row[f"{prefix}_page_settings_json"], {}),
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _dumps_optional(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return _dumps(value.model_dump(mode="json"))
    return _dumps(value)


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


def _loads_optional(raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    return json.loads(raw)


def _contains_legacy_blocks(raw_data: object) -> bool:
    if not isinstance(raw_data, dict):
        return False
    lessons: list[object] = []
    raw_lessons = raw_data.get("lessons")
    if isinstance(raw_lessons, list):
        lessons.extend(raw_lessons)
    raw_packages = raw_data.get("packages")
    if isinstance(raw_packages, list):
        for package in raw_packages:
            if isinstance(package, dict) and isinstance(package.get("lessons"), list):
                lessons.extend(package["lessons"])
    for lesson in lessons:
        if isinstance(lesson, dict):
            board_document = lesson.get("board_document")
            if isinstance(board_document, dict) and isinstance(board_document.get("blocks"), list):
                return True
    return False


def build_initial_course_package() -> CoursePackage:
    lesson_a = create_lesson("勾股定理")
    lesson_b = create_lesson("直角三角形基础")
    lesson_c = create_lesson("欧几里得几何导论")
    return CoursePackage(
        title="OpenClass 课程工作台",
        summary="把 lesson 当作可编辑、可分支、可讲解的课程资产。",
        lessons=[lesson_a, lesson_b, lesson_c],
        course_graph=[
            CourseGraphEdge(
                source_lesson_id=lesson_b.id,
                target_lesson_id=lesson_a.id,
                relationship="recommended_next",
            ),
            CourseGraphEdge(
                source_lesson_id=lesson_a.id,
                target_lesson_id=lesson_c.id,
                relationship="deep_dive",
            ),
        ],
        open_lesson_ids=[lesson_a.id, lesson_b.id],
        active_lesson_id=lesson_a.id,
        workspace_tab_order=[lesson_a.id, lesson_b.id],
    )


def build_initial_workspace_state() -> WorkspaceState:
    package = build_initial_course_package()
    return WorkspaceState(packages=[package], active_package_id=package.id)
