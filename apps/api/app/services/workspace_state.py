from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

from app.models import (
    BoardSegmentKind,
    CoursePackage,
    CoursePackageView,
    DocumentSegmentSearchResult,
    Lesson,
    LessonView,
    ResourceLibraryItem,
    WorkspaceState,
    WorkspaceStateView,
)
from app.services.course_store import SqliteCourseStore
from app.services.config import API_BASE_DIR as BASE_DIR, DATA_DIR, ROOT_DIR, load_root_dotenv
from app.services.course_runtime import active_task_requirements
from app.services.history import commit_operations


def _load_root_dotenv() -> None:
    load_root_dotenv()


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


_load_root_dotenv()

DATABASE_PATH = _path_from_env("OPENCLASS_DATABASE_PATH", DATA_DIR / "openclass.sqlite3")
LEGACY_STORE_PATH = _path_from_env("OPENCLASS_LEGACY_STORE_PATH", DATA_DIR / "store.json")
STORE = SqliteCourseStore(DATABASE_PATH, legacy_json_path=LEGACY_STORE_PATH)
UPLOAD_DIR = _path_from_env("OPENCLASS_UPLOAD_DIR", DATA_DIR / "uploads")
EXPORT_DIR = _path_from_env("OPENCLASS_EXPORT_DIR", DATA_DIR / "exports")


def ensure_data_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_store() -> SqliteCourseStore:
    return STORE


def get_course_store() -> SqliteCourseStore:
    return get_store()


def load_workspace() -> WorkspaceState:
    return get_store().load()


def save_workspace(workspace: WorkspaceState) -> None:
    get_store().save(workspace)


def load_workspace_for_user(user_id: str) -> WorkspaceState:
    return get_store().load_for_user(user_id)


def save_workspace_for_user(user_id: str, workspace: WorkspaceState) -> None:
    get_store().save_for_user(user_id, workspace)


def save_workspace_and_learning_requirement_history_for_user(
    user_id: str,
    workspace: WorkspaceState,
    *,
    learning_requirement_history_operations: list[dict[str, object]] | None = None,
) -> None:
    get_store().save_for_user_with_learning_requirement_history(
        user_id,
        workspace,
        learning_requirement_history_operations=learning_requirement_history_operations or [],
    )


def save_workspace_and_board_task_history_for_user(
    user_id: str,
    workspace: WorkspaceState,
    *,
    board_task_history_operations: list[dict[str, object]] | None = None,
) -> None:
    get_store().save_for_user_with_board_task_history(
        user_id,
        workspace,
        board_task_history_operations=board_task_history_operations or [],
    )


def load_learning_requirement_history_state_for_user(
    user_id: str,
    lesson_id: str,
) -> dict[str, object] | None:
    return get_store().load_learning_requirement_history_state(user_id, lesson_id)


def load_board_task_history_state_for_user(
    user_id: str,
    lesson_id: str,
) -> dict[str, object] | None:
    return get_store().load_board_task_history_state(user_id, lesson_id)


def search_document_segments_for_user(
    user_id: str,
    query: str = "",
    *,
    kind: BoardSegmentKind | None = None,
    limit: int = 20,
) -> list[DocumentSegmentSearchResult]:
    return get_store().search_document_segments(query, owner_user_id=user_id, kind=kind, limit=limit)


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


def get_standalone_package(workspace: WorkspaceState) -> CoursePackage:
    if not workspace.packages:
        raise HTTPException(status_code=404, detail="No standalone course pool available")
    return workspace.packages[0]


def load_workspace_package() -> tuple[WorkspaceState, CoursePackage]:
    workspace = load_workspace()
    package = get_active_package(workspace)
    return workspace, package


def load_workspace_package_for_user(user_id: str) -> tuple[WorkspaceState, CoursePackage]:
    workspace = load_workspace_for_user(user_id)
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


def is_standalone_package(workspace: WorkspaceState, package: CoursePackage) -> bool:
    return bool(workspace.packages and workspace.packages[0].id == package.id)


def resources_visible_to_lesson(
    package: CoursePackage,
    *,
    lesson_id: str | None,
    isolate_lesson_resources: bool,
) -> list[ResourceLibraryItem]:
    if not isolate_lesson_resources:
        return package.resources
    if not lesson_id:
        return []
    return [resource for resource in package.resources if resource.scope_lesson_id == lesson_id]


def package_context_for_lesson(
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson_id: str | None,
) -> CoursePackage:
    resources = resources_visible_to_lesson(
        package,
        lesson_id=lesson_id,
        isolate_lesson_resources=is_standalone_package(workspace, package),
    )
    return package.model_copy(update={"resources": resources})


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


def package_view(
    package: CoursePackage,
    *,
    is_standalone: bool = False,
    resource_lesson_id: str | None = None,
    isolate_lesson_resources: bool = False,
) -> CoursePackageView:
    lessons_for_view = [
        lesson.model_copy(update={"learning_requirements": active_task_requirements(lesson)})
        for lesson in package.lessons
    ]
    visible_package = package.model_copy(
        update={
            "lessons": lessons_for_view,
            "resources": resources_visible_to_lesson(
                package,
                lesson_id=resource_lesson_id,
                isolate_lesson_resources=isolate_lesson_resources,
            )
        }
    )
    package_data = visible_package.model_dump(
        mode="json",
        exclude={"lessons": {"__all__": {"teaching_guide", "board_teaching_guide"}}},
    )
    package_data["is_standalone"] = is_standalone
    return CoursePackageView.model_validate(package_data)


def package_view_for_lesson(
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson_id: str | None,
) -> CoursePackageView:
    standalone = is_standalone_package(workspace, package)
    return package_view(
        package,
        is_standalone=standalone,
        resource_lesson_id=lesson_id,
        isolate_lesson_resources=standalone,
    )


def workspace_view(workspace: WorkspaceState) -> WorkspaceStateView:
    return WorkspaceStateView(
        packages=[
            package_view_for_lesson(workspace, package, package.active_lesson_id)
            for package in workspace.packages
        ],
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
