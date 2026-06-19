from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    ChatRequest,
    ChatResponse,
    CoursePackage,
    InteractionSession,
    InteractionTurnDecision,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


SUPPORTED_INTERACTION_BOARD_TASK_HANDOFF_ROUTES = {"new_task", "side_learning_request"}


class BoardTaskFlowHandler(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        user_id: str,
        request: ChatRequest,
        requirements: LearningRequirementSheet,
        resources: list[ResourceLibraryItem],
        selection_excerpt: str | None,
        selection_text: str | None,
        requirement_history: LearningRequirementHistoryRecorder,
        board_task_history: BoardTaskHistoryRecorder,
        source_interaction_metadata: dict[str, object],
        force_task_attempt: bool = False,
    ) -> ChatResponse | None: ...


class InteractionSessionMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        before: InteractionSession | None,
        after: InteractionSession | None,
        decision: InteractionTurnDecision | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class InteractionBoardTaskHandoffDependencies:
    handle_existing_board_task_flow: BoardTaskFlowHandler
    build_interaction_session_metadata: InteractionSessionMetadataBuilder


@dataclass(frozen=True)
class InteractionBoardTaskHandoffResult:
    response: ChatResponse | None
    source_interaction_metadata: dict[str, object]


def attempt_interaction_board_task_handoff(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
    selection_text: str | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    session_before: InteractionSession | None,
    decision: InteractionTurnDecision,
    deps: InteractionBoardTaskHandoffDependencies,
) -> InteractionBoardTaskHandoffResult:
    if decision.route not in SUPPORTED_INTERACTION_BOARD_TASK_HANDOFF_ROUTES:
        raise ValueError(f"unsupported interaction board-task handoff route: {decision.route}")
    if session_before is None:
        raise ValueError("interaction board-task handoff requires a previous interaction session")
    if lesson.active_interaction_session is None:
        raise ValueError("interaction board-task handoff requires an active session")
    if lesson.active_interaction_session != session_before:
        raise ValueError("interaction board-task handoff session mismatch")

    lesson.active_interaction_session = None
    source_interaction_metadata = deps.build_interaction_session_metadata(
        before=session_before,
        after=None,
        decision=decision,
    )
    record_workflow_step(
        NodeId.INTERACTION_NEW_TASK,
        decision=decision.route,
        reason=decision.reason,
    )
    response = deps.handle_existing_board_task_flow(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=resources,
        selection_excerpt=selection_excerpt,
        selection_text=selection_text,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        source_interaction_metadata=source_interaction_metadata,
        force_task_attempt=True,
    )
    if response is not None:
        response.interaction_decision = decision
    return InteractionBoardTaskHandoffResult(
        response=response,
        source_interaction_metadata=source_interaction_metadata,
    )
