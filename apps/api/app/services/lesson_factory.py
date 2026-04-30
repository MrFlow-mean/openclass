from __future__ import annotations

import re

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    ResourceReferenceContext,
    TeachingGuide,
    new_id,
    now_iso,
)
from app.services.rich_document import build_document


def slugify(value: str) -> str:
    lowered = re.sub(r"\s+", "-", value.strip().lower())
    lowered = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", lowered)
    return lowered.strip("-") or new_id("lesson")


def build_requirements(topic: str) -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme=topic,
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


def build_document_for_topic(
    topic: str,
    reference_context: ResourceReferenceContext | None = None,
) -> BoardDocument:
    _ = reference_context
    return build_blank_document(topic)


def build_blank_document(topic: str) -> BoardDocument:
    return build_document(title=topic, content_html="<p></p>")


def _outline_from_document(document: BoardDocument) -> list[str]:
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    headings = [line for line in lines if len(line) <= 42][:8]
    return headings or [document.title]


def build_teaching_guide(
    lesson_id: str,
    title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    _ = title, document, requirements
    return TeachingGuide(
        lesson_id=lesson_id,
        summary="",
        structure_note="",
        pacing="",
        mappings=[],
        strategy="",
    )


def build_lesson(
    topic: str,
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    commit_label: str,
    commit_message: str,
    tags: list[str],
) -> Lesson:
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, topic, document, requirements)
    commit = CommitRecord(
        label=commit_label,
        message=commit_message,
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main",
                head_commit_id=commit.id,
                base_commit_id=commit.id,
            )
        },
        commits=[commit],
        current_branch="main",
    )
    return Lesson(
        id=lesson_id,
        title=topic,
        slug=slugify(topic),
        summary=requirements.learning_goal,
        tags=tags,
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def create_lesson(
    topic: str,
    requirements: LearningRequirementSheet | None = None,
    reference_context: ResourceReferenceContext | None = None,
) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_document_for_topic(topic, reference_context)
    return build_lesson(
        topic,
        document=document,
        requirements=requirements,
        commit_label="Initial blank document",
        commit_message=f"Created empty rich document for {topic}",
        tags=[topic, *requirements.board_scope[:2]],
    )


def create_empty_lesson(topic: str, requirements: LearningRequirementSheet | None = None) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_blank_document(topic)
    return build_lesson(
        topic,
        document=document,
        requirements=requirements,
        commit_label="Initial blank document",
        commit_message=f"Created empty rich document for {topic}",
        tags=[topic],
    )
