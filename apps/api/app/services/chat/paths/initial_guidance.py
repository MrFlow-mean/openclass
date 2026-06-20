from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import InitialLearningWorkModeDecision
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step

InitialGuidanceOutcome = Literal["unknown", "narrow_topic"]


class MinimalInitialLearningStateBuilder(Protocol):
    def __call__(
        self,
        requirements: LearningRequirementSheet,
        *,
        decision: InitialLearningWorkModeDecision,
        user_message: str,
        generate_board: bool,
    ) -> tuple[LearningRequirementSheet, LearningClarificationStatus]: ...


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class InitialLearningWorkModeMetadataBuilder(Protocol):
    def __call__(self, decision: InitialLearningWorkModeDecision) -> dict[str, object]: ...


class ReferenceMetadataBuilder(Protocol):
    def __call__(self, *, resolution: ResourceResolution) -> dict[str, object]: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
    ) -> None: ...


class InitialGuidanceResponseBuilder(Protocol):
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
        resource_matches: list[ResourceMatch] | None = None,
        selected_reference: ResourceReferenceContext | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class InitialGuidanceDependencies:
    minimal_initial_learning_state: MinimalInitialLearningStateBuilder
    task_metadata: TaskMetadataBuilder
    initial_learning_work_mode_metadata: InitialLearningWorkModeMetadataBuilder
    reference_metadata: ReferenceMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: InitialGuidanceResponseBuilder


def handle_initial_guidance(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    decision: InitialLearningWorkModeDecision,
    outcome: InitialGuidanceOutcome,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
    requirement_history: LearningRequirementHistoryRecorder,
    deps: InitialGuidanceDependencies,
) -> ChatResponse:
    if outcome == "unknown":
        chatbot_message = decision.guided_discovery_reply.strip()
        assistant_message_source = "initial_learning_guided_discovery"
        commit_label = "Initial learning guided discovery"
        commit_message = "Suggested learning directions when the initial learning purpose was unclear"
        board_decision = BoardDecision(action="no_change", reason="本轮学习目的不明确，只建议学习方向，不生成板书。")
    elif outcome == "narrow_topic":
        chatbot_message = decision.next_question.strip()
        assistant_message_source = "initial_learning_work_mode"
        commit_label = "Initial learning topic clarification"
        commit_message = "Asked the learner to narrow a broad new-knowledge request"
        board_decision = BoardDecision(action="no_change", reason="本轮只缩小新知识学习主题，不生成板书。")
    else:
        raise ValueError(f"unsupported initial guidance outcome: {outcome}")

    if not chatbot_message:
        raise ValueError(f"{outcome} initial guidance requires a visible message")

    requirements, learning_clarification = deps.minimal_initial_learning_state(
        requirements,
        decision=decision,
        user_message=request.message,
        generate_board=False,
    )
    lesson.learning_requirements = requirements
    commit_operations(
        lesson,
        [],
        label=commit_label,
        message=commit_message,
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": assistant_message_source,
            "interaction_mode": request.interaction_mode,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **deps.initial_learning_work_mode_metadata(decision),
            **deps.reference_metadata(resolution=resource_resolution),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
    record_workflow_step(
        NodeId.PERSIST_CHAT_COMMIT,
        decision="committed",
        commit_id=lesson.history_graph.commits[-1].id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
