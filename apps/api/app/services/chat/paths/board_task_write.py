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
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.board_teaching import build_board_teaching_guide
from app.services.course_runtime import refresh_lesson_runtime
from app.services.decision_trace import decision_trace_metadata
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision


class RequirementsFromBoardTask(Protocol):
    def __call__(
        self,
        *,
        base: LearningRequirementSheet,
        board_task: BoardTaskRequirementSheet,
        action_type: BoardTaskAction | None,
        focus: BoardFocusRef | None = None,
    ) -> LearningRequirementSheet: ...


class ResourceSummaryBuilder(Protocol):
    def __call__(self, resources: list[ResourceLibraryItem]) -> str: ...


class ConversationSummaryBuilder(Protocol):
    def __call__(self, conversation: list[ConversationTurn]) -> str: ...


class BoardDirectedExplanationGenerator(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        requirements: LearningRequirementSheet,
        resources: list[ResourceLibraryItem],
        conversation: list[ConversationTurn],
        request: ChatRequest,
        learning_clarification: LearningClarificationStatus,
        action_type: str,
        target_excerpt: str,
        interaction_context: dict[str, object] | None = None,
    ) -> tuple[str, str, dict[str, object] | None]: ...


class RecentBoardEditFocusForCommit(Protocol):
    def __call__(
        self,
        *,
        lesson: Lesson,
        fallback_focus: BoardFocusRef | None,
        section_titles: list[str],
    ) -> BoardFocusRef | None: ...


class BoardPatchMetadataBuilder(Protocol):
    def __call__(self, edit_outcome) -> dict[str, object]: ...


class ImplicitBoardSearchEvidenceBuilder(Protocol):
    def __call__(self, *, route: str, target_scope: str | None, reason: str) -> dict[str, object]: ...


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


class ClearTaskRequirements(Protocol):
    def __call__(self, lesson: Lesson) -> None: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class BoardTaskWriteResponseBuilder(Protocol):
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
        requirement_cleared: bool = False,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
        completed_board_task_sheet: BoardTaskRequirementSheet | None = None,
        board_document_operation_status: str = "none",
        board_document_operation_failure_reason: str | None = None,
        board_patch_diff=None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskWriteDependencies:
    requirements_from_board_task: RequirementsFromBoardTask
    resource_summary: ResourceSummaryBuilder
    conversation_summary: ConversationSummaryBuilder
    generate_board_directed_explanation_message: BoardDirectedExplanationGenerator
    recent_board_edit_focus_for_commit: RecentBoardEditFocusForCommit
    board_patch_metadata: BoardPatchMetadataBuilder
    implicit_board_search_evidence: ImplicitBoardSearchEvidenceBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    clear_task_requirements: ClearTaskRequirements
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskWriteResponseBuilder


def handle_board_task_write(
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
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    route_decision: BoardTaskRouteDecision | None = None,
    action_decision: BoardTaskActionDecision | None = None,
    search_evidence: dict[str, object] | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
    deps: BoardTaskWriteDependencies,
) -> ChatResponse:
    interaction_metadata = source_interaction_metadata or {}
    target_focus = route_decision.target_focus if route_decision else None
    target_scope = (route_decision.target_scope if route_decision else None) or ("focus" if target_focus else "append")
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="expand_target" if target_focus else "append_section",
        focus=target_focus,
    )
    task_requirements.action_instruction = (
        route_decision.write_proposal if route_decision and route_decision.write_proposal else board_task.question_or_topic
    )
    stamp = board_task_history.record_update(
        sheet=board_task,
        status="awaiting_confirmation" if board_task.confirmation_status == "confirmed" else "ready",
    )
    edit_outcome = edit_existing_document(
        lesson=lesson,
        requirements=task_requirements,
        clarification=learning_clarification,
        resource_summary=deps.resource_summary(resources),
        conversation_summary=deps.conversation_summary(request.conversation),
        user_instruction=task_requirements.action_instruction,
        selection_excerpt=None,
        focus=target_focus,
        target_scope=target_scope,
        allow_replace_document=False,
    )
    if edit_outcome.changed:
        old_text = lesson.board_document.content_text
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        recent_focus = deps.recent_board_edit_focus_for_commit(
            lesson=lesson,
            fallback_focus=target_focus,
            section_titles=edit_outcome.section_titles,
        )
        new_text = lesson.board_document.content_text
        appended_excerpt = new_text[len(old_text) :].strip() if new_text.startswith(old_text) else edit_outcome.new_document.content_text
        if edit_outcome.chatbot_message and board_task.confirmation_status != "confirmed":
            chatbot_message = edit_outcome.chatbot_message
            chatbot_message_source = edit_outcome.assistant_message_source
            board_explanation_directive = {
                "status": "approved",
                "source": "board_document_editor_ai",
                "target_excerpt": appended_excerpt or edit_outcome.new_document.content_text,
            }
        else:
            chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_board_directed_explanation_message(
                lesson=lesson,
                requirements=task_requirements,
                resources=resources,
                conversation=request.conversation,
                request=request,
                learning_clarification=learning_clarification,
                action_type="explain_target",
                target_excerpt=appended_excerpt or edit_outcome.new_document.content_text,
            )
    else:
        chatbot_message = edit_outcome.chatbot_message
        chatbot_message_source = edit_outcome.assistant_message_source
        board_explanation_directive = None
        recent_focus = None

    implicit_search_evidence = deps.implicit_board_search_evidence(
        route="write",
        target_scope=target_scope,
        reason="写链路没有独立定位证据；由任务清单和 Board AI 裁决进入。",
    )

    if not edit_outcome.changed:
        failed_stamp = board_task_history.execution_failed(
            reason=edit_outcome.summary or "Board task write did not produce a safe document change.",
            metadata={
                "assistant_message_source": chatbot_message_source,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_task_route": "write",
                "board_task_decision": route_decision.model_dump(mode="json") if route_decision else None,
                "board_task_cleared": False,
                "target_scope": target_scope,
                **deps.board_patch_metadata(edit_outcome),
                "board_search_evidence": search_evidence or implicit_search_evidence,
            },
        )
        workspace_state.normalize_package_state(package)
        deps.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_cleared=False,
            board_task_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
            board_patch_diff=edit_outcome.diff_preview,
        )

    commit_operations(
        lesson,
        edit_outcome.operations or [],
        label="Board task write",
        message="Wrote missing existing-board task content and prepared a board-grounded explanation",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_edit",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            "target_scope": target_scope,
            "recent_board_edit_focus": recent_focus.model_dump(mode="json") if recent_focus else None,
            "board_explanation_directive": board_explanation_directive,
            **deps.board_patch_metadata(edit_outcome),
            **interaction_metadata,
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=route_decision,
                role_executed="board_editor",
                document_changed=edit_outcome.changed,
                reason=edit_outcome.summary or (route_decision.reason if route_decision else ""),
                target_scope=target_scope,
            ),
            "board_search_evidence": search_evidence or implicit_search_evidence,
            **deps.task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                focus=target_focus,
                requirement_cleared=edit_outcome.changed,
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=stamp,
                route="write",
                decision=route_decision.model_dump(mode="json") if route_decision else None,
                cleared=edit_outcome.changed,
            ),
        },
    )
    consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id) if edit_outcome.changed else stamp
    if edit_outcome.changed:
        lesson.board_task_requirements = None
        deps.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        requirement_cleared=edit_outcome.changed,
        board_task_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
        completed_board_task_sheet=board_task if edit_outcome.changed else None,
        board_patch_diff=edit_outcome.diff_preview,
    )
