from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models import CoursePackageView, UserView
from app.routers.auth import current_user
from app.services.resource_service import (
    add_uploaded_resource,
    delete_uploaded_resource_file,
    remove_resource_from_package,
)
from app.services.workspace_state import (
    UPLOAD_DIR,
    find_lesson_package,
    get_active_package,
    get_lesson,
    is_standalone_package,
    load_workspace_for_user,
    package_view_for_lesson,
    save_workspace_for_user,
)

router = APIRouter()


def _load_resource_target(user_id: str, lesson_id: str | None):
    workspace = load_workspace_for_user(user_id)
    if lesson_id:
        package, lesson = find_lesson_package(workspace, lesson_id)
        package.active_lesson_id = lesson.id
        workspace.active_package_id = package.id
        return workspace, package, lesson.id
    package = get_active_package(workspace)
    return workspace, package, package.active_lesson_id


@router.post("/api/resources/upload", response_model=CoursePackageView)
def upload_resource(
    file: UploadFile = File(...),
    lesson_id: str | None = Form(None),
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, package, response_lesson_id = _load_resource_target(user.id, lesson_id)
    scope_lesson_id = None
    if is_standalone_package(workspace, package):
        if not response_lesson_id:
            raise HTTPException(status_code=400, detail="Lesson id is required for standalone course resources")
        get_lesson(package, response_lesson_id)
        scope_lesson_id = response_lesson_id

    add_uploaded_resource(package, file, UPLOAD_DIR, scope_lesson_id=scope_lesson_id)
    save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, response_lesson_id)


@router.post("/api/resources/{resource_id}/delete", response_model=CoursePackageView)
def delete_resource(
    resource_id: str,
    lesson_id: str | None = None,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace, package, response_lesson_id = _load_resource_target(user.id, lesson_id)
    resource = next((candidate for candidate in package.resources if candidate.id == resource_id), None)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Unknown resource {resource_id}")
    if is_standalone_package(workspace, package) and resource.scope_lesson_id != response_lesson_id:
        raise HTTPException(status_code=404, detail=f"Unknown resource {resource_id}")
    resource = remove_resource_from_package(package, resource_id)
    save_workspace_for_user(user.id, workspace)
    delete_uploaded_resource_file(resource, UPLOAD_DIR)
    return package_view_for_lesson(workspace, package, response_lesson_id)
