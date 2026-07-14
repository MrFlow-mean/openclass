from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.models import (
    BoardDocument,
    BoardSegmentKind,
    ChatResponse,
    CoursePackageView,
    CreateBranchRequest,
    DocumentAIEditRequest,
    DocumentSaveRequest,
    DocumentSegmentSearchResponse,
    ManualCommitRequest,
    RestoreCommitRequest,
    SwitchBranchRequest,
    UserView,
)
from app.routers.auth import current_user
from app.services.chat_service import document_ai_edit_request
from app.services.board_asset_store import get_board_asset_store
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import create_branch, current_head_commit, restore_commit, switch_branch
from app.services.html_document_export import export_html
from app.services.rich_document import (
    document_changed,
    export_docx,
    import_docx,
    is_document_empty,
    rich_structure_counts,
    rich_structure_score,
    would_flatten_rich_document,
)
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import (
    EXPORT_DIR,
    UPLOAD_DIR,
    commit_document_snapshot,
    find_lesson_package,
    load_workspace_for_user,
    package_view_for_lesson,
    save_workspace_for_user,
    search_document_segments_for_user,
)

router = APIRouter()

_DOCX_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_EXPORT_SCOPE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _temporary_export_target(
    *,
    owner_user_id: str,
    lesson_id: str,
    suffix: str,
) -> tuple[Path, Path]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    owner_scope = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:12]
    lesson_scope = _EXPORT_SCOPE_TOKEN_RE.sub("-", lesson_id).strip("-")[:48] or "lesson"
    export_directory = Path(
        tempfile.mkdtemp(
            prefix=f"openclass-export-{owner_scope}-{lesson_scope}-",
            dir=EXPORT_DIR,
        )
    )
    return export_directory, export_directory / f"document{suffix}"


def _remove_export_directory(export_directory: Path) -> None:
    shutil.rmtree(export_directory, ignore_errors=True)


def _export_document_response(
    *,
    document: BoardDocument,
    owner_user_id: str,
    lesson_id: str,
    download_stem: str,
    suffix: str,
    media_type: str,
    exporter: Callable[..., Path],
) -> FileResponse:
    export_directory, target_path = _temporary_export_target(
        owner_user_id=owner_user_id,
        lesson_id=lesson_id,
        suffix=suffix,
    )
    try:
        exporter(document, target_path, asset_resolver=_board_asset_resolver(owner_user_id))
        return FileResponse(
            target_path,
            media_type=media_type,
            filename=f"{download_stem}{suffix}",
            headers=_DOCX_NO_STORE_HEADERS,
            background=BackgroundTask(_remove_export_directory, export_directory),
        )
    except Exception:
        _remove_export_directory(export_directory)
        raise


@router.get("/api/board-assets/{asset_id}/content")
def get_board_asset_content(
    asset_id: str,
    user: UserView = Depends(current_user),
) -> Response:
    store = get_board_asset_store()
    resolved = store.read_bytes(asset_id, user.id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Board asset content is unavailable.")
    asset, content = resolved
    return Response(
        content=content,
        media_type=asset.mime_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(asset.file_name)}",
            "ETag": f'"{asset.content_hash}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/documents/search", response_model=DocumentSegmentSearchResponse)
def search_documents(
    q: str = "",
    kind: BoardSegmentKind | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: UserView = Depends(current_user),
) -> DocumentSegmentSearchResponse:
    return DocumentSegmentSearchResponse(
        query=q,
        kind=kind,
        results=search_document_segments_for_user(user.id, q, kind=kind, limit=limit),
    )


def _save_document_request(lesson_id: str, request: DocumentSaveRequest, user_id: str) -> CoursePackageView:
    workspace = load_workspace_for_user(user_id)
    package, lesson = find_lesson_package(workspace, lesson_id)
    package.active_lesson_id = lesson.id
    current_head = current_head_commit(lesson)
    is_autosave = request.metadata.get("autosave") is True or request.metadata.get("kind") == "auto_document_save"
    guard_document: BoardDocument | None = None
    if request.base_commit_id and request.base_commit_id != current_head.id:
        if is_autosave:
            return package_view_for_lesson(workspace, package, lesson.id)
        raise HTTPException(status_code=409, detail="文档已在本次保存前更新，请刷新后再保存")
    if not document_changed(lesson.board_document, request.document):
        return package_view_for_lesson(workspace, package, lesson.id)
    if is_autosave:
        guard_document = _recent_structured_snapshot_for_autosave(lesson, current_head, request.document) or current_head.snapshot
        if would_flatten_rich_document(
            current_document=guard_document,
            new_document=request.document,
        ):
            return package_view_for_lesson(workspace, package, lesson.id)
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/save",
        lesson=lesson,
        trace_prefix="document_save",
    ):
        previous_updated_at = lesson.updated_at
        lesson.board_document = request.document
        commit_metadata: dict[str, object] = {
            "kind": "manual_document_save",
            **request.metadata,
            **_document_save_structure_metadata(
                base_commit_id=request.base_commit_id or current_head.id,
                current_head_commit_id=current_head.id,
                before_document=current_head.snapshot,
                after_document=request.document,
                flatten_guard_evaluated=is_autosave,
            ),
        }
        commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata=commit_metadata,
        )
        committed_head = current_head_commit(lesson)
        if is_autosave and guard_document and would_flatten_rich_document(
            current_document=guard_document,
            new_document=committed_head.snapshot,
        ):
            _rollback_unpersisted_commit(
                lesson,
                commit_id=committed_head.id,
                restore_head=current_head,
                updated_at=previous_updated_at,
            )
            return package_view_for_lesson(workspace, package, lesson.id)
        committed_head.metadata.update(
            _document_save_structure_metadata(
                base_commit_id=request.base_commit_id or current_head.id,
                current_head_commit_id=current_head.id,
                before_document=current_head.snapshot,
                after_document=committed_head.snapshot,
                flatten_guard_evaluated=is_autosave,
            )
        )
        refresh_lesson_runtime(lesson)
        save_workspace_for_user(user_id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


def _visible_document_text(document: BoardDocument) -> str:
    return " ".join((document.content_text or "").split())


def _structured_document_score(document: BoardDocument) -> int:
    return rich_structure_score(rich_structure_counts(document))


def _recent_structured_snapshot_for_autosave(
    lesson,
    current_head,
    new_document: BoardDocument,
) -> BoardDocument | None:
    target_text = _visible_document_text(new_document)
    if not target_text:
        return None
    commits_by_id = {commit.id: commit for commit in lesson.history_graph.commits}
    cursor = current_head
    seen: set[str] = set()
    while cursor and cursor.id not in seen:
        seen.add(cursor.id)
        if _structured_document_score(cursor.snapshot) >= 8 and _visible_document_text(cursor.snapshot) == target_text:
            return cursor.snapshot
        parent_id = cursor.parent_ids[0] if cursor.parent_ids else None
        cursor = commits_by_id.get(parent_id) if parent_id else None
    return None


def _rollback_unpersisted_commit(lesson, *, commit_id: str, restore_head, updated_at: str) -> None:
    branch = lesson.history_graph.branches[lesson.history_graph.current_branch]
    branch.head_commit_id = restore_head.id
    lesson.board_document = restore_head.snapshot
    lesson.updated_at = updated_at
    lesson.history_graph.commits = [
        commit for commit in lesson.history_graph.commits if commit.id != commit_id
    ]


def _document_save_structure_metadata(
    *,
    base_commit_id: str,
    current_head_commit_id: str,
    before_document: BoardDocument,
    after_document: BoardDocument,
    flatten_guard_evaluated: bool,
) -> dict[str, object]:
    before_counts = rich_structure_counts(before_document)
    after_counts = rich_structure_counts(after_document)
    return {
        "base_commit_id": base_commit_id,
        "current_head_commit_id": current_head_commit_id,
        "structure_before": before_counts,
        "structure_after": after_counts,
        "structure_score_before": rich_structure_score(before_counts),
        "structure_score_after": rich_structure_score(after_counts),
        "flatten_guard_evaluated": flatten_guard_evaluated,
        "flatten_guard_triggered": False,
    }


@router.post("/api/lessons/{lesson_id}/manual-commit", response_model=CoursePackageView)
def manual_commit(
    lesson_id: str,
    request: ManualCommitRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
        save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/document/save", response_model=CoursePackageView)
def save_document(
    lesson_id: str,
    request: DocumentSaveRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    return _save_document_request(lesson_id, request, user.id)


@router.post("/api/lessons/{lesson_id}/document/save-beacon", response_model=CoursePackageView)
async def save_document_beacon(
    lesson_id: str,
    request: Request,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
        save_request = DocumentSaveRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid document save payload") from exc
    return _save_document_request(lesson_id, save_request, user.id)


@router.post("/api/lessons/{lesson_id}/document/ai-edit", response_model=ChatResponse)
def ai_edit_document(
    lesson_id: str,
    request: DocumentAIEditRequest,
    user: UserView = Depends(current_user),
) -> ChatResponse:
    return document_ai_edit_request(
        lesson_id,
        request.instruction,
        request.selection_text,
        request.conversation,
        user_id=user.id,
    )


@router.post("/api/lessons/{lesson_id}/document/import-docx", response_model=CoursePackageView)
def import_document_docx(
    lesson_id: str,
    file: UploadFile = File(...),
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
        save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.get("/api/lessons/{lesson_id}/document/export-docx")
def export_document_docx(lesson_id: str, user: UserView = Depends(current_user)) -> FileResponse:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    document = _exportable_document_for_lesson(lesson)
    if is_document_empty(document):
        raise HTTPException(status_code=409, detail="当前板书文档为空，不能导出 DOCX")
    return _export_document_response(
        document=document,
        owner_user_id=user.id,
        lesson_id=lesson.id,
        download_stem=lesson.slug or lesson.id,
        suffix=".docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        exporter=export_docx,
    )


@router.get("/api/lessons/{lesson_id}/document/export-html")
def export_document_html(lesson_id: str, user: UserView = Depends(current_user)) -> FileResponse:
    workspace = load_workspace_for_user(user.id)
    _, lesson = find_lesson_package(workspace, lesson_id)
    document = _exportable_document_for_lesson(lesson)
    if is_document_empty(document):
        raise HTTPException(status_code=409, detail="当前板书文档为空，不能导出 HTML")
    return _export_document_response(
        document=document,
        owner_user_id=user.id,
        lesson_id=lesson.id,
        download_stem=lesson.slug or lesson.id,
        suffix=".html",
        media_type="text/html; charset=utf-8",
        exporter=export_html,
    )


def _exportable_document_for_lesson(lesson) -> BoardDocument:
    if not is_document_empty(lesson.board_document):
        return lesson.board_document
    try:
        head_snapshot = current_head_commit(lesson).snapshot
    except Exception:
        return lesson.board_document
    if not is_document_empty(head_snapshot):
        return head_snapshot
    return lesson.board_document


def _board_asset_resolver(user_id: str):
    store = get_board_asset_store()

    def resolve(asset_id: str) -> tuple[str, bytes] | None:
        resolved = store.read_bytes(asset_id, user_id)
        if resolved is None:
            return None
        asset, content = resolved
        return asset.mime_type, content

    return resolve


@router.post("/api/lessons/{lesson_id}/branches", response_model=CoursePackageView)
def create_lesson_branch(
    lesson_id: str,
    request: CreateBranchRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
        save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/branches/checkout", response_model=CoursePackageView)
def checkout_lesson_branch(
    lesson_id: str,
    request: SwitchBranchRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
        save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/restore", response_model=CoursePackageView)
def restore_lesson_commit(
    lesson_id: str,
    request: RestoreCommitRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
        save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)


@router.post("/api/lessons/{lesson_id}/apply-proposal", response_model=CoursePackageView)
def apply_patch_proposal(
    lesson_id: str,
    proposal: ManualCommitRequest,
    user: UserView = Depends(current_user),
) -> CoursePackageView:
    workspace = load_workspace_for_user(user.id)
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
            save_workspace_for_user(user.id, workspace)
    return package_view_for_lesson(workspace, package, lesson.id)
