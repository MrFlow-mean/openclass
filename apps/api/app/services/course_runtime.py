from __future__ import annotations

from app.models import BoardDocument, LearningRequirementSheet, Lesson, new_id
from app.services.lesson_factory import build_requirements, build_teaching_guide, create_lesson
from app.services.openai_course_ai import build_generated_lesson, openai_course_ai


def normalize_requirements(
    requirements: LearningRequirementSheet,
    *,
    lesson_title: str,
    document: BoardDocument,
) -> LearningRequirementSheet:
    normalized = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    normalized.theme = lesson_title
    normalized.board_scope = [block.title for block in document.blocks[:6]]
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
    return openai_course_ai.generate_teaching_guide(
        lesson_id=lesson_id,
        lesson_title=lesson_title,
        requirements=normalized,
        document=document,
    ) or build_teaching_guide(lesson_id, lesson_title, document, normalized)


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
) -> Lesson:
    generated = openai_course_ai.generate_lesson_document(topic=topic)
    if generated is None:
        return create_lesson(topic, requirements=requirements)

    document = BoardDocument(title=generated.title, blocks=generated.blocks)
    if requirements is None:
        normalized_requirements = openai_course_ai.generate_learning_requirements(
            lesson_title=generated.title,
            lesson_summary=generated.summary,
            lesson_tags=generated.tags,
            block_titles=[block.title for block in generated.blocks],
            user_message=f"我想学习 {topic}",
            selection_excerpt=None,
        ) or build_requirements(topic)
    else:
        normalized_requirements = requirements

    normalized_requirements = normalize_requirements(
        normalized_requirements,
        lesson_title=generated.title,
        document=document,
    )
    guide = build_internal_teaching_guide(
        lesson_id=new_id("lesson"),
        lesson_title=generated.title,
        document=document,
        requirements=normalized_requirements,
    )
    lesson = build_generated_lesson(
        topic=topic,
        generated=generated,
        requirements=normalized_requirements,
        guide_template=guide,
    )
    lesson.learning_requirements = normalized_requirements
    lesson.summary = normalized_requirements.learning_goal
    return lesson
