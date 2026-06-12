from __future__ import annotations

from typing import Callable

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceBoardProposal,
)
from app.services import workspace_state
from app.services.chat.handlers.initial_board import InitialBoardRuntime, run_initial_board_generation
from app.services.chat.intent import _requests_document_artifact_generation, _requests_learning_start
from app.services.chat.metadata import _reference_metadata, _learning_requirement_metadata
from app.services.chat.response import _response
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.learning_requirement_manager import is_generation_control_request
from app.services.rich_document import is_document_empty
from app.services.resource_resolver import ResourceResolution


def should_generate_board_after_reference_confirmation(text: str) -> bool:
    return (
        _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def request_with_pending_resource_board_action(lesson: Lesson, request: ChatRequest) -> ChatRequest:
    if request.resource_board_action is not None or lesson.pending_resource_board_proposal is None:
        return request
    if not is_document_empty(lesson.board_document):
        return request
    if not (
        is_generation_control_request(request.message)
        or _requests_document_artifact_generation(request.message)
    ):
        return request
    return request.model_copy(
        update={
            "resource_board_action": "generate",
            "resource_board_proposal_id": lesson.pending_resource_board_proposal.id,
        }
    )


def matching_pending_resource_board_proposal(
    lesson: Lesson,
    request: ChatRequest,
) -> ResourceBoardProposal | None:
    proposal = lesson.pending_resource_board_proposal
    if proposal is None:
        return None
    if request.resource_board_proposal_id and request.resource_board_proposal_id != proposal.id:
        return None
    return proposal


def should_store_resource_board_proposal(
    *,
    lesson: Lesson,
    request: ChatRequest,
    resource_resolution: ResourceResolution,
) -> bool:
    if not is_document_empty(lesson.board_document):
        return False
    if request.resource_board_action is not None or request.resource_reference_action is not None:
        return False
    if is_generation_control_request(request.message) or _requests_document_artifact_generation(request.message):
        return False
    return resource_resolution.selected_reference is not None and resource_resolution.evidence_bundle is not None


def skip_pending_resource_board_proposal(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    requirement_history: LearningRequirementHistoryRecorder,
    save_workspace_for_user: Callable[..., None],
) -> ChatResponse | None:
    if request.resource_board_action != "skip":
        return None
    proposal = matching_pending_resource_board_proposal(lesson, request)
    if proposal is None:
        return None
    lesson.pending_resource_board_proposal = None
    chatbot_message = "已跳过这次资料板书生成建议。"
    commit_operations(
        lesson,
        [],
        label="Resource board proposal skipped",
        message="Skipped a pending resource-backed board proposal",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "resource_board_proposal",
            "interaction_mode": request.interaction_mode,
            "resource_board_action": "skip",
            "resource_board_proposal_id": proposal.id,
            "resource_board_proposal": proposal.model_dump(mode="json"),
            "pending_resource_board_proposal": None,
            **_learning_requirement_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
        },
    )
    workspace_state.normalize_package_state(package)
    save_workspace_for_user(
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
        board_decision=BoardDecision(action="no_change", reason="用户跳过了资料板书生成建议。"),
        requirement_history=requirement_history,
    )


def resource_board_proposal_unavailable_response(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    requirement_history: LearningRequirementHistoryRecorder,
    save_workspace_for_user: Callable[..., None],
) -> ChatResponse:
    lesson.pending_resource_board_proposal = None
    chatbot_message = "这次资料板书生成建议已经失效，需要重新定位资料章节后再生成。"
    commit_operations(
        lesson,
        [],
        label="Resource board proposal unavailable",
        message="Stopped a resource-backed board generation without reusable evidence",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "resource_board_proposal",
            "interaction_mode": request.interaction_mode,
            "resource_board_action": request.resource_board_action,
            "resource_board_proposal_id": request.resource_board_proposal_id,
            "resource_resolution_status": "missing",
            "resource_backed_generation": False,
            "pending_resource_board_proposal": None,
            **_learning_requirement_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
        },
    )
    workspace_state.normalize_package_state(package)
    save_workspace_for_user(
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
        board_decision=BoardDecision(action="no_change", reason="资料板书生成建议缺少可追溯正文证据。"),
        requirement_history=requirement_history,
    )


def run_confirmed_resource_initial_board_generation(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary: str,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    runtime: InitialBoardRuntime,
) -> ChatResponse | None:
    if request.resource_reference_action != "confirm" or resource_resolution.selected_reference is None:
        return None
    lesson.pending_resource_board_proposal = None
    return run_initial_board_generation(
        trigger="resource_reference_confirm",
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_resolution=resource_resolution,
        resource_summary=resource_summary,
        requirement_history=requirement_history,
        track_initial_requirement_run=track_initial_requirement_run,
        runtime=runtime,
    )


def prompt_for_resource_reference(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    commit_message: str,
    save_workspace_for_user: Callable[..., None],
) -> ChatResponse:
    reference_prompt = resource_resolution.reference_prompt
    if reference_prompt is None:
        raise ValueError("resource reference prompt response requires a reference prompt")
    chatbot_message = reference_prompt.question
    board_proposal = remember_resource_board_proposal(
        lesson,
        resource_resolution,
        require_empty_document=True,
    )
    commit_operations(
        lesson,
        [],
        label="Resource reference prompt",
        message=commit_message,
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "resource_resolver",
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **_learning_requirement_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **_reference_metadata(resolution=resource_resolution),
            "resource_board_proposal": board_proposal.model_dump(mode="json") if board_proposal else None,
            "pending_resource_board_proposal": (
                lesson.pending_resource_board_proposal.model_dump(mode="json")
                if lesson.pending_resource_board_proposal
                else None
            ),
        },
    )
    workspace_state.normalize_package_state(package)
    requirement_history_arg = requirement_history if track_initial_requirement_run else None
    save_workspace_for_user(
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
        board_decision=BoardDecision(
            action="await_reference_choice",
            reason=reference_prompt.reason,
        ),
        resource_matches=resource_resolution.matches,
        resource_evidence_bundle=resource_resolution.evidence_bundle,
        resource_board_proposal=board_proposal,
        reference_prompt=reference_prompt,
        requirement_history=requirement_history_arg,
    )


def remember_resource_board_proposal(
    lesson: Lesson,
    resource_resolution: ResourceResolution,
    *,
    require_empty_document: bool,
) -> ResourceBoardProposal | None:
    if require_empty_document and not is_document_empty(lesson.board_document):
        return None
    board_proposal = resource_board_proposal_from_resolution(resource_resolution)
    if board_proposal is not None:
        lesson.pending_resource_board_proposal = board_proposal
    return board_proposal


def resource_board_proposal_from_resolution(resource_resolution: ResourceResolution) -> ResourceBoardProposal | None:
    evidence_bundle = resource_resolution.evidence_bundle
    if evidence_bundle is None or evidence_bundle.target_id is None:
        return None
    reference_prompt = resource_resolution.reference_prompt
    if reference_prompt is not None:
        return ResourceBoardProposal(
            resource_id=reference_prompt.resource_id,
            chapter_id=reference_prompt.chapter_id,
            target_title=reference_prompt.chapter_title,
            reason=reference_prompt.reason,
            evidence_bundle=evidence_bundle,
        )
    selected_reference = resource_resolution.selected_reference
    if selected_reference is not None:
        reason = resource_resolution.matches[0].reason if resource_resolution.matches else "已定位到正文证据。"
        return ResourceBoardProposal(
            resource_id=selected_reference.resource_id,
            chapter_id=selected_reference.chapter_id,
            target_title=selected_reference.chapter_title,
            reason=reason,
            evidence_bundle=evidence_bundle,
        )
    if not resource_resolution.matches:
        return None
    match = resource_resolution.matches[0]
    return ResourceBoardProposal(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        target_title=match.chapter_title,
        reason=match.reason,
        evidence_bundle=evidence_bundle,
    )
