from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    Lesson,
    PatchOperation,
    now_iso,
)

_CHAT_HISTORY_KINDS = {
    "basic_chat",
    "learning_requirement_refinement",
    "board_task_requirement_refinement",
    "chat_flow",
    "board_section_teaching",
}
_DOCUMENT_HISTORY_KINDS = {
    "manual_document_save",
    "manual_document_edit",
    "auto_document_save",
    "board_document_generation",
    "board_document_edit",
    "import_docx",
}
_RESTORE_HISTORY_KINDS = {"restore_snapshot"}
_SYSTEM_HISTORY_KINDS = {"initial_document"}
_HISTORY_NODE_KINDS = {"chat", "document", "restore", "system"}

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
    commit_metadata = _with_history_node_metadata(
        metadata=commit_metadata,
        operations=operations,
        label=label,
        message=message,
    )

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


def _with_history_node_metadata(
    *,
    metadata: dict[str, object],
    operations: list[PatchOperation],
    label: str,
    message: str,
) -> dict[str, object]:
    node_kind = _history_node_kind(metadata=metadata, operations=operations)
    inferred = {
        "history_node_kind": node_kind,
        "history_node_title": _history_node_title(label=label, metadata=metadata, node_kind=node_kind),
        "history_node_summary": _history_node_summary(message=message, metadata=metadata, node_kind=node_kind),
    }
    return {**inferred, **metadata}


def _history_node_kind(*, metadata: dict[str, object], operations: list[PatchOperation]) -> str:
    explicit = metadata.get("history_node_kind")
    if isinstance(explicit, str) and explicit in _HISTORY_NODE_KINDS:
        return explicit
    metadata_kind = metadata.get("kind")
    kind = metadata_kind if isinstance(metadata_kind, str) else ""
    if kind in _RESTORE_HISTORY_KINDS:
        return "restore"
    if kind in _SYSTEM_HISTORY_KINDS:
        return "system"
    if metadata.get("document_changed") is True or operations or kind in _DOCUMENT_HISTORY_KINDS:
        return "document"
    if kind in _CHAT_HISTORY_KINDS or _metadata_text(metadata, "user_message") or _metadata_text(metadata, "assistant_message"):
        return "chat"
    return "system"


def _history_node_title(*, label: str, metadata: dict[str, object], node_kind: str) -> str:
    explicit = _metadata_text(metadata, "history_node_title")
    if explicit:
        return explicit
    if node_kind == "chat":
        user_message = _metadata_text(metadata, "user_message")
        return _compact_history_text(user_message, 64) if user_message else label
    return label


def _history_node_summary(*, message: str, metadata: dict[str, object], node_kind: str) -> str:
    explicit = _metadata_text(metadata, "history_node_summary")
    if explicit:
        return explicit
    if node_kind == "chat":
        assistant_message = _metadata_text(metadata, "assistant_message")
        return _compact_history_text(assistant_message, 160) if assistant_message else message
    if node_kind == "restore":
        restored_label = _metadata_text(metadata, "restored_commit_label")
        return f"Restored snapshot from {restored_label}" if restored_label else message
    return message


def _metadata_text(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _compact_history_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _restore_document_from_commit(lesson: Lesson, commit: CommitRecord) -> Lesson:
    lesson.board_document = commit.snapshot
    lesson.board_teaching_guide = None
    lesson.board_teaching_progress = None
    lesson.learning_requirements = None
    lesson.board_task_requirements = None
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
    _restore_document_from_commit(lesson, source_commit)
    return lesson


def switch_branch(lesson: Lesson, branch_name: str) -> Lesson:
    branch = lesson.history_graph.branches[branch_name]
    source_commit = get_commit(lesson, branch.head_commit_id)
    lesson.history_graph.current_branch = branch_name
    _restore_document_from_commit(lesson, source_commit)
    return lesson


def restore_commit(lesson: Lesson, commit_id: str, label: str) -> Lesson:
    commit = get_commit(lesson, commit_id)
    _restore_document_from_commit(lesson, commit)
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
            "active_requirement_sheet_after": None,
            "active_board_task_sheet_after": None,
        },
    )
