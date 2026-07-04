from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    ResourceAIIndexStatus,
    ResourceAIQueryRequest,
    ResourceAIQueryResponse,
    UserView,
)
from app.routers.auth import current_user
from app.services.resource_ai import build_resource_ai_index_status, query_resource_ai
from app.services.workspace_state import (
    find_lesson_package,
    load_workspace_for_user,
    package_context_for_lesson,
)


router = APIRouter()


@router.get("/api/lessons/{lesson_id}/resources/index", response_model=list[ResourceAIIndexStatus])
def get_lesson_resource_index(
    lesson_id: str,
    user: UserView = Depends(current_user),
) -> list[ResourceAIIndexStatus]:
    workspace = load_workspace_for_user(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    visible_package = package_context_for_lesson(workspace, package, lesson.id)
    return build_resource_ai_index_status(visible_package.resources)


@router.post("/api/lessons/{lesson_id}/resources/query", response_model=ResourceAIQueryResponse)
def query_lesson_resources(
    lesson_id: str,
    request: ResourceAIQueryRequest,
    user: UserView = Depends(current_user),
) -> ResourceAIQueryResponse:
    workspace = load_workspace_for_user(user.id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    visible_package = package_context_for_lesson(workspace, package, lesson.id)
    if request.resource_id and all(resource.id != request.resource_id for resource in visible_package.resources):
        raise HTTPException(status_code=404, detail=f"Unknown visible resource {request.resource_id}")
    try:
        return query_resource_ai(visible_package.resources, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
