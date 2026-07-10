from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models import EvidenceConfirmationRequest, EvidenceConfirmationResult, SourceIngestionRecord, SourceStructureView, UserView
from app.routers.auth import current_user
from app.services import workspace_state
from app.services.learning_source_reference_service import LearningSourceReferenceError, apply_evidence_confirmation
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import SourceIngestionError, source_ingestion_service
from app.services.source_structure_indexer import source_structure_indexer
from app.services.source_structure_store import source_structure_store

router = APIRouter()


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
        if source_uri and source_uri.strip():
            return source_ingestion_service.add_url_source(
                owner_user_id=user.id,
                package=package,
                source_uri=source_uri,
                title=title,
            )
    except SourceIngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Provide either a file or source_uri.")


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
