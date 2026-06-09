from __future__ import annotations

import json

from dataclasses import dataclass
from typing import Any, Callable

from app.models import ChatRequest, ChatResponse, LearningClarificationStatus, LearningRequirementSheet, Lesson
from app.services import workspace_state
from app.services.board_document_editor import generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide
from app.services.chat.metadata import (
    board_document_failure_metadata,
    board_document_quality_metadata,
    reference_metadata,
    requirement_history_metadata,
    task_metadata,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import is_document_empty


def should_track_initial_requirement_run(lesson: Lesson) -> bool:
    return is_document_empty(lesson.board_document)


def freeze_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> RequirementHistoryStamp:
    return requirement_history.freeze(
        requirements=requirements,
        clarification=learning_clarification,
        forced=learning_clarification.forced_start or not learning_clarification.ready_for_board,
    )


def frozen_requirement_snapshot(
    requirement_history: LearningRequirementHistoryRecorder,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus] | None:
    snapshot = requirement_history.snapshot
    if snapshot.status != "frozen" or not snapshot.latest_sheet_json or not snapshot.latest_clarification_json:
        return None
    try:
        requirements = LearningRequirementSheet.model_validate(json.loads(snapshot.latest_sheet_json))
        clarification = LearningClarificationStatus.model_validate(json.loads(snapshot.latest_clarification_json))
    except Exception:
        return None
    return requirements, clarification


def normalize_requirement_for_board_generation(
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus]:
    frozen_requirements = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    frozen_clarification = LearningClarificationStatus.model_validate(
        learning_clarification.model_dump(mode="json")
    )
    frozen_requirements.current_questions = []
    frozen_requirements.risk_notes = []
    frozen_requirements.location_clarification_question = ""
    frozen_clarification.progress = 100
    frozen_clarification.missing_items = []
    frozen_clarification.can_start = True
    frozen_clarification.next_question = ""
    if not frozen_clarification.ready_for_board:
        frozen_clarification.forced_start = True
    frozen_clarification.ready_for_board = True
    return frozen_requirements, frozen_clarification


def prepare_initial_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    enabled: bool,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus, RequirementHistoryStamp | None]:
    if not enabled:
        return requirements, learning_clarification, None
    existing_frozen = frozen_requirement_snapshot(requirement_history)
    if existing_frozen is not None:
        frozen_requirements, frozen_clarification = existing_frozen
        return frozen_requirements, frozen_clarification, requirement_history.current_stamp()
    frozen_requirements, frozen_clarification = normalize_requirement_for_board_generation(
        requirements=requirements,
        learning_clarification=learning_clarification,
    )
    frozen_stamp = freeze_requirement_for_board_generation(
        requirement_history,
        requirements=frozen_requirements,
        learning_clarification=frozen_clarification,
    )
    return frozen_requirements, frozen_clarification, frozen_stamp


@dataclass(frozen=True)
class InitialBoardGenerationStartDeps:
    with_task_details: Callable[..., LearningRequirementSheet]
    latest_learning_clarification: Callable[..., LearningClarificationStatus]
    resource_summary: Callable[[list[Any]], str]
    checkpoint_initial_requirement_before_generation: Callable[..., None]
    post_initial_board_generation_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


@dataclass(frozen=True)
class InitialBoardResourceGenerationDeps:
    with_task_details: Callable[..., LearningRequirementSheet]
    checkpoint_initial_requirement_before_generation: Callable[..., None]
    post_initial_board_generation_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


@dataclass(frozen=True)
class InitialBoardReadyGenerationDeps:
    with_task_details: Callable[..., LearningRequirementSheet]
    checkpoint_initial_requirement_before_generation: Callable[..., None]
    post_initial_board_generation_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


@dataclass(frozen=True)
class InitialBoardExplicitGenerationDeps:
    with_task_details: Callable[..., LearningRequirementSheet]
    checkpoint_initial_requirement_before_generation: Callable[..., None]
    post_initial_board_generation_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_initial_board_generation_start(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[Any],
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: InitialBoardGenerationStartDeps,
) -> ChatResponse:
    learning_clarification = deps.latest_learning_clarification(lesson, requirements=requirements)
    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
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
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=deps.resource_summary(resources),
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    chatbot_message = edit_outcome.chatbot_message
    if not edit_outcome.changed:
        failed_stamp = (
            requirement_history.generation_failed(
                reason=edit_outcome.summary or chatbot_message,
                metadata=board_document_failure_metadata(edit_outcome),
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
        return deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        chatbot_message, chatbot_message_source = deps.post_initial_board_generation_message(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=deps.resource_summary(resources),
            edit_outcome=edit_outcome,
        )
    requirement_cleared = edit_outcome.changed
    metadata = {
        "kind": "board_document_generation",
        "user_message": request.message,
        "assistant_message": chatbot_message,
        "assistant_message_source": chatbot_message_source,
        "board_editor_message": edit_outcome.chatbot_message,
        "board_generation_action": request.board_generation_action,
        "board_edit_operation": edit_outcome.operation,
        "board_edit_summary": edit_outcome.summary,
        "board_section_titles": edit_outcome.section_titles,
        **board_document_quality_metadata(edit_outcome),
        **requirement_history_metadata(
            frozen_requirement,
            run_status_after_commit="consumed" if frozen_requirement is not None else None,
        ),
        **task_metadata(
            requirements=requirements,
            learning_clarification=learning_clarification,
            requirement_cleared=requirement_cleared,
        ),
    }

    commit_operations(
        lesson,
        [],
        label="Board document generation",
        message="Generated board document from the learning requirement sheet",
        new_document=lesson.board_document,
        metadata=metadata,
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
    return deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        requirement_cleared=requirement_cleared,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )


def generate_board_from_confirmed_resource(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary_for_turn: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: InitialBoardResourceGenerationDeps,
) -> ChatResponse:
    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
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
                metadata=board_document_failure_metadata(edit_outcome),
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
        return deps.build_response(
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
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        chatbot_message, chatbot_message_source = deps.post_initial_board_generation_message(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=resource_summary_for_turn,
            edit_outcome=edit_outcome,
        )
    requirement_cleared = edit_outcome.changed
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
            **board_document_quality_metadata(edit_outcome),
            **requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **reference_metadata(resolution=resource_resolution),
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
    return deps.build_response(
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


def execute_initial_board_explicit_generation(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    selected_reference: Any,
    resource_summary_for_turn: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: InitialBoardExplicitGenerationDeps,
) -> ChatResponse:
    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
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
    edit_outcome = generate_from_requirements(
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
                metadata=board_document_failure_metadata(edit_outcome),
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
        return deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=edit_outcome.board_decision,
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        chatbot_message, chatbot_message_source = deps.post_initial_board_generation_message(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=resource_summary_for_turn,
            edit_outcome=edit_outcome,
        )
    requirement_cleared = edit_outcome.changed
    commit_operations(
        lesson,
        [],
        label="Board document generation",
        message="Generated board document from an explicit learner request",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_generation",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "board_generation_action": "explicit_board_request",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **board_document_quality_metadata(edit_outcome),
            **requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **reference_metadata(resolution=resource_resolution),
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
    return deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )


def execute_initial_board_ready_generation(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    selected_reference: Any,
    resource_summary_for_turn: str,
    chatbot_message: str,
    solver_metadata: dict[str, object],
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: InitialBoardReadyGenerationDeps,
) -> ChatResponse:
    requirements = deps.with_task_details(
        requirements,
        action_type="generate_board",
        instruction=requirements.action_instruction or request.message,
    )
    requirements, learning_clarification, frozen_requirement = prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
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
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    if not edit_outcome.changed:
        failed_stamp = requirement_history.generation_failed(
            reason=edit_outcome.summary or edit_outcome.chatbot_message,
            metadata=board_document_failure_metadata(edit_outcome),
        )
        workspace_state.normalize_package_state(package)
        deps.save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message or chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=edit_outcome.board_decision,
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
    refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
    lesson.board_teaching_guide = build_board_teaching_guide(lesson)
    lesson.board_teaching_progress = None
    post_generation_message, post_generation_source = deps.post_initial_board_generation_message(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        edit_outcome=edit_outcome,
    )
    commit_operations(
        lesson,
        [],
        label="Board document generation",
        message="Generated board document from a frozen learning requirement sheet",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_generation",
            "user_message": request.message,
            "assistant_message": post_generation_message,
            "assistant_message_source": post_generation_source,
            "chatbot_requirement_reply": chatbot_message,
            "board_editor_message": edit_outcome.chatbot_message,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "board_generation_action": "ready_requirement_sheet",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **board_document_quality_metadata(edit_outcome),
            **requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=True,
            ),
            **reference_metadata(resolution=resource_resolution),
            **solver_metadata,
        },
    )
    consumed_stamp = requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
    deps.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    return deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=post_generation_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=True,
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )
