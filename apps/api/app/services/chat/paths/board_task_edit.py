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
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, record_workflow_step


BOARD_TASK_EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}


class RequirementsFromBoardTaskBuilder(Protocol):
    def __call__(
        self,
        *,
        base: LearningRequirementSheet,
        board_task: BoardTaskRequirementSheet,
        action_type: BoardTaskAction,
        focus: BoardFocusRef | None,
    ) -> LearningRequirementSheet: ...


class ReadyCheckpointPersister(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        package: CoursePackage,
        requirement_history: LearningRequirementHistoryRecorder,
        board_task_history: BoardTaskHistoryRecorder,
        stamp: BoardTaskHistoryStamp,
    ) -> None: ...


class ResourceSummaryBuilder(Protocol):
    def __call__(self, resources: list[ResourceLibraryItem]) -> str: ...


class ConversationSummaryBuilder(Protocol):
    def __call__(self, conversation: list[ConversationTurn]) -> str: ...


class BoardDocumentEditExecutor(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        clarification: LearningClarificationStatus,
        resource_summary: str,
        conversation_summary: str,
        user_instruction: str,
        selection_excerpt: str | None,
        focus: BoardFocusRef | None,
        target_scope: str,
        allow_replace_document: bool,
    ) -> BoardDocumentEditOutcome: ...


class RuntimeRefresher(Protocol):
    def __call__(
        self,
        lesson: Lesson,
        *,
        document: object,
        requirements: LearningRequirementSheet,
    ) -> None: ...


class BoardTeachingGuideBuilder(Protocol):
    def __call__(self, lesson: Lesson) -> object: ...


class RecentBoardEditFocusBuilder(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        fallback_focus: BoardFocusRef | None,
        section_titles: list[str],
    ) -> BoardFocusRef | None: ...


class BoardPatchMetadataBuilder(Protocol):
    def __call__(self, edit_outcome: BoardDocumentEditOutcome) -> dict[str, object]: ...


class BoardSearchEvidenceMetadataBuilder(Protocol):
    def __call__(self, resolution: FocusResolution | None) -> dict[str, object]: ...


class ImplicitBoardSearchEvidenceBuilder(Protocol):
    def __call__(self, *, route: str, target_scope: str, reason: str) -> dict[str, object]: ...


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


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        focus: BoardFocusRef | None = None,
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


class TaskRequirementsClearer(Protocol):
    def __call__(self, lesson: Lesson) -> None: ...


class NormalizePackageState(Protocol):
    def __call__(self, package: CoursePackage) -> None: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


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


class BoardTaskEditResponseBuilder(Protocol):
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
        resolved_focus: BoardFocusRef | None = None,
        requirement_cleared: bool = False,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
        board_document_operation_status: str = "none",
        board_document_operation_failure_reason: str | None = None,
        completed_board_task_sheet: BoardTaskRequirementSheet | None = None,
        board_patch_diff: object | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskEditDependencies:
    requirements_from_board_task: RequirementsFromBoardTaskBuilder
    persist_ready_checkpoint: ReadyCheckpointPersister
    resource_summary: ResourceSummaryBuilder
    conversation_summary: ConversationSummaryBuilder
    edit_existing_document: BoardDocumentEditExecutor
    refresh_lesson_runtime: RuntimeRefresher
    build_board_teaching_guide: BoardTeachingGuideBuilder
    recent_board_edit_focus_for_commit: RecentBoardEditFocusBuilder
    board_patch_metadata: BoardPatchMetadataBuilder
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    implicit_board_search_evidence: ImplicitBoardSearchEvidenceBuilder
    decision_trace_metadata: DecisionTraceMetadataBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    clear_task_requirements: TaskRequirementsClearer
    normalize_package_state: NormalizePackageState
    save_workspace_for_user: SaveWorkspaceForUser
    commit_operations: CommitOperations
    build_response: BoardTaskEditResponseBuilder


def handle_board_task_edit_terminal(
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
    selection_excerpt: str | None,
    resolution: FocusResolution | None,
    action_type: BoardTaskAction | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    decision: BoardTaskRouteDecision,
    action_decision: BoardTaskActionDecision | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskEditDependencies,
) -> ChatResponse:
    if decision.route != "edit":
        raise ValueError(f"board task edit handler requires route='edit', got {decision.route!r}")

    interaction_metadata = source_interaction_metadata or {}
    focus = decision.target_focus or (resolution.focus if resolution else None)
    edit_action = action_type if action_type in BOARD_TASK_EDIT_ACTIONS else "rewrite_target"
    target_scope = decision.target_scope or (
        "whole_document" if focus and focus.match_id and focus.match_id.startswith("whole_document:") else "focus"
    )
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type=edit_action,
        focus=focus,
    )
    stamp = board_task_history.record_update(sheet=board_task, status="ready")
    deps.persist_ready_checkpoint(
        user_id=user_id,
        workspace=workspace,
        package=package,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        stamp=stamp,
    )
    edit_outcome = deps.edit_existing_document(
        lesson=lesson,
        requirements=task_requirements,
        clarification=learning_clarification,
        resource_summary=deps.resource_summary(resources),
        conversation_summary=deps.conversation_summary(request.conversation),
        user_instruction=request.message,
        selection_excerpt=selection_excerpt,
        focus=focus,
        target_scope=target_scope,
        allow_replace_document=target_scope == "whole_document",
    )

    if edit_outcome.changed:
        deps.refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
        lesson.board_teaching_guide = deps.build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        record_workflow_step(
            NodeId.BOARD_EDIT_EXECUTE,
            decision=edit_outcome.operation_status,
            reason=edit_outcome.summary or decision.reason,
            run_id=stamp.run_id,
            version_id=stamp.version_id,
        )

    if not edit_outcome.changed:
        failed_stamp = board_task_history.execution_failed(
            reason=edit_outcome.summary or "Board task edit did not produce a safe document change.",
            metadata={
                "assistant_message_source": edit_outcome.assistant_message_source,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_task_route": "edit",
                "board_task_decision": decision.model_dump(mode="json"),
                "board_task_cleared": False,
                "target_scope": target_scope,
                **deps.board_patch_metadata(edit_outcome),
                **deps.board_search_evidence_metadata(resolution),
            },
        )
        deps.normalize_package_state(package)
        deps.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        record_workflow_step(
            NodeId.BOARD_TASK_FAILURE,
            decision="execution_failed",
            reason=edit_outcome.summary or "Board task edit did not produce a safe document change.",
            run_id=failed_stamp.run_id,
            version_id=failed_stamp.version_id,
        )
        response = deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            resolved_focus=focus,
            requirement_cleared=False,
            board_task_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
            board_patch_diff=edit_outcome.diff_preview,
        )
        record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
        return response

    recent_focus = deps.recent_board_edit_focus_for_commit(
        lesson=lesson,
        fallback_focus=None if target_scope == "whole_document" else focus,
        section_titles=edit_outcome.section_titles,
    )
    deps.commit_operations(
        lesson,
        edit_outcome.operations or [],
        label="Board task edit",
        message="Executed an existing-board edit task",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_edit",
            "user_message": request.message,
            "assistant_message": edit_outcome.chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            "target_scope": target_scope,
            "recent_board_edit_focus": recent_focus.model_dump(mode="json") if recent_focus else None,
            **deps.board_patch_metadata(edit_outcome),
            **interaction_metadata,
            **deps.decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="board_editor",
                document_changed=edit_outcome.changed,
                reason=edit_outcome.summary or decision.reason,
                target_scope=target_scope,
            ),
            "board_search_evidence": (
                resolution.evidence.model_dump(mode="json")
                if resolution and resolution.evidence
                else deps.implicit_board_search_evidence(
                    route="edit",
                    target_scope=target_scope,
                    reason="编辑链路使用全文或继承目标范围，没有独立检索证据。",
                )
            ),
            **deps.task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                focus=focus,
                requirement_cleared=True,
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=stamp,
                route="edit",
                decision=decision.model_dump(mode="json"),
                cleared=True,
            ),
        },
    )
    commit = lesson.history_graph.commits[-1]
    consumed_stamp = board_task_history.consume(commit_id=commit.id)
    lesson.board_task_requirements = None
    deps.clear_task_requirements(lesson)
    deps.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    record_workflow_step(
        NodeId.PERSIST_BOARD_COMMIT,
        decision="committed",
        run_id=consumed_stamp.run_id,
        version_id=consumed_stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=edit_outcome.chatbot_message,
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        resolved_focus=focus,
        requirement_cleared=True,
        board_task_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
        completed_board_task_sheet=board_task,
        board_patch_diff=edit_outcome.diff_preview,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
