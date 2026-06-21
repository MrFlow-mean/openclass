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
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step


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
        reference_context: ResourceReferenceContext | None = None,
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


class ConfirmedResourceGenerationResponseBuilder(Protocol):
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
class ConfirmedResourceGenerationDependencies:
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
    reference_metadata: ReferenceMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: ConfirmedResourceGenerationResponseBuilder


def handle_confirmed_resource_generation(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary_for_turn: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: ConfirmedResourceGenerationDependencies,
) -> ChatResponse:
    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    ready_requirement = (
        requirement_history.current_stamp()
        if track_initial_requirement_run and requirement_history.snapshot.status == "ready"
        else None
    )
    record_workflow_step(
        NodeId.RESOURCE_CONFIRMED_GENERATE,
        decision="confirmed",
        reason="Confirmed resource reference will generate the first board.",
        run_id=ready_requirement.run_id if ready_requirement else None,
        version_id=ready_requirement.version_id if ready_requirement else None,
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
    if ready_requirement is not None:
        record_workflow_step(
            NodeId.INITIAL_REQUIREMENT_READY,
            decision="ready",
            run_id=ready_requirement.run_id,
            version_id=ready_requirement.version_id,
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
        reference_context=resource_resolution.selected_reference,
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    chatbot_message = edit_outcome.chatbot_message
    if not edit_outcome.changed:
        failed_stamp = (
            requirement_history.generation_failed(
                reason=edit_outcome.summary or chatbot_message,
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
                reason=edit_outcome.failure_reason or edit_outcome.summary or chatbot_message,
                run_id=failed_stamp.run_id,
                version_id=failed_stamp.version_id,
            )
        response = deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=edit_outcome.board_decision,
            resource_matches=resource_resolution.matches,
            selected_reference=resource_resolution.selected_reference,
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
    requirement_cleared = edit_outcome.changed
    deps.commit_operations(
        lesson,
        [],
        label="Resource-backed board generation",
        message="Generated board document from a confirmed uploaded resource chapter",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_generation",
            "resource_backed_generation": True,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "interaction_mode": request.interaction_mode,
            "resource_reference_action": request.resource_reference_action,
            "board_generation_action": "resource_reference_confirm",
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
                requirement_cleared=requirement_cleared,
            ),
            **deps.reference_metadata(resolution=resource_resolution),
        },
    )
    consumed_stamp = (
        requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if frozen_requirement is not None
        else None
    )
    if requirement_cleared:
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
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=resource_resolution.selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
