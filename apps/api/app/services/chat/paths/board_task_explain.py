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
        interaction_context: dict[str, object] | None = None,
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


class BoardTaskExplainResponseBuilder(Protocol):
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
        board_task_stamp: BoardTaskHistoryStamp | None = None,
        completed_board_task_sheet: BoardTaskRequirementSheet | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class BoardTaskExplainDependencies:
    generate_board_directed_explanation_message: DirectedExplanationGenerator
    requirements_from_board_task: RequirementsFromBoardTaskBuilder
    clear_task_requirements: TaskRequirementsClearer
    board_search_evidence_metadata: BoardSearchEvidenceMetadataBuilder
    task_metadata: TaskMetadataBuilder
    board_task_metadata: BoardTaskMetadataBuilder
    commit_operations: CommitOperations
    save_workspace_for_user: SaveWorkspaceForUser
    build_response: BoardTaskExplainResponseBuilder


def handle_board_task_explain(
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
    action_decision: BoardTaskActionDecision | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    interaction_metadata: dict[str, object],
    sequence_plan: SequencePlan | None = None,
    deps: BoardTaskExplainDependencies,
) -> ChatResponse:
    if decision.route != "explain":
        raise ValueError("board task explain handler requires an explain route")
    if sequence_plan is not None:
        raise ValueError("board task explain handler must not handle sequence plans")

    focus = decision.target_focus or (resolution.focus if resolution else None)
    if focus is None:
        raise ValueError("board task explain handler requires a resolved target focus")

    focus_excerpt = build_board_task_explanation_target_excerpt(
        board_task=board_task,
        focus=focus,
        decision=decision,
        resolution=resolution,
    )
    explanation_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=focus,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = (
        deps.generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=explanation_requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=focus_excerpt,
        )
    )
    stamp = board_task_history.record_update(sheet=board_task, status="ready")
    record_workflow_step(
        NodeId.BOARD_EXPLAIN_DIRECTIVE,
        decision=chatbot_message_source,
        reason=decision.reason,
        run_id=stamp.run_id,
        version_id=stamp.version_id,
    )

    cleared = chatbot_message_source == "chatbot_board_directed" and bool(chatbot_message)
    if not chatbot_message:
        failure_reason = "Board-directed explanation failed because Chatbot returned empty."
        failed_stamp = board_task_history.execution_failed(
            reason=failure_reason,
            metadata={
                "assistant_message_source": chatbot_message_source,
                "board_explanation_failed": True,
                "board_task_route": "explain",
                "board_task_cleared": False,
                "board_explanation_directive": board_explanation_directive,
                "board_task_decision": decision.model_dump(mode="json"),
                **deps.board_search_evidence_metadata(resolution),
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
            NodeId.BOARD_TASK_FAILURE,
            decision="execution_failed",
            reason=failure_reason,
            run_id=failed_stamp.run_id,
            version_id=failed_stamp.version_id,
        )
        response = deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message="",
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason=failure_reason),
            resolved_focus=focus,
            requirement_cleared=False,
            board_task_stamp=failed_stamp,
        )
        record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
        return response

    deps.commit_operations(
        lesson,
        [],
        label="Board task explanation",
        message="Executed an existing-board explanation task",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            **interaction_metadata,
            **deps.board_search_evidence_metadata(resolution),
            **decision_trace_metadata(
                message=request.message,
                board_action_decision=action_decision,
                route_decision=decision,
                role_executed="chatbot_board_directed",
                document_changed=False,
                reason=decision.reason,
            ),
            **deps.task_metadata(
                requirements=explanation_requirements,
                learning_clarification=learning_clarification,
                focus=focus,
                focus_candidates=resolution.candidates if resolution else [],
                requirement_cleared=cleared,
            ),
            **deps.board_task_metadata(
                board_task=board_task,
                stamp=stamp,
                route="explain",
                decision=decision.model_dump(mode="json"),
                cleared=cleared,
            ),
        },
    )
    commit = lesson.history_graph.commits[-1]
    consumed_stamp = board_task_history.consume(commit_id=commit.id) if cleared else stamp
    if cleared:
        lesson.board_task_requirements = None
        deps.clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    deps.save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    record_workflow_step(
        NodeId.BOARD_EXPLAIN_COMMIT,
        decision="committed",
        reason=decision.reason,
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
        resolved_focus=focus,
        requirement_cleared=cleared,
        board_task_stamp=consumed_stamp,
        completed_board_task_sheet=board_task if cleared else None,
    )
    record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
    return response


def build_board_task_explanation_target_excerpt(
    *,
    board_task: BoardTaskRequirementSheet,
    focus: BoardFocusRef,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> str:
    parts = [
        "已有板书任务清单已进入 explain 路线。",
        f"用户目标线索：{board_task.target_hint or '未单独提供'}",
        f"用户问题/主题：{board_task.question_or_topic or '未单独提供'}",
        f"定位裁决：{decision.reason or '已定位目标内容'}",
        f"当前允许讲解的目标内容：\n{focus_context(focus)}",
    ]
    other_candidates = [
        candidate
        for candidate in (decision.candidate_focuses or (resolution.candidates if resolution else []))
        if (candidate.segment_id, candidate.excerpt) != (focus.segment_id, focus.excerpt)
    ]
    if other_candidates:
        candidate_lines = [
            (
                f"{index}. "
                f"{candidate.display_label or ' / '.join(candidate.heading_path) or '板书片段'}"
                "（正文摘录仅供板书侧后续授权，不交给 Chatbot）"
            )
            for index, candidate in enumerate(other_candidates[:4], start=1)
        ]
        parts.append(
            "同一任务中还存在的后续候选目标，仅作为顺序讲解上下文，不得越界讲解：\n"
            + "\n".join(candidate_lines)
        )
    return "\n\n".join(part for part in parts if part.strip())
