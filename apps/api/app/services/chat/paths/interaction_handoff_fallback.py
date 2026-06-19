from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    CoursePackage,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


SUPPORTED_INTERACTION_HANDOFF_FALLBACK_ROUTES = {"new_task", "side_learning_request"}


class InteractionMessageGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        resources: list[ResourceLibraryItem],
        conversation: list[ConversationTurn],
        request: ChatRequest,
        session: InteractionSession,
        decision: InteractionTurnDecision | None,
    ) -> tuple[str, str, dict[str, object] | None]: ...


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
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class InteractionHandoffFallbackResponseBuilder(Protocol):
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
        interaction_decision: InteractionTurnDecision | None = None,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class InteractionHandoffFallbackDependencies:
    generate_interaction_message: InteractionMessageGenerator
    task_metadata: TaskMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: InteractionHandoffFallbackResponseBuilder


def handle_interaction_handoff_fallback(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    session_before: InteractionSession | None,
    decision: InteractionTurnDecision,
    source_interaction_metadata: dict[str, object] | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    deps: InteractionHandoffFallbackDependencies,
) -> ChatResponse:
    if decision.route not in SUPPORTED_INTERACTION_HANDOFF_FALLBACK_ROUTES:
        raise ValueError(f"unsupported interaction handoff fallback route: {decision.route}")
    if session_before is None:
        raise ValueError("interaction handoff fallback requires a previous interaction session")
    if lesson.active_interaction_session is not None:
        raise ValueError("interaction handoff fallback requires the active session to be cleared first")
    if source_interaction_metadata is None:
        raise ValueError("interaction handoff fallback requires source interaction metadata")

    chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_interaction_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=session_before,
        decision=decision,
    )
    record_workflow_step(
        NodeId.INTERACTION_TERMINAL,
        decision=decision.route,
        reason=decision.reason,
    )
    commit_operations(
        lesson,
        [],
        label="Interaction session ended",
        message="Exited a rule-based interaction session and found no executable board task in the same turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **source_interaction_metadata,
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        requirement_history=requirement_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
