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
from app.services.blank_board_requirement_quality import (
    assess_blank_board_requirement_reply,
    allows_core_quality_repair,
    build_guidance_metadata,
    merge_guidance_repair,
)
from app.services.course_runtime import active_task_requirements
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import BlankBoardRequirementRefinement, emit_ai_stream_event, openai_course_ai


LearningRequirementRefinementRoute = Literal["ordinary_chat", "requirement_refining"]


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
) -> LearningRequirementRefinementOutcome | None:
    active_requirement = _active_requirement_from_state(lesson, history_state)
    active_clarification = _active_clarification_from_state(history_state)
    base_requirement = active_requirement or build_requirements(lesson.title)
    result = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
    )
    if not isinstance(result, BlankBoardRequirementRefinement):
        return None
    result, quality_repaired, quality_issues = _repair_guided_reply_if_needed(
        result=result,
        board_document_state=board_document_state,
        conversation_summary=conversation_summary,
        user_message=user_message,
        base_requirement=base_requirement,
        active_clarification=active_clarification,
    )
    _emit_validated_chatbot_message(result)

    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    if result.route == "ordinary_chat":
        return LearningRequirementRefinementOutcome(
            route="ordinary_chat",
            chatbot_message=_first_text(result.chatbot_message),
            active_requirement_sheet=active_requirement,
            learning_clarification=active_clarification or _basic_chat_clarification(),
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata={},
            changed=False,
        )

    requirement_state = build_blank_board_requirement_state(
        lesson=lesson,
        base_requirement=base_requirement,
        result=result,
    )
    metadata = build_guidance_metadata(
        result,
        quality_repaired=quality_repaired,
        quality_issues=quality_issues,
    )
    stamp = recorder.record_update(
        requirements=requirement_state.requirement,
        clarification=requirement_state.clarification,
        change_summary=requirement_state.clarification.summary or "更新空白板书学习需求清单。",
        metadata=metadata,
    )
    return LearningRequirementRefinementOutcome(
        route="requirement_refining",
        chatbot_message=_first_text(result.chatbot_message, result.next_question, result.summary),
        active_requirement_sheet=requirement_state.requirement,
        learning_clarification=requirement_state.clarification,
        history_stamp=stamp,
        history_operations=list(recorder.operations),
        guidance_metadata=metadata,
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


def _repair_guided_reply_if_needed(
    *,
    result: BlankBoardRequirementRefinement,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    user_message: str,
    base_requirement: LearningRequirementSheet,
    active_clarification: LearningClarificationStatus | None,
) -> tuple[BlankBoardRequirementRefinement, bool, list[str]]:
    reply_quality = assess_blank_board_requirement_reply(result, user_message=user_message)
    if not reply_quality.needs_repair:
        return result, False, []
    allow_core_updates = allows_core_quality_repair(reply_quality.issues)
    repaired = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
        quality_repair_context={
            "repair_reason": reply_quality.repair_reason,
            "previous_output": result.model_dump(mode="json"),
            "must_preserve": (
                [
                    "route",
                    "do_not_invent_user_facts",
                ]
                if allow_core_updates
                else [
                    "route",
                    "work_mode",
                    "granularity",
                    "learning_goal",
                    "current_level",
                    "target_scenario",
                    "known_background",
                    "summary",
                    "ready_for_board",
                ]
            ),
            "must_improve": [
                "chatbot_message",
                "guidance_strategy",
                "learning_map_summary",
                "entry_point_options",
                "recommended_entry_point",
                "reason_for_recommendation",
                "learner_profile_inference",
                "next_question",
                "current_level_or_known_background_question",
                "natural_conversation_no_internal_fields",
                "single_main_question",
                "novice_intro_no_external_goal_question",
                "delegated_intro_entry_ready",
                "matched_guidance_method_for_current_user_signal",
                "record_observed_facts_to_requirement_sheet",
            ],
        },
    )
    if not isinstance(repaired, BlankBoardRequirementRefinement):
        return result, False, reply_quality.issues
    return merge_guidance_repair(result, repaired, allow_core_updates=allow_core_updates), True, reply_quality.issues


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _emit_validated_chatbot_message(result: BlankBoardRequirementRefinement) -> None:
    message = _first_text(result.chatbot_message, result.next_question, result.summary)
    if not message:
        return
    emit_ai_stream_event(
        {
            "type": "field_delta",
            "role": "pm",
            "field": "chatbot_message",
            "delta": message,
            "value": message,
        }
    )
