from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    CourseGraphEdge,
    CoursePackage,
    CoursePackageView,
    CreatePackageRequest,
    GenerateLessonRequest,
    MoveLessonRequest,
    ReorderTabsRequest,
    UpdatePackageRequest,
    UserView,
    WorkspaceStateView,
)
from app.routers.auth import current_user
from app.services.ai_logging import ai_usage_logger
from app.services.course_runtime import build_lesson_for_topic
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.route_context import bind_ai_request_context
from app.services.resource_service import delete_uploaded_resource_file
from app.services.workspace_state import (
    UPLOAD_DIR,
    find_lesson_package,
    get_package,
    get_standalone_package,
    is_standalone_package,
    load_workspace_for_user,
    load_workspace_package_for_user,
    normalize_package_state,
    package_view_for_lesson,
    save_workspace_for_user,
    workspace_view,
)

router = APIRouter()


@router.get("/api/workspace", response_model=WorkspaceStateView)
def get_workspace(user: UserView = Depends(current_user)) -> WorkspaceStateView:
    return workspace_view(load_workspace_for_user(user.id))


@router.post("/api/packages", response_model=WorkspaceStateView)
def create_package(request: CreatePackageRequest, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Package title is required")

    workspace = load_workspace_for_user(user.id)
    package = CoursePackage(
        title=title,
        summary=request.summary.strip(),
        lessons=[],
    )
    workspace.packages.append(package)
    workspace.active_package_id = package.id
    save_workspace_for_user(user.id, workspace)
    return workspace_view(workspace)


@router.post("/api/packages/{package_id}/open", response_model=WorkspaceStateView)
def open_package(package_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace = load_workspace_for_user(user.id)
    get_package(workspace, package_id)
    workspace.active_package_id = package_id
    save_workspace_for_user(user.id, workspace)
    return workspace_view(workspace)


@router.post("/api/packages/{package_id}", response_model=WorkspaceStateView)
def update_package(
    package_id: str,
    request: UpdatePackageRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace = load_workspace_for_user(user.id)
    package = get_package(workspace, package_id)

    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Package title is required")
        package.title = title

    if request.summary is not None:
        package.summary = request.summary.strip()

    save_workspace_for_user(user.id, workspace)
    return workspace_view(workspace)


@router.post("/api/packages/{package_id}/delete", response_model=WorkspaceStateView)
def delete_package(package_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace = load_workspace_for_user(user.id)
    get_package(workspace, package_id)

    if len(workspace.packages) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the only course package")

    workspace.packages = [package for package in workspace.packages if package.id != package_id]
    if workspace.active_package_id == package_id:
        workspace.active_package_id = workspace.packages[0].id if workspace.packages else None

    save_workspace_for_user(user.id, workspace)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/move", response_model=WorkspaceStateView)
def move_lesson(
    lesson_id: str,
    request: MoveLessonRequest,
    user: UserView = Depends(current_user),
) -> WorkspaceStateView:
    workspace = load_workspace_for_user(user.id)
    source_package, lesson = find_lesson_package(workspace, lesson_id)
    target_package = get_package(workspace, request.target_package_id)

    if source_package.id == target_package.id:
        raise HTTPException(status_code=400, detail="Lesson is already in the selected package")

    source_package.lessons = [current for current in source_package.lessons if current.id != lesson_id]
    source_package.open_lesson_ids = [current for current in source_package.open_lesson_ids if current != lesson_id]
    source_package.workspace_tab_order = [current for current in source_package.workspace_tab_order if current != lesson_id]
    if source_package.active_lesson_id == lesson_id:
        source_package.active_lesson_id = None

    moving_resources = []
    if is_standalone_package(workspace, source_package):
        moving_resources = [
            resource for resource in source_package.resources if resource.scope_lesson_id == lesson_id
        ]
        source_package.resources = [
            resource for resource in source_package.resources if resource.scope_lesson_id != lesson_id
        ]

    target_package.lessons.append(lesson)
    if moving_resources and not is_standalone_package(workspace, target_package):
        for resource in moving_resources:
            resource.scope_lesson_id = None
        target_package.resources.extend(moving_resources)
    if lesson.id not in target_package.open_lesson_ids:
        target_package.open_lesson_ids.append(lesson.id)
    if lesson.id not in target_package.workspace_tab_order:
        target_package.workspace_tab_order.append(lesson.id)
    if target_package.active_lesson_id is None:
        target_package.active_lesson_id = lesson.id

    normalize_package_state(source_package)
    normalize_package_state(target_package)
    save_workspace_for_user(user.id, workspace)
    return workspace_view(workspace)


@router.post("/api/lessons/{lesson_id}/delete", response_model=WorkspaceStateView)
def delete_lesson(lesson_id: str, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    workspace = load_workspace_for_user(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)
    removed_resources = []
    if is_standalone_package(workspace, package):
        removed_resources = [
            resource for resource in package.resources if resource.scope_lesson_id == lesson_id
        ]
        package.resources = [
            resource for resource in package.resources if resource.scope_lesson_id != lesson_id
        ]

    package.lessons = [current for current in package.lessons if current.id != lesson_id]
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = None

    normalize_package_state(package)
    save_workspace_for_user(user.id, workspace)
    for resource in removed_resources:
        delete_uploaded_resource_file(resource, UPLOAD_DIR)
    return workspace_view(workspace)


@router.get("/api/course-package", response_model=CoursePackageView)
def get_course_package(user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace, package = load_workspace_package_for_user(user.id)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/lessons/generate", response_model=CoursePackageView)
def generate_lesson(
    request: GenerateLessonRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
    source_package = None
    if request.branch_from_lesson_id:
        source_package, _ = find_lesson_package(workspace, request.branch_from_lesson_id)
    if request.target_package_id:
        package = get_package(workspace, request.target_package_id)
    elif source_package is not None:
        package = source_package
    else:
        package = get_standalone_package(workspace)
    if source_package is not None and source_package.id != package.id:
        raise HTTPException(status_code=400, detail="Branch source lesson must be in the target package")

    with bind_ai_request_context(
        "/api/lessons/generate",
        trace_prefix="generate_lesson",
        generation_topic=request.topic,
        branch_from_lesson_id=request.branch_from_lesson_id,
        target_package_id=package.id,
        start_blank=request.start_blank,
    ):
        if not request.start_blank:
            ai_usage_logger.log_event(
                "lesson_generation_request",
                topic=request.topic,
                branch_from_lesson_id=request.branch_from_lesson_id,
            )
        lesson = (
            create_empty_lesson(request.topic)
            if request.start_blank
            else build_lesson_for_topic(request.topic)
        )
        package.lessons.append(lesson)
        package.open_lesson_ids.append(lesson.id)
        package.workspace_tab_order.append(lesson.id)
        package.active_lesson_id = lesson.id
        workspace.active_package_id = package.id
        if request.branch_from_lesson_id:
            package.course_graph.append(
                CourseGraphEdge(
                    source_lesson_id=request.branch_from_lesson_id,
                    target_lesson_id=lesson.id,
                    relationship="deep_dive",
                )
            )
        save_workspace_for_user(user.id, workspace)
        if not request.start_blank:
            ai_usage_logger.log_event(
                "lesson_generation_response",
                lesson_id=lesson.id,
                lesson_title=lesson.title,
                summary=lesson.summary,
                tags=lesson.tags,
            )
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/workspace/reorder", response_model=CoursePackageView)
def reorder_workspace_tabs(
    request: ReorderTabsRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, package = load_workspace_package_for_user(user.id)
    package.workspace_tab_order = request.ordered_lesson_ids
    package.open_lesson_ids = request.ordered_lesson_ids
    package.active_lesson_id = request.active_lesson_id or (
        request.ordered_lesson_ids[0] if request.ordered_lesson_ids else None
    )
    save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/lessons/{lesson_id}/open", response_model=CoursePackageView)
def open_lesson_tab(lesson_id: str, user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)
    if lesson_id not in package.open_lesson_ids:
        package.open_lesson_ids.append(lesson_id)
    if lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(lesson_id)
    package.active_lesson_id = lesson_id
    workspace.active_package_id = package.id
    save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson_id)


@router.post("/api/lessons/{lesson_id}/close", response_model=CoursePackageView)
def close_lesson_tab(lesson_id: str, user: UserView = Depends(current_user)) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
    package, _ = find_lesson_package(workspace, lesson_id)
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = package.workspace_tab_order[0] if package.workspace_tab_order else None
    save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.get("/api/lessons/{lesson_id}/head")
def get_lesson_head(lesson_id: str, user: UserView = Depends(current_user)) -> dict[str, str]:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    head = current_head_commit(lesson)
    return {"lesson_id": lesson_id, "head_commit_id": head.id}
