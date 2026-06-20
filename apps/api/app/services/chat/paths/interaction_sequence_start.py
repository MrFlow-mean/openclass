from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    CoursePackage,
    InteractionSession,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    WorkspaceState,
)
from app.services import workspace_state
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.decision_trace import decision_trace_metadata
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.interaction_rules import interaction_context_payload, interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution, focus_context
from app.services.sequence_planner import SequencePlan
from app.services.workflow_trace import NodeId, record_workflow_step


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


class RequirementsFromBoardTaskBuilder(Protocol):
    def __call__(
        self,
        *,
        base: LearningRequirementSheet,
        board_task: BoardTaskRequirementSheet,
        action_type: BoardTaskAction | None,
        focus: BoardFocusRef | None = None,
    ) -> LearningRequirementSheet: ...


class TaskRequirementsClearer(Protocol):
    def __call__(self, lesson: Lesson) -> None: ...


class BoardSearchEvidenceMetadataBuilder(Protocol):
    def __call__(self, resolution: FocusResolution | None) -> dict[str, object]: ...


class TaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        focus: BoardFocusRef | None = None,
        focus_candidates: list[BoardFocusRef] | None = None,
        requirement_cleared: bool = False,
    ) -> dict[str, object]: ...


class BoardTaskMetadataBuilder(Protocol):
    def __call__(
        self,
        *,
        board_task: BoardTaskRequirementSheet | None,
        stamp: BoardTaskHistoryStamp | None,
        route: str | None = None,
        decision: dict[str, object] | None = None,
        cleared: bool = False,
    ) -> dict[str, object]: ...


class CommitOperations(Protocol):
    def __call__(
        self,
        lesson: Lesson,
        operations: list[object],
        *,
        label: str,
        message: str,
        new_document: object | None = None,
        metadata: dict[str, object] | None = None,
    ) -> object: ...


class SaveWorkspaceForUser(Protocol):
    def __call__(
        self,
        *,
        user_id: str,
        workspace: WorkspaceState,
        requirement_history: LearningRequirementHistoryRecorder | None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
    ) -> None: ...


class SequenceStartResponseBuilder(Protocol):
    def __call__(
        self,
        *,
        workspace: WorkspaceState,
        package: CoursePackage,
        lesson: Lesson,
        chatbot_message: str,
        requirements: LearningRequirementSheet,
        learning_clarification: LearningClarificationStatus,
        board_decision: BoardDecision,
        resolved_focus: BoardFocusRef | None = None,
        focus_candidates: list[BoardFocusRef] | None = None,
        requirement_cleared: bool = False,
        requirement_history: LearningRequirementHistoryRecorder | None = None,
        board_task_history: BoardTaskHistoryRecorder | None = None,
        board_task_stamp: BoardTaskHistoryStamp | None = None,
        completed_board_task_sheet: BoardTaskRequirementSheet | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class InteractionSequenceStartDependencies:
    generate_board_directed_explanation_message: DirectedExplanationGenerator
    requirements_from_board_task: RequirementsFromBoardTaskBuilder
    clear_task_requirements: TaskRequirementsClearer
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: SequenceStartResponseBuilder


def handle_interaction_sequence_start(
    *,
    workspace: WorkspaceState,
    package: CoursePackage,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    action_decision: BoardTaskActionDecision | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    sequence_plan: SequencePlan,
    requirement_history: LearningRequirementHistoryRecorder,
    interaction_metadata: dict[str, object],
    deps: InteractionSequenceStartDependencies,
) -> ChatResponse:
    if not sequence_plan.items:
        raise ValueError("sequence start requires sequence items")

    sequence_items = sequence_plan.items
    first_focus = sequence_items[0]
    session_before = lesson.active_interaction_session
    sequence_mode = ATOMIC_EXPLANATION_SEQUENCE_MODE
    unit_label = _sequence_unit_label(sequence_mode)
    session_after = InteractionSession(
        status="active",
        rule_text="按板书内容的最小可讲单元顺序逐个讲解。",
        interaction_goal=(
            f"按最小内容单元讲解 {first_focus.heading_path[-1]}"
            if first_focus.heading_path
            else board_task.question_or_topic or board_task.target_hint
        ),
        target_focus=first_focus,
        reference_context=focus_context(first_focus),
        compliant_input_rule=f"用户确认理解、提出当前{unit_label}问题，或要求继续下一个{unit_label}。",
        expected_user_behavior=f"用户确认当前{unit_label}是否可以接受；没有问题时继续下一个{unit_label}。",
        assistant_behavior=f"每轮只讲当前{unit_label}，结尾询问是否继续下一个{unit_label}。",
        progress_note=f"准备讲解第 1/{len(sequence_items)} 个{unit_label}。",
        turn_count=0,
        source_board_task_run_id=board_task_stamp.run_id,
        source_board_task_version_id=board_task_stamp.version_id,
        source_board_task_route="explain",
        sequence_items=sequence_items,
        sequence_index=0,
        sequence_mode=sequence_mode,
    )
    lesson.active_interaction_session = session_after
    explanation_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=first_focus,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = (
        deps.generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=explanation_requirements,
            resources=resources,
            conversation=request.conversation,
            request=request.model_copy(
                update={
                    "message": _sequence_instruction(
                        request_message=request.message,
                        focus=first_focus,
                        index=0,
                        total=len(sequence_items),
                        sequence_mode=sequence_mode,
                    )
                }
            ),
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=focus_context(first_focus),
            interaction_context=interaction_context_payload(session=session_after),
        )
    )
    lesson.board_task_requirements = None
    deps.clear_task_requirements(lesson)
    deps.commit_operations(
        lesson,
        [],
        label="Section explanation session start",
        message="Started a sequential section explanation session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            "section_explanation_sequence": [item.model_dump(mode="json") for item in sequence_items],
            "explanation_sequence": [item.model_dump(mode="json") for item in sequence_items],
            "explanation_sequence_mode": sequence_mode,
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                sequence_plan=sequence_plan,
                role_executed="chatbot_board_directed",
                document_changed=False,
                reason=sequence_plan.reason,
            ),
            **deps.task_metadata(
                requirements=explanation_requirements,
                learning_clarification=learning_clarification,
                focus=first_focus,
                focus_candidates=sequence_items,
                requirement_cleared=True,
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=board_task_stamp,
                route="explain",
                decision=decision.model_dump(mode="json"),
                cleared=True,
            ),
            **interaction_session_metadata(before=session_before, after=session_after),
        },
    )
    commit = lesson.history_graph.commits[-1]
    record_workflow_step(
        NodeId.BOARD_SEQUENCE_START,
        decision="started",
        reason=sequence_plan.reason,
        run_id=board_task_stamp.run_id,
        version_id=board_task_stamp.version_id,
        commit_id=commit.id,
    )
    consumed_stamp = board_task_history.consume(commit_id=commit.id)
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
        run_id=consumed_stamp.run_id,
        version_id=consumed_stamp.version_id,
        commit_id=commit.id,
    )
    response = deps.build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        resolved_focus=first_focus,
        focus_candidates=sequence_items,
        requirement_cleared=True,
        board_task_stamp=consumed_stamp,
        completed_board_task_sheet=board_task,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def _sequence_unit_label(sequence_mode: str) -> str:
    if sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE:
        return "讲解单元"
    return "子节"


def _sequence_instruction(
    *,
    request_message: str,
    focus: BoardFocusRef,
    index: int,
    total: int,
    sequence_mode: str = ATOMIC_EXPLANATION_SEQUENCE_MODE,
) -> str:
    unit_label = _sequence_unit_label(sequence_mode)
    next_note = (
        f"讲完后请询问学习者是否可以继续下一个{unit_label}。"
        if index + 1 < total
        else "讲完后请确认学习者是否还有问题；如果没有问题，本组顺序讲解可以结束。"
    )
    atom_instruction = (
        "如果当前目标是题目、练习或带参考答案的内容，必须讲题目要求、关键线索、"
        "推理步骤、答案如何得到和易错点；不能只翻译、复述或直接报答案。"
        if sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
        else ""
    )
    return (
        f"{request_message}\n"
        f"系统顺序讲解要求：本轮只讲第 {index + 1}/{total} 个{unit_label}："
        f"{focus.display_label or ' / '.join(focus.heading_path)}。"
        f"{next_note}不要越界讲解其它{unit_label}。{atom_instruction}"
    )
