from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.board_teaching import build_board_teaching_guide
from app.services.chat.intent import EDIT_ACTIONS
from app.services.chat.metadata import _board_task_metadata, _focus_metadata
from app.services.chat.response import _response
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution


@dataclass(frozen=True)
class EditBlackboardRuntime:
    resource_summary: Callable[[list[ResourceLibraryItem]], str]
    conversation_summary: Callable[[list[ConversationTurn]], str]
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    recent_board_edit_focus_for_commit: Callable[..., BoardFocusRef | None]
    implicit_board_search_evidence: Callable[..., dict[str, object]]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]


def handle_board_task_write(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    runtime: EditBlackboardRuntime,
    route_decision: BoardTaskRouteDecision | None = None,
    search_evidence: dict[str, object] | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    interaction_metadata = source_interaction_metadata or {}
    target_focus = route_decision.target_focus if route_decision else None
    target_scope = (route_decision.target_scope if route_decision else None) or ("focus" if target_focus else "append")
    task_requirements = runtime.requirements_from_board_task(
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
        resource_summary=runtime.resource_summary(resources),
        conversation_summary=runtime.conversation_summary(request.conversation),
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
        recent_focus = runtime.recent_board_edit_focus_for_commit(
            lesson=lesson,
            fallback_focus=target_focus,
            section_titles=edit_outcome.section_titles,
        )
        new_text = lesson.board_document.content_text
        appended_excerpt = new_text[len(old_text):].strip() if new_text.startswith(old_text) else edit_outcome.new_document.content_text
        if edit_outcome.chatbot_message and board_task.confirmation_status != "confirmed":
            chatbot_message = edit_outcome.chatbot_message
            chatbot_message_source = edit_outcome.assistant_message_source
            board_explanation_directive = {
                "status": "approved",
                "source": "board_document_editor_ai",
                "target_excerpt": appended_excerpt or edit_outcome.new_document.content_text,
            }
        else:
            chatbot_message, chatbot_message_source, board_explanation_directive = runtime.generate_board_directed_explanation_message(
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
                "board_search_evidence": search_evidence
                or runtime.implicit_board_search_evidence(
                    route="write",
                    target_scope=target_scope,
                    reason="写链路没有独立定位证据；由任务清单和 Board AI 裁决进入。",
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        runtime.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
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
        )

    commit_operations(
        lesson,
        [],
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
            **interaction_metadata,
            "board_search_evidence": search_evidence
            or runtime.implicit_board_search_evidence(
                route="write",
                target_scope=target_scope,
                reason="写链路没有独立定位证据；由任务清单和 Board AI 裁决进入。",
            ),
            **_focus_metadata(focus=target_focus),
            **_board_task_metadata(
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
        runtime.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    runtime.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return _response(
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
    )


def handle_board_task_edit(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    action_type: str | None,
    selection_excerpt: str | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    runtime: EditBlackboardRuntime,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    interaction_metadata = source_interaction_metadata or {}
    focus = decision.target_focus or (resolution.focus if resolution else None)
    edit_action = action_type if action_type in EDIT_ACTIONS else "rewrite_target"
    target_scope = decision.target_scope or (
        "whole_document" if focus and focus.match_id and focus.match_id.startswith("whole_document:") else "focus"
    )
    task_requirements = runtime.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type=edit_action,
        focus=focus,
    )
    edit_outcome = edit_existing_document(
        lesson=lesson,
        requirements=task_requirements,
        clarification=learning_clarification,
        resource_summary=runtime.resource_summary(resources),
        conversation_summary=runtime.conversation_summary(request.conversation),
        user_instruction=request.message,
        selection_excerpt=selection_excerpt,
        focus=focus,
        target_scope=target_scope,
        allow_replace_document=target_scope == "whole_document",
    )
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
    stamp = board_task_history.record_update(sheet=board_task, status="ready")
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
                **runtime.board_search_evidence_metadata(resolution),
            },
        )
        workspace_state.normalize_package_state(package)
        runtime.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
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
        )
    recent_focus = runtime.recent_board_edit_focus_for_commit(
        lesson=lesson,
        fallback_focus=None if target_scope == "whole_document" else focus,
        section_titles=edit_outcome.section_titles,
    )
    commit_operations(
        lesson,
        [],
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
            **interaction_metadata,
            "board_search_evidence": (
                resolution.evidence.model_dump(mode="json")
                if resolution and resolution.evidence
                else runtime.implicit_board_search_evidence(
                    route="edit",
                    target_scope=target_scope,
                    reason="编辑链路使用全文或继承目标范围，没有独立检索证据。",
                )
            ),
            **_focus_metadata(focus=focus),
            **_board_task_metadata(
                board_task=board_task,
                stamp=stamp,
                route="edit",
                decision=decision.model_dump(mode="json"),
                cleared=True,
            ),
        },
    )
    consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
    lesson.board_task_requirements = None
    runtime.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    runtime.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return _response(
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
    )
