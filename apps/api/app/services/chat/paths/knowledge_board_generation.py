from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardTaskAction,
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
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.openai_course_ai import InitialLearningWorkModeDecision
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step


class MinimalInitialLearningState(Protocol):
    def __call__(
        self,
        base: LearningRequirementSheet,
        *,
        decision: InitialLearningWorkModeDecision,
        user_message: str,
        generate_board: bool,
    ) -> tuple[LearningRequirementSheet, LearningClarificationStatus]: ...


class WithTaskDetails(Protocol):
    def __call__(
        self,
        requirements: LearningRequirementSheet,
        *,
        action_type: BoardTaskAction | None,
        instruction: str,
    ) -> LearningRequirementSheet: ...


class PrepareInitialRequirementForBoardGeneration(Protocol):
    def __call__(
        self,
        requirement_history: LearningRequirementHistoryRecorder,
        *,
        enabled: bool,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
    ) -> tuple[LearningRequirementSheet, LearningClarificationStatus, RequirementHistoryStamp | None]: ...


class CheckpointInitialRequirementBeforeGeneration(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        requirement_history: LearningRequirementHistoryRecorder,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        stamp: RequirementHistoryStamp | None,
    ) -> None: ...


class GenerateFromRequirements(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        clarification: LearningClarificationStatus,
        resource_summary: str,
        requirement_run_id: str | None = None,
        frozen_requirement_version_id: str | None = None,
    ): ...


class RefreshLessonRuntime(Protocol):
    def __call__(
        self,
        lesson: Lesson,
        *,
        document: object,
        requirements: LearningRequirementSheet,
    ) -> None: ...


class BuildBoardTeachingGuide(Protocol):
    def __call__(self, lesson: Lesson) -> object: ...


class PostInitialBoardGenerationMessage(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        resource_summary: str,
        edit_outcome,
    ) -> tuple[str, str]: ...


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


class TaskRequirementsClearer(Protocol):
    def __call__(self, lesson: Lesson) -> None: ...


class BoardDocumentFailureMetadataBuilder(Protocol):
    def __call__(self, edit_outcome) -> dict[str, object]: ...


class BoardDocumentQualityMetadataBuilder(Protocol):
    def __call__(self, edit_outcome) -> dict[str, object]: ...


class RequirementHistoryMetadataBuilder(Protocol):
    def __call__(
        self,
        stamp: RequirementHistoryStamp | None,
        *,
        run_status_after_commit: str | None = None,
    ) -> dict[str, object]: ...


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class InitialLearningWorkModeMetadataBuilder(Protocol):
    def __call__(self, decision: InitialLearningWorkModeDecision | None) -> dict[str, object]: ...


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


class KnowledgeBoardMinimalGenerationResponseBuilder(Protocol):
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
        selected_reference: ResourceReferenceContext | None = None,
        requirement_cleared: bool = False,
        requirement_stamp: RequirementHistoryStamp | None = None,
        board_document_operation_status: str = "none",
        board_document_operation_failure_reason: str | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class KnowledgeBoardMinimalGenerationDependencies:
    minimal_initial_learning_state: MinimalInitialLearningState
    with_task_details: WithTaskDetails
    prepare_initial_requirement_for_board_generation: PrepareInitialRequirementForBoardGeneration
    checkpoint_initial_requirement_before_generation: CheckpointInitialRequirementBeforeGeneration
    generate_from_requirements: GenerateFromRequirements
    refresh_lesson_runtime: RefreshLessonRuntime
    build_board_teaching_guide: BuildBoardTeachingGuide
    post_initial_board_generation_message: PostInitialBoardGenerationMessage
    commit_operations: CommitOperations
    clear_task_requirements: TaskRequirementsClearer
    board_document_failure_metadata: BoardDocumentFailureMetadataBuilder
    board_document_quality_metadata: BoardDocumentQualityMetadataBuilder
    requirement_history_metadata: RequirementHistoryMetadataBuilder
    task_metadata: TaskMetadataBuilder
    initial_learning_work_mode_metadata: InitialLearningWorkModeMetadataBuilder
    reference_metadata: ReferenceMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: KnowledgeBoardMinimalGenerationResponseBuilder


def handle_knowledge_board_minimal_generation(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    decision: InitialLearningWorkModeDecision,
    resource_summary_for_turn: str,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: KnowledgeBoardMinimalGenerationDependencies,
) -> ChatResponse:
    _validate_trigger_contract(
        decision=decision,
        resource_resolution=resource_resolution,
        selected_reference=selected_reference,
    )
    requirements, learning_clarification = deps.minimal_initial_learning_state(
        requirements,
        decision=decision,
        user_message=request.message,
        generate_board=True,
    )
    if not learning_clarification.ready_for_board or requirements.action_type != "generate_board":
        raise ValueError("knowledge_board minimal generation requires a ready generate_board minimal state")

    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = (
        deps.prepare_initial_requirement_for_board_generation(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
        )
    )
    deps.checkpoint_initial_requirement_before_generation(
        user_id=user_id,
        workspace=workspace,
        package=package,
        lesson=lesson,
        requirement_history=requirement_history,
        requirements=requirements,
        learning_clarification=learning_clarification,
        stamp=frozen_requirement,
    )
    if frozen_requirement is not None:
        record_workflow_step(
            NodeId.INITIAL_REQUIREMENT_FREEZE,
            decision="frozen",
            run_id=frozen_requirement.run_id,
            version_id=frozen_requirement.version_id,
        )
    record_workflow_step(
        NodeId.INITIAL_BOARD_GENERATE,
        decision="board_editor",
        run_id=frozen_requirement.run_id if frozen_requirement else None,
        version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    edit_outcome = deps.generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    if not edit_outcome.changed:
        failed_stamp = (
            requirement_history.generation_failed(
                reason=edit_outcome.summary or edit_outcome.chatbot_message,
                metadata=deps.board_document_failure_metadata(edit_outcome),
            )
            if frozen_requirement is not None
            else None
        )
        workspace_state.normalize_package_state(package)
        deps.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        if failed_stamp is not None:
            record_workflow_step(
                NodeId.INITIAL_GENERATION_FAILED,
                decision="generation_failed",
                reason=edit_outcome.failure_reason or edit_outcome.summary or edit_outcome.chatbot_message,
                run_id=failed_stamp.run_id,
                version_id=failed_stamp.version_id,
            )
        response = deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
        record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
        return response

    deps.refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
    lesson.board_teaching_guide = deps.build_board_teaching_guide(lesson)
    lesson.board_teaching_progress = None
    chatbot_message, chatbot_message_source = deps.post_initial_board_generation_message(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        edit_outcome=edit_outcome,
    )
    deps.commit_operations(
        lesson,
        [],
        label="Knowledge board generation",
        message="Generated a focused new-knowledge board from a minimal requirement sheet",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_generation",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "interaction_mode": request.interaction_mode,
            "board_generation_action": "knowledge_board_minimal_requirement",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **deps.board_document_quality_metadata(edit_outcome),
            **deps.requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=True,
            ),
            **deps.initial_learning_work_mode_metadata(decision),
            **deps.reference_metadata(resolution=resource_resolution),
        },
    )
    consumed_stamp = (
        requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if frozen_requirement is not None
        else None
    )
    deps.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    if consumed_stamp is not None:
        record_workflow_step(
            NodeId.INITIAL_BOARD_COMMIT,
            decision="committed",
            run_id=consumed_stamp.run_id,
            version_id=consumed_stamp.version_id,
            commit_id=lesson.history_graph.commits[-1].id,
        )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=True,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def _validate_trigger_contract(
    *,
    decision: InitialLearningWorkModeDecision,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
) -> None:
    if decision.work_mode != "knowledge_board":
        raise ValueError("knowledge_board minimal generation requires work_mode=knowledge_board")
    if selected_reference is not None or resource_resolution.selected_reference is not None:
        raise ValueError("knowledge_board minimal generation does not accept confirmed resource context")
    if resource_resolution.reference_prompt is not None:
        raise ValueError("knowledge_board minimal generation does not own resource prompt handling")
