from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.resource_resolver import ResourceResolution
from app.services.segment_resolver import FocusResolution, focus_context


@dataclass(frozen=True)
class BoardTaskExplainHandlerDeps:
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    task_metadata: Callable[..., dict[str, object]]
    board_task_metadata: Callable[..., dict[str, object]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


@dataclass(frozen=True)
class BoardExplanationFallbackDeps:
    with_task_details: Callable[..., LearningRequirementSheet]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    task_metadata: Callable[..., dict[str, object]]
    reference_metadata: Callable[..., dict[str, object]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]
    build_response: Callable[..., ChatResponse]


def execute_board_task_explain(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    requirement_history: LearningRequirementHistoryRecorder,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    interaction_metadata: dict[str, object],
    deps: BoardTaskExplainHandlerDeps,
) -> ChatResponse:
    focus = decision.target_focus or (resolution.focus if resolution else None)
    focus_excerpt = _board_task_explanation_target_excerpt(
        board_task=board_task,
        focus=focus,
        decision=decision,
        resolution=resolution,
    )
    task_requirements = deps.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=focus,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=task_requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        learning_clarification=learning_clarification,
        action_type="explain_target",
        target_excerpt=focus_excerpt,
    )
    stamp = board_task_history.record_update(sheet=board_task, status="ready")
    cleared = chatbot_message_source == "chatbot_board_directed" and bool(chatbot_message)
    if not chatbot_message:
        failed_stamp = board_task_history.execution_failed(
            reason="Board-directed explanation failed because Chatbot returned empty.",
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
        return deps.build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message="",
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(
                action="no_change",
                reason="Board-directed explanation failed because Chatbot returned empty.",
            ),
            resolved_focus=focus,
            requirement_cleared=False,
            board_task_stamp=failed_stamp,
        )
    commit_operations(
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
            **deps.task_metadata(
                requirements=task_requirements,
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
    consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id) if cleared else stamp
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
    return deps.build_response(
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


def execute_board_explanation_fallback(
    *,
    workspace: Any,
    package: Any,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    target_excerpt: str,
    resource_resolution: ResourceResolution,
    selected_reference: Any,
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
    deps: BoardExplanationFallbackDeps,
) -> ChatResponse:
    requirements = deps.with_task_details(
        requirements,
        action_type="explain_target",
        instruction=request.message,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = deps.generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        learning_clarification=learning_clarification,
        action_type="explain_target",
        target_excerpt=target_excerpt,
    )
    requirement_cleared = bool(chatbot_message)
    commit_operations(
        lesson,
        [],
        label="Board explanation",
        message="Answered only after receiving a board-side explanation directive",
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
            "board_explanation_directive": board_explanation_directive,
            **deps.reference_metadata(resolution=resource_resolution),
        },
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
        board_decision=BoardDecision(action="no_change", reason="本轮是板书指令授权后的讲解，不修改板书。"),
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
        requirement_cleared=requirement_cleared,
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )


def _board_task_explanation_target_excerpt(
    *,
    board_task: BoardTaskRequirementSheet,
    focus: BoardFocusRef | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> str:
    parts = [
        "已有板书任务清单已进入 explain 路线。",
        f"用户目标线索：{board_task.target_hint or '未单独提供'}",
        f"用户问题/主题：{board_task.question_or_topic or '未单独提供'}",
        f"定位裁决：{decision.reason or '已定位目标内容'}",
    ]
    if focus is not None:
        parts.append(f"当前允许讲解的目标内容：\n{focus_context(focus)}")
    other_candidates = [
        candidate
        for candidate in (decision.candidate_focuses or (resolution.candidates if resolution else []))
        if focus is None or (candidate.segment_id, candidate.excerpt) != (focus.segment_id, focus.excerpt)
    ]
    if other_candidates:
        candidate_lines = [
            f"{index}. {candidate.display_label or ' / '.join(candidate.heading_path) or '板书片段'}（正文摘录仅供板书侧后续授权，不交给 Chatbot）"
            for index, candidate in enumerate(other_candidates[:4], start=1)
        ]
        parts.append("同一任务中还存在的后续候选目标，仅作为顺序讲解上下文，不得越界讲解：\n" + "\n".join(candidate_lines))
    return "\n\n".join(part for part in parts if part.strip())
