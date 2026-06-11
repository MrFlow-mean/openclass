from __future__ import annotations

from typing import Callable

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services import workspace_state
from app.services.chat.handlers.initial_board import InitialBoardRuntime, run_initial_board_generation
from app.services.chat.intent import _requests_document_artifact_generation, _requests_learning_start
from app.services.chat.metadata import _reference_metadata, _learning_requirement_metadata
from app.services.chat.response import _response
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.learning_requirement_manager import is_generation_control_request
from app.services.resource_resolver import ResourceResolution


def should_generate_board_after_reference_confirmation(text: str) -> bool:
    return (
        _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def run_confirmed_resource_initial_board_generation(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    runtime: InitialBoardRuntime,
) -> ChatResponse | None:
    if request.resource_reference_action != "confirm" or resource_resolution.selected_reference is None:
        return None
    return run_initial_board_generation(
        trigger="resource_reference_confirm",
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_resolution=resource_resolution,
        resource_summary=resource_summary,
        requirement_history=requirement_history,
        track_initial_requirement_run=track_initial_requirement_run,
        runtime=runtime,
    )


def prompt_for_resource_reference(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    commit_message: str,
    save_workspace_for_user: Callable[..., None],
) -> ChatResponse:
    reference_prompt = resource_resolution.reference_prompt
    if reference_prompt is None:
        raise ValueError("resource reference prompt response requires a reference prompt")
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
            **_learning_requirement_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **_reference_metadata(resolution=resource_resolution),
        },
    )
    workspace_state.normalize_package_state(package)
    requirement_history_arg = requirement_history if track_initial_requirement_run else None
    save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    return _response(
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
        requirement_history=requirement_history_arg,
    )
