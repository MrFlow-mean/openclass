from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.models import (
    ChatRequest,
    ChatResponse,
    CreatePackageRequest,
    CreateBranchRequest,
    CourseGraphEdge,
    CoursePackage,
    CoursePackageView,
    DocumentAIEditRequest,
    DocumentSaveRequest,
    GenerateLessonRequest,
    Lesson,
    LessonView,
    ManualCommitRequest,
    MoveLessonRequest,
    ReorderTabsRequest,
    RestoreCommitRequest,
    SelectionRef,
    SwitchBranchRequest,
    WorkspaceState,
    WorkspaceStateView,
)
from app.services.ai_logging import (
    ai_log_context,
    ai_usage_logger,
    log_ai_interaction_message,
    new_trace_id,
)
from app.services.ai_workflow import course_workflow
from app.services.course_runtime import build_lesson_for_topic, refresh_lesson_runtime
from app.services.course_store import FileCourseStore
from app.services.history import (
    commit_operations,
    create_branch,
    current_head_commit,
    restore_commit,
    switch_branch,
)
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.openai_realtime import openai_realtime_teacher
from app.services.resource_library import build_resource_item
from app.services.rich_document import export_docx, import_docx, is_document_empty

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STORE = FileCourseStore(DATA_DIR / "store.json")
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Board Course System API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RealtimeConnectRequest(BaseModel):
    offer_sdp: str
    latest_assistant_message: str | None = None
    client_session_id: str | None = None


class RealtimeConnectResponse(BaseModel):
    answer_sdp: str
    model: str
    voice: str


class RealtimeTranscriptLogRequest(BaseModel):
    client_session_id: str | None = None
    lesson_title: str | None = None
    role: Literal["user", "assistant"]
    transport_event_type: str
    transcript: str


def _load_workspace() -> WorkspaceState:
    return STORE.load()


def _save_workspace(workspace: WorkspaceState) -> None:
    STORE.save(workspace)


def _get_package(workspace: WorkspaceState, package_id: str) -> CoursePackage:
    for package in workspace.packages:
        if package.id == package_id:
            return package
    raise HTTPException(status_code=404, detail=f"Unknown course package {package_id}")


def _get_active_package(workspace: WorkspaceState) -> CoursePackage:
    if not workspace.packages:
        raise HTTPException(status_code=404, detail="No course package available")
    if workspace.active_package_id:
        return _get_package(workspace, workspace.active_package_id)
    workspace.active_package_id = workspace.packages[0].id
    return workspace.packages[0]


def _load_workspace_package() -> tuple[WorkspaceState, CoursePackage]:
    workspace = _load_workspace()
    package = _get_active_package(workspace)
    return workspace, package


def _get_lesson(package: CoursePackage, lesson_id: str) -> Lesson:
    for lesson in package.lessons:
        if lesson.id == lesson_id:
            return lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def _find_lesson_package(workspace: WorkspaceState, lesson_id: str) -> tuple[CoursePackage, Lesson]:
    for package in workspace.packages:
        for lesson in package.lessons:
            if lesson.id == lesson_id:
                return package, lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def _normalize_package_state(package: CoursePackage) -> None:
    lesson_ids = [lesson.id for lesson in package.lessons]
    valid_ids = set(lesson_ids)
    package.open_lesson_ids = [lesson_id for lesson_id in package.open_lesson_ids if lesson_id in valid_ids]
    package.workspace_tab_order = [lesson_id for lesson_id in package.workspace_tab_order if lesson_id in valid_ids]
    package.course_graph = [
        edge
        for edge in package.course_graph
        if edge.source_lesson_id in valid_ids and edge.target_lesson_id in valid_ids
    ]

    if not package.lessons:
        package.active_lesson_id = None
        package.open_lesson_ids = []
        package.workspace_tab_order = []
        return

    if not package.workspace_tab_order:
        package.workspace_tab_order = [package.lessons[0].id]

    if not package.open_lesson_ids:
        package.open_lesson_ids = list(package.workspace_tab_order)

    for lesson_id in package.workspace_tab_order:
        if lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(lesson_id)

    if package.active_lesson_id not in valid_ids:
        package.active_lesson_id = package.workspace_tab_order[0]
    elif package.active_lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(package.active_lesson_id)
        if package.active_lesson_id not in package.open_lesson_ids:
            package.open_lesson_ids.append(package.active_lesson_id)


def _lesson_view(lesson: Lesson) -> LessonView:
    return LessonView.model_validate(
        lesson.model_dump(mode="json", exclude={"teaching_guide", "board_teaching_guide"})
    )


def _package_view(package: CoursePackage) -> CoursePackageView:
    return CoursePackageView.model_validate(
        package.model_dump(
            mode="json",
            exclude={"lessons": {"__all__": {"teaching_guide", "board_teaching_guide"}}},
        )
    )


def _workspace_view(workspace: WorkspaceState) -> WorkspaceStateView:
    return WorkspaceStateView(
        packages=[_package_view(package) for package in workspace.packages],
        active_package_id=workspace.active_package_id,
    )


def _short_text(value: str, limit: int = 96) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _chat_flow_label(request: ChatRequest, action: str, *, auto_applied: bool) -> str:
    if auto_applied:
        prefix = "AI 写入"
    elif request.interaction_mode == "direct_edit":
        prefix = "AI 直接编辑"
    else:
        prefix = {
            "clarify_request": "AI 澄清",
            "no_change": "AI 讲解",
            "edit_board": "AI 文档生成",
            "append_section": "AI 追加章节",
            "create_new_lesson": "AI 新开课程",
            "await_scope_choice": "AI 范围选择",
            "await_reference_choice": "AI 资料确认",
        }.get(action, "AI 流程")
    return f"{prefix} · {_short_text(request.message, 28)}"


def _chat_flow_metadata(
    *,
    request: ChatRequest,
    teacher_message: str,
    workflow_result: dict[str, object],
    created_lesson: Lesson | None,
    auto_applied: bool,
) -> dict[str, object]:
    board_decision = workflow_result["board_decision"]
    learning_clarification = workflow_result["learning_clarification"]
    return {
        "kind": "chat_flow",
        "user_message": request.message,
        "assistant_message": teacher_message,
        "interaction_mode": request.interaction_mode,
        "board_action": board_decision.action,
        "selection": request.selection.model_dump(mode="json") if request.selection else None,
        "learning_clarification": learning_clarification.model_dump(mode="json"),
        "board_teaching_guide": (
            workflow_result["board_teaching_guide"].model_dump(mode="json")
            if workflow_result.get("board_teaching_guide") is not None
            else None
        ),
        "created_lesson_id": created_lesson.id if created_lesson else None,
        "created_lesson_title": created_lesson.title if created_lesson else None,
        "auto_applied": auto_applied,
    }


def _chat_flow_message(request: ChatRequest, teacher_message: str) -> str:
    return f"用户：{_short_text(request.message)}\nAI：{_short_text(teacher_message, 120)}"


@contextmanager
def _bind_ai_request_context(
    route_name: str,
    *,
    lesson: Lesson | None = None,
    trace_prefix: str = "trace",
    trace_id: str | None = None,
    **extra: object,
) -> Iterator[dict[str, object]]:
    context: dict[str, object] = {
        "trace_id": trace_id or new_trace_id(trace_prefix),
        "route": route_name,
    }
    if lesson is not None:
        context["lesson_id"] = lesson.id
        context["lesson_title"] = lesson.title
    context.update({key: value for key, value in extra.items() if value is not None})
    with ai_log_context(**context):
        yield context


def _commit_document_snapshot(
    lesson: Lesson,
    *,
    label: str,
    message: str,
    metadata: dict[str, object] | None = None,
) -> None:
    commit_operations(
        lesson,
        [],
        label=label,
        message=message,
        new_document=lesson.board_document,
        metadata=metadata,
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openai": openai_course_ai.status(),
        "realtime": openai_realtime_teacher.status(),
    }


@app.get("/api/workspace", response_model=WorkspaceStateView)
def get_workspace() -> WorkspaceStateView:
    return _workspace_view(_load_workspace())


@app.post("/api/packages", response_model=WorkspaceStateView)
def create_package(request: CreatePackageRequest) -> WorkspaceStateView:
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Package title is required")

    workspace = _load_workspace()
    package = CoursePackage(
        title=title,
        summary=request.summary.strip() or "空课程包，等待你在顶部继续新建页面。",
        lessons=[],
    )
    workspace.packages.append(package)
    workspace.active_package_id = package.id
    _save_workspace(workspace)
    return _workspace_view(workspace)


@app.post("/api/packages/{package_id}/open", response_model=WorkspaceStateView)
def open_package(package_id: str) -> WorkspaceStateView:
    workspace = _load_workspace()
    _get_package(workspace, package_id)
    workspace.active_package_id = package_id
    _save_workspace(workspace)
    return _workspace_view(workspace)


@app.post("/api/lessons/{lesson_id}/move", response_model=WorkspaceStateView)
def move_lesson(lesson_id: str, request: MoveLessonRequest) -> WorkspaceStateView:
    workspace = _load_workspace()
    source_package, lesson = _find_lesson_package(workspace, lesson_id)
    target_package = _get_package(workspace, request.target_package_id)

    if source_package.id == target_package.id:
        raise HTTPException(status_code=400, detail="Lesson is already in the selected package")

    source_package.lessons = [current for current in source_package.lessons if current.id != lesson_id]
    source_package.open_lesson_ids = [current for current in source_package.open_lesson_ids if current != lesson_id]
    source_package.workspace_tab_order = [current for current in source_package.workspace_tab_order if current != lesson_id]
    if source_package.active_lesson_id == lesson_id:
        source_package.active_lesson_id = None

    target_package.lessons.append(lesson)
    if lesson.id not in target_package.open_lesson_ids:
        target_package.open_lesson_ids.append(lesson.id)
    if lesson.id not in target_package.workspace_tab_order:
        target_package.workspace_tab_order.append(lesson.id)
    if target_package.active_lesson_id is None:
        target_package.active_lesson_id = lesson.id

    _normalize_package_state(source_package)
    _normalize_package_state(target_package)
    _save_workspace(workspace)
    return _workspace_view(workspace)


@app.post("/api/lessons/{lesson_id}/delete", response_model=WorkspaceStateView)
def delete_lesson(lesson_id: str) -> WorkspaceStateView:
    workspace = _load_workspace()
    package, _ = _find_lesson_package(workspace, lesson_id)

    package.lessons = [current for current in package.lessons if current.id != lesson_id]
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = None

    _normalize_package_state(package)
    _save_workspace(workspace)
    return _workspace_view(workspace)


@app.get("/api/course-package", response_model=CoursePackageView)
def get_course_package() -> CoursePackageView:
    _, package = _load_workspace_package()
    return _package_view(package)


@app.post("/api/lessons/generate", response_model=CoursePackageView)
def generate_lesson(request: GenerateLessonRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    with _bind_ai_request_context(
        "/api/lessons/generate",
        trace_prefix="generate_lesson",
        generation_topic=request.topic,
        branch_from_lesson_id=request.branch_from_lesson_id,
        start_blank=request.start_blank,
    ):
        if not request.start_blank:
            ai_usage_logger.log_event(
                "lesson_generation_request",
                topic=request.topic,
                branch_from_lesson_id=request.branch_from_lesson_id,
            )
        lesson = (
            create_empty_lesson(request.topic)
            if request.start_blank
            else build_lesson_for_topic(request.topic)
        )
        package.lessons.append(lesson)
        package.open_lesson_ids.append(lesson.id)
        package.workspace_tab_order.append(lesson.id)
        package.active_lesson_id = lesson.id
        if request.branch_from_lesson_id:
            package.course_graph.append(
                CourseGraphEdge(
                    source_lesson_id=request.branch_from_lesson_id,
                    target_lesson_id=lesson.id,
                    relationship="deep_dive",
                )
            )
        _save_workspace(workspace)
        if not request.start_blank:
            ai_usage_logger.log_event(
                "lesson_generation_response",
                lesson_id=lesson.id,
                lesson_title=lesson.title,
                summary=lesson.summary,
                tags=lesson.tags,
            )
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/manual-commit", response_model=CoursePackageView)
def manual_commit(lesson_id: str, request: ManualCommitRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/manual-commit",
        lesson=lesson,
        trace_prefix="manual_commit",
        commit_label=request.label,
    ):
        if request.document is not None:
            lesson.board_document = request.document
        _commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata={"kind": "manual_document_edit"},
        )
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/document/save", response_model=CoursePackageView)
def save_document(lesson_id: str, request: DocumentSaveRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/save",
        lesson=lesson,
        trace_prefix="document_save",
    ):
        lesson.board_document = request.document
        _commit_document_snapshot(
            lesson,
            label=request.label,
            message=request.message,
            metadata={"kind": "manual_document_save"},
        )
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/document/ai-edit", response_model=ChatResponse)
def ai_edit_document(lesson_id: str, request: DocumentAIEditRequest) -> ChatResponse:
    selection = None
    if request.selection_text:
        selection = SelectionRef(kind="board", lesson_id=lesson_id, excerpt=request.selection_text)
    instruction = request.instruction if request.replace_whole else request.instruction
    return chat_on_lesson(
        lesson_id,
        ChatRequest(
            message=instruction,
            selection=selection,
            interaction_mode="direct_edit",
            conversation=request.conversation,
        ),
    )


@app.post("/api/lessons/{lesson_id}/document/import-docx", response_model=CoursePackageView)
def import_document_docx(lesson_id: str, file: UploadFile = File(...)) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    destination = UPLOAD_DIR / f"{lesson_id}_{file.filename}"
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/import-docx",
        lesson=lesson,
        trace_prefix="import_docx",
        filename=file.filename,
    ):
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        lesson.board_document = import_docx(destination, title=lesson.board_document.title or lesson.title)
        _commit_document_snapshot(
            lesson,
            label="Import DOCX",
            message=f"Imported {file.filename} into the rich document editor",
            metadata={"kind": "import_docx", "filename": file.filename},
        )
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.get("/api/lessons/{lesson_id}/document/export-docx")
def export_document_docx(lesson_id: str) -> FileResponse:
    _, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    target_path = EXPORT_DIR / f"{lesson.slug or lesson.id}.docx"
    export_docx(lesson.board_document, target_path)
    return FileResponse(
        target_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{lesson.slug or lesson.id}.docx",
    )


@app.post("/api/lessons/{lesson_id}/branches", response_model=CoursePackageView)
def create_lesson_branch(lesson_id: str, request: CreateBranchRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches",
        lesson=lesson,
        trace_prefix="create_branch",
        branch_name=request.name,
        from_commit_id=request.from_commit_id,
    ):
        create_branch(lesson, request.name, request.from_commit_id)
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/branches/checkout", response_model=CoursePackageView)
def checkout_lesson_branch(lesson_id: str, request: SwitchBranchRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/branches/checkout",
        lesson=lesson,
        trace_prefix="switch_branch",
        branch_name=request.name,
    ):
        switch_branch(lesson, request.name)
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/restore", response_model=CoursePackageView)
def restore_lesson_commit(lesson_id: str, request: RestoreCommitRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/restore",
        lesson=lesson,
        trace_prefix="restore_commit",
        commit_id=request.commit_id,
        restore_label=request.label,
    ):
        restore_commit(lesson, request.commit_id, request.label)
        refresh_lesson_runtime(lesson)
        _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/workspace/reorder", response_model=CoursePackageView)
def reorder_workspace_tabs(request: ReorderTabsRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    package.workspace_tab_order = request.ordered_lesson_ids
    package.open_lesson_ids = request.ordered_lesson_ids
    package.active_lesson_id = request.active_lesson_id or (
        request.ordered_lesson_ids[0] if request.ordered_lesson_ids else None
    )
    _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/open", response_model=CoursePackageView)
def open_lesson_tab(lesson_id: str) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    _get_lesson(package, lesson_id)
    if lesson_id not in package.open_lesson_ids:
        package.open_lesson_ids.append(lesson_id)
    if lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(lesson_id)
    package.active_lesson_id = lesson_id
    _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/close", response_model=CoursePackageView)
def close_lesson_tab(lesson_id: str) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [current for current in package.workspace_tab_order if current != lesson_id]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = package.workspace_tab_order[0] if package.workspace_tab_order else None
    _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(lesson_id: str, request: ChatRequest) -> ChatResponse:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        lesson=lesson,
        trace_prefix="chat",
        selection_kind=request.selection.kind if request.selection else None,
    ):
        log_ai_interaction_message(
            channel="text",
            direction="input",
            role="user",
            transport="typed_text",
            content=request.message,
            metadata={
                "selection": request.selection,
                "interaction_mode": request.interaction_mode,
                "scope_action": request.scope_action,
                "resource_reference_action": request.resource_reference_action,
            },
        )
        ai_usage_logger.log_event(
            "chat_request",
            message=request.message,
            selection=request.selection,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            resource_chapter_id=request.resource_chapter_id,
            resource_reference_action=request.resource_reference_action,
            resource_reference_resource_id=request.resource_reference_resource_id,
            resource_reference_chapter_id=request.resource_reference_chapter_id,
            conversation=request.conversation,
        )

        try:
            was_blank_document = is_document_empty(lesson.board_document)
            workflow_result = course_workflow.invoke(
                {"lesson": lesson, "course_package": package, "request": request}
            )
            lesson.learning_requirements = workflow_result["learning_requirement_sheet"]
            lesson.summary = workflow_result["learning_requirement_sheet"].learning_goal
            lesson.board_teaching_guide = workflow_result.get("board_teaching_guide")
            created_lesson = workflow_result.get("generated_lesson")
            if created_lesson is None:
                lesson.teaching_guide = workflow_result["teaching_guide"]
            teacher_message = workflow_result["teacher_message"]
            teacher_document = workflow_result.get("teacher_document")
            auto_applied_document = (
                created_lesson is None
                and workflow_result["board_decision"].action in {"edit_board", "append_section"}
                and bool(workflow_result.get("document_updated"))
                and teacher_document is not None
            )

            if auto_applied_document and teacher_document is not None:
                lesson.board_document = teacher_document
                lesson.teaching_guide = workflow_result["teaching_guide"]
                if was_blank_document:
                    teacher_message = f"我已经把这次需求生成到右侧板书里了。\n{teacher_message}"

            if created_lesson is not None:
                package.lessons.append(created_lesson)
                package.course_graph.append(
                    CourseGraphEdge(
                        source_lesson_id=lesson.id,
                        target_lesson_id=created_lesson.id,
                        relationship="deep_dive",
                    )
                )
                package.open_lesson_ids.append(created_lesson.id)
                package.workspace_tab_order.append(created_lesson.id)
                package.active_lesson_id = created_lesson.id

            response_selected_reference = (
                workflow_result.get("selected_reference")
                if auto_applied_document or created_lesson is not None
                else None
            )

            flow_metadata = _chat_flow_metadata(
                request=request,
                teacher_message=teacher_message,
                workflow_result=workflow_result,
                created_lesson=created_lesson,
                auto_applied=auto_applied_document,
            )
            flow_label = _chat_flow_label(
                request,
                workflow_result["board_decision"].action,
                auto_applied=auto_applied_document,
            )
            _commit_document_snapshot(
                lesson,
                label=flow_label,
                message=_chat_flow_message(request, teacher_message),
                metadata=flow_metadata,
            )
            _save_workspace(workspace)

            response = ChatResponse(
                teacher_message=teacher_message,
                learning_requirement_sheet=workflow_result["learning_requirement_sheet"],
                learning_clarification=workflow_result["learning_clarification"],
                board_decision=workflow_result["board_decision"],
                needs_clarification=workflow_result.get("needs_clarification", False),
                clarification_questions=workflow_result.get("clarification_questions", []),
                patch_proposal=None,
                scope_options=workflow_result.get("scope_options", []),
                resource_matches=workflow_result.get("resource_matches", []),
                reference_prompt=workflow_result.get("reference_prompt"),
                selected_reference=response_selected_reference,
                created_lesson=_lesson_view(created_lesson) if created_lesson else None,
                course_package=_package_view(package),
            )
        except Exception as exc:
            ai_usage_logger.log_event("chat_error", error=str(exc))
            raise

        ai_usage_logger.log_event(
            "chat_response",
            teacher_message=response.teacher_message,
            learning_requirement_sheet=response.learning_requirement_sheet,
            learning_clarification=response.learning_clarification,
            board_decision=response.board_decision,
            needs_clarification=response.needs_clarification,
            clarification_questions=response.clarification_questions,
            patch_proposal=response.patch_proposal,
            scope_options=response.scope_options,
            resource_matches=response.resource_matches,
            reference_prompt=response.reference_prompt,
            selected_reference=response.selected_reference,
            created_lesson=response.created_lesson,
        )
        log_ai_interaction_message(
            channel="text",
            direction="output",
            role="assistant",
            transport="chat_response",
            content=response.teacher_message,
            metadata={
                "board_action": response.board_decision.action,
                "needs_clarification": response.needs_clarification,
                "created_lesson_id": response.created_lesson.id if response.created_lesson else None,
            },
        )
        return response


@app.post("/api/lessons/{lesson_id}/realtime/connect", response_model=RealtimeConnectResponse)
def connect_realtime_session(
    lesson_id: str, request: RealtimeConnectRequest
) -> RealtimeConnectResponse:
    if not openai_realtime_teacher.enabled:
        raise HTTPException(status_code=503, detail="OpenAI Realtime is not configured")

    _, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/connect",
        lesson=lesson,
        trace_prefix="realtime",
        trace_id=request.client_session_id,
    ):
        ai_usage_logger.log_event(
            "realtime_connect_request",
            offer_sdp=request.offer_sdp,
            latest_assistant_message=request.latest_assistant_message,
        )
        try:
            answer_sdp = openai_realtime_teacher.create_call(
                lesson=lesson,
                offer_sdp=request.offer_sdp,
                latest_assistant_message=request.latest_assistant_message,
            )
        except RuntimeError as exc:
            ai_usage_logger.log_event("realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            ai_usage_logger.log_event("realtime_connect_error", error=str(exc))
            raise HTTPException(status_code=502, detail=f"Realtime connect failed: {exc}") from exc

        response = RealtimeConnectResponse(
            answer_sdp=answer_sdp,
            model=openai_realtime_teacher.config.model,
            voice=openai_realtime_teacher.config.voice,
        )
        ai_usage_logger.log_event(
            "realtime_connect_response",
            answer_sdp=response.answer_sdp,
            model=response.model,
            voice=response.voice,
        )
        return response


@app.post("/api/lessons/{lesson_id}/realtime/events")
def log_realtime_event(lesson_id: str, request: RealtimeTranscriptLogRequest) -> dict[str, str]:
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/realtime/events",
        trace_prefix="realtime",
        trace_id=request.client_session_id,
        lesson_id=lesson_id,
        lesson_title=request.lesson_title,
    ):
        ai_usage_logger.log_event(
            "realtime_transcript",
            role=request.role,
            transport_event_type=request.transport_event_type,
            transcript=request.transcript,
        )
        log_ai_interaction_message(
            channel="voice",
            direction="input" if request.role == "user" else "output",
            role=request.role,
            transport=request.transport_event_type,
            content=request.transcript,
            metadata={"lesson_title": request.lesson_title},
        )
    return {"status": "ok"}


@app.post("/api/lessons/{lesson_id}/apply-proposal", response_model=CoursePackageView)
def apply_patch_proposal(lesson_id: str, proposal: ManualCommitRequest) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    with _bind_ai_request_context(
        "/api/lessons/{lesson_id}/apply-proposal",
        lesson=lesson,
        trace_prefix="apply_proposal",
        proposal_label=proposal.label,
    ):
        if proposal.document is not None:
            lesson.board_document = proposal.document
            _commit_document_snapshot(
                lesson,
                label=proposal.label,
                message=proposal.message,
                metadata={"kind": "apply_proposal"},
            )
            refresh_lesson_runtime(lesson)
            _save_workspace(workspace)
    return _package_view(package)


@app.post("/api/resources/upload", response_model=CoursePackageView)
def upload_resource(file: UploadFile = File(...)) -> CoursePackageView:
    workspace, package = _load_workspace_package()
    original_name = Path(file.filename or "resource").name
    destination = UPLOAD_DIR / f"{uuid4().hex[:8]}_{original_name}"
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    resource = build_resource_item(destination, original_name)
    package.resources.append(resource)
    _save_workspace(workspace)
    return _package_view(package)


@app.get("/api/lessons/{lesson_id}/head")
def get_lesson_head(lesson_id: str) -> dict[str, str]:
    _, package = _load_workspace_package()
    lesson = _get_lesson(package, lesson_id)
    head = current_head_commit(lesson)
    return {"lesson_id": lesson_id, "head_commit_id": head.id}
