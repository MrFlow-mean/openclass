from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models import (
    AddMaintainerRequest,
    CourseContributionView,
    ForkCourseResponse,
    OpenCourseDetail,
    OpenCourseListResponse,
    PublishPackageRequest,
    ReviewContributionRequest,
    SubmitContributionRequest,
    UserView,
)
from app.routers.auth import current_user, optional_current_user
from app.services.collaboration import CourseCollaborationService
from app.services.workspace_state import DATABASE_PATH, UPLOAD_DIR

router = APIRouter(prefix="/api")
collaboration_service = CourseCollaborationService(DATABASE_PATH, UPLOAD_DIR)


@router.post("/packages/{package_id}/publish", response_model=OpenCourseDetail)
def publish_package(
    package_id: str,
    request: PublishPackageRequest,
    user: UserView = Depends(current_user),
) -> OpenCourseDetail:
    return collaboration_service.publish_package(user, package_id, summary=request.summary)


@router.get("/open-courses", response_model=OpenCourseListResponse)
def list_open_courses() -> OpenCourseListResponse:
    return collaboration_service.list_open_courses()


@router.get("/open-courses/{publication_id}", response_model=OpenCourseDetail)
def get_open_course(
    publication_id: str,
    viewer: UserView | None = Depends(optional_current_user),
) -> OpenCourseDetail:
    return collaboration_service.get_open_course(publication_id, viewer=viewer)


@router.post("/open-courses/{publication_id}/fork", response_model=ForkCourseResponse)
def fork_open_course(
    publication_id: str,
    user: UserView = Depends(current_user),
) -> ForkCourseResponse:
    fork, course_package = collaboration_service.fork_open_course(user, publication_id)
    return ForkCourseResponse(fork=fork, course_package=course_package)


@router.post("/forks/{fork_id}/contributions", response_model=CourseContributionView)
def submit_contribution(
    fork_id: str,
    request: SubmitContributionRequest,
    user: UserView = Depends(current_user),
) -> CourseContributionView:
    return collaboration_service.submit_contribution(
        user,
        fork_id,
        title=request.title,
        description=request.description,
    )


@router.get("/contributions/{contribution_id}", response_model=CourseContributionView)
def get_contribution(
    contribution_id: str,
    viewer: UserView | None = Depends(optional_current_user),
) -> CourseContributionView:
    return collaboration_service.get_contribution(contribution_id, viewer=viewer)


@router.post("/contributions/{contribution_id}/review", response_model=CourseContributionView)
def review_contribution(
    contribution_id: str,
    request: ReviewContributionRequest,
    user: UserView = Depends(current_user),
) -> CourseContributionView:
    return collaboration_service.review_contribution(
        user,
        contribution_id,
        action=request.action,
        message=request.message,
    )


@router.post("/open-courses/{publication_id}/maintainers", response_model=OpenCourseDetail)
def add_maintainer(
    publication_id: str,
    request: AddMaintainerRequest,
    user: UserView = Depends(current_user),
) -> OpenCourseDetail:
    return collaboration_service.add_maintainer(user, publication_id, request.email)


@router.delete("/open-courses/{publication_id}/maintainers/{maintainer_user_id}", response_model=OpenCourseDetail)
def remove_maintainer(
    publication_id: str,
    maintainer_user_id: str,
    user: UserView = Depends(current_user),
) -> OpenCourseDetail:
    return collaboration_service.remove_maintainer(user, publication_id, maintainer_user_id)
