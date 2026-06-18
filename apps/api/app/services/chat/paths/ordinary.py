from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat_turn_gate import ChatTurnGateDecision
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, record_workflow_step


class BoardSummaryBuilder(Protocol):
    def __call__(self, lesson: Lesson) -> str: ...


class ConversationSummaryBuilder(Protocol):
    def __call__(self, conversation: list[ConversationTurn]) -> str: ...


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class ReferenceMetadataBuilder(Protocol):
    def __call__(self, *, resolution: ResourceResolution) -> dict[str, object]: ...


class ChatTurnGateMetadataBuilder(Protocol):
    def __call__(self, decision: ChatTurnGateDecision) -> dict[str, object]: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class OrdinaryResponseBuilder(Protocol):
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
        resource_matches: list[ResourceMatch] | None = None,
        selected_reference: ResourceReferenceContext | None = None,
        requirement_cleared: bool = False,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class OrdinaryChatHandlerDependencies:
    board_summary: BoardSummaryBuilder
    conversation_summary: ConversationSummaryBuilder
    task_metadata: TaskMetadataBuilder
    reference_metadata: ReferenceMetadataBuilder
    chat_turn_gate_metadata: ChatTurnGateMetadataBuilder
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: OrdinaryResponseBuilder


def handle_ordinary_chat(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_summary_for_turn: str,
    resource_resolution: ResourceResolution,
    selected_reference: ResourceReferenceContext | None,
    chat_turn_gate: ChatTurnGateDecision,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    deps: OrdinaryChatHandlerDependencies,
) -> ChatResponse:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=deps.board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=deps.conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=None,
        interaction_mode=request.interaction_mode,
        interaction_context={
            "turn_mode": "ordinary_chat",
            "gate_reason": chat_turn_gate.reason,
        },
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"
    record_workflow_step(
        NodeId.ORDINARY_CHAT_GENERATE,
        decision=chatbot_message_source,
        reason=chat_turn_gate.reason,
    )
    board_decision = BoardDecision(action="no_change", reason="本轮是普通聊天，不进入学习需求或板书任务链路。")
    requirement_cleared = False

    commit_operations(
        lesson,
        [],
        label="Chat turn",
        message="Recorded an ordinary chatbot conversation turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **deps.task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **deps.reference_metadata(resolution=resource_resolution),
            **deps.chat_turn_gate_metadata(chat_turn_gate),
        },
    )
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
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
        board_decision=board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response
