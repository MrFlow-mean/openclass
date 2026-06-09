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
from app.services.board_teaching import teach_first_section, teach_next_section
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder


@dataclass(frozen=True)
class BoardTeachingTurnDeps:
    latest_learning_clarification: Callable[..., LearningClarificationStatus]
    resource_summary: Callable[[list[Any]], str]
    conversation_summary: Callable[..., str]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_board_teaching_turn(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[Any],
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: BoardTeachingTurnDeps,
) -> ChatResponse:
    learning_clarification = deps.latest_learning_clarification(lesson, requirements=requirements)
    if request.teaching_action == "restart":
        lesson.board_teaching_progress = None
        teaching_result = teach_first_section(
            lesson=lesson,
            resource_summary=deps.resource_summary(resources),
            conversation_summary=deps.conversation_summary(request.conversation),
        )
    else:
        teaching_result = teach_next_section(
            lesson=lesson,
            resource_summary=deps.resource_summary(resources),
            conversation_summary=deps.conversation_summary(request.conversation),
        )
    commit_operations(
        lesson,
        [],
        label="Board teaching turn",
        message="Recorded a section-by-section board teaching turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": teaching_result.chatbot_message,
            "assistant_message_source": teaching_result.assistant_message_source,
            "interaction_mode": request.interaction_mode,
            "teaching_action": request.teaching_action,
            "teaching_progress": teaching_result.progress_view.model_dump(mode="json"),
            "board_explanation_directive": teaching_result.board_explanation_directive,
            "learning_clarification": learning_clarification.model_dump(mode="json"),
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
        chatbot_message=teaching_result.chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason="本轮是分节讲解，不修改板书。"),
        teaching_progress=teaching_result.progress_view,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )
