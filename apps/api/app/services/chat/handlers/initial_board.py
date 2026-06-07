from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models import (
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services import workspace_state
from app.services.board_document_editor import generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide
from app.services.chat.metadata import (
    _board_document_failure_metadata,
    _board_document_quality_metadata,
    _reference_metadata,
    _requirement_history_metadata,
    _task_metadata,
)
from app.services.chat.response import _response
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder, RequirementHistoryStamp
from app.services.resource_resolver import ResourceResolution


@dataclass(frozen=True)
class InitialBoardRuntime:
    with_task_details: Callable[..., LearningRequirementSheet]
    prepare_initial_requirement_for_board_generation: Callable[
        ..., tuple[LearningRequirementSheet, LearningClarificationStatus, RequirementHistoryStamp | None]
    ]
    checkpoint_initial_requirement_before_generation: Callable[..., None]
    post_initial_board_generation_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]


def generate_board_from_confirmed_resource(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary_for_turn: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    runtime: InitialBoardRuntime,
) -> ChatResponse:
    requirements = runtime.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = runtime.prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
    )
    runtime.checkpoint_initial_requirement_before_generation(
        user_id=user_id,
        workspace=workspace,
        package=package,
        lesson=lesson,
        requirement_history=requirement_history,
        requirements=requirements,
        learning_clarification=learning_clarification,
        stamp=frozen_requirement,
    )
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    chatbot_message = edit_outcome.chatbot_message
    if not edit_outcome.changed:
        failed_stamp = (
            requirement_history.generation_failed(
                reason=edit_outcome.summary or chatbot_message,
                metadata=_board_document_failure_metadata(edit_outcome),
            )
            if frozen_requirement is not None
            else None
        )
        workspace_state.normalize_package_state(package)
        runtime.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
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
    refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
    lesson.board_teaching_guide = build_board_teaching_guide(lesson)
    lesson.board_teaching_progress = None
    chatbot_message, chatbot_message_source = runtime.post_initial_board_generation_message(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        edit_outcome=edit_outcome,
    )
    requirement_cleared = True
    commit_operations(
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
            **_board_document_quality_metadata(edit_outcome),
            **_requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_reference_metadata(resolution=resource_resolution),
        },
    )
    consumed_stamp = (
        requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if frozen_requirement is not None
        else None
    )
    runtime.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    runtime.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    return _response(
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
