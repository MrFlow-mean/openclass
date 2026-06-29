from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    TeachingGuide,
    new_id,
)


def create_test_lesson(
    title: str = "Test page",
    *,
    lesson_id: str | None = None,
    content_text: str = "",
) -> Lesson:
    resolved_lesson_id = lesson_id or new_id("lesson")
    document = BoardDocument(title=title, content_text=content_text)
    initial_commit = CommitRecord(
        label="Initial test document",
        message=f"Created test document for {title}",
        branch_name="main",
        snapshot=document,
        metadata={"kind": "test_initial_document"},
    )
    return Lesson(
        id=resolved_lesson_id,
        title=title,
        slug=resolved_lesson_id,
        summary="",
        board_document=document,
        teaching_guide=TeachingGuide(
            lesson_id=resolved_lesson_id,
            summary="",
            structure_note="",
            pacing="",
            mappings=[],
            strategy="",
        ),
        history_graph=LessonHistoryGraph(
            branches={
                "main": BranchRef(
                    name="main",
                    head_commit_id=initial_commit.id,
                    base_commit_id=initial_commit.id,
                )
            },
            commits=[initial_commit],
            current_branch="main",
        ),
    )


def create_test_requirement_sheet(title: str = "Test page") -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme=title,
        learning_goal="",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )
