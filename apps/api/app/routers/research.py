from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from app.models import UserView
from app.research_models import (
    ResearchArtifact,
    ResearchArtifactCreate,
    ResearchAskRequest,
    ResearchAskResponse,
    ResearchCapabilities,
    ResearchChatMessage,
    ResearchChatRequest,
    ResearchChatResponse,
    ResearchChatThread,
    ResearchChatThreadCreate,
    ResearchChatThreadUpdate,
    ResearchNote,
    ResearchNoteCreate,
    ResearchNoteUpdate,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchEpisodeProfile,
    ResearchEpisodeProfileCreate,
    ResearchEpisodeProfileUpdate,
    ResearchSpeakerProfile,
    ResearchSpeakerProfileCreate,
    ResearchSpeakerProfileUpdate,
    ResearchTransformation,
    ResearchTransformationCreate,
    ResearchTransformationRun,
    ResearchTransformationUpdate,
)
from app.routers.auth import current_user
from app.services import workspace_state
from app.services.research_store import research_store
from app.services.research_configuration_store import research_configuration_store
from app.services.research_artifact_jobs import research_artifact_job_runner
from app.services.research_workspace import ResearchWorkspaceError, research_workspace_service


@asynccontextmanager
async def _research_lifespan(_app):
    for artifact in research_store.list_pending_artifacts():
        research_artifact_job_runner.submit(
            artifact_id=artifact.id,
            process=lambda artifact=artifact: research_workspace_service.process_queued_artifact(
                owner_user_id=artifact.owner_user_id,
                package_id=artifact.package_id,
                artifact_id=artifact.id,
            ),
        )
    yield


router = APIRouter(lifespan=_research_lifespan)


def _package(package_id: str, user: UserView) -> None:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


def _schedule_artifact(
    background_tasks: BackgroundTasks,
    *,
    owner_user_id: str,
    package_id: str,
    artifact_id: str,
) -> None:
    research_artifact_job_runner.schedule(
        background_tasks,
        artifact_id=artifact_id,
        process=lambda: research_workspace_service.process_queued_artifact(
            owner_user_id=owner_user_id,
            package_id=package_id,
            artifact_id=artifact_id,
        ),
    )


def _resume_pending_artifacts(
    background_tasks: BackgroundTasks,
    *,
    owner_user_id: str,
    package_id: str,
) -> None:
    for artifact in research_store.list_artifacts(owner_user_id=owner_user_id, package_id=package_id):
        if artifact.status in {"queued", "generating"}:
            _schedule_artifact(
                background_tasks,
                owner_user_id=owner_user_id,
                package_id=package_id,
                artifact_id=artifact.id,
            )


@router.get("/api/packages/{package_id}/research/capabilities", response_model=ResearchCapabilities)
def get_research_capabilities(package_id: str, user: UserView = Depends(current_user)) -> ResearchCapabilities:
    _package(package_id, user)
    return research_workspace_service.capabilities()


@router.get("/api/packages/{package_id}/research/notes", response_model=list[ResearchNote])
def list_research_notes(package_id: str, user: UserView = Depends(current_user)) -> list[ResearchNote]:
    _package(package_id, user)
    return research_store.list_notes(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/research/notes", response_model=ResearchNote)
def create_research_note(
    package_id: str,
    request: ResearchNoteCreate,
    user: UserView = Depends(current_user),
) -> ResearchNote:
    _package(package_id, user)
    try:
        return research_workspace_service.create_note(owner_user_id=user.id, package_id=package_id, request=request)
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/packages/{package_id}/research/notes/{note_id}", response_model=ResearchNote)
def update_research_note(
    package_id: str,
    note_id: str,
    request: ResearchNoteUpdate,
    user: UserView = Depends(current_user),
) -> ResearchNote:
    _package(package_id, user)
    try:
        return research_workspace_service.update_note(
            owner_user_id=user.id,
            package_id=package_id,
            note_id=note_id,
            request=request,
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc


@router.delete("/api/packages/{package_id}/research/notes/{note_id}", response_model=ResearchNote)
def delete_research_note(package_id: str, note_id: str, user: UserView = Depends(current_user)) -> ResearchNote:
    _package(package_id, user)
    removed = research_store.delete_note(owner_user_id=user.id, package_id=package_id, note_id=note_id)
    if removed is None:
        raise _not_found("笔记不存在。")
    return removed


@router.post("/api/packages/{package_id}/research/search", response_model=ResearchSearchResponse)
def search_research_workspace(
    package_id: str,
    request: ResearchSearchRequest,
    user: UserView = Depends(current_user),
) -> ResearchSearchResponse:
    _package(package_id, user)
    try:
        return research_workspace_service.search(owner_user_id=user.id, package_id=package_id, request=request)
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/packages/{package_id}/research/ask", response_model=ResearchAskResponse)
def ask_research_workspace(
    package_id: str,
    request: ResearchAskRequest,
    user: UserView = Depends(current_user),
) -> ResearchAskResponse:
    _package(package_id, user)
    try:
        return research_workspace_service.ask(owner_user_id=user.id, package_id=package_id, request=request)
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/packages/{package_id}/research/threads", response_model=list[ResearchChatThread])
def list_research_threads(package_id: str, user: UserView = Depends(current_user)) -> list[ResearchChatThread]:
    _package(package_id, user)
    return research_store.list_threads(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/research/threads", response_model=ResearchChatThread)
def create_research_thread(
    package_id: str,
    request: ResearchChatThreadCreate,
    user: UserView = Depends(current_user),
) -> ResearchChatThread:
    _package(package_id, user)
    return research_workspace_service.create_thread(owner_user_id=user.id, package_id=package_id, request=request)


@router.patch("/api/packages/{package_id}/research/threads/{thread_id}", response_model=ResearchChatThread)
def update_research_thread(
    package_id: str,
    thread_id: str,
    request: ResearchChatThreadUpdate,
    user: UserView = Depends(current_user),
) -> ResearchChatThread:
    _package(package_id, user)
    try:
        return research_workspace_service.update_thread(
            owner_user_id=user.id,
            package_id=package_id,
            thread_id=thread_id,
            request=request,
        )
    except ResearchWorkspaceError as exc:
        raise _not_found(str(exc)) from exc


@router.delete("/api/packages/{package_id}/research/threads/{thread_id}", response_model=ResearchChatThread)
def delete_research_thread(
    package_id: str,
    thread_id: str,
    user: UserView = Depends(current_user),
) -> ResearchChatThread:
    _package(package_id, user)
    removed = research_store.delete_thread(owner_user_id=user.id, package_id=package_id, thread_id=thread_id)
    if removed is None:
        raise _not_found("资料对话不存在。")
    return removed


@router.get(
    "/api/packages/{package_id}/research/threads/{thread_id}/messages",
    response_model=list[ResearchChatMessage],
)
def list_research_messages(
    package_id: str,
    thread_id: str,
    user: UserView = Depends(current_user),
) -> list[ResearchChatMessage]:
    _package(package_id, user)
    thread = research_store.get_thread(owner_user_id=user.id, package_id=package_id, thread_id=thread_id)
    if thread is None:
        raise _not_found("资料对话不存在。")
    return research_store.list_messages(thread_id=thread.id)


@router.post(
    "/api/packages/{package_id}/research/threads/{thread_id}/messages",
    response_model=ResearchChatResponse,
)
def send_research_message(
    package_id: str,
    thread_id: str,
    request: ResearchChatRequest,
    user: UserView = Depends(current_user),
) -> ResearchChatResponse:
    _package(package_id, user)
    try:
        return research_workspace_service.chat(
            owner_user_id=user.id,
            package_id=package_id,
            thread_id=thread_id,
            request=request,
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 409, detail=str(exc)) from exc


@router.get("/api/packages/{package_id}/research/artifacts", response_model=list[ResearchArtifact])
def list_research_artifacts(
    package_id: str,
    background_tasks: BackgroundTasks,
    user: UserView = Depends(current_user),
) -> list[ResearchArtifact]:
    _package(package_id, user)
    artifacts = research_store.list_artifacts(owner_user_id=user.id, package_id=package_id)
    _resume_pending_artifacts(
        background_tasks,
        owner_user_id=user.id,
        package_id=package_id,
    )
    return artifacts


@router.post("/api/packages/{package_id}/research/artifacts", response_model=ResearchArtifact, status_code=202)
def generate_research_artifact(
    package_id: str,
    request: ResearchArtifactCreate,
    background_tasks: BackgroundTasks,
    user: UserView = Depends(current_user),
) -> ResearchArtifact:
    _package(package_id, user)
    try:
        artifact = research_workspace_service.queue_artifact(
            owner_user_id=user.id,
            package_id=package_id,
            request=request,
        )
        _schedule_artifact(
            background_tasks,
            owner_user_id=user.id,
            package_id=package_id,
            artifact_id=artifact.id,
        )
        return artifact
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/packages/{package_id}/research/artifacts/{artifact_id}", response_model=ResearchArtifact)
def get_research_artifact(
    package_id: str,
    artifact_id: str,
    background_tasks: BackgroundTasks,
    user: UserView = Depends(current_user),
) -> ResearchArtifact:
    _package(package_id, user)
    artifact = research_store.get_artifact(
        owner_user_id=user.id,
        package_id=package_id,
        artifact_id=artifact_id,
    )
    if artifact is None:
        raise _not_found("资料产物不存在。")
    if artifact.status in {"queued", "generating"}:
        _schedule_artifact(
            background_tasks,
            owner_user_id=user.id,
            package_id=package_id,
            artifact_id=artifact.id,
        )
    return artifact


@router.post(
    "/api/packages/{package_id}/research/artifacts/{artifact_id}/retry",
    response_model=ResearchArtifact,
    status_code=202,
)
def retry_research_artifact(
    package_id: str,
    artifact_id: str,
    background_tasks: BackgroundTasks,
    user: UserView = Depends(current_user),
) -> ResearchArtifact:
    _package(package_id, user)
    try:
        artifact = research_workspace_service.retry_artifact(
            owner_user_id=user.id,
            package_id=package_id,
            artifact_id=artifact_id,
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 409, detail=str(exc)) from exc
    _schedule_artifact(
        background_tasks,
        owner_user_id=user.id,
        package_id=package_id,
        artifact_id=artifact.id,
    )
    return artifact


@router.delete("/api/packages/{package_id}/research/artifacts/{artifact_id}", response_model=ResearchArtifact)
def delete_research_artifact(
    package_id: str,
    artifact_id: str,
    user: UserView = Depends(current_user),
) -> ResearchArtifact:
    _package(package_id, user)
    current = research_store.get_artifact(
        owner_user_id=user.id,
        package_id=package_id,
        artifact_id=artifact_id,
    )
    if current is not None and current.status in {"queued", "generating"}:
        raise HTTPException(status_code=409, detail="正在生成的资料产物不能删除。")
    removed = research_store.delete_artifact(owner_user_id=user.id, package_id=package_id, artifact_id=artifact_id)
    if removed is None:
        raise _not_found("资料产物不存在。")
    return removed


@router.get("/api/packages/{package_id}/research/artifacts/{artifact_id}/audio")
def get_research_artifact_audio(
    package_id: str,
    artifact_id: str,
    user: UserView = Depends(current_user),
) -> FileResponse:
    _package(package_id, user)
    raw_path = research_store.get_artifact_audio_path(
        owner_user_id=user.id,
        package_id=package_id,
        artifact_id=artifact_id,
    )
    if not raw_path:
        raise _not_found("资料产物没有可用音频。")
    path = Path(raw_path).expanduser().resolve()
    allowed_root = (workspace_state.EXPORT_DIR / "research-audio").resolve()
    if not path.is_file() or allowed_root not in path.parents:
        raise _not_found("资料产物音频不可用。")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{artifact_id}.mp3")


@router.get("/api/packages/{package_id}/research/transformations", response_model=list[ResearchTransformation])
def list_research_transformations(
    package_id: str,
    user: UserView = Depends(current_user),
) -> list[ResearchTransformation]:
    _package(package_id, user)
    return research_configuration_store.list_transformations(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/research/transformations", response_model=ResearchTransformation)
def create_research_transformation(
    package_id: str,
    request: ResearchTransformationCreate,
    user: UserView = Depends(current_user),
) -> ResearchTransformation:
    _package(package_id, user)
    try:
        return research_workspace_service.create_transformation(
            owner_user_id=user.id, package_id=package_id, request=request
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/packages/{package_id}/research/transformations/{transformation_id}",
    response_model=ResearchTransformation,
)
def update_research_transformation(
    package_id: str,
    transformation_id: str,
    request: ResearchTransformationUpdate,
    user: UserView = Depends(current_user),
) -> ResearchTransformation:
    _package(package_id, user)
    try:
        return research_workspace_service.update_transformation(
            owner_user_id=user.id,
            package_id=package_id,
            transformation_id=transformation_id,
            request=request,
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc


@router.delete(
    "/api/packages/{package_id}/research/transformations/{transformation_id}",
    response_model=ResearchTransformation,
)
def delete_research_transformation(
    package_id: str,
    transformation_id: str,
    user: UserView = Depends(current_user),
) -> ResearchTransformation:
    _package(package_id, user)
    removed = research_configuration_store.delete_transformation(
        owner_user_id=user.id, package_id=package_id, item_id=transformation_id
    )
    if removed is None:
        raise _not_found("转换不存在。")
    return removed


@router.post(
    "/api/packages/{package_id}/research/transformations/{transformation_id}/run",
    response_model=ResearchArtifact,
    status_code=202,
)
def run_research_transformation(
    package_id: str,
    transformation_id: str,
    request: ResearchTransformationRun,
    background_tasks: BackgroundTasks,
    user: UserView = Depends(current_user),
) -> ResearchArtifact:
    _package(package_id, user)
    try:
        artifact = research_workspace_service.queue_transformation_artifact(
            owner_user_id=user.id,
            package_id=package_id,
            transformation_id=transformation_id,
            request=request,
        )
        _schedule_artifact(
            background_tasks,
            owner_user_id=user.id,
            package_id=package_id,
            artifact_id=artifact.id,
        )
        return artifact
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 409, detail=str(exc)) from exc


@router.get("/api/packages/{package_id}/research/speaker-profiles", response_model=list[ResearchSpeakerProfile])
def list_research_speaker_profiles(
    package_id: str,
    user: UserView = Depends(current_user),
) -> list[ResearchSpeakerProfile]:
    _package(package_id, user)
    return research_configuration_store.list_speaker_profiles(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/research/speaker-profiles", response_model=ResearchSpeakerProfile)
def create_research_speaker_profile(
    package_id: str,
    request: ResearchSpeakerProfileCreate,
    user: UserView = Depends(current_user),
) -> ResearchSpeakerProfile:
    _package(package_id, user)
    try:
        return research_workspace_service.create_speaker_profile(
            owner_user_id=user.id, package_id=package_id, request=request
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/packages/{package_id}/research/speaker-profiles/{profile_id}",
    response_model=ResearchSpeakerProfile,
)
def update_research_speaker_profile(
    package_id: str,
    profile_id: str,
    request: ResearchSpeakerProfileUpdate,
    user: UserView = Depends(current_user),
) -> ResearchSpeakerProfile:
    _package(package_id, user)
    try:
        return research_workspace_service.update_speaker_profile(
            owner_user_id=user.id, package_id=package_id, profile_id=profile_id, request=request
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc


@router.delete(
    "/api/packages/{package_id}/research/speaker-profiles/{profile_id}",
    response_model=ResearchSpeakerProfile,
)
def delete_research_speaker_profile(
    package_id: str,
    profile_id: str,
    user: UserView = Depends(current_user),
) -> ResearchSpeakerProfile:
    _package(package_id, user)
    removed = research_configuration_store.delete_speaker_profile(
        owner_user_id=user.id, package_id=package_id, item_id=profile_id
    )
    if removed is None:
        raise _not_found("说话者配置不存在。")
    return removed


@router.get("/api/packages/{package_id}/research/episode-profiles", response_model=list[ResearchEpisodeProfile])
def list_research_episode_profiles(
    package_id: str,
    user: UserView = Depends(current_user),
) -> list[ResearchEpisodeProfile]:
    _package(package_id, user)
    return research_configuration_store.list_episode_profiles(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/research/episode-profiles", response_model=ResearchEpisodeProfile)
def create_research_episode_profile(
    package_id: str,
    request: ResearchEpisodeProfileCreate,
    user: UserView = Depends(current_user),
) -> ResearchEpisodeProfile:
    _package(package_id, user)
    try:
        return research_workspace_service.create_episode_profile(
            owner_user_id=user.id, package_id=package_id, request=request
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch(
    "/api/packages/{package_id}/research/episode-profiles/{profile_id}",
    response_model=ResearchEpisodeProfile,
)
def update_research_episode_profile(
    package_id: str,
    profile_id: str,
    request: ResearchEpisodeProfileUpdate,
    user: UserView = Depends(current_user),
) -> ResearchEpisodeProfile:
    _package(package_id, user)
    try:
        return research_workspace_service.update_episode_profile(
            owner_user_id=user.id, package_id=package_id, profile_id=profile_id, request=request
        )
    except ResearchWorkspaceError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 400, detail=str(exc)) from exc


@router.delete(
    "/api/packages/{package_id}/research/episode-profiles/{profile_id}",
    response_model=ResearchEpisodeProfile,
)
def delete_research_episode_profile(
    package_id: str,
    profile_id: str,
    user: UserView = Depends(current_user),
) -> ResearchEpisodeProfile:
    _package(package_id, user)
    removed = research_configuration_store.delete_episode_profile(
        owner_user_id=user.id, package_id=package_id, item_id=profile_id
    )
    if removed is None:
        raise _not_found("节目配置不存在。")
    return removed
