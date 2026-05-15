from __future__ import annotations

from app.models import BoardDocument, LearningRequirementSheet, Lesson, ResourceReferenceContext
from app.services.lesson_factory import (
    build_requirements,
    build_teaching_guide,
    create_empty_lesson,
    create_lesson,
)


def _document_outline(document: BoardDocument) -> list[str]:
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    return [line for line in lines if len(line) <= 44][:8] or [document.title]


def normalize_requirements(
    requirements: LearningRequirementSheet,
    *,
    lesson_title: str,
    document: BoardDocument,
) -> LearningRequirementSheet:
    normalized = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    normalized.theme = lesson_title
    normalized.board_scope = _document_outline(document)
    if not normalized.current_questions:
        normalized.current_questions = [f"如何理解 {lesson_title}"]
    return normalized


def effective_requirements(lesson: Lesson) -> LearningRequirementSheet:
    base = lesson.learning_requirements or build_requirements(lesson.title)
    return normalize_requirements(base, lesson_title=lesson.title, document=lesson.board_document)


def build_internal_teaching_guide(
    *,
    lesson_id: str,
    lesson_title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
):
    normalized = normalize_requirements(requirements, lesson_title=lesson_title, document=document)
    return build_teaching_guide(lesson_id, lesson_title, document, normalized)


def refresh_lesson_runtime(
    lesson: Lesson,
    *,
    document: BoardDocument | None = None,
    requirements: LearningRequirementSheet | None = None,
) -> Lesson:
    current_document = document or lesson.board_document
    normalized = normalize_requirements(
        requirements or effective_requirements(lesson),
        lesson_title=lesson.title,
        document=current_document,
    )
    lesson.board_document = current_document
    lesson.learning_requirements = normalized
    lesson.teaching_guide = build_internal_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=current_document,
        requirements=normalized,
    )
    lesson.summary = normalized.learning_goal
    return lesson


def build_lesson_for_topic(
    topic: str,
    *,
    requirements: LearningRequirementSheet | None = None,
    reference_context: ResourceReferenceContext | None = None,
) -> Lesson:
    if requirements is None:
        if reference_context is None:
            lesson = create_empty_lesson(topic)
        else:
            lesson = create_lesson(topic, reference_context=reference_context)
    else:
        lesson = create_lesson(topic, requirements=requirements, reference_context=reference_context)
    refresh_lesson_runtime(lesson, requirements=requirements)
    return lesson
