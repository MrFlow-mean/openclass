from __future__ import annotations

from app.models import BoardDocument, LearningRequirementSheet, Lesson, ResourceReferenceContext
from app.services.lesson_factory import (
    build_requirements,
    build_teaching_guide,
    create_empty_lesson,
    create_lesson,
)


def normalize_requirements(
    requirements: LearningRequirementSheet,
    *,
    lesson_title: str,
    document: BoardDocument,
) -> LearningRequirementSheet:
    normalized = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    default_requirements = build_requirements(lesson_title)
    if not normalized.theme.strip():
        normalized.theme = lesson_title
    legacy_questions = [
        f"“{lesson_title}”的核心问题是什么",
        "它包含哪些关键概念、步骤或例子",
        "学习后如何检查是否真正理解",
    ]
    legacy_defaults = {
        "learning_goal": f"围绕“{lesson_title}”建立可讲授、可复习、可练习的结构化讲义",
        "level": "根据用户背景和资料难度动态调整",
        "known_background": "用户背景尚未完全明确，先采用循序渐进的讲解方式",
        "target_depth": "能复述核心内容，并能用例子解释或完成基础练习",
        "success_criteria": "用户能说清主线、解释关键概念，并完成至少一个检查问题",
    }
    for field_name, legacy_value in legacy_defaults.items():
        if getattr(normalized, field_name) == legacy_value:
            setattr(normalized, field_name, getattr(default_requirements, field_name))
    if normalized.current_questions == legacy_questions:
        normalized.current_questions = list(default_requirements.current_questions)
    normalized.board_scope = []
    if not normalized.current_questions and normalized.action_type is None:
        normalized.current_questions = [f"如何理解 {normalized.theme or lesson_title}"]
    return normalized


def effective_requirements(lesson: Lesson) -> LearningRequirementSheet:
    base = lesson.learning_requirements or build_requirements(lesson.title)
    return normalize_requirements(base, lesson_title=lesson.title, document=lesson.board_document)


def active_task_requirements(lesson: Lesson) -> LearningRequirementSheet | None:
    if lesson.learning_requirements is None:
        return None
    return normalize_requirements(
        lesson.learning_requirements,
        lesson_title=lesson.title,
        document=lesson.board_document,
    )


def visible_lesson_summary(requirements: LearningRequirementSheet, *, lesson_title: str) -> str:
    default_requirements = build_requirements(lesson_title)
    is_default_empty_state = (
        requirements.learning_goal == default_requirements.learning_goal
        and not requirements.learning_need_checklist
        and requirements.action_type is None
    )
    return "" if is_default_empty_state else requirements.learning_goal


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
    should_persist_requirements = requirements is not None or lesson.learning_requirements is not None
    normalized = normalize_requirements(
        requirements or effective_requirements(lesson),
        lesson_title=lesson.title,
        document=current_document,
    )
    lesson.board_document = current_document
    lesson.learning_requirements = normalized if should_persist_requirements else None
    lesson.teaching_guide = build_internal_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=current_document,
        requirements=normalized,
    )
    lesson.summary = visible_lesson_summary(normalized, lesson_title=lesson.title)
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
