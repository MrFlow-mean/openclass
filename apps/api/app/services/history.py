from __future__ import annotations

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    InteractionSession,
    LearningRequirementSheet,
    Lesson,
    PatchOperation,
    now_iso,
)


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


def restore_lesson_runtime_from_commit(lesson: Lesson, commit: CommitRecord) -> None:
    if not isinstance(commit.metadata, dict):
        return
    if "active_requirement_sheet_after" in commit.metadata:
        raw_requirements = commit.metadata.get("active_requirement_sheet_after")
        lesson.learning_requirements = (
            LearningRequirementSheet.model_validate(raw_requirements)
            if isinstance(raw_requirements, dict)
            else None
        )
    if "active_interaction_session_after" in commit.metadata:
        raw_session = commit.metadata.get("active_interaction_session_after")
        lesson.active_interaction_session = (
            InteractionSession.model_validate(raw_session)
            if isinstance(raw_session, dict)
            else None
        )


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

    commit = CommitRecord(
        label=label,
        message=message,
        branch_name=branch_name,
        parent_ids=[head.id],
        operations=operations,
        snapshot=new_document,
        metadata=metadata or {},
    )
    lesson.board_document = new_document
    lesson.history_graph.commits.append(commit)
    lesson.history_graph.branches[branch_name].head_commit_id = commit.id
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
    lesson.board_document = source_commit.snapshot
    restore_lesson_runtime_from_commit(lesson, source_commit)
    lesson.updated_at = now_iso()
    return lesson


def switch_branch(lesson: Lesson, branch_name: str) -> Lesson:
    branch = lesson.history_graph.branches[branch_name]
    source_commit = get_commit(lesson, branch.head_commit_id)
    lesson.history_graph.current_branch = branch_name
    lesson.board_document = source_commit.snapshot
    restore_lesson_runtime_from_commit(lesson, source_commit)
    lesson.updated_at = now_iso()
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
        },
    )
