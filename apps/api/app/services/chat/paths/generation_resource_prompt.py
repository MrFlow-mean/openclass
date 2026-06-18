from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferencePrompt,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step


GENERATION_RESOURCE_PROMPT_REQUIREMENT_SUMMARY = (
    "Generation requirement persisted while awaiting resource confirmation."
)


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


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


class GenerationResourcePromptResponseBuilder(Protocol):
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
        resource_matches: list[ResourceMatch] | None = None,
        reference_prompt: ResourceReferencePrompt | None = None,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class GenerationResourcePromptDependencies:
    task_metadata: TaskMetadataBuilder
    reference_metadata: ReferenceMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: GenerationResourcePromptResponseBuilder


def handle_generation_resource_prompt(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    requirement_history: LearningRequirementHistoryRecorder,
    include_requirement_history: bool,
    requirement_stamp: RequirementHistoryStamp | None,
    deps: GenerationResourcePromptDependencies,
) -> ChatResponse:
    reference_prompt = resource_resolution.reference_prompt
    if reference_prompt is None:
        raise ValueError("generation resource prompt handler requires a ResourceReferencePrompt")

    if include_requirement_history and requirement_stamp is None:
        requirement_stamp = requirement_history.record_update(
            requirements=requirements,
            clarification=learning_clarification,
            change_summary=GENERATION_RESOURCE_PROMPT_REQUIREMENT_SUMMARY,
        )
    record_workflow_step(
        NodeId.INITIAL_REQUIREMENT_COLLECT,
        decision="recorded" if requirement_stamp is not None else "not_tracked",
        run_id=requirement_stamp.run_id if requirement_stamp is not None else None,
        version_id=requirement_stamp.version_id if requirement_stamp is not None else None,
    )
    lesson.learning_requirements = requirements
    chatbot_message = reference_prompt.question
    record_workflow_step(
        NodeId.RESOURCE_REFERENCE_PROMPT,
        decision="prompted_after_requirement_update",
    )
    commit_operations(
        lesson,
        [],
        label="Resource reference prompt",
        message="Asked the learner to confirm a relevant resource chapter before continuing",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "resource_resolver",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **deps.reference_metadata(resolution=resource_resolution),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
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
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(
            action="await_reference_choice",
            reason=reference_prompt.reason,
        ),
        resource_matches=resource_resolution.matches,
        reference_prompt=reference_prompt,
        requirement_history=requirement_history if include_requirement_history else None,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
