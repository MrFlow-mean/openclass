from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
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


BOARD_TASK_CHAT_ACTION: BoardTaskAction = "explain_target"


class RequirementsFromBoardTaskBuilder(Protocol):
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


class InteractionSessionStarter(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        user_id: str,
        request: ChatRequest,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        resources: list[ResourceLibraryItem],
        selection_text: str | None,
        action_type: BoardTaskAction | None,
        requirement_history: LearningRequirementHistoryRecorder,
        board_task: BoardTaskRequirementSheet | None = None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
        board_task_decision: BoardTaskRouteDecision | None = None,
        resolved_focus: BoardFocusRef | None = None,
        source_interaction_metadata: dict[str, object] | None = None,
    ) -> ChatResponse | None: ...


@dataclass(frozen=True)
class BoardTaskChatHandoffDependencies:
    requirements_from_board_task: RequirementsFromBoardTaskBuilder
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    decision_trace_metadata: DecisionTraceMetadataBuilder
    start_interaction_session: InteractionSessionStarter


def handle_board_task_chat_handoff(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
    board_task: BoardTaskRequirementSheet,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision | None,
    decision: BoardTaskRouteDecision,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskChatHandoffDependencies,
) -> ChatResponse | None:
    if decision.route != "chat":
        raise ValueError(f"board task chat handoff requires route='chat', got {decision.route!r}")

    focus = decision.target_focus or (resolution.focus if resolution else None)
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type=BOARD_TASK_CHAT_ACTION,
        focus=focus,
    )
    lesson.learning_requirements = task_requirements
    interaction_metadata = source_interaction_metadata or {}
    return deps.start_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        selection_text=selection_text,
        action_type=BOARD_TASK_CHAT_ACTION,
        requirement_history=requirement_history,
        board_task=board_task,
        board_task_history=board_task_history,
        board_task_stamp=board_task_stamp,
        board_task_decision=decision,
        resolved_focus=focus,
        source_interaction_metadata={
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **deps.decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="interaction_session",
                document_changed=False,
                reason=decision.reason,
            ),
        },
    )
