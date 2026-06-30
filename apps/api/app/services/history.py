from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.models import (
    BoardDocument,
    BoardTaskRequirementSheet,
    BranchRef,
    CommitRecord,
    InteractionSession,
    LearningRequirementSheet,
    Lesson,
    PatchOperation,
    now_iso,
)

_commit_metadata_context: ContextVar[dict[str, object] | None] = ContextVar(
    "openclass_commit_metadata_context",
    default=None,
)


@contextmanager
def bind_commit_metadata(metadata: dict[str, object]) -> Iterator[None]:
    token = _commit_metadata_context.set(metadata or None)
    try:
        yield
    finally:
        _commit_metadata_context.reset(token)


def current_head_commit(lesson: Lesson) -> CommitRecord:
    branch = lesson.history_graph.branches[lesson.history_graph.current_branch]
    for commit in reversed(lesson.history_graph.commits):
        if commit.id == branch.head_commit_id:
            return commit
    return lesson.history_graph.commits[-1]


def get_commit(lesson: Lesson, commit_id: str) -> CommitRecord:
    for commit in lesson.history_graph.commits:
        if commit.id == commit_id:
            return commit
    raise ValueError(f"Unknown commit {commit_id}")


def commit_operations(
    lesson: Lesson,
    operations: list[PatchOperation],
    label: str,
    message: str,
    new_document: BoardDocument | None = None,
    metadata: dict[str, object] | None = None,
) -> Lesson:
    head = current_head_commit(lesson)
    branch_name = lesson.history_graph.current_branch
    if new_document is None:
        new_document = lesson.board_document
    snapshot_document = BoardDocument.model_validate(new_document.model_dump(mode="json"))
    context_metadata = _commit_metadata_context.get() or {}
    commit_metadata = {**context_metadata, **(metadata or {})}

    commit = CommitRecord(
        label=label,
        message=message,
        branch_name=branch_name,
        parent_ids=[head.id],
        operations=operations,
        snapshot=snapshot_document,
        metadata=commit_metadata,
    )
    lesson.board_document = new_document
    lesson.history_graph.commits.append(commit)
    lesson.history_graph.branches[branch_name].head_commit_id = commit.id
    lesson.updated_at = now_iso()
    return lesson


def _metadata_model(commit: CommitRecord, key: str, model):
    raw_value = commit.metadata.get(key) if isinstance(commit.metadata, dict) else None
    return model.model_validate(raw_value) if isinstance(raw_value, dict) else None


def _active_board_task_after(commit: CommitRecord) -> BoardTaskRequirementSheet | None:
    if not isinstance(commit.metadata, dict):
        return None
    raw_task = commit.metadata.get("active_board_task_sheet_after")
    if not isinstance(raw_task, dict) and isinstance(commit.metadata.get("new_board_task"), dict):
        raw_task = commit.metadata.get("new_board_task")
    if not isinstance(raw_task, dict) and commit.metadata.get("board_task_cleared") is False:
        raw_task = commit.metadata.get("board_task_sheet")
    return BoardTaskRequirementSheet.model_validate(raw_task) if isinstance(raw_task, dict) else None


def restore_lesson_runtime_from_commit(lesson: Lesson, commit: CommitRecord) -> Lesson:
    lesson.board_document = commit.snapshot
    lesson.learning_requirements = _metadata_model(commit, "active_requirement_sheet_after", LearningRequirementSheet)
    lesson.active_interaction_session = _metadata_model(
        commit,
        "active_interaction_session_after",
        InteractionSession,
    )
    lesson.board_task_requirements = _active_board_task_after(commit)
    lesson.updated_at = now_iso()
    return lesson


def create_branch(lesson: Lesson, branch_name: str, from_commit_id: str | None = None) -> Lesson:
    source_commit = get_commit(lesson, from_commit_id) if from_commit_id else current_head_commit(lesson)
    lesson.history_graph.branches[branch_name] = BranchRef(
        name=branch_name,
        head_commit_id=source_commit.id,
        base_commit_id=source_commit.id,
    )
    lesson.history_graph.current_branch = branch_name
    restore_lesson_runtime_from_commit(lesson, source_commit)
    return lesson


def switch_branch(lesson: Lesson, branch_name: str) -> Lesson:
    branch = lesson.history_graph.branches[branch_name]
    source_commit = get_commit(lesson, branch.head_commit_id)
    lesson.history_graph.current_branch = branch_name
    restore_lesson_runtime_from_commit(lesson, source_commit)
    return lesson


def restore_commit(lesson: Lesson, commit_id: str, label: str) -> Lesson:
    commit = get_commit(lesson, commit_id)
    restore_lesson_runtime_from_commit(lesson, commit)
    return commit_operations(
        lesson,
        operations=[],
        label=label,
        message=f"Restored snapshot from {commit.label}",
        new_document=commit.snapshot,
        metadata={
            "kind": "restore_snapshot",
            "restored_commit_id": commit.id,
            "restored_commit_label": commit.label,
            "active_requirement_sheet_after": (
                lesson.learning_requirements.model_dump(mode="json")
                if lesson.learning_requirements is not None
                else None
            ),
            "active_interaction_session_after": (
                lesson.active_interaction_session.model_dump(mode="json")
                if lesson.active_interaction_session is not None
                else None
            ),
            "active_board_task_sheet_after": (
                lesson.board_task_requirements.model_dump(mode="json")
                if lesson.board_task_requirements is not None
                else None
            ),
        },
    )
