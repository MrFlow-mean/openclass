from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.models import (
    BoardDecision,
    BoardTaskAction,
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
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.history import commit_operations
from app.services.interaction_rules import (
    apply_interaction_decision,
    decide_interaction_turn,
    interaction_session_metadata,
)
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import is_document_empty
from app.services.segment_resolver import FocusResolution


@dataclass(frozen=True)
class BoardTaskInteractionHandlerDeps:
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    start_interaction_session: Callable[..., ChatResponse | None]


@dataclass(frozen=True)
class InteractionTurnHandlerDeps:
    latest_learning_clarification: Callable[..., LearningClarificationStatus]
    handle_section_explanation_sequence_turn: Callable[..., ChatResponse | None]
    resource_summary: Callable[[list[ResourceLibraryItem]], str]
    conversation_summary: Callable[[list[ConversationTurn]], str]
    infer_board_task_action: Callable[..., BoardTaskAction | None]
    requests_explanation: Callable[[str], bool]
    handle_existing_board_task_flow: Callable[..., ChatResponse | None]
    generate_interaction_chatbot_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    task_metadata: Callable[..., dict[str, object]]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def handle_existing_interaction_session(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
    selection_text: str | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    deps: InteractionTurnHandlerDeps,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None:
        return None

    learning_clarification = deps.latest_learning_clarification(lesson, requirements=requirements)
    section_sequence_response = deps.handle_section_explanation_sequence_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        requirement_history=requirement_history,
    )
    if section_sequence_response is not None:
        return section_sequence_response
    decision = decide_interaction_turn(
        lesson=lesson,
        session=session_before,
        resource_summary=deps.resource_summary(resources),
        conversation_summary=deps.conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=selection_excerpt,
    )
    if decision is None:
        chatbot_message = ""
        lesson.active_interaction_session = session_before
        commit_operations(
            lesson,
            [],
            label="Interaction turn",
            message="Recorded an interaction-rule turn without a route decision",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": "interaction_decision_empty",
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **deps.task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=session_before),
            },
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
            board_decision=BoardDecision(action="no_change", reason=""),
            requirement_history=requirement_history,
        )

    if decision.route in {"exit_rule", "new_task", "side_learning_request"}:
        lesson.active_interaction_session = None
        interaction_exit_metadata = interaction_session_metadata(before=session_before, after=None, decision=decision)
        should_attempt_board_task = decision.route in {"new_task", "side_learning_request"} or bool(
            deps.infer_board_task_action(
                request,
                has_selection=bool(selection_excerpt),
                document_empty=is_document_empty(lesson.board_document),
            )
            or deps.requests_explanation(request.message)
        )
        if should_attempt_board_task:
            board_task_response = deps.handle_existing_board_task_flow(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                resources=resources,
                selection_excerpt=selection_excerpt,
                selection_text=selection_text,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
                source_interaction_metadata=interaction_exit_metadata,
                force_task_attempt=decision.route in {"new_task", "side_learning_request"},
            )
            if board_task_response is not None:
                board_task_response.interaction_decision = decision
                return board_task_response
        chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_interaction_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
            decision=decision,
        )
        commit_operations(
            lesson,
            [],
            label="Interaction session ended",
            message="Exited a rule-based interaction session and found no executable board task in the same turn",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "board_explanation_directive": board_explanation_directive,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **deps.task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_exit_metadata,
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
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            requirement_history=requirement_history,
        )

    session_after = apply_interaction_decision(session_before, decision)
    reply_session = session_after or session_before
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=reply_session,
        decision=decision,
    )
    commit_operations(
        lesson,
        [],
        label="Interaction turn",
        message="Recorded an interaction-rule chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
        },
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        requirement_history=requirement_history,
    )


def execute_board_task_chat_interaction(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    requirement_history: LearningRequirementHistoryRecorder,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    interaction_metadata: dict[str, object],
    deps: BoardTaskInteractionHandlerDeps,
) -> ChatResponse | None:
    focus = decision.target_focus or (resolution.focus if resolution else None)
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=focus,
    )
    lesson.learning_requirements = task_requirements
    return deps.start_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        selection_text=selection_text,
        action_type="explain_target",
        requirement_history=requirement_history,
        board_task=board_task,
        board_task_history=board_task_history,
        board_task_stamp=board_task_stamp,
        board_task_decision=decision,
        resolved_focus=focus,
        source_interaction_metadata={
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
        },
    )
