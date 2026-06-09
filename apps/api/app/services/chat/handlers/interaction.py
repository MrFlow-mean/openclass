from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.models import (
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution


@dataclass(frozen=True)
class BoardTaskInteractionHandlerDeps:
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    start_interaction_session: Callable[..., ChatResponse | None]


def execute_board_task_chat_interaction(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    requirement_history: LearningRequirementHistoryRecorder,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    interaction_metadata: dict[str, object],
    deps: BoardTaskInteractionHandlerDeps,
) -> ChatResponse | None:
    focus = decision.target_focus or (resolution.focus if resolution else None)
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=focus,
    )
    lesson.learning_requirements = task_requirements
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
        action_type="explain_target",
        requirement_history=requirement_history,
        board_task=board_task,
        board_task_history=board_task_history,
        board_task_stamp=board_task_stamp,
        board_task_decision=decision,
        resolved_focus=focus,
        source_interaction_metadata={
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
        },
    )
