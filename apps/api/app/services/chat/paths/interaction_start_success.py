from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
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
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, record_workflow_step


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


class TaskRequirementsClearer(Protocol):
    def __call__(self, lesson: Lesson) -> None: ...


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


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class InteractionStartSuccessResponseBuilder(Protocol):
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
        resolved_focus: BoardFocusRef | None = None,
        focus_candidates: list[BoardFocusRef] | None = None,
        requirement_cleared: bool = False,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class InteractionStartSuccessDependencies:
    generate_interaction_message: InteractionMessageGenerator
    clear_task_requirements: TaskRequirementsClearer
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: InteractionStartSuccessResponseBuilder


def handle_interaction_start_success(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    resolved_session: InteractionSession,
    focus_resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    source_interaction_metadata: dict[str, object],
    board_task: BoardTaskRequirementSheet | None = None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    board_task_decision_metadata: dict[str, object] | None = None,
    deps: InteractionStartSuccessDependencies,
) -> ChatResponse:
    session_before = lesson.active_interaction_session
    session_after = resolved_session
    if board_task is not None and board_task_stamp is not None:
        session_after = session_after.model_copy(
            update={
                "source_board_task_run_id": board_task_stamp.run_id,
                "source_board_task_version_id": board_task_stamp.version_id,
                "source_board_task_route": "chat",
            }
        )
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_interaction_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=session_after,
        decision=None,
    )
    deps.clear_task_requirements(lesson)
    if board_task is not None:
        lesson.board_task_requirements = None
    focus_candidates = focus_resolution.candidates if focus_resolution else []
    commit_operations(
        lesson,
        [],
        label="Interaction session start",
        message="Started a rule-based interaction session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **source_interaction_metadata,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=session_after.target_focus,
                focus_candidates=focus_candidates,
                requirement_cleared=True,
            ),
            **(
                deps.board_task_metadata(
                    board_task=board_task,
                    stamp=board_task_stamp,
                    route="chat",
                    decision=board_task_decision_metadata,
                    cleared=board_task is not None,
                )
                if board_task is not None
                else {}
            ),
            **interaction_session_metadata(
                before=session_before,
                after=session_after,
            ),
        },
    )
    consumed_board_task_stamp = (
        board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if board_task is not None and board_task_history is not None
        else board_task_stamp
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    record_workflow_step(
        NodeId.INTERACTION_START_PERSIST,
        decision="started",
        reason=session_after.interaction_goal,
        run_id=consumed_board_task_stamp.run_id if consumed_board_task_stamp else None,
        version_id=consumed_board_task_stamp.version_id if consumed_board_task_stamp else None,
        commit_id=lesson.history_graph.commits[-1].id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(
            action="no_change",
            reason=session_after.interaction_goal,
        ),
        resolved_focus=session_after.target_focus,
        focus_candidates=focus_candidates,
        requirement_cleared=True,
        requirement_history=requirement_history,
        board_task_stamp=consumed_board_task_stamp,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
