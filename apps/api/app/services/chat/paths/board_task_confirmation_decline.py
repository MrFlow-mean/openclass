from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    WorkspaceState,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


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
        new_document: object,
        metadata: dict[str, object],
    ) -> object: ...


class NormalizePackageState(Protocol):
    def __call__(self, package: CoursePackage) -> None: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class BoardTaskConfirmationDeclineResponseBuilder(Protocol):
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
        board_task_stamp: BoardTaskHistoryStamp | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskConfirmationDeclineDependencies:
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    normalize_package_state: NormalizePackageState
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskConfirmationDeclineResponseBuilder


def handle_board_task_confirmation_decline(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    existing_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskConfirmationDeclineDependencies,
) -> ChatResponse:
    current_stamp = board_task_history.current_stamp()
    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="awaiting_confirmation",
        reason=existing_task.question_or_topic or existing_task.target_hint,
        run_id=current_stamp.run_id,
        version_id=current_stamp.version_id,
    )
    stamp = board_task_history.not_executed(reason="用户取消了扩写确认。")
    lesson.board_task_requirements = None
    deps.commit_operations(
        lesson,
        [],
        label="Board task cancelled",
        message="Cancelled an awaiting board write task",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": "",
            "assistant_message_source": "board_task_cancelled",
            **(source_interaction_metadata or {}),
            **deps.board_task_metadata(
                board_task=existing_task,
                stamp=stamp,
                route="await_write_confirmation",
                cleared=True,
            ),
        },
    )
    deps.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    commit = lesson.history_graph.commits[-1]
    record_workflow_step(
        NodeId.BOARD_WRITE_CONFIRMATION_HANDLE,
        decision="declined",
        reason="用户取消了扩写确认。",
        run_id=stamp.run_id,
        version_id=stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message="",
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason="用户取消了扩写。"),
        board_task_stamp=stamp,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
