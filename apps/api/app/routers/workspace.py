from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.models import (
    CourseGraphEdge,
    CoursePackage,
    CoursePackageView,
    CreatePackageRequest,
    GenerateLessonRequest,
    MoveLessonRequest,
    ReorderTabsRequest,
    ResourceCopyrightAppeal,
    ResourceCopyrightAppealCreateRequest,
    ResourceCopyrightAppealResolveRequest,
    ResourceCopyrightAppealView,
    ResourceLibraryItem,
    UpdatePackageRequest,
    UserView,
    WorkspaceStateView,
)
from app.routers.auth import current_admin, current_user
from app.services.ai_logging import ai_usage_logger
from app.services.course_runtime import build_lesson_for_topic
from app.services.history import current_head_commit
from app.services.lesson_factory import create_empty_lesson
from app.services.resource_copyright_audit import audit_resource_public_distribution
from app.services.resource_library import build_resource_item
from app.services.route_context import bind_ai_request_context
from app.services.resource_service import delete_uploaded_resource_file
from app.services.workspace_state import (
    UPLOAD_DIR,
    find_lesson_package,
    get_course_store,
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


def _safe_upload_name(filename: str | None) -> str:
    name = Path(filename or "").name.strip()
    return name or "resource"


def _find_resource_for_user(user_id: str, resource_id: str) -> ResourceLibraryItem:
    workspace = load_workspace_for_user(user_id)
    for package in workspace.packages:
        for resource in package.resources:
            if resource.id == resource_id:
                return resource
    raise HTTPException(status_code=404, detail="没有找到这份资料")


@router.get("/api/workspace", response_model=WorkspaceStateView)
def get_workspace(user: UserView = Depends(current_user)) -> WorkspaceStateView:
    # 打开前端工作台时先读取当前用户的所有课程包和活动课程。
    return workspace_view(load_workspace_for_user(user.id))


@router.post("/api/packages", response_model=WorkspaceStateView)
def create_package(request: CreatePackageRequest, user: UserView = Depends(current_user)) -> WorkspaceStateView:
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Package title is required")

    # 课程包是最外层学习空间，里面可以放多个 lesson、资料和课程图谱关系。
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


@router.post("/api/resources/upload", response_model=CoursePackageView)
async def upload_resource(file: UploadFile = File(...), user: UserView = Depends(current_user)) -> CoursePackageView:
    # 资料上传只负责保存原文件并建立通用资料索引；具体引用和生成仍由 ResourceResolver / BoardEditor 决定。
    original_name = _safe_upload_name(file.filename)
    suffix = Path(original_name).suffix
    stored_path = UPLOAD_DIR / f"{uuid4().hex}{suffix}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="上传文件不能为空")
        stored_path.write_bytes(content)
        resource = build_resource_item(stored_path, original_name)
        resource.copyright_audit = audit_resource_public_distribution(resource)
    except HTTPException:
        stored_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="资料解析失败，请换一个文件再试") from exc
    finally:
        await file.close()

    workspace, package = load_workspace_package_for_user(user.id)
    if is_standalone_package(workspace, package):
        resource.scope_lesson_id = package.active_lesson_id
    package.resources.append(resource)
    normalize_package_state(package)
    save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, package.active_lesson_id)


@router.post("/api/resources/{resource_id}/copyright-appeals", response_model=ResourceCopyrightAppeal)
def create_resource_copyright_appeal(
    resource_id: str,
    request: ResourceCopyrightAppealCreateRequest,
    user: UserView = Depends(current_user),
) -> ResourceCopyrightAppeal:
    resource = _find_resource_for_user(user.id, resource_id)
    return get_course_store().create_resource_copyright_appeal(
        owner_user_id=user.id,
        resource=resource,
        message=request.message,
        evidence_text=request.evidence_text,
        evidence_urls=request.evidence_urls,
    )


@router.get("/api/resources/{resource_id}/copyright-appeals", response_model=list[ResourceCopyrightAppeal])
def list_resource_copyright_appeals(
    resource_id: str,
    user: UserView = Depends(current_user),
) -> list[ResourceCopyrightAppeal]:
    _find_resource_for_user(user.id, resource_id)
    return get_course_store().list_resource_copyright_appeals(
        owner_user_id=user.id,
        resource_id=resource_id,
    )


@router.get("/api/admin/copyright-appeals", response_model=list[ResourceCopyrightAppealView])
def list_admin_resource_copyright_appeals(
    _: UserView = Depends(current_admin),
) -> list[ResourceCopyrightAppealView]:
    return get_course_store().list_admin_resource_copyright_appeals(status="open")


@router.post("/api/admin/copyright-appeals/{appeal_id}/resolve", response_model=ResourceCopyrightAppealView)
def resolve_admin_resource_copyright_appeal(
    appeal_id: str,
    request: ResourceCopyrightAppealResolveRequest,
    user: UserView = Depends(current_admin),
) -> ResourceCopyrightAppealView:
    try:
        return get_course_store().resolve_resource_copyright_appeal(
            appeal_id=appeal_id,
            reviewer_user_id=user.id,
            decision=request.decision,
            resolution_reason=request.resolution_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/lessons/generate", response_model=CoursePackageView)
def generate_lesson(
    request: GenerateLessonRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    # 创建 lesson 时只负责把一节课放进课程包；真正的 AI 教学回合发生在 chat 接口。
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
        # start_blank=true 会创建空白板书，后续由聊天链路收集需求并生成第一版板书。
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
    # 前端打开 lesson tab 时，后端只更新活动 lesson 和 tab 顺序，不触发 AI。
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
