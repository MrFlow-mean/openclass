from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.models import (
    ChatResponse,
    CoursePackageView,
    CreateBranchRequest,
    DocumentAIEditRequest,
    DocumentSaveRequest,
    ManualCommitRequest,
    RestoreCommitRequest,
    SwitchBranchRequest,
)
from app.services.chat_service import document_ai_edit_request
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import create_branch, restore_commit, switch_branch
from app.services.rich_document import export_docx, import_docx
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import (
    EXPORT_DIR,
    UPLOAD_DIR,
    commit_document_snapshot,
    find_lesson_package,
    load_workspace,
    package_view_for_lesson,
    save_workspace,
)

router = APIRouter()


def _save_document_request(lesson_id: str, request: DocumentSaveRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/save",
        lesson=lesson,
        trace_prefix="document_save",
    ):
        lesson.board_document = request.document
        commit_metadata: dict[str, object] = {
            "kind": "manual_document_save",
            **request.metadata,
        }
        commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata=commit_metadata,
        )
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/manual-commit", response_model=CoursePackageView)
def manual_commit(lesson_id: str, request: ManualCommitRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/manual-commit",
        lesson=lesson,
        trace_prefix="manual_commit",
        commit_label=request.label,
    ):
        if request.document is not None:
            lesson.board_document = request.document
        commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata={"kind": "manual_document_edit"},
        )
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/document/save", response_model=CoursePackageView)
def save_document(lesson_id: str, request: DocumentSaveRequest) -> CoursePackageView:
    return _save_document_request(lesson_id, request)


@router.post("/api/lessons/{lesson_id}/document/save-beacon", response_model=CoursePackageView)
async def save_document_beacon(lesson_id: str, request: Request) -> CoursePackageView:
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
        save_request = DocumentSaveRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid document save payload") from exc
    return _save_document_request(lesson_id, save_request)


@router.post("/api/lessons/{lesson_id}/document/ai-edit", response_model=ChatResponse)
def ai_edit_document(lesson_id: str, request: DocumentAIEditRequest) -> ChatResponse:
    return document_ai_edit_request(
        lesson_id,
        request.instruction,
        request.selection_text,
        request.conversation,
    )


@router.post("/api/lessons/{lesson_id}/document/import-docx", response_model=CoursePackageView)
def import_document_docx(lesson_id: str, file: UploadFile = File(...)) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    safe_name = Path(file.filename or "document.docx").name
    destination = UPLOAD_DIR / f"{lesson_id}_{safe_name}"
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/import-docx",
        lesson=lesson,
        trace_prefix="import_docx",
        filename=safe_name,
    ):
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        lesson.board_document = import_docx(destination, title=lesson.board_document.title or lesson.title)
        commit_document_snapshot(
            lesson,
            label="Import DOCX",
            message=f"Imported {safe_name} into the rich document editor",
            metadata={"kind": "import_docx", "filename": safe_name},
        )
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.get("/api/lessons/{lesson_id}/document/export-docx")
def export_document_docx(lesson_id: str) -> FileResponse:
    workspace = load_workspace()
    _, lesson = find_lesson_package(workspace, lesson_id)
    target_path = EXPORT_DIR / f"{lesson.slug or lesson.id}.docx"
    export_docx(lesson.board_document, target_path)
    return FileResponse(
        target_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{lesson.slug or lesson.id}.docx",
    )


@router.post("/api/lessons/{lesson_id}/branches", response_model=CoursePackageView)
def create_lesson_branch(lesson_id: str, request: CreateBranchRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches",
        lesson=lesson,
        trace_prefix="create_branch",
        branch_name=request.name,
        from_commit_id=request.from_commit_id,
    ):
        create_branch(lesson, request.name, request.from_commit_id)
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/branches/checkout", response_model=CoursePackageView)
def checkout_lesson_branch(lesson_id: str, request: SwitchBranchRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches/checkout",
        lesson=lesson,
        trace_prefix="switch_branch",
        branch_name=request.name,
    ):
        switch_branch(lesson, request.name)
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/restore", response_model=CoursePackageView)
def restore_lesson_commit(lesson_id: str, request: RestoreCommitRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/restore",
        lesson=lesson,
        trace_prefix="restore_commit",
        commit_id=request.commit_id,
        restore_label=request.label,
    ):
        restore_commit(lesson, request.commit_id, request.label)
        refresh_lesson_runtime(lesson)
        save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/apply-proposal", response_model=CoursePackageView)
def apply_patch_proposal(lesson_id: str, proposal: ManualCommitRequest) -> CoursePackageView:
    workspace = load_workspace()
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/apply-proposal",
        lesson=lesson,
        trace_prefix="apply_proposal",
        proposal_label=proposal.label,
    ):
        if proposal.document is not None:
            lesson.board_document = proposal.document
            commit_document_snapshot(
                lesson,
                label=proposal.label,
                message=proposal.message,
                metadata={"kind": "apply_proposal"},
            )
            refresh_lesson_runtime(lesson)
            save_workspace(workspace)
    return package_view_for_lesson(workspace, package, lesson.id)
