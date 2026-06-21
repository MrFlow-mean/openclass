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
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.workflow_trace import NodeId, record_workflow_step


class BoardTaskActivator(Protocol):
    def __call__(self, lesson: Lesson, board_task: BoardTaskRequirementSheet) -> None: ...


class BoardTaskUpdateEmitter(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        sheet: BoardTaskRequirementSheet,
        stamp: BoardTaskHistoryStamp | None,
    ) -> None: ...


class BoardTaskClarificationMessageBuilder(Protocol):
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


class AwaitWriteConfirmationResponseBuilder(Protocol):
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
class BoardTaskAwaitWriteConfirmationDependencies:
    activate_board_task_requirements: BoardTaskActivator
    emit_board_task_update: BoardTaskUpdateEmitter
    build_clarification_message: BoardTaskClarificationMessageBuilder
    commit_operations: CommitOperations
    board_task_metadata: BoardTaskMetadataBuilder
    normalize_package_state: NormalizePackageState
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: AwaitWriteConfirmationResponseBuilder


def handle_board_task_await_write_confirmation(
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
    requirement_history: LearningRequirementHistoryRecorder,
    route_decision: BoardTaskRouteDecision,
    interaction_metadata: dict[str, object] | None = None,
    board_search_evidence_metadata: dict[str, object] | None = None,
    decision_trace_metadata: dict[str, object] | None = None,
    deps: BoardTaskAwaitWriteConfirmationDependencies,
) -> ChatResponse:
    if route_decision.route != "await_write_confirmation":
        raise ValueError("await-write-confirmation terminal requires route='await_write_confirmation'")

    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.requested_action = "write"
    next_task.location_status = "content_absent"
    next_task.confirmation_status = "awaiting"
    next_task.progress = 100
    next_task.missing_items = []
    next_task.clarification_question = ""
    deps.activate_board_task_requirements(lesson, next_task)

    stamp = board_task_history.record_update(
        sheet=next_task,
        status="awaiting_confirmation",
        change_summary=route_decision.reason or "Awaiting learner confirmation before writing new board content.",
    )
    deps.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
    chatbot_message, chatbot_message_source = deps.build_clarification_message(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        request=request,
        board_task=next_task,
        context="板书里没有对应内容。请询问用户是否要先扩写板书，再继续学习。",
    )
    deps.commit_operations(
        lesson,
        [],
        label="Board write confirmation",
        message="Asked the learner to confirm writing absent board content",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **(interaction_metadata or {}),
            **(board_search_evidence_metadata or {}),
            **(decision_trace_metadata or {}),
            **deps.board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route=route_decision.route,
                decision=route_decision.model_dump(mode="json"),
                cleared=False,
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
    commit_id = lesson.history_graph.commits[-1].id
    record_workflow_step(
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION,
        decision="awaiting_confirmation",
        reason=route_decision.reason,
        run_id=stamp.run_id,
        version_id=stamp.version_id,
        commit_id=commit_id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=route_decision.reason),
        board_task_history=board_task_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
