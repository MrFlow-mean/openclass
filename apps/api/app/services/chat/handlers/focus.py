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
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.segment_resolver import FocusResolution


@dataclass(frozen=True)
class FocusClarificationDeps:
    generate_focus_candidate_message: Callable[..., tuple[str, str]]
    task_metadata: Callable[..., dict[str, object]]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_focus_clarification(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    resolution: FocusResolution,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    commit_message: str,
    deps: FocusClarificationDeps,
) -> ChatResponse:
    lesson.learning_requirements = requirements
    chatbot_message, chatbot_message_source = deps.generate_focus_candidate_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        resolution=resolution,
    )
    commit_operations(
        lesson,
        [],
        label="Board focus clarification",
        message=commit_message,
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=None,
                focus_candidates=resolution.candidates,
                requirement_cleared=False,
            ),
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
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
        focus_candidates=resolution.candidates,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )
