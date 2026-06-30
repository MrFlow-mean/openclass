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
    build_guidance_metadata,
)
from app.services.course_runtime import active_task_requirements
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import (
    BlankBoardRequirementRefinement,
    BlankBoardRequirementRefinementResult,
    emit_ai_stream_event,
    openai_course_ai,
)


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
    refinement = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
        include_stream_result=True,
    )
    visible_chat_buffer = ""
    visible_chat_was_streamed = False
    structured_parse_failed = False
    if isinstance(refinement, BlankBoardRequirementRefinementResult):
        result = refinement.result
        visible_chat_buffer = refinement.visible_chat_buffer.strip()
        visible_chat_was_streamed = refinement.visible_chat_was_streamed
        structured_parse_failed = refinement.structured_parse_failed
    else:
        result = refinement
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    if result is None and visible_chat_buffer:
        return LearningRequirementRefinementOutcome(
            route="requirement_refining",
            chatbot_message=visible_chat_buffer,
            active_requirement_sheet=active_requirement,
            learning_clarification=active_clarification or _basic_chat_clarification(),
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata=_stream_metadata(
                visible_chat_was_streamed=visible_chat_was_streamed,
                structured_parse_failed=structured_parse_failed,
                requirement_update_skipped=True,
            ),
            changed=False,
        )
    if not isinstance(result, BlankBoardRequirementRefinement):
        return None
    if visible_chat_buffer:
        result = result.model_copy(update={"chatbot_message": visible_chat_buffer})
    quality_repaired, quality_issues = _assess_guided_reply_quality(
        result=result,
        user_message=user_message,
    )
    if not visible_chat_was_streamed:
        _emit_validated_chatbot_message(result)
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
    )
    metadata = build_guidance_metadata(
        result,
        quality_repaired=quality_repaired,
        quality_issues=quality_issues,
        quality_repair_skipped=bool(quality_issues),
    )
    metadata.update(
        _stream_metadata(
            visible_chat_was_streamed=visible_chat_was_streamed,
            structured_parse_failed=structured_parse_failed,
            requirement_update_skipped=False,
        )
    )
    stamp = recorder.record_update(
        requirements=requirement_state.requirement,
        clarification=requirement_state.clarification,
        change_summary=requirement_state.clarification.summary or "更新空白板书学习需求清单。",
        metadata=metadata,
    )
    return LearningRequirementRefinementOutcome(
        route="requirement_refining",
        chatbot_message=_first_text(visible_chat_buffer, result.chatbot_message, result.next_question, result.summary),
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


def _assess_guided_reply_quality(
    *,
    result: BlankBoardRequirementRefinement,
    user_message: str,
) -> tuple[bool, list[str]]:
    reply_quality = assess_blank_board_requirement_reply(result, user_message=user_message)
    if not reply_quality.needs_repair:
        return False, []
    return False, reply_quality.issues


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


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
