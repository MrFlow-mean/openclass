from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.models import CourseGraphEdge, CoursePackage, WorkspaceState
from app.services.lesson_factory import create_lesson


class FileCourseStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> WorkspaceState:
        if not self.path.exists():
            workspace = build_initial_workspace_state()
            self.save(workspace)
            return workspace
        raw_text = self.path.read_text(encoding="utf-8")
        try:
            raw_data = json.loads(raw_text)
            if _contains_legacy_blocks(raw_data):
                self._backup_legacy_store(raw_text)
                workspace = build_initial_workspace_state()
                self.save(workspace)
                return workspace
            if isinstance(raw_data, dict) and isinstance(raw_data.get("packages"), list):
                return WorkspaceState.model_validate(raw_data)
            package = CoursePackage.model_validate(raw_data)
            workspace = WorkspaceState(packages=[package], active_package_id=package.id)
            self.save(workspace)
            return workspace
        except Exception:
            self._backup_legacy_store(raw_text)
            workspace = build_initial_workspace_state()
            self.save(workspace)
            return workspace

    def save(self, workspace: WorkspaceState) -> None:
        self.path.write_text(
            json.dumps(workspace.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _backup_legacy_store(self, raw_text: str) -> None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = self.path.with_name(f"{self.path.stem}.legacy-blocks-backup-{timestamp}.json")
        backup_path.write_text(raw_text, encoding="utf-8")


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
        title="AI 黑板课程工作台",
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
