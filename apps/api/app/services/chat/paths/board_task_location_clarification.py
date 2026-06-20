from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
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
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, record_workflow_step


class RequirementsFromBoardTask(Protocol):
    def __call__(
        self,
        *,
        base: LearningRequirementSheet,
        board_task: BoardTaskRequirementSheet,
        action_type: BoardTaskAction | None,
        focus: BoardFocusRef | None = None,
    ) -> LearningRequirementSheet: ...


class FocusCandidateMessageGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        resources: list[ResourceLibraryItem],
        conversation: list[ConversationTurn],
        request: ChatRequest,
        resolution: FocusResolution,
    ) -> tuple[str, str]: ...


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


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        focus: BoardFocusRef | None = None,
        focus_candidates: list[BoardFocusRef] | None = None,
        requirement_cleared: bool = False,
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


class BoardTaskUpdateEmitter(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        sheet: BoardTaskRequirementSheet,
        stamp: BoardTaskHistoryStamp | None,
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


class BoardTaskLocationClarificationResponseBuilder(Protocol):
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
        focus_candidates: list[BoardFocusRef] | None = None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskLocationClarificationDependencies:
    requirements_from_board_task: RequirementsFromBoardTask
    generate_focus_candidate_message: FocusCandidateMessageGenerator
    decision_trace_metadata: DecisionTraceMetadataBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    emit_board_task_update: BoardTaskUpdateEmitter
    normalize_package_state: NormalizePackageState
    save_workspace_for_user: SaveWorkspaceForUser
    commit_operations: CommitOperations
    build_response: BoardTaskLocationClarificationResponseBuilder


def is_normal_location_clarification(
    *,
    board_task: BoardTaskRequirementSheet,
    route_decision: BoardTaskRouteDecision,
) -> bool:
    if route_decision.route != "clarify_location":
        return False
    if route_decision.location_status not in {"missing", "ambiguous"}:
        return False
    return not _requires_unresolved_edit_conversion(board_task)


def handle_board_task_location_clarification(
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
    board_action: BoardTaskAction | None,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision | None,
    route_decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskLocationClarificationDependencies,
) -> ChatResponse:
    if route_decision.route != "clarify_location":
        raise ValueError("location clarification handler requires a clarify_location route")
    if route_decision.location_status not in {"missing", "ambiguous"}:
        raise ValueError("location clarification handler only supports missing or ambiguous locations")
    if _requires_unresolved_edit_conversion(board_task):
        raise ValueError("unresolved edit conversion is delegated to a separate handler")

    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="ready",
        reason=board_task.question_or_topic or board_task.target_hint,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )
    record_workflow_step(
        NodeId.BOARD_TARGET_RESOLVE,
        decision=resolution.status if resolution else route_decision.location_status,
        reason=(resolution.question if resolution and resolution.question else route_decision.reason),
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )

    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.location_status = "ambiguous" if route_decision.location_status == "ambiguous" else "missing"
    if board_task.requested_action == "edit":
        next_task.failure_count += 1

    _activate_board_task_requirements(lesson, next_task)
    stamp = board_task_history.record_update(sheet=next_task, change_summary=route_decision.reason)
    deps.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)

    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=next_task,
        action_type=board_action,
    )
    focus_resolution = resolution or FocusResolution(
        focus=None,
        candidates=route_decision.candidate_focuses,
        status="ambiguous" if route_decision.candidate_focuses else "missing",
        question=route_decision.reason,
    )
    chatbot_message, chatbot_message_source = deps.generate_focus_candidate_message(
        lesson=lesson,
        requirements=task_requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        resolution=focus_resolution,
    )
    deps.commit_operations(
        lesson,
        [],
        label="Board task location clarification",
        message="Asked the learner to confirm the board task location",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **(source_interaction_metadata or {}),
            **_board_search_evidence_metadata(resolution),
            **deps.decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=route_decision,
                role_executed="focus_resolver",
                document_changed=False,
                reason=route_decision.reason,
            ),
            **deps.task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                focus=None,
                focus_candidates=route_decision.candidate_focuses,
                requirement_cleared=False,
            ),
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
    commit = lesson.history_graph.commits[-1]
    record_workflow_step(
        NodeId.BOARD_ROUTE_CLARIFY_LOCATION,
        decision=route_decision.location_status,
        reason=route_decision.reason,
        run_id=stamp.run_id,
        version_id=stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="await_focus_choice", reason=route_decision.reason),
        focus_candidates=route_decision.candidate_focuses,
        board_task_history=board_task_history,
        board_task_stamp=stamp,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def _requires_unresolved_edit_conversion(board_task: BoardTaskRequirementSheet) -> bool:
    return board_task.requested_action == "edit" and board_task.failure_count + 1 >= 2


def _activate_board_task_requirements(lesson: Lesson, board_task: BoardTaskRequirementSheet) -> None:
    lesson.learning_requirements = None
    lesson.board_task_requirements = board_task


def _board_search_evidence_metadata(resolution: FocusResolution | None) -> dict[str, object]:
    return {
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
    }
