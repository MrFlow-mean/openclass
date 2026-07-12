from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.models import (
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services.board_document_sensor import BoardDocumentSensorReading
from app.services.blank_board_requirement_mapping import build_blank_board_requirement_state
from app.services.course_runtime import active_task_requirements
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import (
    BlankBoardRequirementRefinement,
    BlankBoardRequirementRefinementResult,
    InitialLearningWorkModeDecision,
    openai_course_ai,
)


LearningRequirementRefinementRoute = Literal[
    "ordinary_chat",
    "requirement_refining",
    "refinement_failed",
]

LEARNING_REQUIREMENT_REFINEMENT_FAILURE_REASON = "本轮学习需求没有成功更新，请重试刚才的输入。"


@dataclass(frozen=True)
class LearningRequirementRefinementOutcome:
    route: LearningRequirementRefinementRoute
    chatbot_message: str
    active_requirement_sheet: LearningRequirementSheet | None
    learning_clarification: LearningClarificationStatus
    history_stamp: RequirementHistoryStamp
    history_operations: list[dict[str, Any]]
    guidance_metadata: dict[str, Any]
    changed: bool


def refine_blank_board_requirement(
    *,
    owner_user_id: str,
    lesson: Lesson,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    user_message: str,
    history_state: dict[str, Any] | None,
    resource_summary: str = "",
    include_stream_result: bool = True,
    initial_work_mode_decision: InitialLearningWorkModeDecision | None = None,
    source_requested_by_user: bool = False,
    resolved_source_chapter: bool = False,
) -> LearningRequirementRefinementOutcome | None:
    active_requirement = _active_requirement_from_state(lesson, history_state)
    active_clarification = _active_clarification_from_state(history_state)
    base_requirement = active_requirement or build_requirements(lesson.title)
    refinement = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
        resource_summary=resource_summary,
        include_stream_result=include_stream_result,
        initial_work_mode_decision=(
            initial_work_mode_decision.model_dump(mode="json")
            if initial_work_mode_decision is not None
            else None
        ),
    )
    visible_chat_buffer = ""
    visible_chat_was_streamed = False
    structured_parse_failed = False
    failure_kind = "model_call_failed"
    failure_reason = ""
    if isinstance(refinement, BlankBoardRequirementRefinementResult):
        result = refinement.result
        visible_chat_buffer = refinement.visible_chat_buffer.strip()
        visible_chat_was_streamed = refinement.visible_chat_was_streamed
        structured_parse_failed = refinement.structured_parse_failed
        failure_kind = refinement.failure_kind or (
            "structured_parse_failed" if structured_parse_failed else "model_call_failed"
        )
        failure_reason = refinement.failure_reason
    else:
        result = refinement
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    if not isinstance(result, BlankBoardRequirementRefinement):
        return build_failed_blank_board_requirement_outcome(
            owner_user_id=owner_user_id,
            lesson=lesson,
            history_state=history_state,
            initial_work_mode_decision=initial_work_mode_decision,
            visible_chat_buffer=visible_chat_buffer,
            visible_chat_was_streamed=visible_chat_was_streamed,
            structured_parse_failed=structured_parse_failed,
            failure_code=_requirement_failure_code(failure_kind),
            failure_detail=failure_reason,
        )
    if initial_work_mode_decision is not None and initial_work_mode_decision.route == "learning_intake":
        initial_update: dict[str, object] = {
            "route": "requirement_refining",
            "work_mode": initial_work_mode_decision.work_mode,
            "granularity": initial_work_mode_decision.granularity,
            "learning_goal": _first_text(result.learning_goal, initial_work_mode_decision.topic),
        }
        if resolved_source_chapter and initial_work_mode_decision.granularity == "source_chapter":
            initial_update.update(
                {
                    "work_mode": "knowledge_board",
                    "learning_goal": initial_work_mode_decision.topic,
                    "boundary": initial_work_mode_decision.topic,
                    "summary": initial_work_mode_decision.topic,
                    "missing_items": [],
                    "next_question": "",
                    "guidance_strategy": "none",
                    "learning_map_summary": "",
                    "entry_point_options": [],
                    "recommended_entry_point": "",
                    "reason_for_recommendation": "",
                    "checklist": [],
                    "ready_for_board": True,
                }
            )
        result = result.model_copy(update=initial_update)
    if result.route == "ordinary_chat" and _contains_requirement_payload(result):
        result = result.model_copy(update={"route": "requirement_refining"})
    if visible_chat_buffer:
        result = result.model_copy(update={"chatbot_message": visible_chat_buffer})
    if result.route == "ordinary_chat":
        return LearningRequirementRefinementOutcome(
            route="ordinary_chat",
            chatbot_message=_first_text(visible_chat_buffer, result.chatbot_message),
            active_requirement_sheet=active_requirement,
            learning_clarification=active_clarification or _basic_chat_clarification(),
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata=_stream_metadata(
                visible_chat_was_streamed=visible_chat_was_streamed,
                structured_parse_failed=structured_parse_failed,
                requirement_update_skipped=False,
            ),
            changed=False,
        )

    requirement_state = build_blank_board_requirement_state(
        lesson=lesson,
        base_requirement=base_requirement,
        result=result,
        resolved_source_chapter=resolved_source_chapter,
    )
    requirement = requirement_state.requirement
    if source_requested_by_user and not requirement.source_grounding.requested_by_user:
        requirement = requirement.model_copy(
            deep=True,
            update={
                "source_grounding": requirement.source_grounding.model_copy(
                    update={"requested_by_user": True}
                )
            },
        )
    metadata = _build_guidance_metadata(result)
    metadata.update(
        _stream_metadata(
            visible_chat_was_streamed=visible_chat_was_streamed,
            structured_parse_failed=structured_parse_failed,
            requirement_update_skipped=False,
        )
    )
    stamp = recorder.record_update(
        requirements=requirement,
        clarification=requirement_state.clarification,
        change_summary=requirement_state.clarification.summary or "更新空白板书学习需求清单。",
        metadata=metadata,
    )
    return LearningRequirementRefinementOutcome(
        route="requirement_refining",
        chatbot_message=_first_text(visible_chat_buffer, result.chatbot_message, result.next_question, result.summary),
        active_requirement_sheet=requirement,
        learning_clarification=requirement_state.clarification,
        history_stamp=stamp,
        history_operations=list(recorder.operations),
        guidance_metadata=metadata,
        changed=bool(recorder.operations),
    )


def build_failed_blank_board_requirement_outcome(
    *,
    owner_user_id: str,
    lesson: Lesson,
    history_state: dict[str, Any] | None,
    initial_work_mode_decision: InitialLearningWorkModeDecision | None = None,
    visible_chat_buffer: str = "",
    visible_chat_was_streamed: bool = False,
    structured_parse_failed: bool = False,
    failure_code: str = "missing_requirement_refinement_outcome",
    failure_detail: str = "",
) -> LearningRequirementRefinementOutcome:
    active_requirement = _active_requirement_from_state(lesson, history_state)
    active_clarification = _active_clarification_from_state(history_state)
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    history_reason = "学习需求整理失败，保留上一版本等待重试。"
    failure_metadata = {
        **_stream_metadata(
            visible_chat_was_streamed=visible_chat_was_streamed,
            structured_parse_failed=structured_parse_failed,
            requirement_update_skipped=True,
        ),
        "failure_code": failure_code,
        "failure_detail": failure_detail[:800],
        "discarded_unvalidated_pm_draft": bool(visible_chat_buffer),
    }
    stamp = recorder.record_event(
        event_type="refinement_failed",
        change_summary=history_reason,
        metadata=failure_metadata,
    )
    return LearningRequirementRefinementOutcome(
        route="refinement_failed",
        chatbot_message="",
        active_requirement_sheet=active_requirement,
        learning_clarification=_failed_refinement_clarification(
            active_clarification,
            initial_work_mode_decision,
        ),
        history_stamp=stamp,
        history_operations=list(recorder.operations),
        guidance_metadata=failure_metadata,
        changed=bool(recorder.operations),
    )


def _active_requirement_from_state(
    lesson: Lesson,
    history_state: dict[str, Any] | None,
) -> LearningRequirementSheet | None:
    from_history = _model_from_history_json(history_state, "latest_sheet_json", LearningRequirementSheet)
    if from_history is not None:
        return from_history
    if history_state and history_state.get("status") in {"collecting", "ready"}:
        return active_task_requirements(lesson)
    return None


def _active_clarification_from_state(
    history_state: dict[str, Any] | None,
) -> LearningClarificationStatus | None:
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


def _basic_chat_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="basic_chat",
        reason="当前聊天框只执行基础你问我答，不进入文档工作流。",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode="unknown",
        granularity="unclear",
    )


def _failed_refinement_clarification(
    active: LearningClarificationStatus | None,
    initial_decision: InitialLearningWorkModeDecision | None,
) -> LearningClarificationStatus:
    if active is not None:
        return active
    return LearningClarificationStatus(
        progress=0,
        label="collecting",
        reason="本轮需求整理失败，尚未改变学习需求清单。",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode=(initial_decision.work_mode if initial_decision is not None else "unknown"),
        granularity=(initial_decision.granularity if initial_decision is not None else "unclear"),
    )


def _requirement_failure_code(failure_kind: str) -> str:
    return {
        "structured_parse_failed": "structured_requirement_parse_failed",
        "deadline_exceeded": "requirement_refinement_deadline_exceeded",
        "output_budget_exceeded": "requirement_refinement_output_budget_exceeded",
        "provider_unavailable": "requirement_provider_unavailable",
        "model_call_failed": "requirement_model_call_failed",
    }.get(failure_kind, "requirement_model_call_failed")


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _contains_requirement_payload(result: BlankBoardRequirementRefinement) -> bool:
    return bool(
        result.learning_goal.strip()
        or result.work_mode in {"knowledge_board", "narrow_topic", "practice_artifact"}
        or result.granularity in {"single_knowledge_point", "broad_topic", "practice_artifact"}
        or result.key_facts
    )


def _stream_metadata(
    *,
    visible_chat_was_streamed: bool,
    structured_parse_failed: bool,
    requirement_update_skipped: bool,
) -> dict[str, Any]:
    return {
        "visible_chat_source": "streamed_buffer" if visible_chat_was_streamed else "validated_result",
        "visible_chat_was_streamed": visible_chat_was_streamed,
        "structured_parse_failed": structured_parse_failed,
        "requirement_update_skipped": requirement_update_skipped,
    }


def _build_guidance_metadata(result: BlankBoardRequirementRefinement) -> dict[str, Any]:
    return {
        "guidance_strategy": result.guidance_strategy,
        "learning_map_summary": result.learning_map_summary,
        "entry_point_options": [
            option.model_dump(mode="json")
            for option in result.entry_point_options
            if (option.label or "").strip()
        ],
        "recommended_entry_point": result.recommended_entry_point,
        "reason_for_recommendation": result.reason_for_recommendation,
        "learner_profile_inference": result.learner_profile_inference,
    }
