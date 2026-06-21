from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import (
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services import turn_intent
from app.services.learning_requirement_manager import is_generation_control_request
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import is_document_empty


TextTriggeredGenerationTrigger = Literal["document_artifact_request", "generation_control_request"]


@dataclass(frozen=True)
class TextTriggeredGenerationRequest:
    trigger: TextTriggeredGenerationTrigger
    reason: str


def classify_text_triggered_generation_request(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
) -> TextTriggeredGenerationRequest | None:
    if request.board_generation_action == "start":
        return None
    if request.resource_reference_action is not None:
        return None
    if resource_resolution.reference_prompt is not None:
        return None
    if not is_document_empty(lesson.board_document):
        return None
    if turn_intent.wants_document_artifact_generation(request.message):
        return TextTriggeredGenerationRequest(
            trigger="document_artifact_request",
            reason="Blank-board text asks for a document-like learning artifact.",
        )
    if is_generation_control_request(request.message) and _has_actionable_generation_context(
        requirements,
        learning_clarification,
    ):
        return TextTriggeredGenerationRequest(
            trigger="generation_control_request",
            reason="Blank-board text asks to proceed using an existing actionable requirement context.",
        )
    return None


def _has_actionable_generation_context(
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    if requirements.action_type == "generate_board" and requirements.action_instruction.strip():
        return True
    return any(
        fact.value.strip() and fact.category in {"learning", "level", "vocabulary", "scenario", "output"}
        for fact in learning_clarification.key_facts
    )
