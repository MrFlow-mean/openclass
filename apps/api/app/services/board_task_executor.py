from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardReadContext,
    BoardSearchEvidence,
    BoardTaskAction,
    BoardTaskRequirementSheet,
    DiffPreviewItem,
    EvidenceBundle,
    LearningClarificationStatus,
    LearningRequirementSheet,
    InteractionSession,
    Lesson,
    SelectionRef,
)
from app.services.board_document_editor import edit_existing_document
from app.services.board_range_reader import build_board_read_context
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations
from app.services.interaction_rule_compiler import compile_interaction_session
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.resource_resolver import evidence_metadata
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus


@dataclass(frozen=True)
class BoardTaskExecutionOutcome:
    chatbot_message: str
    board_decision: BoardDecision
    active_board_task_sheet: BoardTaskRequirementSheet | None
    board_task_stamp: BoardTaskHistoryStamp
    board_task_questions: list[str]
    history_operations: list[dict[str, Any]]
    resolved_focus: BoardFocusRef | None = None
    focus_candidates: list[BoardFocusRef] | None = None
    board_search_evidence: BoardSearchEvidence | None = None
    board_document_operation_status: str = "none"
    board_document_operation_failure_reason: str | None = None
    board_patch_diff: list[DiffPreviewItem] | None = None
    active_interaction_session: InteractionSession | None = None


def execute_ready_board_task(
    *,
    owner_user_id: str,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    selection: SelectionRef | None,
    conversation_summary: str,
    history_stamp: BoardTaskHistoryStamp,
    history_operations: list[dict[str, Any]],
    evidence_bundle: EvidenceBundle | None = None,
) -> BoardTaskExecutionOutcome:
    operations = list(history_operations)
    recorder = _recorder_from_pending_history(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        stamp=history_stamp,
        sheet=board_task,
        operations=operations,
    )
    action_type = _action_type_for_task(board_task)
    resolution = resolve_board_focus(
        lesson=lesson,
        user_message=user_message,
        selection=selection,
        action_type=action_type,
        board_task=board_task,
    )
    decision = _route_decision(
        lesson=lesson,
        board_task=board_task,
        resolution=resolution,
    )
    if _needs_focus(board_task=board_task, decision=decision) and _decision_focus(decision, resolution) is None:
        decision = _clarify_decision(decision=decision, resolution=resolution)

    if decision.route == "explain":
        return _execute_explain(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            conversation_summary=conversation_summary,
            recorder=recorder,
            operations=operations,
            decision=decision,
            resolution=resolution,
            evidence_bundle=evidence_bundle,
        )
    if decision.route == "chat":
        return _execute_chat(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            recorder=recorder,
            operations=operations,
            decision=decision,
            resolution=resolution,
            evidence_bundle=evidence_bundle,
        )
    if decision.route in {"write", "edit"}:
        return _execute_write_or_edit(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            conversation_summary=conversation_summary,
            recorder=recorder,
            operations=operations,
            decision=decision,
            resolution=resolution,
            evidence_bundle=evidence_bundle,
        )
    if decision.route == "await_write_confirmation":
        return _await_write_confirmation(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            recorder=recorder,
            operations=operations,
            decision=decision,
            resolution=resolution,
        )
    return _clarify_location(
        lesson=lesson,
        board_task=board_task,
        user_message=user_message,
        recorder=recorder,
        operations=operations,
        decision=decision,
        resolution=resolution,
    )


def _execute_explain(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    conversation_summary: str,
    recorder: BoardTaskHistoryRecorder,
    operations: list[dict[str, Any]],
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
    evidence_bundle: EvidenceBundle | None,
) -> BoardTaskExecutionOutcome:
    focus = _decision_focus(decision, resolution)
    if focus is None:
        return _clarify_location(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            recorder=recorder,
            operations=operations,
            decision=_clarify_decision(decision=decision, resolution=resolution),
            resolution=resolution,
        )
    read_context = _read_context_for_resolution(lesson=lesson, resolution=resolution, focus=focus)
    focus = read_context.target_focus
    ai_reply = openai_course_ai.generate_basic_chat_reply(
        board_document_state={"status": "non_empty", "has_content": True},
        conversation_summary=conversation_summary,
        user_message=user_message,
        resource_summary=evidence_bundle.context_text if evidence_bundle else "",
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    assistant_message_source = "chatbot_unreferenced_board_explanation" if chatbot_message else "chatbot_empty"
    cleared = bool(chatbot_message)
    source_stamp = recorder.current_stamp()
    commit_operations(
        lesson,
        [],
        label="Board task explanation",
        message="Executed an existing-board explanation task",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": user_message,
            "assistant_message": chatbot_message,
            "assistant_message_source": assistant_message_source,
            "document_changed": False,
            "chatbot_board_context": "not_referenced",
            "resolved_focus": focus.model_dump(mode="json"),
            **evidence_metadata(evidence_bundle),
            **_board_task_metadata(
                board_task=board_task,
                stamp=source_stamp,
                route="explain",
                decision=decision,
                cleared=cleared,
            ),
            **_location_metadata(resolution),
        },
    )
    stamp = source_stamp
    if cleared:
        stamp = recorder.consume(
            commit_id=lesson.history_graph.commits[-1].id,
            change_summary="Board explanation task was executed and consumed.",
        )
        lesson.board_task_requirements = None
    else:
        lesson.board_task_requirements = board_task
    operations.extend(recorder.operations)
    return BoardTaskExecutionOutcome(
        chatbot_message=chatbot_message,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        active_board_task_sheet=None if cleared else board_task,
        board_task_stamp=stamp,
        board_task_questions=[] if cleared else _board_task_questions(board_task),
        history_operations=operations,
        resolved_focus=focus,
        focus_candidates=resolution.candidates,
        board_search_evidence=resolution.evidence,
    )


def _execute_chat(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    recorder: BoardTaskHistoryRecorder,
    operations: list[dict[str, Any]],
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
    evidence_bundle: EvidenceBundle | None,
) -> BoardTaskExecutionOutcome:
    focus = _decision_focus(decision, resolution)
    if focus is None:
        return _clarify_location(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            recorder=recorder,
            operations=operations,
            decision=_clarify_decision(decision=decision, resolution=resolution),
            resolution=resolution,
        )
    source_stamp = recorder.current_stamp()
    read_context = _read_context_for_resolution(lesson=lesson, resolution=resolution, focus=focus)
    focus = read_context.target_focus
    target_excerpt = read_context.target_excerpt or focus_context(focus)
    session = compile_interaction_session(
        board_task=board_task,
        focus=focus,
        target_excerpt=target_excerpt,
        board_task_stamp=source_stamp,
    )
    chatbot_message = _interaction_opening_message(session)
    lesson.learning_requirements = None
    lesson.board_task_requirements = None
    lesson.active_interaction_session = session
    commit_operations(
        lesson,
        [],
        label="Interaction session start",
        message="Started an existing-board interaction session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_session_start",
            "user_message": user_message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "interaction_session",
            "document_changed": False,
            "resolved_focus": focus.model_dump(mode="json"),
            "active_interaction_session_after": session.model_dump(mode="json"),
            "interaction_session_after": session.model_dump(mode="json"),
            **evidence_metadata(evidence_bundle),
            **_board_task_metadata(
                board_task=board_task,
                stamp=source_stamp,
                route="chat",
                decision=decision,
                cleared=True,
            ),
            **_location_metadata(resolution),
        },
    )
    stamp = recorder.consume(
        commit_id=lesson.history_graph.commits[-1].id,
        change_summary="Board chat task started an interaction session and was consumed.",
    )
    operations.extend(recorder.operations)
    return BoardTaskExecutionOutcome(
        chatbot_message=chatbot_message,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        active_board_task_sheet=None,
        board_task_stamp=stamp,
        board_task_questions=[],
        history_operations=operations,
        resolved_focus=focus,
        focus_candidates=resolution.candidates,
        board_search_evidence=resolution.evidence,
        active_interaction_session=session,
    )


def _execute_write_or_edit(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    conversation_summary: str,
    recorder: BoardTaskHistoryRecorder,
    operations: list[dict[str, Any]],
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
    evidence_bundle: EvidenceBundle | None,
) -> BoardTaskExecutionOutcome:
    focus = _decision_focus(decision, resolution)
    if focus is None:
        return _clarify_location(
            lesson=lesson,
            board_task=board_task,
            user_message=user_message,
            recorder=recorder,
            operations=operations,
            decision=_clarify_decision(decision=decision, resolution=resolution),
            resolution=resolution,
        )
    read_context = _read_context_for_resolution(lesson=lesson, resolution=resolution, focus=focus)
    focus = read_context.target_focus
    action_type: BoardTaskAction = "rewrite_target" if decision.route == "edit" else "expand_target"
    target_scope = decision.target_scope or "focus"
    task_requirements = _requirements_from_board_task(
        lesson=lesson,
        board_task=board_task,
        action_type=action_type,
        focus=focus,
    )
    if decision.write_proposal.strip():
        task_requirements.action_instruction = decision.write_proposal.strip()
    edit_outcome = edit_existing_document(
        lesson=lesson,
        requirements=task_requirements,
        clarification=_task_clarification(board_task),
        resource_summary=evidence_bundle.context_text if evidence_bundle else "",
        conversation_summary=conversation_summary,
        user_instruction=task_requirements.action_instruction or user_message,
        selection_excerpt=None,
        focus=focus,
        target_scope=target_scope,
        allow_replace_document=False,
    )
    source_stamp = recorder.current_stamp()
    if not edit_outcome.changed:
        stamp = recorder.execution_failed(
            reason=edit_outcome.failure_reason or edit_outcome.summary or "Board task edit did not change the document.",
            metadata={
                "board_task_route": decision.route,
                "board_task_decision": decision.model_dump(mode="json"),
                "board_document_operation_status": edit_outcome.operation_status,
            },
        )
        operations.extend(recorder.operations)
        lesson.board_task_requirements = board_task
        return BoardTaskExecutionOutcome(
            chatbot_message=edit_outcome.chatbot_message,
            board_decision=edit_outcome.board_decision,
            active_board_task_sheet=board_task,
            board_task_stamp=stamp,
            board_task_questions=_board_task_questions(board_task),
            history_operations=operations,
            resolved_focus=focus,
            focus_candidates=resolution.candidates,
            board_search_evidence=resolution.evidence,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
    lesson.learning_requirements = None
    lesson.active_interaction_session = None
    commit_operations(
        lesson,
        edit_outcome.operations or [],
        label="Board task edit" if decision.route == "edit" else "Board task write",
        message=f"Executed an existing-board {decision.route} task",
        new_document=edit_outcome.new_document,
        metadata={
            "kind": "board_document_edit",
            "user_message": user_message,
            "assistant_message": edit_outcome.chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "document_changed": True,
            "board_document_operation_status": edit_outcome.operation_status,
            "board_document_editor_operation": edit_outcome.operation,
            "board_document_editor_summary": edit_outcome.summary,
            "board_patch_diff": [item.model_dump(mode="json") for item in edit_outcome.diff_preview or []],
            "resolved_focus": focus.model_dump(mode="json"),
            "target_scope": target_scope,
            **evidence_metadata(evidence_bundle),
            **_board_task_metadata(
                board_task=board_task,
                stamp=source_stamp,
                route=decision.route,
                decision=decision,
                cleared=True,
            ),
            **_location_metadata(resolution),
        },
    )
    stamp = recorder.consume(
        commit_id=lesson.history_graph.commits[-1].id,
        change_summary=f"Board {decision.route} task was executed and consumed.",
    )
    lesson.board_task_requirements = None
    operations.extend(recorder.operations)
    return BoardTaskExecutionOutcome(
        chatbot_message=edit_outcome.chatbot_message,
        board_decision=edit_outcome.board_decision,
        active_board_task_sheet=None,
        board_task_stamp=stamp,
        board_task_questions=[],
        history_operations=operations,
        resolved_focus=focus,
        focus_candidates=resolution.candidates,
        board_search_evidence=resolution.evidence,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=None,
        board_patch_diff=edit_outcome.diff_preview or [],
    )


def _await_write_confirmation(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    recorder: BoardTaskHistoryRecorder,
    operations: list[dict[str, Any]],
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
) -> BoardTaskExecutionOutcome:
    next_task = board_task.model_copy(
        update={
            "requested_action": "write",
            "location_status": "content_absent",
            "confirmation_status": "awaiting",
            "question_or_topic": decision.write_proposal or board_task.question_or_topic,
            "missing_items": [],
            "progress": 100,
            "clarification_question": "",
        }
    )
    stamp = recorder.record_update(
        sheet=next_task,
        status="awaiting_confirmation",
        change_summary=decision.reason or "Awaiting learner confirmation before writing absent board content.",
    )
    lesson.board_task_requirements = next_task
    chatbot_message = "当前板书里还没有定位到对应内容。要不要先把这部分补写进板书，再继续讲解？"
    commit_operations(
        lesson,
        [],
        label="Board write confirmation",
        message="Asked the learner to confirm writing absent board content",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": user_message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "workflow",
            "document_changed": False,
            **_board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route="await_write_confirmation",
                decision=decision,
                cleared=False,
            ),
            **_location_metadata(resolution),
        },
    )
    operations.extend(recorder.operations)
    return BoardTaskExecutionOutcome(
        chatbot_message=chatbot_message,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        active_board_task_sheet=next_task,
        board_task_stamp=stamp,
        board_task_questions=[],
        history_operations=operations,
        focus_candidates=resolution.candidates,
        board_search_evidence=resolution.evidence,
    )


def _clarify_location(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    user_message: str,
    recorder: BoardTaskHistoryRecorder,
    operations: list[dict[str, Any]],
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
) -> BoardTaskExecutionOutcome:
    question = resolution.question or decision.reason or "我还没有定位到要处理的板书位置。请选中一段内容，或说明标题、前后文。"
    next_task = board_task.model_copy(
        update={
            "location_status": "ambiguous" if decision.location_status == "ambiguous" else "missing",
            "clarification_question": question,
            "missing_items": ["位置"],
            "progress": 67 if board_task.requested_action and board_task.question_or_topic.strip() else 34,
        }
    )
    stamp = recorder.record_update(sheet=next_task, change_summary=question)
    lesson.board_task_requirements = next_task
    commit_operations(
        lesson,
        [],
        label="Board task location clarification",
        message="Asked the learner to confirm the board task location",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": user_message,
            "assistant_message": question,
            "assistant_message_source": "workflow",
            "document_changed": False,
            **_board_task_metadata(
                board_task=next_task,
                stamp=stamp,
                route="clarify_location",
                decision=decision,
                cleared=False,
            ),
            **_location_metadata(resolution),
        },
    )
    operations.extend(recorder.operations)
    return BoardTaskExecutionOutcome(
        chatbot_message=question,
        board_decision=BoardDecision(action="await_focus_choice", reason=decision.reason or question),
        active_board_task_sheet=next_task,
        board_task_stamp=stamp,
        board_task_questions=[question],
        history_operations=operations,
        focus_candidates=decision.candidate_focuses or resolution.candidates,
        board_search_evidence=resolution.evidence,
    )


def _route_decision(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    resolution: FocusResolution,
) -> BoardTaskRouteDecision:
    if resolution.status == "content_absent" and board_task.requested_action in {"explain", "chat"}:
        return _fallback_route_decision(board_task=board_task, resolution=resolution)
    generated = openai_course_ai.generate_board_task_route_decision(
        lesson_title=lesson.title,
        board_task=board_task,
        location_evidence=resolution.evidence.model_dump(mode="json") if resolution.evidence else {"status": resolution.status},
        resource_summary="",
    )
    return generated or _fallback_route_decision(board_task=board_task, resolution=resolution)


def _fallback_route_decision(
    *,
    board_task: BoardTaskRequirementSheet,
    resolution: FocusResolution,
) -> BoardTaskRouteDecision:
    if resolution.status == "content_absent" and board_task.requested_action in {"explain", "chat"}:
        return BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            reason="目标内容不在当前板书中，需要先确认是否扩写。",
            write_proposal=board_task.question_or_topic or board_task.target_hint,
            target_scope="append",
        )
    if resolution.resolved and resolution.focus is not None and board_task.requested_action in {"write", "edit", "explain", "chat"}:
        return BoardTaskRouteDecision(
            route=board_task.requested_action,
            location_status="found",
            target_focus=resolution.focus,
            reason="已根据任务清单和定位证据找到目标位置。",
            target_scope="focus",
        )
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous" if resolution.status == "ambiguous" else "missing",
        candidate_focuses=resolution.candidates,
        reason=resolution.question or "目标位置尚未定位。",
    )


def _clarify_decision(
    *,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
) -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous" if resolution.status == "ambiguous" else "missing",
        candidate_focuses=decision.candidate_focuses or resolution.candidates,
        reason=decision.reason or resolution.question or "目标位置尚未定位。",
    )


def _needs_focus(*, board_task: BoardTaskRequirementSheet, decision: BoardTaskRouteDecision) -> bool:
    if decision.route in {"explain", "edit", "chat"}:
        return True
    if decision.route == "write" and decision.target_scope != "append":
        return True
    return board_task.location_kind == "target_range" and decision.route in {"write", "edit", "explain", "chat"}


def _decision_focus(
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution,
) -> BoardFocusRef | None:
    if decision.target_focus is not None and resolution.focus is not None:
        if _same_focus(decision.target_focus, resolution.focus):
            return resolution.focus
    return decision.target_focus or resolution.focus


def _action_type_for_task(board_task: BoardTaskRequirementSheet) -> BoardTaskAction | None:
    if board_task.requested_action == "explain":
        return "explain_target"
    if board_task.requested_action == "edit":
        return "rewrite_target"
    if board_task.requested_action == "write":
        return "expand_target"
    return None


def _requirements_from_board_task(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    action_type: BoardTaskAction,
    focus: BoardFocusRef | None,
) -> LearningRequirementSheet:
    requirements = effective_requirements(lesson)
    instruction = board_task.question_or_topic.strip() or board_task.target_hint.strip()
    return requirements.model_copy(
        update={
            "theme": instruction or requirements.theme,
            "learning_goal": instruction or requirements.learning_goal,
            "current_questions": [],
            "board_workflow": "act_on_existing_board",
            "target_location": focus,
            "location_status": "resolved" if focus else board_task.location_status,
            "action_type": action_type,
            "action_instruction": instruction,
        }
    )


def _task_clarification(board_task: BoardTaskRequirementSheet) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="已有板书任务清单已完整，进入执行链路。",
        missing_items=[],
        can_start=True,
        forced_start=False,
        summary=board_task.question_or_topic or board_task.target_hint,
        next_question="",
        ready_for_board=False,
    )


def _recorder_from_pending_history(
    *,
    owner_user_id: str,
    lesson_id: str,
    stamp: BoardTaskHistoryStamp,
    sheet: BoardTaskRequirementSheet,
    operations: list[dict[str, Any]],
) -> BoardTaskHistoryRecorder:
    latest_version_number = 0
    latest_sheet_json = json.dumps(sheet.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    for operation in operations:
        if (
            operation.get("type") == "insert_board_task_version"
            and operation.get("run_id") == stamp.run_id
            and operation.get("id") == stamp.version_id
        ):
            latest_version_number = int(operation.get("version_number") or latest_version_number)
            latest_sheet_json = str(operation.get("sheet_json") or latest_sheet_json)
    state = {
        "run_id": stamp.run_id,
        "status": stamp.phase,
        "latest_version_id": stamp.version_id,
        "latest_version_number": latest_version_number,
        "latest_sheet_json": latest_sheet_json,
    }
    return BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson_id,
        state=state,
    )


def _board_task_metadata(
    *,
    board_task: BoardTaskRequirementSheet,
    stamp: BoardTaskHistoryStamp,
    route: str,
    decision: BoardTaskRouteDecision,
    cleared: bool,
) -> dict[str, object]:
    return {
        "board_task_sheet": board_task.model_dump(mode="json"),
        "active_board_task_sheet_after": None if cleared else board_task.model_dump(mode="json"),
        "board_task_cleared": cleared,
        "board_task_run_id": stamp.run_id,
        "board_task_version_id": stamp.version_id,
        "board_task_phase": stamp.phase,
        "board_task_route": route,
        "board_task_decision": decision.model_dump(mode="json"),
    }


def _location_metadata(resolution: FocusResolution) -> dict[str, object]:
    return {
        "board_search_status": resolution.status,
        "focus_candidates": [candidate.model_dump(mode="json") for candidate in resolution.candidates],
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution.evidence else None,
    }


def _board_task_questions(sheet: BoardTaskRequirementSheet) -> list[str]:
    question = sheet.clarification_question.strip()
    return [question] if question else []


def _interaction_opening_message(session: InteractionSession) -> str:
    if session.rule_steps:
        first_step = session.rule_steps[0]
        if first_step.expected_user_input.strip():
            return f"好，我们按这个规则来。你先输入：{first_step.expected_user_input.strip()}"
    if session.compliant_input_rule.strip():
        return f"好，我们按这个规则开始。{session.compliant_input_rule.strip()}"
    return "好，我们按这个规则开始。你先按规则输入。"


def _read_context_for_resolution(
    *,
    lesson: Lesson,
    resolution: FocusResolution,
    focus: BoardFocusRef,
) -> BoardReadContext:
    read_context = resolution.evidence.read_context if resolution.evidence else None
    if read_context is not None and _same_focus(read_context.target_focus, focus):
        return read_context
    return build_board_read_context(lesson=lesson, focus=focus)


def _same_focus(left: BoardFocusRef, right: BoardFocusRef) -> bool:
    if left.match_id and right.match_id and left.match_id == right.match_id:
        return True
    if left.segment_id and right.segment_id and left.segment_id == right.segment_id:
        return True
    if left.text_hash and right.text_hash and left.text_hash == right.text_hash:
        return True
    return False
