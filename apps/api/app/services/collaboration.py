"""开放课程协作：发布、fork、贡献审核与 merge。

Fork 复制资料文件到独立 upload 路径；merge 在事务内写回主线 lesson 与 history。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.constants import COMMIT_KIND_COURSE_CONTRIBUTION_MERGE
from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    ContributionLessonChange,
    ContributionResourceChange,
    CourseContributionEventView,
    CourseContributionStatus,
    CourseContributionSummary,
    CourseContributionView,
    CourseForkView,
    CourseGraphEdge,
    CourseMaintainerView,
    CoursePackage,
    CoursePackageView,
    OpenCourseDetail,
    OpenCourseListResponse,
    OpenCourseStats,
    OpenCourseSummary,
    PublicUserView,
    ResourceLibraryItem,
    UserView,
    new_id,
    now_iso,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.workspace_state import (
    get_package,
    load_workspace_for_user,
    normalize_package_state,
    package_view,
    package_view_for_lesson,
    save_workspace_for_user,
)


class CourseCollaborationService:
    def __init__(self, database_path: Path, upload_dir: Path) -> None:
        self.database_path = database_path
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS course_publications (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_course_publications_owner
                    ON course_publications(owner_user_id, package_id);

                CREATE INDEX IF NOT EXISTS idx_course_publications_status
                    ON course_publications(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS course_publication_maintainers (
                    publication_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (publication_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_course_publication_maintainers_user
                    ON course_publication_maintainers(user_id);

                CREATE TABLE IF NOT EXISTS course_forks (
                    id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL,
                    source_owner_user_id TEXT NOT NULL,
                    source_package_id TEXT NOT NULL,
                    fork_owner_user_id TEXT NOT NULL,
                    fork_package_id TEXT NOT NULL,
                    id_map_json TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_course_forks_publication
                    ON course_forks(publication_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_course_forks_owner_package
                    ON course_forks(fork_owner_user_id, fork_package_id);

                CREATE TABLE IF NOT EXISTS course_contributions (
                    id TEXT PRIMARY KEY,
                    fork_id TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    source_owner_user_id TEXT NOT NULL,
                    source_package_id TEXT NOT NULL,
                    contributor_user_id TEXT NOT NULL,
                    fork_package_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    lesson_changes_json TEXT NOT NULL,
                    resource_changes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_by_user_id TEXT,
                    reviewed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_course_contributions_publication
                    ON course_contributions(publication_id, status, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_course_contributions_fork
                    ON course_contributions(fork_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS course_contribution_events (
                    id TEXT PRIMARY KEY,
                    contribution_id TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_course_contribution_events_contribution
                    ON course_contribution_events(contribution_id, created_at);
                """
            )

    # -----------------------------------------------------------------------
    # 公开 API：publish / list / fork
    # -----------------------------------------------------------------------

    def publish_package(self, user: UserView, package_id: str, summary: str | None = None) -> OpenCourseDetail:
        self._require_account_user(user)
        workspace = load_workspace_for_user(user.id)
        package = get_package(workspace, package_id)
        now = now_iso()
        with self._connect() as conn:
            with conn:
                row = conn.execute(
                    """
                    SELECT * FROM course_publications
                    WHERE owner_user_id = ? AND package_id = ?
                    """,
                    (user.id, package_id),
                ).fetchone()
                publication_summary = (summary or package.summary).strip()
                if row is None:
                    publication_id = new_id("publication")
                    conn.execute(
                        """
                        INSERT INTO course_publications(
                            id, owner_user_id, package_id, title, summary, slug, status, published_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'public', ?, ?)
                        """,
                        (
                            publication_id,
                            user.id,
                            package_id,
                            package.title,
                            publication_summary,
                            self._unique_slug(conn, user, package.title, package_id),
                            now,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO course_publication_maintainers(
                            publication_id, user_id, role, added_at
                        ) VALUES (?, ?, 'owner', ?)
                        """,
                        (publication_id, user.id, now),
                    )
                else:
                    publication_id = row["id"]
                    conn.execute(
                        """
                        UPDATE course_publications
                        SET title = ?, summary = ?, status = 'public', updated_at = ?
                        WHERE id = ?
                        """,
                        (package.title, publication_summary, now, publication_id),
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO course_publication_maintainers(
                            publication_id, user_id, role, added_at
                        ) VALUES (?, ?, 'owner', ?)
                        """,
                        (publication_id, user.id, row["published_at"]),
                    )
        return self.get_open_course(publication_id, viewer=user)

    def list_open_courses(self) -> OpenCourseListResponse:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM course_publications
                WHERE status = 'public'
                ORDER BY updated_at DESC, title
                """
            ).fetchall()
        return OpenCourseListResponse(courses=[self._summary_from_publication(row) for row in rows])

    def get_open_course(self, publication_id: str, viewer: UserView | None = None) -> OpenCourseDetail:
        row = self._publication_row(publication_id)
        package = self._load_source_package(row)
        contributions = self._contribution_summaries(publication_id)
        viewer_fork = self._viewer_fork(publication_id, viewer.id) if viewer is not None else None
        return OpenCourseDetail(
            course=self._summary_from_publication(row, package=package),
            package=package_view(package, is_standalone=False),
            maintainers=self._maintainers(publication_id),
            contributions=contributions,
            viewer_can_review=bool(viewer and self._can_review(row, viewer.id)),
            viewer_is_owner=bool(viewer and row["owner_user_id"] == viewer.id),
            viewer_fork=viewer_fork,
        )

    def fork_open_course(self, user: UserView, publication_id: str) -> tuple[CourseForkView, CoursePackageView]:
        self._require_account_user(user)
        row = self._publication_row(publication_id)
        source_package = self._load_source_package(row)
        fork_package, id_map = clone_package_for_fork(source_package, self.upload_dir)
        workspace = load_workspace_for_user(user.id)
        workspace.packages.append(fork_package)
        workspace.active_package_id = fork_package.id
        normalize_package_state(fork_package)
        save_workspace_for_user(user.id, workspace)
        now = now_iso()
        fork_id = new_id("fork")
        baseline_json = _dumps(source_package.model_dump(mode="json"))
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO course_forks(
                        id, publication_id, source_owner_user_id, source_package_id,
                        fork_owner_user_id, fork_package_id, id_map_json, baseline_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fork_id,
                        publication_id,
                        row["owner_user_id"],
                        row["package_id"],
                        user.id,
                        fork_package.id,
                        _dumps(id_map),
                        baseline_json,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE course_publications SET updated_at = ? WHERE id = ?",
                    (now, publication_id),
                )
        fork_view = CourseForkView(
            id=fork_id,
            publication_id=publication_id,
            fork_package_id=fork_package.id,
            source_package_id=row["package_id"],
            created_at=now,
            updated_at=now,
        )
        return fork_view, package_view_for_lesson(workspace, fork_package, fork_package.active_lesson_id)

    # -----------------------------------------------------------------------
    # 贡献提交与 maintainer 审核
    # -----------------------------------------------------------------------

    def submit_contribution(
        self,
        user: UserView,
        fork_id: str,
        *,
        title: str,
        description: str,
    ) -> CourseContributionView:
        self._require_account_user(user)
        title = title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Contribution title is required")
        fork = self._fork_row(fork_id)
        if fork["fork_owner_user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Only the fork owner can submit improvements")
        fork_workspace = load_workspace_for_user(user.id)
        fork_package = get_package(fork_workspace, fork["fork_package_id"])
        baseline_package = CoursePackage.model_validate(json.loads(fork["baseline_json"]))
        id_map = json.loads(fork["id_map_json"])
        lesson_changes = lesson_changes_for_contribution(
            baseline_package,
            fork_package,
            self._load_source_package(self._publication_row(fork["publication_id"])),
            id_map,
        )
        resource_changes = resource_changes_for_contribution(baseline_package, fork_package, id_map)
        now = now_iso()
        contribution_id = new_id("contribution")
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO course_contributions(
                        id, fork_id, publication_id, source_owner_user_id, source_package_id,
                        contributor_user_id, fork_package_id, title, description, status,
                        snapshot_json, lesson_changes_json, resource_changes_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                    """,
                    (
                        contribution_id,
                        fork_id,
                        fork["publication_id"],
                        fork["source_owner_user_id"],
                        fork["source_package_id"],
                        user.id,
                        fork["fork_package_id"],
                        title,
                        description.strip(),
                        _dumps(fork_package.model_dump(mode="json")),
                        _dumps([change.model_dump(mode="json") for change in lesson_changes]),
                        _dumps([change.model_dump(mode="json") for change in resource_changes]),
                        now,
                        now,
                    ),
                )
                self._insert_event(conn, contribution_id, user.id, "submitted", description.strip(), now)
                conn.execute("UPDATE course_forks SET updated_at = ? WHERE id = ?", (now, fork_id))
                conn.execute(
                    "UPDATE course_publications SET updated_at = ? WHERE id = ?",
                    (now, fork["publication_id"]),
                )
        return self.get_contribution(contribution_id, viewer=user)

    def get_contribution(self, contribution_id: str, viewer: UserView | None = None) -> CourseContributionView:
        row = self._contribution_row(contribution_id)
        publication = self._publication_row(row["publication_id"])
        if viewer is not None and row["contributor_user_id"] != viewer.id and not self._can_review(publication, viewer.id):
            raise HTTPException(status_code=403, detail="You do not have access to this contribution")
        source_package = self._load_source_package(publication)
        baseline_package = CoursePackage.model_validate(json.loads(self._fork_row(row["fork_id"])["baseline_json"]))
        proposed_package = CoursePackage.model_validate(json.loads(row["snapshot_json"]))
        return CourseContributionView(
            **self._contribution_summary_from_row(row).model_dump(mode="json"),
            course=self._summary_from_publication(publication, package=source_package),
            baseline_package=package_view(baseline_package, is_standalone=False),
            proposed_package=package_view(proposed_package, is_standalone=False),
            source_package=package_view(source_package, is_standalone=False),
            events=self._contribution_events(contribution_id),
        )

    def review_contribution(
        self,
        user: UserView,
        contribution_id: str,
        *,
        action: str,
        message: str,
    ) -> CourseContributionView:
        self._require_account_user(user)
        row = self._contribution_row(contribution_id)
        publication = self._publication_row(row["publication_id"])
        if not self._can_review(publication, user.id):
            raise HTTPException(status_code=403, detail="Only course maintainers can review improvements")
        if row["status"] not in {"open", "changes_requested"}:
            raise HTTPException(status_code=409, detail="This contribution is already closed")
        now = now_iso()
        next_status: CourseContributionStatus
        event_type = action
        if action == "merge":
            self._merge_contribution(row, publication, user.id)
            next_status = "merged"
        elif action == "request_changes":
            next_status = "changes_requested"
        elif action == "close":
            next_status = "closed"
        else:
            raise HTTPException(status_code=400, detail="Unknown review action")
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    UPDATE course_contributions
                    SET status = ?, reviewed_by_user_id = ?, reviewed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_status, user.id, now, now, contribution_id),
                )
                self._insert_event(conn, contribution_id, user.id, event_type, message.strip(), now)
                conn.execute(
                    "UPDATE course_publications SET updated_at = ? WHERE id = ?",
                    (now, row["publication_id"]),
                )
        return self.get_contribution(contribution_id, viewer=user)

    def add_maintainer(self, user: UserView, publication_id: str, email: str) -> OpenCourseDetail:
        self._require_account_user(user)
        publication = self._publication_row(publication_id)
        if publication["owner_user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Only the owner can manage maintainers")
        target_user = self._user_by_email(email)
        if target_user is None:
            raise HTTPException(status_code=404, detail="No user found for that email")
        now = now_iso()
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO course_publication_maintainers(
                        publication_id, user_id, role, added_at
                    ) VALUES (?, ?, 'maintainer', ?)
                    """,
                    (publication_id, target_user.id, now),
                )
        return self.get_open_course(publication_id, viewer=user)

    def remove_maintainer(self, user: UserView, publication_id: str, maintainer_user_id: str) -> OpenCourseDetail:
        self._require_account_user(user)
        publication = self._publication_row(publication_id)
        if publication["owner_user_id"] != user.id:
            raise HTTPException(status_code=403, detail="Only the owner can manage maintainers")
        if maintainer_user_id == user.id:
            raise HTTPException(status_code=400, detail="The owner cannot be removed")
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    DELETE FROM course_publication_maintainers
                    WHERE publication_id = ? AND user_id = ? AND role = 'maintainer'
                    """,
                    (publication_id, maintainer_user_id),
                )
        return self.get_open_course(publication_id, viewer=user)

    # -----------------------------------------------------------------------
    # Merge 写回（仅 maintainer / owner）
    # -----------------------------------------------------------------------

    def _merge_contribution(self, contribution_row: sqlite3.Row, publication_row: sqlite3.Row, merged_by_user_id: str) -> None:
        source_workspace = load_workspace_for_user(publication_row["owner_user_id"])
        source_package = get_package(source_workspace, publication_row["package_id"])
        proposed_package = CoursePackage.model_validate(json.loads(contribution_row["snapshot_json"]))
        baseline_package = CoursePackage.model_validate(json.loads(self._fork_row(contribution_row["fork_id"])["baseline_json"]))
        id_map = json.loads(self._fork_row(contribution_row["fork_id"])["id_map_json"])
        fork_to_source_lesson = {fork_id: source_id for source_id, fork_id in id_map.get("lessons", {}).items()}
        added_lesson_map: dict[str, str] = {}
        source_lessons = {lesson.id: lesson for lesson in source_package.lessons}
        proposed_lessons = {lesson.id: lesson for lesson in proposed_package.lessons}

        for change in [ContributionLessonChange.model_validate(item) for item in json.loads(contribution_row["lesson_changes_json"])]:
            if change.status == "edited" and change.source_lesson_id and change.fork_lesson_id:
                source_lesson = source_lessons.get(change.source_lesson_id)
                proposed_lesson = proposed_lessons.get(change.fork_lesson_id)
                if source_lesson is None or proposed_lesson is None:
                    continue
                next_document = proposed_lesson.board_document.model_copy(update={"id": source_lesson.board_document.id}, deep=True)
                source_lesson.title = proposed_lesson.title
                source_lesson.slug = proposed_lesson.slug
                source_lesson.summary = proposed_lesson.summary
                source_lesson.tags = list(proposed_lesson.tags)
                source_lesson.learning_requirements = proposed_lesson.learning_requirements
                source_lesson.active_interaction_session = proposed_lesson.active_interaction_session
                commit_operations(
                    source_lesson,
                    operations=[],
                    label=f"Merge contribution: {contribution_row['title']}",
                    message=contribution_row["description"] or f"Merged improvement {contribution_row['id']}",
                    new_document=next_document,
                    metadata={
                        "kind": COMMIT_KIND_COURSE_CONTRIBUTION_MERGE,
                        "contribution_id": contribution_row["id"],
                        "contributor_user_id": contribution_row["contributor_user_id"],
                        "fork_id": contribution_row["fork_id"],
                        "merged_by_user_id": merged_by_user_id,
                        "active_requirement_sheet_after": (
                            source_lesson.learning_requirements.model_dump(mode="json")
                            if source_lesson.learning_requirements is not None
                            else None
                        ),
                        "active_interaction_session_after": (
                            source_lesson.active_interaction_session.model_dump(mode="json")
                            if source_lesson.active_interaction_session is not None
                            else None
                        ),
                    },
                )
                refresh_lesson_runtime(source_lesson)
            elif change.status == "added" and change.fork_lesson_id:
                proposed_lesson = proposed_lessons.get(change.fork_lesson_id)
                if proposed_lesson is None:
                    continue
                cloned_lesson, lesson_map = clone_lesson_for_package(proposed_lesson)
                added_lesson_map[change.fork_lesson_id] = cloned_lesson.id
                fork_to_source_lesson[change.fork_lesson_id] = cloned_lesson.id
                source_package.lessons.append(cloned_lesson)
            elif change.status == "deleted" and change.source_lesson_id:
                source_package.lessons = [
                    lesson for lesson in source_package.lessons if lesson.id != change.source_lesson_id
                ]

        source_package.title = proposed_package.title
        source_package.summary = proposed_package.summary
        self._merge_resources(source_package, proposed_package, id_map, fork_to_source_lesson)
        self._merge_graph_and_order(source_package, proposed_package, fork_to_source_lesson)
        normalize_package_state(source_package)
        save_workspace_for_user(publication_row["owner_user_id"], source_workspace)

    def _merge_resources(
        self,
        source_package: CoursePackage,
        proposed_package: CoursePackage,
        id_map: dict[str, Any],
        fork_to_source_lesson: dict[str, str],
    ) -> None:
        mapped_fork_resource_ids = set(id_map.get("resources", {}).values())
        source_resource_by_id = {resource.id: resource for resource in source_package.resources}
        for proposed_resource in proposed_package.resources:
            if proposed_resource.id in mapped_fork_resource_ids:
                continue
            cloned_resource = clone_resource_for_package(proposed_resource, fork_to_source_lesson, self.upload_dir)
            if cloned_resource.id not in source_resource_by_id:
                source_package.resources.append(cloned_resource)

    def _merge_graph_and_order(
        self,
        source_package: CoursePackage,
        proposed_package: CoursePackage,
        fork_to_source_lesson: dict[str, str],
    ) -> None:
        source_by_id = {lesson.id: lesson for lesson in source_package.lessons}
        ordered_lessons = []
        used = set()
        for proposed_lesson in proposed_package.lessons:
            source_id = fork_to_source_lesson.get(proposed_lesson.id)
            if source_id and source_id in source_by_id and source_id not in used:
                ordered_lessons.append(source_by_id[source_id])
                used.add(source_id)
        ordered_lessons.extend([lesson for lesson in source_package.lessons if lesson.id not in used])
        source_package.lessons = ordered_lessons
        source_package.course_graph = [
            CourseGraphEdge(
                source_lesson_id=fork_to_source_lesson[edge.source_lesson_id],
                target_lesson_id=fork_to_source_lesson[edge.target_lesson_id],
                relationship=edge.relationship,
            )
            for edge in proposed_package.course_graph
            if edge.source_lesson_id in fork_to_source_lesson and edge.target_lesson_id in fork_to_source_lesson
        ]

    def _publication_row(self, publication_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM course_publications WHERE id = ? AND status = 'public'",
                (publication_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Open course not found")
        return row

    def _fork_row(self, fork_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM course_forks WHERE id = ?", (fork_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Fork not found")
        return row

    def _contribution_row(self, contribution_id: str) -> sqlite3.Row:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM course_contributions WHERE id = ?", (contribution_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Contribution not found")
        return row

    def _load_source_package(self, publication_row: sqlite3.Row) -> CoursePackage:
        workspace = load_workspace_for_user(publication_row["owner_user_id"])
        return get_package(workspace, publication_row["package_id"])

    def _summary_from_publication(self, row: sqlite3.Row, *, package: CoursePackage | None = None) -> OpenCourseSummary:
        current_package = package
        try:
            current_package = current_package or self._load_source_package(row)
        except HTTPException:
            current_package = None
        stats = self._stats(row["id"], current_package)
        return OpenCourseSummary(
            id=row["id"],
            package_id=row["package_id"],
            owner=self._public_user(row["owner_user_id"]),
            title=current_package.title if current_package is not None else row["title"],
            summary=row["summary"],
            topics=_package_topics(current_package) if current_package is not None else [],
            stats=stats,
            published_at=row["published_at"],
            updated_at=_latest_package_update(current_package) or row["updated_at"],
        )

    def _stats(self, publication_id: str, package: CoursePackage | None) -> OpenCourseStats:
        with self._connect() as conn:
            fork_count = conn.execute(
                "SELECT count(*) FROM course_forks WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()[0]
            open_contributions = conn.execute(
                """
                SELECT count(*) FROM course_contributions
                WHERE publication_id = ? AND status IN ('open', 'changes_requested')
                """,
                (publication_id,),
            ).fetchone()[0]
            contributors = conn.execute(
                "SELECT count(DISTINCT contributor_user_id) FROM course_contributions WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()[0]
            maintainers = conn.execute(
                "SELECT count(*) FROM course_publication_maintainers WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()[0]
        return OpenCourseStats(
            lessons=len(package.lessons) if package is not None else 0,
            resources=len(package.resources) if package is not None else 0,
            forks=fork_count,
            open_contributions=open_contributions,
            contributors=contributors,
            maintainers=maintainers,
        )

    def _maintainers(self, publication_id: str) -> list[CourseMaintainerView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM course_publication_maintainers
                WHERE publication_id = ?
                ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, added_at
                """,
                (publication_id,),
            ).fetchall()
        return [
            CourseMaintainerView(
                publication_id=publication_id,
                user=self._public_user(row["user_id"]),
                role=row["role"],
                added_at=row["added_at"],
            )
            for row in rows
        ]

    def _contribution_summaries(self, publication_id: str) -> list[CourseContributionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM course_contributions
                WHERE publication_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (publication_id,),
            ).fetchall()
        return [self._contribution_summary_from_row(row) for row in rows]

    def _contribution_summary_from_row(self, row: sqlite3.Row) -> CourseContributionSummary:
        return CourseContributionSummary(
            id=row["id"],
            publication_id=row["publication_id"],
            fork_id=row["fork_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            contributor=self._public_user(row["contributor_user_id"]),
            lesson_changes=[
                ContributionLessonChange.model_validate(item)
                for item in json.loads(row["lesson_changes_json"])
            ],
            resource_changes=[
                ContributionResourceChange.model_validate(item)
                for item in json.loads(row["resource_changes_json"])
            ],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reviewed_by=self._public_user(row["reviewed_by_user_id"]) if row["reviewed_by_user_id"] else None,
            reviewed_at=row["reviewed_at"],
        )

    def _contribution_events(self, contribution_id: str) -> list[CourseContributionEventView]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM course_contribution_events
                WHERE contribution_id = ?
                ORDER BY created_at, id
                """,
                (contribution_id,),
            ).fetchall()
        return [
            CourseContributionEventView(
                id=row["id"],
                actor=self._public_user(row["actor_user_id"]),
                event_type=row["event_type"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def _viewer_fork(self, publication_id: str, user_id: str) -> CourseForkView | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM course_forks
                WHERE publication_id = ? AND fork_owner_user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (publication_id, user_id),
            ).fetchone()
        return self._fork_view(row) if row is not None else None

    def _fork_view(self, row: sqlite3.Row) -> CourseForkView:
        return CourseForkView(
            id=row["id"],
            publication_id=row["publication_id"],
            fork_package_id=row["fork_package_id"],
            source_package_id=row["source_package_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _can_review(self, publication_row: sqlite3.Row, user_id: str) -> bool:
        if publication_row["owner_user_id"] == user_id:
            return True
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM course_publication_maintainers
                WHERE publication_id = ? AND user_id = ? AND role IN ('owner', 'maintainer')
                """,
                (publication_row["id"], user_id),
            ).fetchone()
        return row is not None

    def _public_user(self, user_id: str) -> PublicUserView:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, display_name, avatar_url FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return PublicUserView(id=user_id, display_name="OpenClass user", avatar_url=None)
        return PublicUserView(
            id=row["id"],
            display_name=row["display_name"] or row["email"].split("@", 1)[0],
            avatar_url=row["avatar_url"],
        )

    def _user_by_email(self, email: str) -> PublicUserView | None:
        normalized = email.strip().lower()
        if not normalized:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, email, display_name, avatar_url FROM users WHERE lower(email) = ?",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return PublicUserView(
            id=row["id"],
            display_name=row["display_name"] or row["email"].split("@", 1)[0],
            avatar_url=row["avatar_url"],
        )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        contribution_id: str,
        actor_user_id: str,
        event_type: str,
        message: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO course_contribution_events(
                id, contribution_id, actor_user_id, event_type, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("event"), contribution_id, actor_user_id, event_type, message, created_at),
        )

    def _unique_slug(self, conn: sqlite3.Connection, user: UserView, title: str, package_id: str) -> str:
        base = _slugify(f"{user.display_name or user.email.split('@', 1)[0]}-{title}") or package_id
        slug = base
        suffix = 2
        while conn.execute("SELECT 1 FROM course_publications WHERE slug = ?", (slug,)).fetchone() is not None:
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    def _require_account_user(self, user: UserView) -> None:
        if user.role == "guest":
            raise HTTPException(status_code=403, detail="Please sign in to collaborate on courses")


def clone_package_for_fork(package: CoursePackage, upload_dir: Path) -> tuple[CoursePackage, dict[str, Any]]:
    lesson_id_map: dict[str, str] = {}
    commit_id_map: dict[str, str] = {}
    resource_id_map: dict[str, str] = {}
    cloned_lessons = []
    for lesson in package.lessons:
        cloned_lesson, lesson_map = clone_lesson_for_package(lesson)
        lesson_id_map[lesson.id] = cloned_lesson.id
        commit_id_map.update(lesson_map)
        cloned_lessons.append(cloned_lesson)
    cloned_resources = [
        clone_resource_for_package(resource, lesson_id_map, upload_dir, resource_id_map=resource_id_map)
        for resource in package.resources
    ]
    cloned_package = CoursePackage(
        title=package.title,
        summary=package.summary,
        lessons=cloned_lessons,
        course_graph=[
            CourseGraphEdge(
                source_lesson_id=lesson_id_map[edge.source_lesson_id],
                target_lesson_id=lesson_id_map[edge.target_lesson_id],
                relationship=edge.relationship,
            )
            for edge in package.course_graph
            if edge.source_lesson_id in lesson_id_map and edge.target_lesson_id in lesson_id_map
        ],
        resources=cloned_resources,
        open_lesson_ids=[lesson_id_map[lesson_id] for lesson_id in package.open_lesson_ids if lesson_id in lesson_id_map],
        active_lesson_id=lesson_id_map.get(package.active_lesson_id or ""),
        workspace_tab_order=[
            lesson_id_map[lesson_id] for lesson_id in package.workspace_tab_order if lesson_id in lesson_id_map
        ],
    )
    return cloned_package, {
        "package": {"source": package.id, "fork": cloned_package.id},
        "lessons": lesson_id_map,
        "commits": commit_id_map,
        "resources": resource_id_map,
    }


def clone_lesson_for_package(lesson: Any) -> tuple[Any, dict[str, str]]:
    raw = lesson.model_dump(mode="json")
    old_commit_ids = [commit["id"] for commit in raw["history_graph"]["commits"]]
    commit_id_map = {commit_id: new_id("commit") for commit_id in old_commit_ids}
    raw["id"] = new_id("lesson")
    raw["board_document"]["id"] = new_id("doc")
    if isinstance(raw.get("teaching_guide"), dict):
        raw["teaching_guide"]["lesson_id"] = raw["id"]
    for commit in raw["history_graph"]["commits"]:
        commit["id"] = commit_id_map[commit["id"]]
        commit["parent_ids"] = [commit_id_map[parent_id] for parent_id in commit.get("parent_ids", []) if parent_id in commit_id_map]
        commit["snapshot"]["id"] = new_id("doc")
    for branch in raw["history_graph"]["branches"].values():
        if branch.get("head_commit_id") in commit_id_map:
            branch["head_commit_id"] = commit_id_map[branch["head_commit_id"]]
        if branch.get("base_commit_id") in commit_id_map:
            branch["base_commit_id"] = commit_id_map[branch["base_commit_id"]]
    return lesson.__class__.model_validate(raw), commit_id_map


def clone_resource_for_package(
    resource: ResourceLibraryItem,
    lesson_id_map: dict[str, str],
    upload_dir: Path,
    *,
    resource_id_map: dict[str, str] | None = None,
) -> ResourceLibraryItem:
    raw = resource.model_dump(mode="json")
    old_id = raw["id"]
    raw["id"] = new_id("resource")
    if resource_id_map is not None:
        resource_id_map[old_id] = raw["id"]
    if raw.get("scope_lesson_id") in lesson_id_map:
        raw["scope_lesson_id"] = lesson_id_map[raw["scope_lesson_id"]]
    raw["source_path"] = _copy_resource_file(raw.get("source_path"), raw["id"], upload_dir)
    for segment in raw.get("segments", []):
        segment["resource_id"] = raw["id"]
    return ResourceLibraryItem.model_validate(raw)


def lesson_changes_for_contribution(
    baseline_package: CoursePackage,
    fork_package: CoursePackage,
    current_source_package: CoursePackage,
    id_map: dict[str, Any],
) -> list[ContributionLessonChange]:
    changes: list[ContributionLessonChange] = []
    fork_by_id = {lesson.id: lesson for lesson in fork_package.lessons}
    baseline_by_id = {lesson.id: lesson for lesson in baseline_package.lessons}
    current_by_id = {lesson.id: lesson for lesson in current_source_package.lessons}
    mapped_fork_ids = set(id_map.get("lessons", {}).values())
    for source_lesson_id, fork_lesson_id in id_map.get("lessons", {}).items():
        baseline = baseline_by_id.get(source_lesson_id)
        proposed = fork_by_id.get(fork_lesson_id)
        current = current_by_id.get(source_lesson_id)
        if baseline is None:
            continue
        if proposed is None:
            changes.append(
                ContributionLessonChange(
                    status="deleted",
                    source_lesson_id=source_lesson_id,
                    fork_lesson_id=fork_lesson_id,
                    title=baseline.title,
                    base_summary=_lesson_summary(baseline),
                    current_summary=_lesson_summary(current),
                    proposed_summary="Removed in fork",
                    current_changed=current is not None and _lesson_signature(current) != _lesson_signature(baseline),
                )
            )
            continue
        if _lesson_signature(proposed) != _lesson_signature(baseline):
            changes.append(
                ContributionLessonChange(
                    status="edited",
                    source_lesson_id=source_lesson_id,
                    fork_lesson_id=fork_lesson_id,
                    title=proposed.title,
                    base_summary=_lesson_summary(baseline),
                    current_summary=_lesson_summary(current),
                    proposed_summary=_lesson_summary(proposed),
                    current_changed=current is not None and _lesson_signature(current) != _lesson_signature(baseline),
                )
            )
    for proposed in fork_package.lessons:
        if proposed.id not in mapped_fork_ids:
            changes.append(
                ContributionLessonChange(
                    status="added",
                    fork_lesson_id=proposed.id,
                    title=proposed.title,
                    proposed_summary=_lesson_summary(proposed),
                )
            )
    return changes


def resource_changes_for_contribution(
    baseline_package: CoursePackage,
    fork_package: CoursePackage,
    id_map: dict[str, Any],
) -> list[ContributionResourceChange]:
    changes: list[ContributionResourceChange] = []
    fork_by_id = {resource.id: resource for resource in fork_package.resources}
    baseline_by_id = {resource.id: resource for resource in baseline_package.resources}
    mapped_fork_ids = set(id_map.get("resources", {}).values())
    for source_resource_id, fork_resource_id in id_map.get("resources", {}).items():
        baseline = baseline_by_id.get(source_resource_id)
        proposed = fork_by_id.get(fork_resource_id)
        if baseline is not None and proposed is None:
            changes.append(
                ContributionResourceChange(
                    status="deleted",
                    source_resource_id=source_resource_id,
                    fork_resource_id=fork_resource_id,
                    name=baseline.name,
                )
            )
    for proposed in fork_package.resources:
        if proposed.id not in mapped_fork_ids:
            changes.append(
                ContributionResourceChange(status="added", fork_resource_id=proposed.id, name=proposed.name)
            )
    return changes


def _lesson_signature(lesson: Any) -> str:
    payload = {
        "title": lesson.title,
        "summary": lesson.summary,
        "tags": lesson.tags,
        "document": lesson.board_document.model_dump(mode="json"),
        "requirements": lesson.learning_requirements.model_dump(mode="json")
        if lesson.learning_requirements is not None
        else None,
        "session": lesson.active_interaction_session.model_dump(mode="json")
        if lesson.active_interaction_session is not None
        else None,
    }
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def _lesson_summary(lesson: Any | None) -> str:
    if lesson is None:
        return ""
    text = (lesson.board_document.content_text or "").strip().replace("\n", " ")
    if len(text) > 160:
        text = f"{text[:157]}..."
    return text or lesson.summary


def _package_topics(package: CoursePackage | None) -> list[str]:
    if package is None:
        return []
    topics: list[str] = []
    seen = set()
    for lesson in package.lessons:
        for tag in lesson.tags:
            normalized = tag.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                topics.append(normalized)
    if not topics and package.lessons:
        topics.append("course")
    if package.resources and "resources" not in seen:
        topics.append("resources")
    return topics[:8]


def _latest_package_update(package: CoursePackage | None) -> str | None:
    if package is None:
        return None
    values = [lesson.updated_at for lesson in package.lessons] + [resource.uploaded_at for resource in package.resources]
    timestamps: list[tuple[datetime, str]] = []
    for value in values:
        try:
            timestamps.append((datetime.fromisoformat(value.replace("Z", "+00:00")), value))
        except ValueError:
            continue
    if not timestamps:
        return None
    return max(timestamps, key=lambda item: item[0])[1]


def _copy_resource_file(source_path: str | None, resource_id: str, upload_dir: Path) -> str | None:
    if not source_path:
        return None
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return None
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{resource_id}_{source.name}"
    shutil.copy2(source, destination)
    return str(destination)


def _slugify(value: str) -> str:
    chars = []
    last_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    return "".join(chars).strip("-")[:80]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
