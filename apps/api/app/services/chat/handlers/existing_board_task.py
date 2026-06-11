from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
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
from app.services.board_task_manager import (
    is_write_confirmation,
    is_write_decline,
    make_write_task_from_topic,
    normalize_board_task_sheet,
    update_board_task_from_chat,
)
from app.services.chat.context import compact_text as _compact_text
from app.services.chat.handlers.board_task import BoardTaskRouteRuntime, dispatch_board_task_route
from app.services.chat.handlers.edit_blackboard import EditBlackboardRuntime, handle_board_task_write
from app.services.chat.metadata import _board_task_metadata, _focus_metadata
from app.services.chat.response import _response
from app.services.chat.sequence import SequenceRuntime, _requests_sequential_explanation, _start_section_explanation_sequence
from app.services.chat.intent import (
    _infer_board_task_action,
    _requests_explanation,
    _requests_learning_start,
    _should_force_explain_task,
)
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import is_document_empty
from app.services.segment_resolver import FocusResolution
from app.services.sequence_planner import maybe_apply_sequential_explanation_choice, plan_explanation_sequence
from app.services.turn_intent import wants_whole_document_scope


@dataclass(frozen=True)
class ExistingBoardTaskRuntime:
    edit_runtime: EditBlackboardRuntime
    sequence_runtime: SequenceRuntime
    board_task_route_runtime: BoardTaskRouteRuntime
    resource_summary: Callable[[list[ResourceLibraryItem]], str]
    resolve_board_focus: Callable[..., FocusResolution]
    latest_learning_clarification: Callable[..., LearningClarificationStatus]
    requests_existing_board_generation_control: Callable[[str], bool]
    requests_interaction_rule: Callable[[str], bool]
    maybe_inherit_recent_board_edit_focus: Callable[..., BoardTaskRequirementSheet]
    activate_board_task_requirements: Callable[[Lesson, BoardTaskRequirementSheet], None]
    emit_board_task_update: Callable[..., None]
    generate_board_task_clarification_message: Callable[..., tuple[str, str]]
    generate_focus_candidate_message: Callable[..., tuple[str, str]]
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    save_workspace_for_user: Callable[..., None]


def _requests_whole_document_scope(*values: str) -> bool:
    compact = _compact_text(" ".join(value for value in values if value), limit=300)
    return wants_whole_document_scope(compact)


def _whole_document_focus(lesson: Lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=None,
        kind=None,
        heading_path=[lesson.board_document.title or lesson.title],
        excerpt=_compact_text(lesson.board_document.content_text, limit=2400),
        confidence=1.0,
        reason="用户明确要求处理全文，板书侧将目标范围设为 whole_document。",
        display_label="全文",
        match_id=f"whole_document:{lesson.board_document.id}",
        score_breakdown={"whole_document_scope": 1.0},
    )


def _synthetic_focus_resolution(focus: BoardFocusRef) -> FocusResolution:
    return FocusResolution(focus=focus, candidates=[focus], status="resolved", question="")


def _task_location_evidence(resolution: FocusResolution | None) -> dict[str, object]:
    if resolution is None:
        return {"status": "missing", "focus": None, "candidates": [], "board_search_evidence": None}
    return {
        "status": resolution.status,
        "focus": resolution.focus.model_dump(mode="json") if resolution.focus else None,
        "candidates": [candidate.model_dump(mode="json") for candidate in resolution.candidates],
        "question": resolution.question,
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution.evidence else None,
    }


def _fallback_board_task_decision(
    *,
    board_task: BoardTaskRequirementSheet,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    if board_task.requested_action == "write":
        if resolution is not None and resolution.resolved:
            return BoardTaskRouteDecision(
                route="write",
                location_status="found",
                target_focus=resolution.focus,
                candidate_focuses=resolution.candidates,
                reason="定位器已找到扩写目标位置。",
                write_proposal=board_task.question_or_topic,
            )
        if resolution is not None and resolution.status == "ambiguous":
            return BoardTaskRouteDecision(
                route="clarify_location",
                location_status="ambiguous",
                target_focus=None,
                candidate_focuses=resolution.candidates,
                reason=resolution.question,
            )
        if board_task.confirmation_status == "confirmed":
            return BoardTaskRouteDecision(
                route="write",
                location_status="content_absent" if resolution is None or not resolution.resolved else "found",
                target_focus=resolution.focus if resolution and resolution.focus else None,
                candidate_focuses=resolution.candidates if resolution else [],
                reason="用户已经确认扩写或明确要求写入新内容。",
                write_proposal=board_task.question_or_topic,
            )
        if board_task.confirmation_status == "none":
            return BoardTaskRouteDecision(
                route="write",
                location_status="missing",
                target_focus=None,
                candidate_focuses=[],
                reason="用户明确要求写入或续写板书内容。",
                write_proposal=board_task.question_or_topic,
            )
        return BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            target_focus=None,
            candidate_focuses=[],
            reason="当前板书没有可直接处理的目标内容，需要先确认是否扩写。",
            write_proposal=board_task.question_or_topic,
        )
    if resolution is None or not resolution.resolved:
        if resolution and resolution.status == "ambiguous":
            return BoardTaskRouteDecision(
                route="clarify_location",
                location_status="ambiguous",
                target_focus=None,
                candidate_focuses=resolution.candidates,
                reason=resolution.question,
            )
        if (
            board_task.requested_action in {"explain", "chat"}
            and board_task.question_or_topic
            and not _is_vague_explanation_topic(board_task.question_or_topic)
        ):
            return BoardTaskRouteDecision(
                route="await_write_confirmation",
                location_status="content_absent",
                target_focus=None,
                candidate_focuses=[],
                reason="当前板书没有定位到相关内容，需要先确认是否扩写。",
                write_proposal=board_task.question_or_topic,
            )
        return BoardTaskRouteDecision(
            route="clarify_location",
            location_status="missing",
            target_focus=None,
            candidate_focuses=resolution.candidates if resolution else [],
            reason=resolution.question if resolution else "还不能定位目标位置。",
        )
    if board_task.requested_action == "edit":
        route = "edit"
    elif board_task.requested_action == "chat":
        route = "chat"
    else:
        route = "explain"
    return BoardTaskRouteDecision(
        route=route,
        location_status="found",
        target_focus=resolution.focus,
        candidate_focuses=resolution.candidates,
        reason="定位器已找到可操作的板书位置。",
    )


def _with_decision_target_scope(
    *,
    decision: BoardTaskRouteDecision,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    scope = decision.target_scope
    if not scope:
        if _requests_whole_document_scope(request_message, board_task.target_hint, board_task.question_or_topic):
            scope = "whole_document"
        elif decision.route == "write" and _decision_focus(decision, resolution) is None:
            scope = "append"
        elif _decision_focus(decision, resolution) is not None:
            scope = "focus"
    if scope == decision.target_scope:
        return decision
    return BoardTaskRouteDecision(
        route=decision.route,
        location_status=decision.location_status,
        target_focus=decision.target_focus,
        candidate_focuses=decision.candidate_focuses,
        reason=decision.reason,
        write_proposal=decision.write_proposal,
        target_scope=scope,
    )


def _is_vague_explanation_topic(topic: str) -> bool:
    compact = _compact_text(topic, limit=160)
    if not compact:
        return True
    residue = re.sub(
        r"(你|我|帮我|为我|请|就|当|当作|作为|是|零基础|0基础|基础|直接|开始|从头|先|"
        r"讲|讲解|讲述|解释|说明|生成|准备|板书|内容|一下|的|地|，|,|。|！|!|？|\?|\s)+",
        "",
        compact,
    )
    return len(residue.strip()) < 2


def _decision_focus(decision: BoardTaskRouteDecision, resolution: FocusResolution | None) -> BoardFocusRef | None:
    return decision.target_focus or (resolution.focus if resolution else None)


def _decision_must_have_focus(
    *,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
) -> bool:
    if decision.route in {"edit", "explain", "chat"}:
        return True
    return decision.route == "write" and bool(board_task.target_hint.strip()) and decision.location_status != "content_absent"


def _clarify_decision_for_missing_focus(
    *,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous" if resolution and resolution.status == "ambiguous" else "missing",
        target_focus=None,
        candidate_focuses=resolution.candidates if resolution else decision.candidate_focuses,
        reason=(resolution.question if resolution and resolution.question else decision.reason or "需要先确认目标位置。"),
        write_proposal=decision.write_proposal,
    )


def _board_task_action_to_board_action(board_task: BoardTaskRequirementSheet) -> BoardTaskAction | None:
    if board_task.requested_action == "edit":
        return "rewrite_target"
    if board_task.requested_action == "explain":
        return "explain_target"
    if board_task.requested_action == "chat":
        return "explain_target"
    if board_task.requested_action == "write":
        return "append_section"
    return None


def handle_existing_board_task_flow(
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
    runtime: ExistingBoardTaskRuntime,
    source_interaction_metadata: dict[str, object] | None = None,
    force_task_attempt: bool = False,
) -> ChatResponse | None:
    if is_document_empty(lesson.board_document):
        return None
    if request.board_generation_action == "start" or request.teaching_action is not None:
        return None
    if request.resource_reference_action is not None:
        return None
    existing_task = lesson.board_task_requirements
    interaction_metadata = source_interaction_metadata or {}
    compact_request = _compact_text(request.message, limit=280)
    if not existing_task and (
        not _should_force_explain_task(compact_request)
        and (
            _requests_learning_start(request.message)
            or bool(re.search(r"(开始|直接|从头|零基础).{0,12}(讲解|讲|学)", compact_request))
            or runtime.requests_existing_board_generation_control(request.message)
        )
    ):
        return None

    learning_clarification = runtime.latest_learning_clarification(lesson, requirements=requirements)
    if (
        existing_task is not None
        and existing_task.confirmation_status == "awaiting"
        and existing_task.requested_action == "write"
    ):
        response = _maybe_handle_awaiting_write_confirmation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            existing_task=existing_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            interaction_metadata=interaction_metadata,
            runtime=runtime,
        )
        if response is not None:
            return response

    action_type = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=False,
    )
    if (
        action_type is None
        and not existing_task
        and request.interaction_mode != "direct_edit"
        and not runtime.requests_interaction_rule(compact_request)
        and not _requests_explanation(request.message)
        and not force_task_attempt
    ):
        return None

    board_task = update_board_task_from_chat(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        user_message=request.message,
        selection=request.selection,
        selection_excerpt=selection_excerpt,
        existing=existing_task,
    )
    if _should_force_explain_task(compact_request) and board_task.requested_action != "explain":
        explain_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
        explain_task.requested_action = "explain"
        explain_task.confirmation_status = "none"
        board_task = normalize_board_task_sheet(
            explain_task,
            selection=request.selection,
            selection_excerpt=selection_excerpt,
        )
    board_task = runtime.maybe_inherit_recent_board_edit_focus(
        lesson=lesson,
        board_task=board_task,
        request_message=request.message,
    )
    runtime.activate_board_task_requirements(lesson, board_task)
    stamp = board_task_history.record_update(sheet=board_task)
    runtime.emit_board_task_update(lesson=lesson, sheet=board_task, stamp=stamp)
    if board_task.progress < 100:
        chatbot_message, chatbot_message_source = runtime.generate_board_task_clarification_message(
            lesson=lesson,
            resources=resources,
            conversation=request.conversation,
            request=request,
            board_task=board_task,
            context=board_task.clarification_question,
        )
        commit_operations(
            lesson,
            [],
            label="Board task clarification",
            message="Asked for a missing field in the existing-board task sheet",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **interaction_metadata,
                **_board_task_metadata(board_task=board_task, stamp=stamp, route="clarify_location", cleared=False),
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
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason=board_task.clarification_question),
            board_task_history=board_task_history,
        )

    board_action = _board_task_action_to_board_action(board_task)
    resolution = None
    original_location_status = board_task.location_status
    if _requests_whole_document_scope(request.message, board_task.target_hint, board_task.question_or_topic):
        resolution = _synthetic_focus_resolution(_whole_document_focus(lesson))
    elif board_task.requested_action != "write" or board_task.target_hint or selection_excerpt:
        locator_query = _compact_text(
            " ".join(part for part in [board_task.target_hint, board_task.question_or_topic] if part),
            limit=500,
        )
        resolution = runtime.resolve_board_focus(
            lesson=lesson,
            user_message=locator_query,
            selection=request.selection,
            selection_text=selection_text,
            action_type=board_action,
            board_task=board_task,
        )
    if resolution and resolution.resolved and resolution.focus:
        resolved_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
        resolved_task.target_location = resolution.focus
        resolved_task.location_status = "selected" if resolution.status == "selected" else "resolved"
        runtime.activate_board_task_requirements(lesson, resolved_task)
        stamp = board_task_history.record_update(
            sheet=resolved_task,
            change_summary="Board-side locator confirmed the target location.",
        )
        runtime.emit_board_task_update(lesson=lesson, sheet=resolved_task, stamp=stamp)
        board_task = resolved_task
    can_use_local_route_decision = (
        resolution is not None
        and resolution.resolved
        and board_task.requested_action in {"write", "edit", "explain", "chat"}
        and original_location_status != "ambiguous"
        and not _requests_sequential_explanation(request.message)
    )
    if can_use_local_route_decision:
        decision = _fallback_board_task_decision(board_task=board_task, resolution=resolution)
    else:
        decision = openai_course_ai.generate_board_task_route_decision(
            lesson_title=lesson.title,
            board_task=board_task,
            location_evidence=_task_location_evidence(resolution),
            resource_summary=runtime.resource_summary(resources),
        ) or _fallback_board_task_decision(board_task=board_task, resolution=resolution)
    decision = _with_decision_target_scope(
        decision=decision,
        board_task=board_task,
        request_message=request.message,
        resolution=resolution,
    )
    if _decision_must_have_focus(board_task=board_task, decision=decision) and _decision_focus(decision, resolution) is None:
        decision = _clarify_decision_for_missing_focus(decision=decision, resolution=resolution)
    decision = maybe_apply_sequential_explanation_choice(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=resolution,
        request_message=request.message,
    )
    decision = _with_decision_target_scope(
        decision=decision,
        board_task=board_task,
        request_message=request.message,
        resolution=resolution,
    )
    sequence_plan = plan_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=resolution,
        request_message=request.message,
    )
    if sequence_plan is not None:
        return _start_section_explanation_sequence(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=stamp,
            decision=decision,
            resolution=resolution,
            sequence_items=sequence_plan.items,
            requirement_history=requirement_history,
            interaction_metadata=interaction_metadata,
            runtime=runtime.sequence_runtime,
        )

    if decision.route == "clarify_location":
        return _handle_focus_clarification(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            board_action=board_action,
            decision=decision,
            resolution=resolution,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            interaction_metadata=interaction_metadata,
            runtime=runtime,
        )

    if decision.route == "await_write_confirmation":
        return _handle_write_confirmation_prompt(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            decision=decision,
            resolution=resolution,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            interaction_metadata=interaction_metadata,
            runtime=runtime,
        )

    return dispatch_board_task_route(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        selection_excerpt=selection_excerpt,
        selection_text=selection_text,
        action_type=action_type,
        board_task=board_task,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        board_task_stamp=stamp,
        decision=decision,
        resolution=resolution,
        source_interaction_metadata=interaction_metadata,
        runtime=runtime.board_task_route_runtime,
    )


def _maybe_handle_awaiting_write_confirmation(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    existing_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    interaction_metadata: dict[str, object],
    runtime: ExistingBoardTaskRuntime,
) -> ChatResponse | None:
    if is_write_decline(request.message):
        stamp = board_task_history.not_executed(reason="用户取消了扩写确认。")
        lesson.board_task_requirements = None
        commit_operations(
            lesson,
            [],
            label="Board task cancelled",
            message="Cancelled an awaiting board write task",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": "",
                "assistant_message_source": "board_task_cancelled",
                **interaction_metadata,
                **_board_task_metadata(board_task=existing_task, stamp=stamp, route="await_write_confirmation", cleared=True),
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
            board_decision=BoardDecision(action="no_change", reason="用户取消了扩写。"),
            board_task_stamp=stamp,
        )
    if is_write_confirmation(request.message):
        confirmed_task = BoardTaskRequirementSheet.model_validate(existing_task.model_dump(mode="json"))
        confirmed_task.confirmation_status = "confirmed"
        confirmed_task.progress = 100
        return handle_board_task_write(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=confirmed_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            runtime=runtime.edit_runtime,
        )
    return None


def _handle_focus_clarification(
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
    board_action,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    interaction_metadata: dict[str, object],
    runtime: ExistingBoardTaskRuntime,
) -> ChatResponse:
    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.location_status = "ambiguous" if decision.location_status == "ambiguous" else "missing"
    next_task.failure_count += 1 if board_task.requested_action == "edit" else 0
    if board_task.requested_action == "edit" and next_task.failure_count >= 2:
        old_stamp = board_task_history.record_update(
            sheet=next_task,
            change_summary="Edit target could not be located twice.",
        )
        board_task_history.not_executed(reason="编辑目标连续两次未定位，旧任务未执行。")
        new_task = make_write_task_from_topic(board_task.question_or_topic)
        runtime.activate_board_task_requirements(lesson, new_task)
        new_stamp = board_task_history.record_update(
            sheet=new_task,
            status="awaiting_confirmation",
            change_summary="Created a write task from an unresolved edit topic.",
        )
        runtime.emit_board_task_update(lesson=lesson, sheet=new_task, stamp=new_stamp)
        chatbot_message, chatbot_message_source = runtime.generate_board_task_clarification_message(
            lesson=lesson,
            resources=resources,
            conversation=request.conversation,
            request=request,
            board_task=new_task,
            context="板书里没有定位到可编辑的原内容。请确认是否改为扩写相关内容。",
        )
        commit_operations(
            lesson,
            [],
            label="Board task converted to write confirmation",
            message="Archived an unresolved edit task and opened a write confirmation task",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                **interaction_metadata,
                **runtime.board_search_evidence_metadata(resolution),
                **_board_task_metadata(board_task=board_task, stamp=old_stamp, route="clarify_location", cleared=True),
                "new_board_task": new_task.model_dump(mode="json"),
                "new_board_task_run_id": new_stamp.run_id,
                "new_board_task_version_id": new_stamp.version_id,
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
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="编辑目标未定位，已转为扩写确认。"),
            board_task_stamp=new_stamp,
        )

    runtime.activate_board_task_requirements(lesson, next_task)
    stamp = board_task_history.record_update(sheet=next_task, change_summary=decision.reason)
    runtime.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
    fallback_resolution = FocusResolution(
        focus=None,
        candidates=decision.candidate_focuses,
        status="ambiguous" if decision.candidate_focuses else "missing",
        question=decision.reason,
    )
    chatbot_message, chatbot_message_source = runtime.generate_focus_candidate_message(
        lesson=lesson,
        requirements=runtime.requirements_from_board_task(
            base=requirements,
            board_task=next_task,
            action_type=board_action,
        ),
        resources=resources,
        conversation=request.conversation,
        request=request,
        resolution=resolution or fallback_resolution,
    )
    commit_operations(
        lesson,
        [],
        label="Board task location clarification",
        message="Asked the learner to confirm the board task location",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **interaction_metadata,
            **runtime.board_search_evidence_metadata(resolution),
            **_focus_metadata(focus=None, focus_candidates=decision.candidate_focuses),
            **_board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route=decision.route,
                decision=decision.model_dump(mode="json"),
                cleared=False,
            ),
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
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="await_focus_choice", reason=decision.reason),
        focus_candidates=decision.candidate_focuses,
        board_task_history=board_task_history,
    )


def _handle_write_confirmation_prompt(
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
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    interaction_metadata: dict[str, object],
    runtime: ExistingBoardTaskRuntime,
) -> ChatResponse:
    next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    next_task.requested_action = "write"
    next_task.location_status = "content_absent"
    next_task.confirmation_status = "awaiting"
    next_task.progress = 100
    next_task.missing_items = []
    next_task.clarification_question = ""
    runtime.activate_board_task_requirements(lesson, next_task)
    stamp = board_task_history.record_update(
        sheet=next_task,
        status="awaiting_confirmation",
        change_summary=decision.reason or "Awaiting learner confirmation before writing new board content.",
    )
    runtime.emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
    chatbot_message, chatbot_message_source = runtime.generate_board_task_clarification_message(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        request=request,
        board_task=next_task,
        context="板书里没有对应内容。请询问用户是否要先扩写板书，再继续学习。",
    )
    commit_operations(
        lesson,
        [],
        label="Board write confirmation",
        message="Asked the learner to confirm writing absent board content",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            **interaction_metadata,
            **runtime.board_search_evidence_metadata(resolution),
            **_board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route=decision.route,
                decision=decision.model_dump(mode="json"),
                cleared=False,
            ),
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
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        board_task_history=board_task_history,
    )
