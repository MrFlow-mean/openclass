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
TextTriggeredGenerationTerminal = Literal["text_triggered_initial_generation"]


@dataclass(frozen=True)
class TextTriggeredGenerationRequest:
    trigger: TextTriggeredGenerationTrigger
    reason: str


@dataclass(frozen=True)
class TextTriggeredGenerationTerminalCandidate:
    terminal: TextTriggeredGenerationTerminal
    request: TextTriggeredGenerationRequest
    priority: int
    reason: str


def text_triggered_generation_terminal_candidates(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
) -> tuple[TextTriggeredGenerationTerminalCandidate, ...]:
    generation_request = classify_text_triggered_generation_request(
        lesson=lesson,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_resolution=resource_resolution,
    )
    if generation_request is None:
        return ()
    return (
        TextTriggeredGenerationTerminalCandidate(
            terminal="text_triggered_initial_generation",
            request=generation_request,
            priority=60,
            reason=generation_request.reason,
        ),
    )


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
    if resource_resolution.reference_prompt is not None or resource_resolution.has_reference:
        return None
    if not is_document_empty(lesson.board_document):
        return None

    if turn_intent.wants_document_artifact_generation(request.message):
        return TextTriggeredGenerationRequest(
            trigger="document_artifact_request",
            reason="Blank-board text asks for a document-like learning artifact.",
        )

    if not is_generation_control_request(request.message):
        return None
    if not _has_actionable_generation_context(requirements, learning_clarification):
        return None
    return TextTriggeredGenerationRequest(
        trigger="generation_control_request",
        reason="Blank-board text asks to proceed using existing actionable requirement context.",
    )


def _has_actionable_generation_context(
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    if requirements.action_type == "generate_board" and requirements.action_instruction.strip():
        return True
    if learning_clarification.ready_for_board or learning_clarification.can_start:
        return True

    for fact in learning_clarification.key_facts:
        if not fact.value.strip():
            continue
        if fact.category in {"learning", "level", "vocabulary", "scenario", "output"}:
            return True

    return any(item.is_clear and item.evidence.strip() for item in learning_clarification.checklist)
