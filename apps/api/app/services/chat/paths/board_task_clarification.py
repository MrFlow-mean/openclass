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
from app.services import workspace_state
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.board_task_manager import make_write_task_from_topic
from app.services.decision_trace import decision_trace_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, record_workflow_step


class ActivateBoardTaskRequirements(Protocol):
    def __call__(self, lesson: Lesson, board_task: BoardTaskRequirementSheet) -> None: ...


class EmitBoardTaskUpdate(Protocol):
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
        conversation: list[ConversationTurn],
        request: ChatRequest,
        board_task: BoardTaskRequirementSheet,
        context: str,
    ) -> tuple[str, str]: ...


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


class RequirementsFromBoardTask(Protocol):
    def __call__(
        self,
        *,
        base: LearningRequirementSheet,
        board_task: BoardTaskRequirementSheet,
        action_type: BoardTaskAction | None,
        focus: BoardFocusRef | None = None,
    ) -> LearningRequirementSheet: ...


class BoardSearchEvidenceMetadataBuilder(Protocol):
    def __call__(self, resolution: FocusResolution | None) -> dict[str, object]: ...


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


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class BoardTaskResponseBuilder(Protocol):
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
        requirement_history: LearningRequirementHistoryRecorder | None = None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskClarificationDependencies:
    activate_board_task_requirements: ActivateBoardTaskRequirements
    emit_board_task_update: EmitBoardTaskUpdate
    generate_board_task_clarification_message: BoardTaskClarificationMessageGenerator
    generate_focus_candidate_message: FocusCandidateMessageGenerator
    requirements_from_board_task: RequirementsFromBoardTask
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskResponseBuilder


def handle_board_task_write_confirmation_decline(
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
    source_interaction_metadata: dict[str, object],
    deps: BoardTaskClarificationDependencies,
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
            **source_interaction_metadata,
            **deps.board_task_metadata(
                board_task=existing_task,
                stamp=stamp,
                route="await_write_confirmation",
                cleared=True,
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


def handle_board_task_missing_fields_clarification(
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
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object],
    deps: BoardTaskClarificationDependencies,
) -> ChatResponse:
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
            **source_interaction_metadata,
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


def handle_board_task_clarify_location(
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
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision,
    board_action: BoardTaskAction | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object],
    deps: BoardTaskClarificationDependencies,
) -> ChatResponse:
    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="ready",
        reason=board_task.question_or_topic or board_task.target_hint,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )
    record_workflow_step(
        NodeId.BOARD_TARGET_RESOLVE,
        decision=resolution.status if resolution else decision.location_status,
        reason=(resolution.question if resolution and resolution.question else decision.reason),
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )
    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.location_status = "ambiguous" if decision.location_status == "ambiguous" else "missing"
    next_task.failure_count += 1 if board_task.requested_action == "edit" else 0
    if board_task.requested_action == "edit" and next_task.failure_count >= 2:
        return _handle_unresolved_edit_conversion(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            next_task=next_task,
            action_decision=action_decision,
            decision=decision,
            resolution=resolution,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            source_interaction_metadata=source_interaction_metadata,
            deps=deps,
        )

    deps.activate_board_task_requirements(lesson, next_task)
    stamp = board_task_history.record_update(sheet=next_task, change_summary=decision.reason)
    deps.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
    focus_resolution = resolution or FocusResolution(
        focus=None,
        candidates=decision.candidate_focuses,
        status="ambiguous" if decision.candidate_focuses else "missing",
        question=decision.reason,
    )
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=next_task,
        action_type=board_action,
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
            **source_interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="focus_resolver",
                document_changed=False,
                reason=decision.reason,
            ),
            **deps.task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                focus=None,
                focus_candidates=decision.candidate_focuses,
                requirement_cleared=False,
            ),
            **deps.board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route=decision.route,
                decision=decision.model_dump(mode="json"),
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
        NodeId.BOARD_ROUTE_CLARIFY_LOCATION,
        decision=decision.location_status,
        reason=decision.reason,
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
        board_decision=BoardDecision(action="await_focus_choice", reason=decision.reason),
        focus_candidates=decision.candidate_focuses,
        board_task_history=board_task_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def _handle_unresolved_edit_conversion(
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
    next_task: BoardTaskRequirementSheet,
    action_decision: BoardTaskActionDecision,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object],
    deps: BoardTaskClarificationDependencies,
) -> ChatResponse:
    old_stamp = board_task_history.record_update(
        sheet=next_task,
        change_summary="Edit target could not be located twice.",
    )
    board_task_history.not_executed(reason="编辑目标连续两次未定位，旧任务未执行。")
    new_task = make_write_task_from_topic(board_task.question_or_topic)
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
            **source_interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="focus_resolver",
                document_changed=False,
                reason="编辑目标未定位，已转为扩写确认。",
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=old_stamp,
                route="clarify_location",
                cleared=True,
            ),
            "new_board_task": new_task.model_dump(mode="json"),
            "new_board_task_run_id": new_stamp.run_id,
            "new_board_task_version_id": new_stamp.version_id,
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
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object],
    deps: BoardTaskClarificationDependencies,
) -> ChatResponse:
    record_workflow_step(
        NodeId.BOARD_TASK_COLLECT,
        decision="ready",
        reason=board_task.question_or_topic or board_task.target_hint,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
    )
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
        change_summary=decision.reason or "Awaiting learner confirmation before writing new board content.",
    )
    deps.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
    chatbot_message, chatbot_message_source = deps.generate_board_task_clarification_message(
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
            **source_interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="board_task_route_decider",
                document_changed=False,
                reason=decision.reason,
            ),
            **deps.board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route=decision.route,
                decision=decision.model_dump(mode="json"),
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
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION,
        decision="awaiting_confirmation",
        reason=decision.reason,
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        board_task_history=board_task_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
