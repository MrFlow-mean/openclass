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
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder


@dataclass(frozen=True)
class GeneralChatHandlerDeps:
    task_metadata: Callable[..., dict[str, object]]
    reference_metadata: Callable[..., dict[str, object]]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_general_chat(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    chatbot_message: str,
    chatbot_message_source: str,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: Any,
    selected_reference: Any,
    solver_metadata: dict[str, object],
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: GeneralChatHandlerDeps,
) -> ChatResponse:
    board_decision = BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。")
    requirement_cleared = False

    commit_operations(
        lesson,
        [],
        label="Chat turn",
        message="Recorded a learner and chatbot chat turn",
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
                requirement_cleared=requirement_cleared,
            ),
            **deps.reference_metadata(resolution=resource_resolution),
            **solver_metadata,
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
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )
