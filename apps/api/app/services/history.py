from __future__ import annotations

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    InteractionSession,
    LearningRequirementSheet,
    Lesson,
    MergeBranchChoice,
    MergeBranchPreviewResponse,
    MergeBranchSectionPreview,
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


def runtime_state_at_commit(
    lesson: Lesson,
    commit_id: str,
) -> tuple[LearningRequirementSheet | None, InteractionSession | None]:
    requirements: LearningRequirementSheet | None = None
    session: InteractionSession | None = None
    for commit in _primary_lineage(lesson, commit_id):
        if not isinstance(commit.metadata, dict):
            continue
        if "active_requirement_sheet_after" in commit.metadata:
            raw_requirements = commit.metadata.get("active_requirement_sheet_after")
            requirements = (
                LearningRequirementSheet.model_validate(raw_requirements)
                if isinstance(raw_requirements, dict)
                else None
            )
        if "active_interaction_session_after" in commit.metadata:
            raw_session = commit.metadata.get("active_interaction_session_after")
            session = InteractionSession.model_validate(raw_session) if isinstance(raw_session, dict) else None
    return requirements, session


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


def build_merge_preview(
    lesson: Lesson,
    source_branch: str,
    target_branch: str | None = None,
) -> MergeBranchPreviewResponse:
    target_branch_name = target_branch or lesson.history_graph.current_branch
    target_ref = _get_branch(lesson, target_branch_name)
    source_ref = _get_branch(lesson, source_branch)
    target_head = get_commit(lesson, target_ref.head_commit_id)
    source_head = get_commit(lesson, source_ref.head_commit_id)
    base_commit = _nearest_common_ancestor(lesson, target_head.id, source_head.id)
    target_requirements, target_session = runtime_state_at_commit(lesson, target_head.id)
    source_requirements, source_session = runtime_state_at_commit(lesson, source_head.id)
    base_requirements, base_session = runtime_state_at_commit(lesson, base_commit.id)
    already_merged = source_head.id == target_head.id or _is_ancestor(lesson, source_head.id, target_head.id)

    return MergeBranchPreviewResponse(
        source_branch=source_branch,
        target_branch=target_branch_name,
        base_commit_id=base_commit.id,
        target_head_commit_id=target_head.id,
        source_head_commit_id=source_head.id,
        can_merge=not already_merged and source_branch != target_branch_name,
        already_merged=already_merged or source_branch == target_branch_name,
        document=_section_preview(
            base_commit.snapshot,
            target_head.snapshot,
            source_head.snapshot,
            summary_fn=_document_summary,
        ),
        requirements=_section_preview(
            base_requirements,
            target_requirements,
            source_requirements,
            summary_fn=_requirement_summary,
        ),
        session=_section_preview(
            base_session,
            target_session,
            source_session,
            summary_fn=_session_summary,
        ),
    )


def merge_branch(
    lesson: Lesson,
    *,
    source_branch: str,
    target_branch: str | None,
    expected_target_head_commit_id: str,
    expected_source_head_commit_id: str,
    document_choice: MergeBranchChoice,
    requirements_choice: MergeBranchChoice,
    session_choice: MergeBranchChoice,
) -> Lesson:
    preview = build_merge_preview(lesson, source_branch=source_branch, target_branch=target_branch)
    if not preview.can_merge:
        raise ValueError("Source branch is already merged or matches the target branch")
    if preview.target_head_commit_id != expected_target_head_commit_id:
        raise ValueError("Target branch changed after merge preview")
    if preview.source_head_commit_id != expected_source_head_commit_id:
        raise ValueError("Source branch changed after merge preview")

    target_head = get_commit(lesson, preview.target_head_commit_id)
    source_head = get_commit(lesson, preview.source_head_commit_id)
    target_requirements, target_session = runtime_state_at_commit(lesson, target_head.id)
    source_requirements, source_session = runtime_state_at_commit(lesson, source_head.id)
    next_document = _choose(document_choice, target_head.snapshot, source_head.snapshot)
    next_requirements = _choose(requirements_choice, target_requirements, source_requirements)
    next_session = _choose(session_choice, target_session, source_session)

    lesson.history_graph.current_branch = preview.target_branch
    lesson.board_document = next_document
    lesson.learning_requirements = next_requirements
    lesson.active_interaction_session = next_session
    commit = CommitRecord(
        label=f"Merge {preview.source_branch}",
        message=f"Merged {preview.source_branch} into {preview.target_branch}",
        branch_name=preview.target_branch,
        parent_ids=[target_head.id, source_head.id],
        operations=[],
        snapshot=next_document,
        metadata={
            "kind": "branch_merge",
            "source_branch": preview.source_branch,
            "target_branch": preview.target_branch,
            "base_commit_id": preview.base_commit_id,
            "target_head_commit_id": target_head.id,
            "source_head_commit_id": source_head.id,
            "document_choice": document_choice,
            "requirements_choice": requirements_choice,
            "session_choice": session_choice,
            "document_status": preview.document.status,
            "requirements_status": preview.requirements.status,
            "session_status": preview.session.status,
            "active_requirement_sheet_after": (
                next_requirements.model_dump(mode="json") if next_requirements is not None else None
            ),
            "active_interaction_session_after": (
                next_session.model_dump(mode="json") if next_session is not None else None
            ),
        },
    )
    lesson.history_graph.commits.append(commit)
    lesson.history_graph.branches[preview.target_branch].head_commit_id = commit.id
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


def _get_branch(lesson: Lesson, branch_name: str) -> BranchRef:
    try:
        return lesson.history_graph.branches[branch_name]
    except KeyError as exc:
        raise ValueError(f"Unknown branch {branch_name}") from exc


def _commits_by_id(lesson: Lesson) -> dict[str, CommitRecord]:
    return {commit.id: commit for commit in lesson.history_graph.commits}


def _primary_lineage(lesson: Lesson, commit_id: str) -> list[CommitRecord]:
    commits_by_id = _commits_by_id(lesson)
    lineage: list[CommitRecord] = []
    next_commit = commits_by_id.get(commit_id)
    while next_commit is not None:
        lineage.append(next_commit)
        parent_id = next_commit.parent_ids[0] if next_commit.parent_ids else None
        next_commit = commits_by_id.get(parent_id) if parent_id else None
    return list(reversed(lineage))


def _ancestor_depths(lesson: Lesson, commit_id: str) -> dict[str, int]:
    commits_by_id = _commits_by_id(lesson)
    depths: dict[str, int] = {}
    stack: list[tuple[str, int]] = [(commit_id, 0)]
    while stack:
        next_id, depth = stack.pop()
        previous_depth = depths.get(next_id)
        if previous_depth is not None and previous_depth <= depth:
            continue
        depths[next_id] = depth
        commit = commits_by_id.get(next_id)
        if commit is None:
            continue
        stack.extend((parent_id, depth + 1) for parent_id in commit.parent_ids)
    return depths


def _is_ancestor(lesson: Lesson, ancestor_id: str, descendant_id: str) -> bool:
    return ancestor_id in _ancestor_depths(lesson, descendant_id)


def _nearest_common_ancestor(lesson: Lesson, left_commit_id: str, right_commit_id: str) -> CommitRecord:
    left_depths = _ancestor_depths(lesson, left_commit_id)
    right_depths = _ancestor_depths(lesson, right_commit_id)
    common_ids = set(left_depths).intersection(right_depths)
    if not common_ids:
        raise ValueError("Branches do not share a common commit")
    commit_order = {commit.id: index for index, commit in enumerate(lesson.history_graph.commits)}
    ancestor_id = min(
        common_ids,
        key=lambda commit_id: (
            max(left_depths[commit_id], right_depths[commit_id]),
            left_depths[commit_id] + right_depths[commit_id],
            -commit_order.get(commit_id, -1),
        ),
    )
    return get_commit(lesson, ancestor_id)


def _signature(value: object) -> object:
    if isinstance(value, (BoardDocument, LearningRequirementSheet, InteractionSession)):
        return value.model_dump(mode="json")
    return None


def _section_preview(
    base_value: object,
    target_value: object,
    source_value: object,
    *,
    summary_fn,
) -> MergeBranchSectionPreview:
    base_signature = _signature(base_value)
    target_signature = _signature(target_value)
    source_signature = _signature(source_value)
    target_changed = target_signature != base_signature
    source_changed = source_signature != base_signature
    if target_signature == source_signature:
        status = "no_change"
        recommended_choice: MergeBranchChoice = "target"
    elif source_changed and not target_changed:
        status = "source_only"
        recommended_choice = "source"
    elif target_changed and not source_changed:
        status = "target_only"
        recommended_choice = "target"
    else:
        status = "conflict"
        recommended_choice = "target"
    return MergeBranchSectionPreview(
        status=status,
        recommended_choice=recommended_choice,
        requires_confirmation=status == "conflict",
        base_summary=summary_fn(base_value),
        target_summary=summary_fn(target_value),
        source_summary=summary_fn(source_value),
    )


def _choose(choice: MergeBranchChoice, target_value, source_value):
    return source_value if choice == "source" else target_value


def _document_summary(value: object) -> str:
    if not isinstance(value, BoardDocument):
        return "无文档"
    text = value.content_text.strip() or value.title.strip()
    return _compact(text, 120) or "空白文档"


def _requirement_summary(value: object) -> str:
    if not isinstance(value, LearningRequirementSheet):
        return "无需求状态"
    text = value.learning_goal.strip() or value.theme.strip()
    return _compact(text, 120) or "空需求状态"


def _session_summary(value: object) -> str:
    if not isinstance(value, InteractionSession):
        return "无聊天 session"
    text = " ".join(
        str(part)
        for part in [value.status, value.interaction_goal, value.progress_note]
        if part
    )
    return _compact(text, 120) or "有聊天 session"


def _compact(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"
