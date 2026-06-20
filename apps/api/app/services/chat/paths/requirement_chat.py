from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class ReferenceMetadataBuilder(Protocol):
    def __call__(self, *, resolution: ResourceResolution) -> dict[str, object]: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
    ) -> None: ...


class CommitOperations(Protocol):
    def __call__(
        self,
        lesson: Lesson,
        operations: list[object],
        *,
        label: str,
        message: str,
        new_document: object,
        metadata: dict[str, object],
    ) -> None: ...


class RequirementChatTerminalResponseBuilder(Protocol):
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
        resource_matches: list[ResourceMatch] | None = None,
        selected_reference: ResourceReferenceContext | None = None,
        requirement_cleared: bool = False,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class RequirementChatTerminalDependencies:
    commit_operations: CommitOperations
    task_metadata: TaskMetadataBuilder
    reference_metadata: ReferenceMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: RequirementChatTerminalResponseBuilder


def handle_requirement_chat_terminal(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    chatbot_message: str,
    chatbot_message_source: str,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
    requirement_history: LearningRequirementHistoryRecorder,
    requirement_stamp: RequirementHistoryStamp,
    chat_turn_gate_metadata: dict[str, object] | None = None,
    solver_metadata: dict[str, object] | None = None,
    deps: RequirementChatTerminalDependencies,
) -> ChatResponse:
    if learning_clarification.ready_for_board:
        raise ValueError("requirement chat terminal requires a not-ready clarification")

    lesson.learning_requirements = requirements
    board_decision = BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。")
    requirement_cleared = False

    deps.commit_operations(
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
            **(chat_turn_gate_metadata or {}),
            **(solver_metadata or {}),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    record_workflow_step(
        NodeId.REQUIREMENT_CHAT_UPDATE,
        decision=requirement_stamp.phase,
        reason="not_ready_for_board",
        run_id=requirement_stamp.run_id,
        version_id=requirement_stamp.version_id,
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
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_history=requirement_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
