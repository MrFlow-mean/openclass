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
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.board_task_manager import make_write_task_from_topic
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, record_workflow_step


UNRESOLVED_EDIT_CONVERSION_FAILURE_THRESHOLD = 2


class BoardTaskActivator(Protocol):
    def __call__(self, lesson: Lesson, sheet: BoardTaskRequirementSheet) -> None: ...


class BoardTaskUpdateEmitter(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        sheet: BoardTaskRequirementSheet,
        stamp: BoardTaskHistoryStamp | None,
    ) -> None: ...


class BoardTaskClarificationMessageGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        resources: list[ResourceLibraryItem],
        conversation: object,
        request: ChatRequest,
        board_task: BoardTaskRequirementSheet,
        context: str,
    ) -> tuple[str, str]: ...


class BoardSearchEvidenceMetadataBuilder(Protocol):
    def __call__(self, resolution: FocusResolution | None) -> dict[str, object]: ...


class DecisionTraceMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        message: str,
        board_action_decision: BoardTaskActionDecision | None,
        route_decision: BoardTaskRouteDecision | None,
        role_executed: str,
        document_changed: bool,
        reason: str,
        target_scope: str | None = None,
    ) -> dict[str, object]: ...


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
    ) -> None: ...


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


class BoardTaskUnresolvedEditConversionResponseBuilder(Protocol):
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
class BoardTaskUnresolvedEditConversionDependencies:
    activate_board_task_requirements: BoardTaskActivator
    emit_board_task_update: BoardTaskUpdateEmitter
    generate_board_task_clarification_message: BoardTaskClarificationMessageGenerator
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    decision_trace_metadata: DecisionTraceMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    normalize_package_state: NormalizePackageState
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskUnresolvedEditConversionResponseBuilder


def _next_unresolved_edit_task(
    *,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
) -> BoardTaskRequirementSheet:
    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.location_status = "ambiguous" if decision.location_status == "ambiguous" else "missing"
    next_task.failure_count += 1
    return next_task


def handle_board_task_unresolved_edit_conversion(
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
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    action_decision: BoardTaskActionDecision | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskUnresolvedEditConversionDependencies,
) -> ChatResponse | None:
    if decision.route != "clarify_location" or board_task.requested_action != "edit":
        return None
    topic = (board_task.question_or_topic or board_task.target_hint).strip()
    if not topic:
        return None
    next_task = _next_unresolved_edit_task(board_task=board_task, decision=decision)
    if next_task.failure_count < UNRESOLVED_EDIT_CONVERSION_FAILURE_THRESHOLD:
        return None

    interaction_metadata = source_interaction_metadata or {}
    old_ready_stamp = board_task_history.record_update(
        sheet=next_task,
        change_summary="Edit target could not be located twice.",
    )
    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="ready",
        reason=board_task.question_or_topic or board_task.target_hint,
        run_id=old_ready_stamp.run_id,
        version_id=old_ready_stamp.version_id,
    )
    record_workflow_step(
        NodeId.BOARD_TARGET_RESOLVE,
        decision=resolution.status if resolution else decision.location_status,
        reason=(resolution.question if resolution and resolution.question else decision.reason),
        run_id=old_ready_stamp.run_id,
        version_id=old_ready_stamp.version_id,
    )
    old_terminal_stamp = board_task_history.not_executed(reason="编辑目标连续两次未定位，旧任务未执行。")
    new_task = make_write_task_from_topic(topic)
    deps.activate_board_task_requirements(lesson, new_task)
    new_stamp = board_task_history.record_update(
        sheet=new_task,
        status="awaiting_confirmation",
        change_summary="Created a write task from an unresolved edit topic.",
    )
    deps.emit_board_task_update(lesson=lesson, sheet=new_task, stamp=new_stamp)
    chatbot_message, chatbot_message_source = deps.generate_board_task_clarification_message(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        request=request,
        board_task=new_task,
        context="板书里没有定位到可编辑的原内容。请确认是否改为扩写相关内容。",
    )
    deps.commit_operations(
        lesson,
        [],
        label="Board task converted to write confirmation",
        message="Archived an unresolved edit task and opened a write confirmation task",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **deps.decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="focus_resolver",
                document_changed=False,
                reason="编辑目标未定位，已转为扩写确认。",
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=old_terminal_stamp,
                route="clarify_location",
                cleared=True,
            ),
            "old_board_task_ready_version_id": old_ready_stamp.version_id,
            "new_board_task": new_task.model_dump(mode="json"),
            "active_board_task_sheet_after": new_task.model_dump(mode="json"),
            "new_board_task_run_id": new_stamp.run_id,
            "new_board_task_version_id": new_stamp.version_id,
            "new_board_task_phase": new_stamp.phase,
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
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION,
        decision="converted_from_unresolved_edit",
        reason="编辑目标未定位，已转为扩写确认。",
        run_id=new_stamp.run_id,
        version_id=new_stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason="编辑目标未定位，已转为扩写确认。"),
        board_task_stamp=new_stamp,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
