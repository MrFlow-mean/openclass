from __future__ import annotations

from app.models import (
    BoardDocument,
    CommitRecord,
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
    commit = CommitRecord(
        label=label,
        message=message,
        branch_name=branch_name,
        parent_ids=[head.id],
        operations=operations,
        snapshot=snapshot_document,
        metadata=metadata or {},
    )
    lesson.board_document = new_document
    lesson.history_graph.commits.append(commit)
    lesson.history_graph.branches[branch_name].head_commit_id = commit.id
    lesson.updated_at = now_iso()
    return lesson
