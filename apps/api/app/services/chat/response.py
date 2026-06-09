from __future__ import annotations

from typing import Any

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatResponse,
    InteractionTurnDecision,
    LearningClarificationStatus,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)


def board_task_questions(sheet: BoardTaskRequirementSheet | None) -> list[str]:
    if sheet is None:
        return []
    question = sheet.clarification_question.strip()
    return [question] if question else []


def response_requirement_stamp(
    requirement_history: LearningRequirementHistoryRecorder | None,
    requirement_stamp: RequirementHistoryStamp | None,
) -> RequirementHistoryStamp | None:
    if requirement_stamp is not None:
        return requirement_stamp
    if requirement_history is None:
        return None
    return requirement_history.current_stamp()


def response_board_task_stamp(
    board_task_history: BoardTaskHistoryRecorder | None,
    board_task_stamp: BoardTaskHistoryStamp | None,
) -> BoardTaskHistoryStamp | None:
    if board_task_stamp is not None:
        return board_task_stamp
    if board_task_history is None:
        return None
    return board_task_history.current_stamp()


def build_chat_response(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    chatbot_message: str,
    requirements: Any,
    learning_clarification: LearningClarificationStatus,
    board_decision: BoardDecision,
    teaching_progress: Any = None,
    resolved_focus: BoardFocusRef | None = None,
    focus_candidates: list[BoardFocusRef] | None = None,
    resource_matches: list[ResourceMatch] | None = None,
    reference_prompt: ResourceReferencePrompt | None = None,
    selected_reference: ResourceReferenceContext | None = None,
    interaction_decision: InteractionTurnDecision | None = None,
    requirement_cleared: bool = False,
    requirement_history: LearningRequirementHistoryRecorder | None = None,
    requirement_stamp: RequirementHistoryStamp | None = None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    completed_board_task_sheet: BoardTaskRequirementSheet | None = None,
    board_document_operation_status: str = "none",
    board_document_operation_failure_reason: str | None = None,
) -> ChatResponse:
    stamp = response_requirement_stamp(requirement_history, requirement_stamp)
    board_task_stamp_value = response_board_task_stamp(board_task_history, board_task_stamp)
    visible_board_task_sheet = lesson.board_task_requirements or completed_board_task_sheet
    visible_requirement_cleared = requirement_cleared or lesson.board_task_requirements is not None
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=None if visible_board_task_sheet is not None else lesson.learning_requirements,
        active_interaction_session=lesson.active_interaction_session,
        interaction_decision=interaction_decision,
        learning_clarification=learning_clarification,
        requirement_run_id=stamp.run_id if stamp else None,
        requirement_version_id=stamp.version_id if stamp else None,
        requirement_phase=stamp.phase if stamp else None,
        board_task_sheet=visible_board_task_sheet,
        active_board_task_sheet=lesson.board_task_requirements,
        board_task_run_id=board_task_stamp_value.run_id if board_task_stamp_value else None,
        board_task_version_id=board_task_stamp_value.version_id if board_task_stamp_value else None,
        board_task_phase=board_task_stamp_value.phase if board_task_stamp_value else None,
        board_task_questions=board_task_questions(lesson.board_task_requirements),
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        patch_proposal=None,
        scope_options=[],
        resource_matches=resource_matches or [],
        reference_prompt=reference_prompt,
        board_edit_prompt=None,
        selected_reference=selected_reference,
        resolved_focus=resolved_focus,
        focus_candidates=focus_candidates or [],
        requirement_cleared=visible_requirement_cleared,
        board_document_operation_status=board_document_operation_status,
        board_document_operation_failure_reason=board_document_operation_failure_reason,
        created_lesson=None,
        teaching_progress=teaching_progress,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )
