from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.models import (
    CoursePackage,
    CoursePackageView,
    Lesson,
    LessonView,
    WorkspaceState,
    WorkspaceStateView,
)
from app.services.course_store import FileCourseStore
from app.services.history import commit_operations

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
STORE = FileCourseStore(DATA_DIR / "store.json")
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"


def ensure_data_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_workspace() -> WorkspaceState:
    return STORE.load()


def save_workspace(workspace: WorkspaceState) -> None:
    STORE.save(workspace)


def get_package(workspace: WorkspaceState, package_id: str) -> CoursePackage:
    for package in workspace.packages:
        if package.id == package_id:
            return package
    raise HTTPException(status_code=404, detail=f"Unknown course package {package_id}")


def get_active_package(workspace: WorkspaceState) -> CoursePackage:
    if not workspace.packages:
        raise HTTPException(status_code=404, detail="No course package available")
    if workspace.active_package_id:
        return get_package(workspace, workspace.active_package_id)
    workspace.active_package_id = workspace.packages[0].id
    return workspace.packages[0]


def load_workspace_package() -> tuple[WorkspaceState, CoursePackage]:
    workspace = load_workspace()
    package = get_active_package(workspace)
    return workspace, package


def get_lesson(package: CoursePackage, lesson_id: str) -> Lesson:
    for lesson in package.lessons:
        if lesson.id == lesson_id:
            return lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def find_lesson_package(workspace: WorkspaceState, lesson_id: str) -> tuple[CoursePackage, Lesson]:
    for package in workspace.packages:
        for lesson in package.lessons:
            if lesson.id == lesson_id:
                return package, lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def normalize_package_state(package: CoursePackage) -> None:
    lesson_ids = [lesson.id for lesson in package.lessons]
    valid_ids = set(lesson_ids)
    package.open_lesson_ids = [lesson_id for lesson_id in package.open_lesson_ids if lesson_id in valid_ids]
    package.workspace_tab_order = [lesson_id for lesson_id in package.workspace_tab_order if lesson_id in valid_ids]
    package.course_graph = [
        edge
        for edge in package.course_graph
        if edge.source_lesson_id in valid_ids and edge.target_lesson_id in valid_ids
    ]

    if not package.lessons:
        package.active_lesson_id = None
        package.open_lesson_ids = []
        package.workspace_tab_order = []
        return

    if not package.workspace_tab_order:
        package.workspace_tab_order = [package.lessons[0].id]

    if not package.open_lesson_ids:
        package.open_lesson_ids = list(package.workspace_tab_order)

    for lesson_id in package.workspace_tab_order:
        if lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(lesson_id)

    if package.active_lesson_id not in valid_ids:
        package.active_lesson_id = package.workspace_tab_order[0]
    elif package.active_lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(package.active_lesson_id)
        if package.active_lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(package.active_lesson_id)


def lesson_view(lesson: Lesson) -> LessonView:
    return LessonView.model_validate(
        lesson.model_dump(mode="json", exclude={"teaching_guide", "board_teaching_guide"})
    )


def package_view(package: CoursePackage) -> CoursePackageView:
    return CoursePackageView.model_validate(
        package.model_dump(
            mode="json",
            exclude={"lessons": {"__all__": {"teaching_guide", "board_teaching_guide"}}},
        )
    )


def workspace_view(workspace: WorkspaceState) -> WorkspaceStateView:
    return WorkspaceStateView(
        packages=[package_view(package) for package in workspace.packages],
        active_package_id=workspace.active_package_id,
    )


def commit_document_snapshot(
    lesson: Lesson,
    *,
    label: str,
    message: str,
    metadata: dict[str, object] | None = None,
) -> None:
    commit_operations(
        lesson,
        [],
        label=label,
        message=message,
        new_document=lesson.board_document,
        metadata=metadata,
    )
