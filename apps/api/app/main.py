from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    ChatRequest,
    ChatResponse,
    CreateBranchRequest,
    CourseGraphEdge,
    CoursePackage,
    CoursePackageView,
    GenerateLessonRequest,
    Lesson,
    LessonView,
    ManualCommitRequest,
    ReorderTabsRequest,
    RestoreCommitRequest,
    SwitchBranchRequest,
)
from app.services.ai_workflow import course_workflow
from app.services.course_runtime import build_lesson_for_topic, refresh_lesson_runtime
from app.services.course_store import FileCourseStore
from app.services.document_ops import apply_patch
from app.services.history import (
    commit_operations,
    create_branch,
    current_head_commit,
    restore_commit,
    switch_branch,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import build_resource_item

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STORE = FileCourseStore(DATA_DIR / "store.json")
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI Board Course System API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_package() -> CoursePackage:
    return STORE.load()


def _save_package(package: CoursePackage) -> None:
    STORE.save(package)


def _get_lesson(package: CoursePackage, lesson_id: str):
    for lesson in package.lessons:
        if lesson.id == lesson_id:
            return lesson
    raise HTTPException(status_code=404, detail=f"Unknown lesson {lesson_id}")


def _lesson_view(lesson: Lesson) -> LessonView:
    return LessonView.model_validate(
        lesson.model_dump(mode="json", exclude={"teaching_guide"})
    )


def _package_view(package: CoursePackage) -> CoursePackageView:
    return CoursePackageView.model_validate(
        package.model_dump(
            mode="json",
            exclude={"lessons": {"__all__": {"teaching_guide"}}},
        )
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "openai": openai_course_ai.status()}


@app.get("/api/course-package", response_model=CoursePackageView)
def get_course_package() -> CoursePackageView:
    return _package_view(_load_package())


@app.post("/api/lessons/generate", response_model=CoursePackageView)
def generate_lesson(request: GenerateLessonRequest) -> CoursePackageView:
    package = _load_package()
    lesson = build_lesson_for_topic(request.topic)
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
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/manual-commit", response_model=CoursePackageView)
def manual_commit(lesson_id: str, request: ManualCommitRequest) -> CoursePackageView:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    lesson.board_document, _ = apply_patch(lesson.board_document, request.operations)
    commit_operations(
        lesson,
        request.operations,
        label=request.label,
        message=request.message,
        new_document=lesson.board_document,
    )
    refresh_lesson_runtime(lesson)
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/branches", response_model=CoursePackageView)
def create_lesson_branch(lesson_id: str, request: CreateBranchRequest) -> CoursePackageView:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    create_branch(lesson, request.name, request.from_commit_id)
    refresh_lesson_runtime(lesson)
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/branches/checkout", response_model=CoursePackageView)
def checkout_lesson_branch(lesson_id: str, request: SwitchBranchRequest) -> CoursePackageView:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    switch_branch(lesson, request.name)
    refresh_lesson_runtime(lesson)
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/restore", response_model=CoursePackageView)
def restore_lesson_commit(lesson_id: str, request: RestoreCommitRequest) -> CoursePackageView:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    restore_commit(lesson, request.commit_id, request.label)
    refresh_lesson_runtime(lesson)
    _save_package(package)
    return _package_view(package)


@app.post("/api/workspace/reorder", response_model=CoursePackageView)
def reorder_workspace_tabs(request: ReorderTabsRequest) -> CoursePackageView:
    package = _load_package()
    package.workspace_tab_order = request.ordered_lesson_ids
    package.open_lesson_ids = request.ordered_lesson_ids
    package.active_lesson_id = request.active_lesson_id or (
        request.ordered_lesson_ids[0] if request.ordered_lesson_ids else None
    )
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/open", response_model=CoursePackageView)
def open_lesson_tab(lesson_id: str) -> CoursePackageView:
    package = _load_package()
    _get_lesson(package, lesson_id)
    if lesson_id not in package.open_lesson_ids:
        package.open_lesson_ids.append(lesson_id)
    if lesson_id not in package.workspace_tab_order:
        package.workspace_tab_order.append(lesson_id)
    package.active_lesson_id = lesson_id
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/close", response_model=CoursePackageView)
def close_lesson_tab(lesson_id: str) -> CoursePackageView:
    package = _load_package()
    package.open_lesson_ids = [current for current in package.open_lesson_ids if current != lesson_id]
    package.workspace_tab_order = [
        current for current in package.workspace_tab_order if current != lesson_id
    ]
    if package.active_lesson_id == lesson_id:
        package.active_lesson_id = package.workspace_tab_order[0] if package.workspace_tab_order else None
    _save_package(package)
    return _package_view(package)


@app.post("/api/lessons/{lesson_id}/chat", response_model=ChatResponse)
def chat_on_lesson(lesson_id: str, request: ChatRequest) -> ChatResponse:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    workflow_result = course_workflow.invoke(
        {"lesson": lesson, "course_package": package, "request": request}
    )
    lesson.learning_requirements = workflow_result["learning_requirement_sheet"]
    refresh_lesson_runtime(
        lesson,
        requirements=workflow_result["learning_requirement_sheet"],
    )

    created_lesson = workflow_result.get("generated_lesson")
    proposal = workflow_result.get("patch_proposal")
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
    _save_package(package)

    return ChatResponse(
        teacher_message=workflow_result["teacher_message"],
        learning_requirement_sheet=workflow_result["learning_requirement_sheet"],
        board_decision=workflow_result["board_decision"],
        needs_clarification=workflow_result.get("needs_clarification", False),
        clarification_questions=workflow_result.get("clarification_questions", []),
        patch_proposal=proposal,
        scope_options=workflow_result.get("scope_options", []),
        resource_matches=workflow_result.get("resource_matches", []),
        created_lesson=_lesson_view(created_lesson) if created_lesson else None,
        course_package=_package_view(package),
    )


@app.post("/api/lessons/{lesson_id}/apply-proposal", response_model=CoursePackageView)
def apply_patch_proposal(lesson_id: str, proposal: ManualCommitRequest) -> CoursePackageView:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    preview_doc, _ = apply_patch(lesson.board_document, proposal.operations)
    commit_operations(
        lesson,
        proposal.operations,
        label=proposal.label,
        message=proposal.message,
        new_document=preview_doc,
    )
    refresh_lesson_runtime(lesson)
    _save_package(package)
    return _package_view(package)


@app.post("/api/resources/upload", response_model=CoursePackageView)
def upload_resource(file: UploadFile = File(...)) -> CoursePackageView:
    package = _load_package()
    destination = UPLOAD_DIR / file.filename
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    resource = build_resource_item(destination, file.filename)
    package.resources.append(resource)
    _save_package(package)
    return _package_view(package)


@app.get("/api/lessons/{lesson_id}/head")
def get_lesson_head(lesson_id: str) -> dict[str, str]:
    package = _load_package()
    lesson = _get_lesson(package, lesson_id)
    head = current_head_commit(lesson)
    return {"lesson_id": lesson_id, "head_commit_id": head.id}
