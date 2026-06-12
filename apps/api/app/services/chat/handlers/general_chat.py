from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceBoardProposal,
    ResourceReferenceContext,
)
from app.services import workspace_state
from app.services.chat.metadata import _reference_metadata, _learning_requirement_metadata
from app.services.chat.response import _response
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.resource_resolver import ResourceResolution


@dataclass(frozen=True)
class GeneralChatRuntime:
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]


def commit_general_chat_turn(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    chatbot_message: str,
    chatbot_message_source: str,
    solver_metadata: dict[str, object],
    runtime: GeneralChatRuntime,
    resource_board_proposal: ResourceBoardProposal | None = None,
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
            **_learning_requirement_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_reference_metadata(resolution=resource_resolution),
            "resource_board_proposal": (
                resource_board_proposal.model_dump(mode="json") if resource_board_proposal else None
            ),
            "pending_resource_board_proposal": (
                lesson.pending_resource_board_proposal.model_dump(mode="json")
                if lesson.pending_resource_board_proposal
                else None
            ),
            **solver_metadata,
        },
    )
    if requirement_cleared:
        runtime.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    runtime.save_workspace_for_user(
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
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        resource_evidence_bundle=resource_resolution.evidence_bundle,
        resource_board_proposal=resource_board_proposal,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )
