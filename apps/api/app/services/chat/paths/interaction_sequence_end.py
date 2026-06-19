from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

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
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


SequenceEndOutcome = Literal["exit_requested", "completed"]


class SequenceEndMessageGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        resources: list[ResourceLibraryItem],
        conversation: list[ConversationTurn],
        request: ChatRequest,
        session: InteractionSession,
    ) -> tuple[str, str]: ...


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


class InteractionSequenceEndResponseBuilder(Protocol):
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
class InteractionSequenceEndDependencies:
    generate_sequence_end_message: SequenceEndMessageGenerator
    task_metadata: TaskMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: InteractionSequenceEndResponseBuilder


def handle_interaction_sequence_end(
    *,
    outcome: SequenceEndOutcome,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    session_before: InteractionSession | None,
    requirement_history: LearningRequirementHistoryRecorder,
    unit_label: str | None = None,
    deps: InteractionSequenceEndDependencies,
) -> ChatResponse:
    if outcome not in {"exit_requested", "completed"}:
        raise ValueError(f"unsupported sequence end outcome: {outcome}")
    if session_before is None:
        raise ValueError("sequence end requires a previous interaction session")
    if not session_before.sequence_items:
        raise ValueError("sequence end requires sequence items")
    if lesson.active_interaction_session != session_before:
        raise ValueError("sequence end requires the current active session to match session_before")
    if outcome == "completed" and not unit_label:
        raise ValueError("sequence completion requires a unit label")

    lesson.active_interaction_session = None
    chatbot_message, chatbot_message_source = deps.generate_sequence_end_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=session_before,
    )
    if outcome == "exit_requested":
        decision = InteractionTurnDecision(
            route="exit_rule",
            reason="用户结束当前顺序讲解。",
            progress_note=session_before.progress_note,
            user_intent="结束顺序讲解",
        )
        commit_label = "Section explanation session ended"
        commit_message = "Ended a sequential section explanation session"
    else:
        decision = InteractionTurnDecision(
            route="exit_rule",
            reason="顺序讲解已经完成。",
            progress_note="顺序讲解已经完成。",
            user_intent=f"确认最后一个{unit_label}无问题",
        )
        commit_label = "Section explanation session completed"
        commit_message = "Completed a sequential section explanation session"

    record_workflow_step(
        NodeId.INTERACTION_EXIT,
        decision="exit_rule",
        reason=decision.reason,
    )
    commit_operations(
        lesson,
        [],
        label=commit_label,
        message=commit_message,
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **interaction_session_metadata(before=session_before, after=None, decision=decision),
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        requirement_history=requirement_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
