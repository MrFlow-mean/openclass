from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.board_task_decider import infer_board_task_action
from app.services.chat.intent import _requests_explanation
from app.services.chat.metadata import _board_task_metadata, _task_metadata
from app.services.chat.response import _response
from app.services.chat.sequence import SequenceRuntime, _handle_section_explanation_sequence_turn
from app.services.history import commit_operations
from app.services.interaction_rules import (
    apply_interaction_decision,
    build_interaction_start,
    decide_interaction_turn,
    interaction_context_payload,
    interaction_session_metadata,
    should_start_interaction,
)
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import is_document_empty


@dataclass(frozen=True)
class InteractionRuntime:
    board_summary: Callable[[Lesson], str]
    resource_summary: Callable[[list[ResourceLibraryItem]], str]
    conversation_summary: Callable[[list[ConversationTurn]], str]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    latest_learning_clarification: Callable[..., LearningClarificationStatus]
    generate_focus_candidate_message: Callable[..., tuple[str, str]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    sequence_runtime: Callable[[], SequenceRuntime]
    handle_existing_board_task_flow: Callable[..., ChatResponse | None]


def _generate_interaction_chatbot_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    session: InteractionSession,
    decision: InteractionTurnDecision | None,
    runtime: InteractionRuntime,
) -> tuple[str, str, dict[str, object] | None]:
    context = interaction_context_payload(session=session, decision=decision)
    if decision is not None and decision.route == "side_learning_request":
        return runtime.generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=conversation,
            request=request,
            learning_clarification=runtime.latest_learning_clarification(lesson, requirements=requirements),
            action_type="side_learning_request",
            target_excerpt=session.reference_context,
            interaction_context=context,
        )
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=runtime.board_summary(lesson),
        resource_summary=runtime.resource_summary(resources),
        conversation_summary=runtime.conversation_summary(conversation),
        user_message=request.message,
        selection_excerpt=session.reference_context,
        interaction_mode="interaction_rule",
        interaction_context=context,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_interaction" if chatbot_message else "chatbot_empty", None


def handle_existing_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
    selection_text: str | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    runtime: InteractionRuntime,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None:
        return None

    learning_clarification = runtime.latest_learning_clarification(lesson, requirements=requirements)
    section_sequence_response = _handle_section_explanation_sequence_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        requirement_history=requirement_history,
        runtime=runtime.sequence_runtime(),
    )
    if section_sequence_response is not None:
        return section_sequence_response
    decision = decide_interaction_turn(
        lesson=lesson,
        session=session_before,
        resource_summary=runtime.resource_summary(resources),
        conversation_summary=runtime.conversation_summary(request.conversation),
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
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=session_before),
            },
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
            board_decision=BoardDecision(action="no_change", reason=""),
            requirement_history=requirement_history,
        )

    if decision.route in {"exit_rule", "new_task", "side_learning_request"}:
        lesson.active_interaction_session = None
        interaction_exit_metadata = interaction_session_metadata(before=session_before, after=None, decision=decision)
        should_attempt_board_task = decision.route in {"new_task", "side_learning_request"} or bool(
            infer_board_task_action(
                request,
                has_selection=bool(selection_excerpt),
                document_empty=is_document_empty(lesson.board_document),
            )
            or _requests_explanation(request.message)
        )
        if should_attempt_board_task:
            board_task_response = runtime.handle_existing_board_task_flow(
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
        chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
            decision=decision,
            runtime=runtime,
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
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_exit_metadata,
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
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            requirement_history=requirement_history,
        )

    session_after = apply_interaction_decision(session_before, decision)
    reply_session = session_after or session_before
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=reply_session,
        decision=decision,
        runtime=runtime,
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
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=False,
            ),
            **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
        },
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        requirement_history=requirement_history,
    )


def maybe_start_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
    action_type: BoardTaskAction | None,
    requirement_history: LearningRequirementHistoryRecorder,
    runtime: InteractionRuntime,
    board_task: BoardTaskRequirementSheet | None = None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    board_task_decision: BoardTaskRouteDecision | None = None,
    resolved_focus: BoardFocusRef | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse | None:
    interaction_metadata = source_interaction_metadata or {}
    if request.interaction_mode == "direct_edit" and action_type != "append_section":
        return None
    if not should_start_interaction(requirements.interaction_rule_draft):
        return None

    start_resolution = build_interaction_start(
        lesson=lesson,
        draft=requirements.interaction_rule_draft,
        user_message=request.message,
        selection=request.selection,
        selection_text=selection_text,
        resolved_focus=resolved_focus,
    )
    if start_resolution.session is None and start_resolution.focus_resolution is not None:
        chatbot_message, chatbot_message_source = runtime.generate_focus_candidate_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            resolution=start_resolution.focus_resolution,
        )
        lesson.learning_requirements = requirements
        commit_operations(
            lesson,
            [],
            label="Interaction focus clarification",
            message="Asked the learner to confirm the source content for an interaction rule",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **interaction_metadata,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=None,
                    focus_candidates=start_resolution.focus_resolution.candidates,
                    requirement_cleared=False,
                ),
                **(
                    _board_task_metadata(
                        board_task=board_task,
                        stamp=board_task_stamp,
                        route="chat",
                        decision=board_task_decision.model_dump(mode="json") if board_task_decision else None,
                        cleared=False,
                    )
                    if board_task is not None
                    else {}
                ),
                **interaction_session_metadata(before=None, after=None),
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
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(
                action="await_focus_choice",
                reason=start_resolution.focus_resolution.question,
            ),
            focus_candidates=start_resolution.focus_resolution.candidates,
            requirement_history=requirement_history,
        )

    if start_resolution.session is None:
        return None

    session_before = lesson.active_interaction_session
    session_after = start_resolution.session
    if board_task is not None and board_task_stamp is not None:
        session_after = session_after.model_copy(
            update={
                "source_board_task_run_id": board_task_stamp.run_id,
                "source_board_task_version_id": board_task_stamp.version_id,
                "source_board_task_route": "chat",
            }
        )
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=session_after,
        decision=None,
        runtime=runtime,
    )
    runtime.clear_task_requirements(lesson)
    if board_task is not None:
        lesson.board_task_requirements = None
    commit_operations(
        lesson,
        [],
        label="Interaction session start",
        message="Started a rule-based interaction session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **interaction_metadata,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=session_after.target_focus,
                focus_candidates=(
                    start_resolution.focus_resolution.candidates
                    if start_resolution.focus_resolution
                    else []
                ),
                requirement_cleared=True,
            ),
            **(
                _board_task_metadata(
                    board_task=board_task,
                    stamp=board_task_stamp,
                    route="chat",
                    decision=board_task_decision.model_dump(mode="json") if board_task_decision else None,
                    cleared=board_task is not None,
                )
                if board_task is not None
                else {}
            ),
            **interaction_session_metadata(
                before=session_before,
                after=session_after,
            ),
        },
    )
    consumed_board_task_stamp = (
        board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if board_task is not None and board_task_history is not None
        else board_task_stamp
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
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(
            action="no_change",
            reason=session_after.interaction_goal,
        ),
        resolved_focus=session_after.target_focus,
        focus_candidates=(
            start_resolution.focus_resolution.candidates
            if start_resolution.focus_resolution
            else []
        ),
        requirement_cleared=True,
        requirement_history=requirement_history,
        board_task_stamp=consumed_board_task_stamp,
    )
