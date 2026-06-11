from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

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
    _learning_requirement_metadata,
)
from app.services.chat.response import _response
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder, RequirementHistoryStamp
from app.services.resource_resolver import ResourceResolution


InitialBoardGenerationTrigger = Literal[
    "explicit_start",
    "explicit_board_request",
    "resource_reference_confirm",
]


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


def run_initial_board_generation(
    *,
    trigger: InitialBoardGenerationTrigger,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_summary: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    runtime: InitialBoardRuntime,
    resource_resolution: ResourceResolution | None = None,
    chatbot_requirement_reply: str | None = None,
    solver_metadata: dict[str, object] | None = None,
    action_instruction: str | None = None,
) -> ChatResponse:
    requirements = runtime.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=action_instruction or request.message,
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
        resource_summary=resource_summary,
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
        response_kwargs = _reference_response_kwargs(resource_resolution)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message or (chatbot_requirement_reply or ""),
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=edit_outcome.board_decision,
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
            **response_kwargs,
        )
    refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
    lesson.board_teaching_guide = build_board_teaching_guide(lesson)
    lesson.board_teaching_progress = None
    chatbot_message, chatbot_message_source = runtime.post_initial_board_generation_message(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_summary=resource_summary,
        edit_outcome=edit_outcome,
    )
    requirement_cleared = True
    label, commit_message, metadata = _generation_commit_details(
        trigger=trigger,
        request=request,
        chatbot_message=chatbot_message,
        chatbot_message_source=chatbot_message_source,
        edit_outcome=edit_outcome,
        requirements=requirements,
        learning_clarification=learning_clarification,
        frozen_requirement=frozen_requirement,
        requirement_cleared=requirement_cleared,
        resource_resolution=resource_resolution,
        chatbot_requirement_reply=chatbot_requirement_reply,
        solver_metadata=solver_metadata or {},
    )
    commit_operations(
        lesson,
        [],
        label=label,
        message=commit_message,
        new_document=lesson.board_document,
        metadata=metadata,
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
    response_kwargs = _reference_response_kwargs(resource_resolution)
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=edit_outcome.board_decision,
        requirement_cleared=requirement_cleared,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
        **response_kwargs,
    )


def _reference_response_kwargs(resource_resolution: ResourceResolution | None) -> dict[str, object]:
    if resource_resolution is None:
        return {}
    return {
        "resource_matches": resource_resolution.matches,
        "selected_reference": resource_resolution.selected_reference,
    }


def _base_generation_metadata(
    *,
    request: ChatRequest,
    chatbot_message: str,
    chatbot_message_source: str,
    edit_outcome,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    frozen_requirement: RequirementHistoryStamp | None,
    requirement_cleared: bool,
    board_generation_action: str | None,
) -> dict[str, object]:
    return {
        "kind": "board_document_generation",
        "user_message": request.message,
        "assistant_message": chatbot_message,
        "assistant_message_source": chatbot_message_source,
        "board_editor_message": edit_outcome.chatbot_message,
        "board_generation_action": board_generation_action,
        "board_edit_operation": edit_outcome.operation,
        "board_edit_summary": edit_outcome.summary,
        "board_section_titles": edit_outcome.section_titles,
        **_board_document_quality_metadata(edit_outcome),
        **_requirement_history_metadata(
            frozen_requirement,
            run_status_after_commit="consumed" if frozen_requirement is not None else None,
        ),
        **_learning_requirement_metadata(
            requirements=requirements,
            learning_clarification=learning_clarification,
            requirement_cleared=requirement_cleared,
        ),
    }


def _generation_commit_details(
    *,
    trigger: InitialBoardGenerationTrigger,
    request: ChatRequest,
    chatbot_message: str,
    chatbot_message_source: str,
    edit_outcome,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    frozen_requirement: RequirementHistoryStamp | None,
    requirement_cleared: bool,
    resource_resolution: ResourceResolution | None,
    chatbot_requirement_reply: str | None,
    solver_metadata: dict[str, object],
) -> tuple[str, str, dict[str, object]]:
    if trigger == "explicit_start":
        metadata = _base_generation_metadata(
            request=request,
            chatbot_message=chatbot_message,
            chatbot_message_source=chatbot_message_source,
            edit_outcome=edit_outcome,
            requirements=requirements,
            learning_clarification=learning_clarification,
            frozen_requirement=frozen_requirement,
            requirement_cleared=requirement_cleared,
            board_generation_action=request.board_generation_action,
        )
        return "Board document generation", "Generated board document from the learning requirement sheet", metadata

    if trigger == "explicit_board_request":
        metadata = {
            **_base_generation_metadata(
                request=request,
                chatbot_message=chatbot_message,
                chatbot_message_source=chatbot_message_source,
                edit_outcome=edit_outcome,
                requirements=requirements,
                learning_clarification=learning_clarification,
                frozen_requirement=frozen_requirement,
                requirement_cleared=requirement_cleared,
                board_generation_action="explicit_board_request",
            ),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
        }
        if resource_resolution is not None:
            metadata.update(_reference_metadata(resolution=resource_resolution))
        return "Board document generation", "Generated board document from an explicit learner request", metadata

    metadata = {
        **_base_generation_metadata(
            request=request,
            chatbot_message=chatbot_message,
            chatbot_message_source=chatbot_message_source,
            edit_outcome=edit_outcome,
            requirements=requirements,
            learning_clarification=learning_clarification,
            frozen_requirement=frozen_requirement,
            requirement_cleared=requirement_cleared,
            board_generation_action="resource_reference_confirm",
        ),
        "resource_backed_generation": True,
        "interaction_mode": request.interaction_mode,
        "resource_reference_action": request.resource_reference_action,
    }
    if resource_resolution is not None:
        metadata.update(_reference_metadata(resolution=resource_resolution))
    return (
        "Resource-backed board generation",
        "Generated board document from a confirmed uploaded resource chapter",
        metadata,
    )
