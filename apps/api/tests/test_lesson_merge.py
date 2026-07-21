from __future__ import annotations

from app.models import AIModelSelection, LearningSourceReference, LessonMergeConflictResolution
from app.services.ai_execution_adapter import StructuredExecutionResult
from app.services import codex_chat
from app.services.history import commit_operations, create_branch, current_head_commit, switch_branch
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.lesson_merge import (
    LessonMergeConflictError,
    LessonMergeStaleError,
    create_merge_session,
    submit_merge_session,
    update_merge_session,
)
from app.services.lesson_merge_ai import propose_ai_merge
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


def test_frozen_source_references_union_independent_additions_and_conflict_on_hash() -> None:
    lesson = create_empty_lesson("Source merge")
    base_requirements = build_requirements("Source merge")
    base_requirements.source_grounding.confirmed_references = [
        LearningSourceReference(
            evidence_bundle_id="bundle_base",
            source_ingestion_id="source_shared",
            source_chapter_id="chapter_shared",
            content_hash="base_hash",
        )
    ]
    lesson.learning_requirements = base_requirements
    base_id = _commit_text(lesson, "# Topic\n\nBody", "Base sources")

    create_branch(lesson, "source", base_id)
    source_requirements = base_requirements.model_copy(deep=True)
    source_requirements.source_grounding.confirmed_references[0].content_hash = "source_hash"
    source_requirements.source_grounding.confirmed_references.append(
        LearningSourceReference(
            evidence_bundle_id="bundle_source",
            source_ingestion_id="source_only",
            source_chapter_id="chapter_source",
            content_hash="source_only_hash",
        )
    )
    lesson.learning_requirements = source_requirements
    _commit_text(lesson, "# Topic\n\nBody", "Source references")

    switch_branch(lesson, "main")
    target_requirements = base_requirements.model_copy(deep=True)
    target_requirements.source_grounding.confirmed_references[0].content_hash = "target_hash"
    target_requirements.source_grounding.confirmed_references.append(
        LearningSourceReference(
            evidence_bundle_id="bundle_target",
            source_ingestion_id="target_only",
            source_chapter_id="chapter_target",
            content_hash="target_only_hash",
        )
    )
    lesson.learning_requirements = target_requirements
    _commit_text(lesson, "# Topic\n\nBody", "Target references")

    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )
    conflict = next(item for item in session.conflicts if item.kind == "source_reference")
    assert conflict.path == "learning_requirements.source_grounding"

    update_merge_session(
        session,
        expected_version=session.version,
        resolutions=[
            LessonMergeConflictResolution(
                conflict_id=conflict.id,
                resolution="target",
            )
        ],
    )
    references = session.draft_runtime.learning_requirements.source_grounding.confirmed_references
    references_by_source = {item.source_ingestion_id: item for item in references}
    assert set(references_by_source) == {"source_shared", "source_only", "target_only"}
    assert references_by_source["source_shared"].content_hash == "target_hash"


def test_ai_merge_resolves_only_explicit_conflicts_and_records_model() -> None:
    lesson, _, _, _ = _divergent_lesson(
        target_text="# Topic\n\nAlpha target\n\nBeta",
        source_text="# Topic\n\nAlpha source\n\nBeta",
    )
    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
        mode="ai",
        ai_model=AIModelSelection(
            provider="openai_codex",
            model="gpt-test",
            reasoning_effort="high",
        ),
    )
    conflict_id = session.conflicts[0].id

    class FakeAdapter:
        def parse_structured(self, **kwargs):
            return StructuredExecutionResult(
                output_parsed=kwargs["schema"].model_validate(
                    {
                        "decisions": [
                            {
                                "conflict_id": conflict_id,
                                "resolution": "source",
                                "explanation": "Source keeps the intended revision.",
                            }
                        ]
                    }
                )
            )

    propose_ai_merge(
        session,
        expected_version=session.version,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )

    assert session.status == "ready"
    assert "Alpha source" in session.draft_document.content_text
    assert session.audit["ai_proposal"]["model"] == "gpt-test"
    assert session.audit["ai_proposal"]["reasoning_effort"] == "high"


def test_merge_commit_resets_ai_thread_and_builds_labeled_handoff_context() -> None:
    lesson, _, _, _ = _divergent_lesson(
        target_text="# Topic\n\nTarget",
        source_text="# Topic\n\nSource",
    )
    source_commit = next(
        commit for commit in lesson.history_graph.commits if commit.label == "Source edit"
    )
    source_commit.metadata.update(
        {
            "kind": "basic_chat",
            "user_message": "Source question",
            "assistant_message": "Source answer",
            "codex_thread_id": "source_thread",
        }
    )
    target_commit = current_head_commit(lesson)
    target_commit.metadata.update(
        {
            "kind": "basic_chat",
            "user_message": "Target question",
            "assistant_message": "Target answer",
            "codex_thread_id": "target_thread",
        }
    )
    session = create_merge_session(
        lesson,
        owner_user_id="user_1",
        source_branch_name="source",
    )
    for conflict in session.conflicts:
        conflict.resolved = True
        conflict.resolution = "target"
    submit_merge_session(lesson, session, expected_version=session.version)

    assert codex_chat._thread_reference_for_current_branch(lesson) == (None, None)
    handoff = codex_chat._merge_handoff_context(lesson)
    assert "Target question" in handoff
    assert "Source question" in handoff
    assert "independent labeled lineages" in handoff

    commit_operations(
        lesson,
        [],
        label="Post-merge conversation",
        message="New merged thread",
        metadata={
            "kind": "basic_chat",
            "codex_thread_id": "merged_thread",
            "codex_turn_id": "merged_turn",
        },
    )
    assert codex_chat._thread_reference_for_current_branch(lesson) == (
        "merged_thread",
        "merged_turn",
    )
