from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.models import EvidenceBundle, EvidenceConfirmationRequest, EvidenceConfirmationResult, SourceIngestionJob, SourceIngestionRecord, SourceStructureView, UserView
from app.routers.auth import current_user
from app.services import workspace_state
from app.services.learning_source_reference_service import LearningSourceReferenceError, apply_evidence_confirmation
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import SourceIngestionError, source_download_path, source_ingestion_service
from app.services.source_structure_indexer import source_structure_indexer
from app.services.source_structure_store import source_structure_store

router = APIRouter()


class SourceUpdateRequest(BaseModel):
    title: str


class SourceContentView(BaseModel):
    source: SourceIngestionRecord
    content: str


class SourceContentUpdateRequest(BaseModel):
    content: str


@router.get("/api/packages/{package_id}/sources", response_model=list[SourceIngestionRecord])
def list_package_sources(package_id: str, user: UserView = Depends(current_user)) -> list[SourceIngestionRecord]:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    return source_ingestion_service.list_sources(owner_user_id=user.id, package_id=package_id)


@router.post("/api/packages/{package_id}/sources", response_model=SourceIngestionRecord)
async def import_package_source(
    package_id: str,
    source_uri: str | None = Form(default=None),
    title: str = Form(default=""),
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    package = workspace_state.get_package(workspace, package_id)
    try:
        if file is not None:
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            return source_ingestion_service.add_file_source(
                owner_user_id=user.id,
                package=package,
                file_name=file.filename or "source",
                content=content,
                mime_type=file.content_type or "application/octet-stream",
                title=title,
            )
        if text and text.strip():
            return source_ingestion_service.add_text_source(
                owner_user_id=user.id,
                package=package,
                text=text,
                title=title,
            )
        if source_uri and source_uri.strip():
            return source_ingestion_service.add_url_source(
                owner_user_id=user.id,
                package=package,
                source_uri=source_uri,
                title=title,
            )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Provide a file, source_uri, or pasted text.")


@router.get("/api/packages/{package_id}/sources/jobs", response_model=list[SourceIngestionJob])
def list_package_source_jobs(
    package_id: str,
    user: UserView = Depends(current_user),
) -> list[SourceIngestionJob]:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    return source_ingestion_service.list_jobs(owner_user_id=user.id, package_id=package_id)


@router.patch("/api/packages/{package_id}/sources/{source_id}", response_model=SourceIngestionRecord)
def update_package_source(
    package_id: str,
    source_id: str,
    request: SourceUpdateRequest,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    updated = source_ingestion_service.rename_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
        title=request.title,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found or title is empty.")
    return updated


@router.post("/api/packages/{package_id}/sources/{source_id}/retry", response_model=SourceIngestionRecord)
def retry_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    try:
        updated = source_ingestion_service.retry_source(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
        )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return updated


@router.get("/api/packages/{package_id}/sources/{source_id}/content", response_model=SourceContentView)
def get_package_source_content(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceContentView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    result = source_ingestion_service.source_content(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    source, content = result
    return SourceContentView(source=source, content=content)


@router.put("/api/packages/{package_id}/sources/{source_id}/content", response_model=SourceContentView)
def update_package_source_content(
    package_id: str,
    source_id: str,
    request: SourceContentUpdateRequest,
    user: UserView = Depends(current_user),
) -> SourceContentView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    try:
        updated = source_ingestion_service.update_source_content(
            owner_user_id=user.id,
            package_id=package_id,
            source_id=source_id,
            content=request.content,
        )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    content_result = source_ingestion_service.source_content(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    return SourceContentView(source=updated, content=content_result[1] if content_result else "")


@router.get("/api/packages/{package_id}/sources/{source_id}/download")
def download_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> FileResponse:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    path = source_download_path(source)
    if path is None:
        raise HTTPException(status_code=404, detail="Source file is unavailable.")
    return FileResponse(
        Path(path),
        media_type=str(source.metadata.get("original_mime_type") or source.mime_type or "application/octet-stream"),
        filename=path.name.split("_", 1)[-1] if "_" in path.name else (source.file_name or path.name),
    )


@router.delete("/api/packages/{package_id}/sources/{source_id}", response_model=SourceIngestionRecord)
def delete_package_source(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceIngestionRecord:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    removed = source_ingestion_service.remove_source(
        owner_user_id=user.id,
        package_id=package_id,
        source_id=source_id,
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return removed


@router.get("/api/packages/{package_id}/sources/{source_id}/structure", response_model=SourceStructureView)
def get_package_source_structure(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceStructureView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return source_structure_store.get_structure_view(source=source)


@router.post("/api/packages/{package_id}/sources/{source_id}/structure/rebuild", response_model=SourceStructureView)
def rebuild_package_source_structure(
    package_id: str,
    source_id: str,
    user: UserView = Depends(current_user),
) -> SourceStructureView:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.get_package(workspace, package_id)
    source = source_evidence_store.get_source(owner_user_id=user.id, package_id=package_id, source_id=source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    source_structure_indexer.rebuild_structure(source)
    return source_structure_store.get_structure_view(source=source)


@router.post("/api/lessons/{lesson_id}/evidence/confirm", response_model=EvidenceConfirmationResult)
def confirm_lesson_evidence(
    lesson_id: str,
    request: EvidenceConfirmationRequest,
    user: UserView = Depends(current_user),
) -> EvidenceConfirmationResult:
    try:
        return apply_evidence_confirmation(
            owner_user_id=user.id,
            lesson_id=lesson_id,
            bundle_id=request.bundle_id,
            action=request.action,
        )
    except LearningSourceReferenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/lessons/{lesson_id}/evidence/pending", response_model=EvidenceBundle | None)
def get_pending_lesson_evidence(
    lesson_id: str,
    user: UserView = Depends(current_user),
) -> EvidenceBundle | None:
    workspace = workspace_state.load_workspace_for_user(user.id)
    workspace_state.find_lesson_package(workspace, lesson_id)
    history_state = workspace_state.load_learning_requirement_history_state_for_user(user.id, lesson_id)
    requirement_run_id = history_state.get("run_id") if history_state else None
    if not isinstance(requirement_run_id, str) or not requirement_run_id:
        return None
    bundle = source_evidence_store.latest_requirement_bundle(
        owner_user_id=user.id,
        lesson_id=lesson_id,
        requirement_run_id=requirement_run_id,
    )
    return bundle if bundle is not None and bundle.status == "candidate" else None
