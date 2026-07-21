from __future__ import annotations

from app.models import LessonMergeConflictResolution
from app.services.history import commit_operations, create_branch, current_head_commit, switch_branch
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.lesson_merge import (
    LessonMergeConflictError,
    LessonMergeStaleError,
    create_merge_session,
    submit_merge_session,
    update_merge_session,
)
from app.services.rich_document import build_document


def _commit_text(lesson, text: str, label: str) -> str:
    document = build_document(
        title=lesson.board_document.title,
        content_text=text,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    commit_operations(
        lesson,
        [],
        label=label,
        message=label,
        new_document=document,
        metadata={"kind": "manual_document_save"},
    )
    return current_head_commit(lesson).id


def _divergent_lesson(*, target_text: str, source_text: str):
    lesson = create_empty_lesson("Merge")
    base_id = _commit_text(lesson, "# Topic\n\nAlpha\n\nBeta", "Base")
    create_branch(lesson, "source", base_id)
    source_head_id = _commit_text(lesson, source_text, "Source edit")
    switch_branch(lesson, "main")
    target_head_id = _commit_text(lesson, target_text, "Target edit")
    return lesson, base_id, target_head_id, source_head_id


def test_non_conflicting_board_changes_merge_into_double_parent_commit() -> None:
    lesson, base_id, target_head_id, source_head_id = _divergent_lesson(
        target_text="# Topic\n\nAlpha target\n\nBeta",
        source_text="# Topic\n\nAlpha\n\nBeta source",
    )

    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )

    assert session.base_commit_id == base_id
    assert session.target_head_commit_id == target_head_id
    assert session.source_head_commit_id == source_head_id
    assert session.conflicts == []
    assert "Alpha target" in session.draft_document.content_text
    assert "Beta source" in session.draft_document.content_text

    commit = submit_merge_session(lesson, session, expected_version=session.version)

    assert commit.parent_ids == [target_head_id, source_head_id]
    assert commit.metadata["kind"] == "branch_merge"
    assert commit.metadata["reset_codex_thread"] is True
    assert lesson.history_graph.branches["source"].head_commit_id == source_head_id
    assert lesson.history_graph.branches["main"].head_commit_id == commit.id


def test_conflicting_board_change_can_use_source_then_be_edited_before_submit() -> None:
    lesson, _, _, _ = _divergent_lesson(
        target_text="# Topic\n\nAlpha target\n\nBeta",
        source_text="# Topic\n\nAlpha source\n\nBeta",
    )
    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )
    board_conflicts = [item for item in session.conflicts if item.kind == "board"]
    assert len(board_conflicts) == 1

    update_merge_session(
        session,
        expected_version=session.version,
        resolutions=[
            LessonMergeConflictResolution(
                conflict_id=board_conflicts[0].id,
                resolution="source",
            )
        ],
    )
    assert "Alpha source" in session.draft_document.content_text

    edited = build_document(
        title=session.draft_document.title,
        content_text="# Topic\n\nAlpha final manual edit\n\nBeta",
        document_id=session.draft_document.id,
        page_settings=session.draft_document.page_settings,
    )
    update_merge_session(
        session,
        expected_version=session.version,
        draft_document=edited,
    )
    commit = submit_merge_session(lesson, session, expected_version=session.version)

    assert "Alpha final manual edit" in commit.snapshot.content_text
    assert session.status == "committed"


def test_unresolved_conflict_and_changed_heads_block_submit() -> None:
    lesson, _, _, _ = _divergent_lesson(
        target_text="# Topic\n\nAlpha target\n\nBeta",
        source_text="# Topic\n\nAlpha source\n\nBeta",
    )
    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )
    try:
        submit_merge_session(lesson, session, expected_version=session.version)
    except LessonMergeConflictError:
        pass
    else:
        raise AssertionError("unresolved merge conflict should block submit")

    for conflict in session.conflicts:
        conflict.resolved = True
        conflict.resolution = "target"
    _commit_text(lesson, "# Topic\n\nAlpha newer\n\nBeta", "New target edit")
    try:
        submit_merge_session(lesson, session, expected_version=session.version)
    except LessonMergeStaleError:
        pass
    else:
        raise AssertionError("changed branch head should block submit")
    assert session.status == "stale"


def test_runtime_fields_form_conflicts_and_can_choose_source() -> None:
    lesson = create_empty_lesson("Runtime merge")
    base_requirements = build_requirements("Runtime merge")
    base_requirements.current_level = "base"
    lesson.learning_requirements = base_requirements
    base_id = _commit_text(lesson, "# Topic\n\nBody", "Base")

    create_branch(lesson, "source", base_id)
    source_requirements = base_requirements.model_copy(deep=True)
    source_requirements.current_level = "source level"
    lesson.learning_requirements = source_requirements
    _commit_text(lesson, "# Topic\n\nBody", "Source runtime")

    switch_branch(lesson, "main")
    target_requirements = base_requirements.model_copy(deep=True)
    target_requirements.current_level = "target level"
    lesson.learning_requirements = target_requirements
    _commit_text(lesson, "# Topic\n\nBody", "Target runtime")

    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )
    conflict = next(item for item in session.conflicts if item.path == "learning_requirements.current_level")
    update_merge_session(
        session,
        expected_version=session.version,
        resolutions=[
            LessonMergeConflictResolution(
                conflict_id=conflict.id,
                resolution="source",
            )
        ],
    )
    for remaining in session.conflicts:
        if not remaining.resolved:
            remaining.resolved = True
            remaining.resolution = "target"
    commit = submit_merge_session(lesson, session, expected_version=session.version)

    assert commit.runtime_snapshot is not None
    assert commit.runtime_snapshot.learning_requirements is not None
    assert commit.runtime_snapshot.learning_requirements.current_level == "source level"
