from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from app.models import CoursePackageView, UserView
from app.routers.auth import current_user
from app.services import workspace_state as workspace_state_service
from app.services.resource_service import (
    add_uploaded_resource,
    delete_uploaded_resource_file,
    remove_resource_from_package,
)
from app.services.document_indexer import delete_resource_index, enqueue_resource_index
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

    resource = add_uploaded_resource(package, file, UPLOAD_DIR, scope_lesson_id=scope_lesson_id)
    save_workspace_for_user(user.id, workspace)
    enqueue_resource_index(workspace_state_service.get_store().path, resource.id)
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
    delete_resource_index(workspace_state_service.get_store().path, resource.id)
    delete_uploaded_resource_file(resource, UPLOAD_DIR)
    return package_view_for_lesson(workspace, package, response_lesson_id)


@router.get("/api/resources/{resource_id}/pages/{page_number}/preview")
def resource_page_preview(
    resource_id: str,
    page_number: int,
    user: UserView = Depends(current_user),
) -> Response:
    workspace = load_workspace_for_user(user.id)
    resource = next(
        (
            candidate
            for package in workspace.packages
            for candidate in package.resources
            if candidate.id == resource_id
        ),
        None,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Unknown resource {resource_id}")
    if resource.mime_type != "application/pdf" or not resource.source_path:
        raise HTTPException(status_code=404, detail="No page preview available for this resource")

    source_path = Path(resource.source_path)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Resource source file is missing")

    try:
        import fitz  # type: ignore[import-not-found]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="PDF preview renderer is not installed") from exc

    try:
        document = fitz.open(str(source_path))
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(status_code=404, detail="Page is out of range")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        data = pixmap.tobytes("png")
    finally:
        try:
            document.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    return Response(content=data, media_type="image/png")
