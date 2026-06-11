from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
from app.services.chat.metadata import _board_task_metadata, _focus_metadata
from app.services.chat.response import _response
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution, focus_context


@dataclass(frozen=True)
class ExplainHandlerRuntime:
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]


def _board_task_explanation_target_excerpt(
    *,
    board_task: BoardTaskRequirementSheet,
    focus: BoardFocusRef | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> str | None:
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


def handle_board_task_explain(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    runtime: ExplainHandlerRuntime,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    interaction_metadata = source_interaction_metadata or {}
    focus = decision.target_focus or (resolution.focus if resolution else None)
    focus_excerpt = _board_task_explanation_target_excerpt(
        board_task=board_task,
        focus=focus,
        decision=decision,
        resolution=resolution,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = runtime.generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=runtime.requirements_from_board_task(
            base=requirements,
            board_task=board_task,
            action_type="explain_target",
            focus=focus,
        ),
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
                **runtime.board_search_evidence_metadata(resolution),
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
            chatbot_message="",
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="Board-directed explanation failed because Chatbot returned empty."),
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
            **runtime.board_search_evidence_metadata(resolution),
            **_focus_metadata(focus=focus, focus_candidates=resolution.candidates if resolution else []),
            **_board_task_metadata(
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
        runtime.clear_task_requirements(lesson)
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
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        resolved_focus=focus,
        requirement_cleared=cleared,
        board_task_stamp=consumed_stamp,
        completed_board_task_sheet=board_task if cleared else None,
    )
