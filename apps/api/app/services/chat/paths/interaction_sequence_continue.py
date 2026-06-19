from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.models import (
    BoardDecision,
    BoardFocusRef,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    CoursePackage,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.board_document_locator import focus_context
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_context_payload, interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.workflow_trace import NodeId, record_workflow_step


SequenceContinueOutcome = Literal["follow_up_current", "advance"]


class DirectedExplanationGenerator(Protocol):
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
        interaction_context: dict[str, object],
    ) -> tuple[str, str, dict[str, object] | None]: ...


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        focus: BoardFocusRef | None = None,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
    ) -> None: ...


class SequenceContinueResponseBuilder(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        chatbot_message: str,
        learning_clarification: LearningClarificationStatus,
        requirements: LearningRequirementSheet,
        board_decision: BoardDecision,
        interaction_decision: InteractionTurnDecision | None = None,
        resolved_focus: BoardFocusRef | None = None,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class InteractionSequenceContinueDependencies:
    generate_board_directed_explanation_message: DirectedExplanationGenerator
    task_metadata: TaskMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: SequenceContinueResponseBuilder


def handle_interaction_sequence_continue(
    *,
    outcome: SequenceContinueOutcome,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    session_before: InteractionSession | None,
    focus: BoardFocusRef,
    unit_label: str,
    requirement_history: LearningRequirementHistoryRecorder,
    deps: InteractionSequenceContinueDependencies,
    next_index: int | None = None,
) -> ChatResponse:
    if outcome not in {"follow_up_current", "advance"}:
        raise ValueError(f"unsupported sequence continue outcome: {outcome}")
    if session_before is None:
        raise ValueError("sequence continue requires a previous interaction session")
    if not session_before.sequence_items:
        raise ValueError("sequence continue requires sequence items")
    if lesson.active_interaction_session != session_before:
        raise ValueError("sequence continue requires the current active session to match session_before")
    if not unit_label:
        raise ValueError("sequence continue requires a unit label")
    if outcome == "advance":
        if next_index is None:
            raise ValueError("sequence advance requires next_index")
        if next_index < 0 or next_index >= len(session_before.sequence_items):
            raise ValueError("sequence advance next_index is out of range")
        if focus != session_before.sequence_items[next_index]:
            raise ValueError("sequence advance focus must match next_index")

    update = {
        "target_focus": focus,
        "reference_context": focus_context(focus),
        "turn_count": session_before.turn_count + 1,
        "status": "active",
        "pause_reason": "",
    }
    if outcome == "advance":
        update.update(
            {
                "sequence_index": next_index,
                "progress_note": f"准备讲解第 {next_index + 1}/{len(session_before.sequence_items)} 个{unit_label}。",
            }
        )
    session_after = session_before.model_copy(update=update)
    lesson.active_interaction_session = session_after

    sequence_request = request.model_copy(
        update={
            "message": _sequence_instruction(
                outcome=outcome,
                request_message=request.message,
                focus=focus,
                session=session_after,
                unit_label=unit_label,
            )
        }
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = (
        deps.generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=requirements.model_copy(update={"target_location": focus, "location_status": "resolved"}),
            resources=resources,
            conversation=request.conversation,
            request=sequence_request,
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=focus_context(focus),
            interaction_context=interaction_context_payload(session=session_after),
        )
    )
    decision = _interaction_decision(outcome=outcome, session_after=session_after, unit_label=unit_label)
    record_workflow_step(
        NodeId.INTERACTION_CONTINUE,
        decision="continue_rule",
        reason=decision.reason,
    )
    commit_operations(
        lesson,
        [],
        label=_commit_label(outcome),
        message=_commit_message(outcome),
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=focus,
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
    record_workflow_step(
        NodeId.PERSIST_CHAT_COMMIT,
        decision="committed",
        commit_id=lesson.history_graph.commits[-1].id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        resolved_focus=focus,
        requirement_history=requirement_history,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def _interaction_decision(
    *,
    outcome: SequenceContinueOutcome,
    session_after: InteractionSession,
    unit_label: str,
) -> InteractionTurnDecision:
    if outcome == "follow_up_current":
        return InteractionTurnDecision(
            route="continue_rule",
            reason=f"用户追问当前{unit_label}，继续围绕当前{unit_label}讲解。",
            progress_note=session_after.progress_note,
            user_intent=f"追问当前{unit_label}",
        )
    return InteractionTurnDecision(
        route="continue_rule",
        reason=f"用户确认当前{unit_label}后继续下一个{unit_label}。",
        progress_note=session_after.progress_note,
        user_intent="继续顺序讲解",
    )


def _sequence_instruction(
    *,
    outcome: SequenceContinueOutcome,
    request_message: str,
    focus: BoardFocusRef,
    session: InteractionSession,
    unit_label: str,
) -> str:
    if outcome == "follow_up_current":
        return (
            f"{request_message}\n"
            f"系统顺序讲解要求：用户正在追问当前第 "
            f"{session.sequence_index + 1}/{len(session.sequence_items)} 个{unit_label}："
            f"{focus.display_label or ' / '.join(focus.heading_path)}。"
            f"请只围绕当前{unit_label}补充解释，不要推进到下一个{unit_label}。"
            f"结尾询问当前{unit_label}是否还有问题，或是否继续下一个{unit_label}。"
        )
    next_note = (
        f"讲完后请询问学习者是否可以继续下一个{unit_label}。"
        if session.sequence_index + 1 < len(session.sequence_items)
        else "讲完后请确认学习者是否还有问题；如果没有问题，本组顺序讲解可以结束。"
    )
    atom_instruction = (
        "如果当前目标是题目、练习或带参考答案的内容，必须讲题目要求、关键线索、"
        "推理步骤、答案如何得到和易错点；不能只翻译、复述或直接报答案。"
        if session.sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
        else ""
    )
    return (
        f"{request_message}\n"
        f"系统顺序讲解要求：本轮只讲第 {session.sequence_index + 1}/{len(session.sequence_items)} 个{unit_label}："
        f"{focus.display_label or ' / '.join(focus.heading_path)}。"
        f"{next_note}不要越界讲解其它{unit_label}。{atom_instruction}"
    )


def _commit_label(outcome: SequenceContinueOutcome) -> str:
    if outcome == "follow_up_current":
        return "Section explanation follow-up"
    return "Section explanation turn"


def _commit_message(outcome: SequenceContinueOutcome) -> str:
    if outcome == "follow_up_current":
        return "Answered a follow-up within the current sequential section"
    return "Continued a sequential section explanation session"
