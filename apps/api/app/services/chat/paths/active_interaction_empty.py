from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    InteractionSession,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
    ) -> None: ...


class ActiveInteractionEmptyResponseBuilder(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        chatbot_message: str,
        learning_clarification: LearningClarificationStatus,
        requirements: LearningRequirementSheet,
        board_decision: BoardDecision,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class ActiveInteractionEmptyDependencies:
    task_metadata: TaskMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: ActiveInteractionEmptyResponseBuilder


def handle_active_interaction_empty_decision(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    session_before: InteractionSession | None,
    requirement_history: LearningRequirementHistoryRecorder,
    deps: ActiveInteractionEmptyDependencies,
) -> ChatResponse:
    if session_before is None:
        raise ValueError("active interaction empty decision requires a previous interaction session")
    if lesson.active_interaction_session != session_before:
        raise ValueError("active interaction empty decision requires the current active session to match session_before")

    chatbot_message = ""
    lesson.active_interaction_session = session_before
    record_workflow_step(NodeId.INTERACTION_TERMINAL, decision="empty", reason=None)
    commit_operations(
        lesson,
        [],
        label="Interaction turn",
        message="Recorded an interaction-rule turn without a route decision",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "interaction_decision_empty",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **interaction_session_metadata(before=session_before, after=session_before),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    record_workflow_step(
        NodeId.PERSIST_CHAT_COMMIT,
        decision="committed",
        commit_id=lesson.history_graph.commits[-1].id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(action="no_change", reason=""),
        requirement_history=requirement_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
