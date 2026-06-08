from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.handlers.edit_blackboard import (
    EditBlackboardRuntime,
    handle_board_task_edit,
    handle_board_task_write,
)
from app.services.chat.handlers.explain import ExplainHandlerRuntime, handle_board_task_explain
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution


@dataclass(frozen=True)
class BoardTaskRouteRuntime:
    edit_runtime: EditBlackboardRuntime
    explain_runtime: ExplainHandlerRuntime
    decision_focus: Callable[[BoardTaskRouteDecision, FocusResolution | None], BoardFocusRef | None]
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    maybe_start_interaction_session: Callable[..., ChatResponse | None]


def dispatch_board_task_route(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
    selection_text: str | None,
    action_type: str | None,
    board_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    runtime: BoardTaskRouteRuntime,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse | None:
    interaction_metadata = source_interaction_metadata or {}
    if decision.route == "write":
        return handle_board_task_write(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            route_decision=decision,
            search_evidence=resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
            source_interaction_metadata=interaction_metadata,
            runtime=runtime.edit_runtime,
        )

    if decision.route == "edit":
        return handle_board_task_edit(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            action_type=action_type,
            selection_excerpt=selection_excerpt,
            decision=decision,
            resolution=resolution,
            source_interaction_metadata=interaction_metadata,
            runtime=runtime.edit_runtime,
        )

    if decision.route == "explain":
        return handle_board_task_explain(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            decision=decision,
            resolution=resolution,
            source_interaction_metadata=interaction_metadata,
            runtime=runtime.explain_runtime,
        )

    if decision.route == "chat":
        focus = runtime.decision_focus(decision, resolution)
        task_requirements = runtime.requirements_from_board_task(
            base=requirements,
            board_task=board_task,
            action_type="explain_target",
            focus=focus,
        )
        lesson.learning_requirements = task_requirements
        return runtime.maybe_start_interaction_session(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            selection_text=selection_text,
            action_type="explain_target",
            requirement_history=requirement_history,
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=board_task_stamp,
            board_task_decision=decision,
            resolved_focus=focus,
            source_interaction_metadata={
                **interaction_metadata,
                **runtime.board_search_evidence_metadata(resolution),
            },
        )

    return None
