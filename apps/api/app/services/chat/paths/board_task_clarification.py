from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.decision_trace import decision_trace_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


class BoardTaskClarificationMessageGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        resources: list[ResourceLibraryItem],
        conversation: list[ConversationTurn],
        request: ChatRequest,
        board_task: BoardTaskRequirementSheet,
        context: str,
    ) -> tuple[str, str]: ...


class BoardTaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        board_task: BoardTaskRequirementSheet | None,
        stamp: BoardTaskHistoryStamp | None,
        route: str | None = None,
        decision: dict[str, object] | None = None,
        cleared: bool = False,
    ) -> dict[str, object]: ...


class CommitOperations(Protocol):
    def __call__(
        self,
        lesson: Lesson,
        operations: list[object],
        *,
        label: str,
        message: str,
        new_document: object | None = None,
        metadata: dict[str, object] | None = None,
    ) -> object: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class BoardTaskClarificationResponseBuilder(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        chatbot_message: str,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        board_decision: BoardDecision,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskClarificationDependencies:
    generate_board_task_clarification_message: BoardTaskClarificationMessageGenerator
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskClarificationResponseBuilder


def handle_board_task_clarification(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision | None,
    requirement_history: LearningRequirementHistoryRecorder,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskClarificationDependencies,
) -> ChatResponse:
    if board_task.progress >= 100:
        raise ValueError("board task clarification requires an incomplete board task")
    if not board_task_stamp.run_id or not board_task_stamp.version_id:
        raise ValueError("board task clarification requires a recorded board task version")

    lesson.board_task_requirements = board_task
    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="collecting",
        reason=board_task.clarification_question,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )
    chatbot_message, chatbot_message_source = deps.generate_board_task_clarification_message(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        request=request,
        board_task=board_task,
        context=board_task.clarification_question,
    )
    deps.commit_operations(
        lesson,
        [],
        label="Board task clarification",
        message="Asked for a missing field in the existing-board task sheet",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **(source_interaction_metadata or {}),
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                role_executed="board_task_manager",
                document_changed=False,
                reason=board_task.clarification_question,
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=board_task_stamp,
                route="clarify_location",
                cleared=False,
            ),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    commit = lesson.history_graph.commits[-1]
    record_workflow_step(
        NodeId.BOARD_TASK_CLARIFY_FIELDS,
        decision="missing_fields",
        reason=board_task.clarification_question,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=board_task.clarification_question),
        board_task_history=board_task_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
