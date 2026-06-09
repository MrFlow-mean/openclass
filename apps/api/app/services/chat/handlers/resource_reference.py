from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services import workspace_state
from app.services.chat.metadata import reference_metadata, task_metadata
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.resource_resolver import ResourceResolution


@dataclass(frozen=True)
class ResourceReferencePromptDeps:
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_resource_reference_prompt(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    commit_message: str,
    deps: ResourceReferencePromptDeps,
) -> ChatResponse:
    reference_prompt = resource_resolution.reference_prompt
    if reference_prompt is None:
        raise ValueError("resource reference prompt handler requires a reference prompt")
    chatbot_message = reference_prompt.question
    commit_operations(
        lesson,
        [],
        label="Resource reference prompt",
        message=commit_message,
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "resource_resolver",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **reference_metadata(resolution=resource_resolution),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    return deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(
            action="await_reference_choice",
            reason=reference_prompt.reason,
        ),
        resource_matches=resource_resolution.matches,
        reference_prompt=reference_prompt,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )
