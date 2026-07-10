from __future__ import annotations

from typing import Any

from app.models import (
    BoardDecision,
    ChatResponse,
    EvidenceBundle,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services import workspace_state
from app.services.board_document_editor import generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide
from app.services.course_runtime import effective_requirements
from app.services.confirmed_source_context import ConfirmedSourceContextError, load_confirmed_source_context
from app.services.history import commit_operations, current_head_commit
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_resolver import evidence_metadata
from app.services.rich_document import is_document_empty
from app.services.source_evidence_store import source_evidence_store


def run_blank_board_generation(
    *,
    workspace,
    package,
    lesson,
    user_id: str,
) -> ChatResponse:
    history_state = workspace_state.load_learning_requirement_history_state_for_user(user_id, lesson.id)
    requirements = _requirement_from_state(history_state) or lesson.learning_requirements
    clarification = _clarification_from_state(history_state)

    if not is_document_empty(lesson.board_document):
        return _failure_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            reason="当前板书不是空白文档，已阻止从零生成。",
        )
    if requirements is None or clarification is None or not clarification.ready_for_board:
        return _failure_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            reason="学习需求尚未清晰，不能开始生成板书。",
        )

    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    try:
        source_context = load_confirmed_source_context(
            owner_user_id=user_id,
            package_id=package.id,
            lesson_id=lesson.id,
            requirement_run_id=recorder.snapshot.run_id,
            requirements=requirements,
        )
    except ConfirmedSourceContextError as exc:
        pending_evidence = source_evidence_store.latest_requirement_bundle(
            owner_user_id=user_id,
            lesson_id=lesson.id,
            requirement_run_id=recorder.snapshot.run_id,
        )
        return _failure_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            reason=str(exc),
            candidate_evidence_bundle=pending_evidence if pending_evidence and pending_evidence.status == "candidate" else None,
        )
    evidence_bundle = source_context.evidence_bundle
    resource_summary = source_context.context_text
    lesson.learning_requirements = requirements
    frozen_stamp = recorder.freeze(
        requirements=requirements,
        clarification=clarification,
        forced=clarification.forced_start,
        change_summary="学习需求已冻结，准备生成空白板书。",
    )
    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
        requirement_run_id=frozen_stamp.run_id,
        frozen_requirement_version_id=frozen_stamp.version_id,
        resource_summary=resource_summary,
    )

    if outcome.operation_status != "succeeded" or not outcome.changed:
        reason = outcome.failure_reason or outcome.summary or "板书生成失败。"
        failure_stamp = recorder.generation_failed(
            reason=reason,
            metadata={
                "board_generation_action": "start",
                "operation": outcome.operation,
            },
        )
        workspace_state.save_workspace_and_learning_requirement_history_for_user(
            user_id,
            workspace,
            learning_requirement_history_operations=recorder.operations,
        )
        return _failure_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirements=requirements,
            clarification=clarification,
            reason=reason,
            stamp=failure_stamp,
            board_decision=outcome.board_decision,
        )

    lesson_for_teaching = lesson.model_copy(deep=True, update={"board_document": outcome.new_document})
    lesson.board_teaching_guide = build_board_teaching_guide(lesson_for_teaching)
    lesson.board_teaching_progress = None
    chatbot_reply = openai_course_ai.generate_post_board_generation_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=lesson.board_teaching_guide.chatbot_brief,
        resource_summary=resource_summary,
        requirement_context=requirements.model_dump(mode="json"),
        editor_summary=outcome.summary,
        section_titles=outcome.section_titles or lesson.board_teaching_guide.teaching_flow,
    )
    chatbot_message = (chatbot_reply.chatbot_message if chatbot_reply else outcome.chatbot_message).strip()
    assistant_message_source = (
        "chatbot_post_board_generation"
        if chatbot_reply and chatbot_message
        else outcome.assistant_message_source
        if chatbot_message
        else "chatbot_empty"
    )

    lesson.learning_requirements = None
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None
    commit_operations(
        lesson,
        [],
        label="Board document generation",
        message="Generated a blank board document from frozen learning requirements",
        new_document=outcome.new_document,
        metadata={
            "kind": "board_document_generation",
            "board_generation_action": "start",
            "user_message": "开始生成板书",
            "assistant_message": chatbot_message,
            "assistant_message_source": assistant_message_source,
            "document_changed": True,
            "board_document_operation_status": outcome.operation_status,
            "board_document_editor_operation": outcome.operation,
            "board_document_editor_summary": outcome.summary,
            "section_titles": outcome.section_titles,
            "board_teaching_flow": lesson.board_teaching_guide.teaching_flow,
            "board_teaching_plan_count": len(lesson.board_teaching_guide.section_plans),
            "teaching_progress_after": None,
            "requirement_run_id": frozen_stamp.run_id,
            "frozen_requirement_version_id": frozen_stamp.version_id,
            "requirement_phase": frozen_stamp.phase,
            "work_mode": requirements.work_mode,
            "granularity": requirements.granularity,
            "active_requirement_sheet_after": None,
            "active_board_task_sheet_after": None,
            "requirement_cleared": True,
            "source_grounding": requirements.source_grounding.model_dump(mode="json"),
            "legacy_evidence_fallback": source_context.used_legacy_bundle,
            **evidence_metadata(evidence_bundle),
        },
    )
    if evidence_bundle is not None:
        source_evidence_store.consume_bundle(owner_user_id=user_id, bundle_id=evidence_bundle.id)
    commit_id = current_head_commit(lesson).id
    consumed_stamp = recorder.consume(
        commit_id=commit_id,
        change_summary="冻结学习需求已被空白板书生成消费。",
    )
    workspace_state.save_workspace_and_learning_requirement_history_for_user(
        user_id,
        workspace,
        learning_requirement_history_operations=recorder.operations,
    )
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=effective_requirements(lesson),
        active_requirement_sheet=None,
        active_interaction_session=None,
        interaction_decision=None,
        learning_clarification=_consumed_clarification(clarification),
        requirement_run_id=consumed_stamp.run_id,
        requirement_version_id=consumed_stamp.version_id,
        requirement_phase=consumed_stamp.phase,
        board_task_sheet=None,
        active_board_task_sheet=None,
        board_task_questions=[],
        board_decision=outcome.board_decision,
        needs_clarification=False,
        clarification_questions=[],
        focus_candidates=[],
        requirement_cleared=True,
        board_document_operation_status="succeeded",
        board_document_operation_failure_reason=None,
        board_patch_diff=outcome.diff_preview or [],
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _requirement_from_state(history_state: dict[str, Any] | None) -> LearningRequirementSheet | None:
    return _model_from_history_json(history_state, "latest_sheet_json", LearningRequirementSheet)


def _clarification_from_state(history_state: dict[str, Any] | None) -> LearningClarificationStatus | None:
    return _model_from_history_json(history_state, "latest_clarification_json", LearningClarificationStatus)


def _model_from_history_json(
    history_state: dict[str, Any] | None,
    key: str,
    schema: type[LearningRequirementSheet] | type[LearningClarificationStatus],
):
    if not history_state:
        return None
    raw = history_state.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return schema.model_validate_json(raw)
    except Exception:
        return None


def _failure_response(
    *,
    workspace,
    package,
    lesson,
    requirements: LearningRequirementSheet | None,
    clarification: LearningClarificationStatus | None,
    reason: str,
    stamp: RequirementHistoryStamp | None = None,
    board_decision: BoardDecision | None = None,
    candidate_evidence_bundle: EvidenceBundle | None = None,
) -> ChatResponse:
    active_requirements = requirements or lesson.learning_requirements
    return ChatResponse(
        chatbot_message="",
        learning_requirement_sheet=effective_requirements(lesson),
        active_requirement_sheet=active_requirements,
        active_interaction_session=None,
        interaction_decision=None,
        learning_clarification=clarification or _blocked_clarification(reason),
        requirement_run_id=stamp.run_id if stamp else None,
        requirement_version_id=stamp.version_id if stamp else None,
        requirement_phase=stamp.phase if stamp else None,
        board_task_sheet=None,
        active_board_task_sheet=None,
        board_task_questions=[],
        board_decision=board_decision or BoardDecision(action="no_change", reason=reason),
        needs_clarification=False,
        clarification_questions=[],
        focus_candidates=[],
        requirement_cleared=False,
        board_document_operation_status="failed",
        board_document_operation_failure_reason=reason,
        candidate_evidence_bundle=candidate_evidence_bundle,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _blocked_clarification(reason: str) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="board_generation_blocked",
        reason=reason,
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode="unknown",
        granularity="unclear",
    )


def _consumed_clarification(clarification: LearningClarificationStatus) -> LearningClarificationStatus:
    return clarification.model_copy(
        update={
            "ready_for_board": False,
            "can_start": False,
            "next_question": "",
        }
    )
