from __future__ import annotations

import re

from app.models import (
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services.course_runtime import effective_requirements
from app.services.openai_course_ai import LearningRequirementUpdate, openai_course_ai


MAX_CONTEXT_CHARS = 1800
MAX_TURNS = 10


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _board_summary(lesson: Lesson) -> str:
    return _compact_text(lesson.board_document.content_text, limit=MAX_CONTEXT_CHARS) or lesson.board_document.title


def _resource_summary(resources: list[ResourceLibraryItem]) -> str:
    lines: list[str] = []
    for resource in resources[:6]:
        chapter_titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        lines.append(f"{resource.name}: {' / '.join(chapter_titles)}" if chapter_titles else resource.name)
    return "\n".join(lines)


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    return "\n".join(
        f"{turn.role}: {_compact_text(turn.content, limit=500)}"
        for turn in conversation[-MAX_TURNS:]
        if turn.content.strip()
    )


def _has_substantive_learning_signal(text: str) -> bool:
    compact = _compact_text(text, limit=240)
    if len(compact) >= 18:
        return True
    if re.search(r"(想学|学习|复习|练习|理解|掌握|讲义|板书|解释|准备)", compact):
        return True
    return any(char in compact for char in "？?，,：:")


def _fallback_update(*, lesson: Lesson, conversation: list[ConversationTurn]) -> LearningRequirementUpdate:
    latest_user = next((turn.content for turn in reversed(conversation) if turn.role == "user"), "")
    if not _has_substantive_learning_signal(latest_user):
        return LearningRequirementUpdate(
            progress=15,
            summary="用户还没有透露足够具体的学习需求。",
            checklist=[
                LearningRequirementChecklistItem(
                    title="明确一个具体学习目标",
                    is_clear=False,
                    evidence="最近对话还没有说明要围绕哪个主题、资料或问题学习。",
                )
            ],
            missing_items=["具体学习目标"],
            next_question="你想围绕哪个主题、资料或具体问题开始学习？",
            ready_for_board=False,
        )

    compact_goal = _compact_text(latest_user, limit=160)
    return LearningRequirementUpdate(
        progress=55,
        summary=f"用户提出了一个待整理的学习请求：{compact_goal}",
        checklist=[
            LearningRequirementChecklistItem(
                title="捕捉到一条可继续澄清的学习请求",
                is_clear=True,
                evidence=compact_goal,
            ),
            LearningRequirementChecklistItem(
                title="补充生成板书所需的关键约束",
                is_clear=False,
                evidence="对话中还没有足够信息说明期望深度、使用方式或输出偏好。",
            ),
        ],
        missing_items=["生成板书前仍需补充关键约束"],
        next_question="你希望这份内容更偏讲解、复习、练习，还是解决某个具体问题？",
        ready_for_board=False,
    )


def _normalize_update(update: LearningRequirementUpdate) -> LearningRequirementUpdate:
    checklist = [
        LearningRequirementChecklistItem(
            title=_compact_text(item.title, limit=80),
            is_clear=item.is_clear,
            evidence=_compact_text(item.evidence, limit=160),
        )
        for item in update.checklist
        if item.title.strip()
    ][:5]
    if not checklist:
        checklist = [
            LearningRequirementChecklistItem(
                title="明确一个具体学习目标",
                is_clear=False,
                evidence="需求管理 AI 没有提取到可展示的清单项。",
            )
        ]

    ready = update.ready_for_board
    progress = max(0, min(100, update.progress))
    if ready:
        progress = 100
    elif progress >= 100:
        progress = 99

    return LearningRequirementUpdate(
        progress=progress,
        summary=_compact_text(update.summary, limit=220),
        checklist=checklist,
        missing_items=[_compact_text(item, limit=80) for item in update.missing_items if item.strip()][:5],
        next_question=_compact_text(update.next_question, limit=160),
        ready_for_board=ready,
    )


def _apply_update_to_requirements(
    requirements: LearningRequirementSheet,
    update: LearningRequirementUpdate,
) -> LearningRequirementSheet:
    updated = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    if update.summary:
        updated.learning_goal = update.summary
    updated.learning_need_checklist = [item.title for item in update.checklist]
    updated.current_questions = [update.next_question] if update.next_question else updated.current_questions
    updated.risk_notes = update.missing_items
    return updated


def _clarification_from_update(update: LearningRequirementUpdate) -> LearningClarificationStatus:
    ready = update.ready_for_board
    return LearningClarificationStatus(
        progress=update.progress,
        label="需求已清晰" if ready else "继续澄清",
        reason=update.summary,
        missing_items=update.missing_items,
        can_start=ready,
        forced_start=False,
        summary=update.summary,
        checklist=update.checklist,
        next_question=update.next_question,
        ready_for_board=ready,
    )


def update_learning_requirements_from_chat(
    *,
    lesson: Lesson,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    user_message: str,
    teacher_message: str,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus]:
    requirements = effective_requirements(lesson)
    existing_checklist = list(requirements.learning_need_checklist)
    ai_update = openai_course_ai.generate_learning_requirement_update(
        lesson_title=lesson.title,
        existing_summary=requirements.learning_goal,
        existing_checklist=existing_checklist,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=user_message,
        teacher_message=teacher_message,
    )
    update = _normalize_update(ai_update or _fallback_update(lesson=lesson, conversation=conversation))
    updated_requirements = _apply_update_to_requirements(requirements, update)
    return updated_requirements, _clarification_from_update(update)
