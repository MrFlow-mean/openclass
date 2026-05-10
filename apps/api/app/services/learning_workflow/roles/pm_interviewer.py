from __future__ import annotations

from typing import Any

from app.models import ConversationTurn, LearningRequirementSheet
from app.services.openai_course_ai import openai_course_ai


def generate_pm_interview_message(
    *,
    lesson_title: str,
    request_message: str,
    requirements: LearningRequirementSheet,
    learning_clarification: dict[str, Any],
    clarification_questions: list[str],
    conversation: list[ConversationTurn],
) -> str | None:
    """PM AI interviews the learner; it never edits structured learning state."""
    return openai_course_ai.generate_clarification_message(
        lesson_title=lesson_title,
        request_message=request_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        clarification_questions=clarification_questions,
        conversation=[turn.model_dump(mode="json") for turn in conversation],
    )
