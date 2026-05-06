from __future__ import annotations

from app.models import BoardDocument, LearningRequirementSheet, Lesson, ResourceReferenceContext
from app.services.lesson_factory import (
    build_requirements,
    build_teaching_guide,
    create_empty_lesson,
)

LEGACY_DEFAULT_REQUIREMENT_VALUES = {
    "learning_goal": {"理解概念、能跟着连续讲义讲清楚并完成基础练习"},
    "level": {"初学到进阶之间"},
    "known_background": {"已有零散印象，但需要结构化讲解"},
    "target_depth": {"先做到能讲清基本定义并会做入门题"},
    "output_preference": {"Word 式连续讲义：定义、直觉、例题、练习、总结"},
    "boundary": {"先不无限展开相邻学科的更大知识域", "优先围绕当前 lesson 的整篇文档主线；超出范围时先决定是仅讲解、补充章节还是新开 lesson。"},
    "success_criteria": {"用户能复述核心概念并完成一题相关练习"},
}


def _document_outline(document: BoardDocument) -> list[str]:
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    return [line for line in lines if len(line) <= 44][:8] or [document.title]


def _clear_legacy_default_requirements(requirements: LearningRequirementSheet) -> None:
    for field_name, legacy_values in LEGACY_DEFAULT_REQUIREMENT_VALUES.items():
        if getattr(requirements, field_name) in legacy_values:
            setattr(requirements, field_name, "")
    default_questions = {
        f"{requirements.theme}的定义是什么",
        f"{requirements.theme} 的定义是什么",
        "它为什么重要",
        "应该怎么用",
    }
    if requirements.current_questions and all(question in default_questions for question in requirements.current_questions):
        requirements.current_questions = []


def normalize_requirements(
    requirements: LearningRequirementSheet,
    *,
    lesson_title: str,
    document: BoardDocument,
) -> LearningRequirementSheet:
    normalized = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    _clear_legacy_default_requirements(normalized)
    normalized.theme = normalized.theme.strip() or lesson_title
    normalized.board_scope = _document_outline(document)
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
    _ = reference_context
    return create_empty_lesson(topic, requirements=requirements)
