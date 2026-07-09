from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models import EvidenceBundle, EvidenceConfirmationRequest, SourceIngestionRecord, UserView
from app.routers.auth import current_user
from app.services import workspace_state
from app.services.source_evidence_store import source_evidence_store
from app.services.source_ingestion_service import SourceIngestionError, source_ingestion_service

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


@router.post("/api/lessons/{lesson_id}/evidence/confirm", response_model=EvidenceBundle)
def confirm_lesson_evidence(
    lesson_id: str,
    request: EvidenceConfirmationRequest,
    user: UserView = Depends(current_user),
) -> EvidenceBundle:
    workspace = workspace_state.load_workspace_for_user(user.id)
    package, _lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    bundle = source_evidence_store.get_bundle(owner_user_id=user.id, bundle_id=request.bundle_id)
    if bundle is None or bundle.lesson_id != lesson_id or bundle.package_id != package.id:
        raise HTTPException(status_code=404, detail="Evidence bundle not found.")
    if request.action == "skip":
        archived = source_evidence_store.archive_bundle(owner_user_id=user.id, bundle_id=request.bundle_id)
        if archived is None:
            raise HTTPException(status_code=404, detail="Evidence bundle not found.")
        return archived
    confirmed = source_evidence_store.confirm_bundle(owner_user_id=user.id, bundle_id=request.bundle_id)
    if confirmed is None:
        raise HTTPException(status_code=404, detail="Evidence bundle not found.")
    return confirmed
