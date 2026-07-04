from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    BoardTaskUpdateStreamPayload,
    Lesson,
    SelectionRef,
)
from app.services.board_document_sensor import BoardDocumentSensorReading
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.openai_course_ai import (
    BoardTaskRequirementRefinement,
    BoardTaskRequirementRefinementResult,
    emit_ai_stream_event,
    openai_course_ai,
)


BoardTaskRefinementRoute = Literal["ordinary_chat", "board_task_refining"]


@dataclass(frozen=True)
class BoardTaskRefinementOutcome:
    route: BoardTaskRefinementRoute
    chatbot_message: str
    active_board_task_sheet: BoardTaskRequirementSheet | None
    history_stamp: BoardTaskHistoryStamp
    history_operations: list[dict[str, Any]]
    guidance_metadata: dict[str, Any]
    board_task_questions: list[str]
    changed: bool


def refine_existing_board_task_requirement(
    *,
    owner_user_id: str,
    lesson: Lesson,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    user_message: str,
    selection: SelectionRef | None,
    history_state: dict[str, Any] | None,
) -> BoardTaskRefinementOutcome | None:
    active_task = _active_board_task_from_state(lesson, history_state)
    refinement = openai_course_ai.generate_board_task_requirement_refinement(
        lesson_title=lesson.title,
        existing_task=active_task.model_dump(mode="json") if active_task else None,
        board_document_state=board_document_state.model_context(),
        board_summary=_board_summary(lesson),
        conversation_summary=conversation_summary,
        user_message=user_message,
        selection_excerpt=selection.excerpt if selection else None,
        include_stream_result=True,
    )
    visible_chat_buffer = ""
    visible_chat_was_streamed = False
    structured_parse_failed = False
    if isinstance(refinement, BoardTaskRequirementRefinementResult):
        result = refinement.result
        visible_chat_buffer = refinement.visible_chat_buffer.strip()
        visible_chat_was_streamed = refinement.visible_chat_was_streamed
        structured_parse_failed = refinement.structured_parse_failed
    else:
        result = refinement

    recorder = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    if result is None and visible_chat_buffer:
        return BoardTaskRefinementOutcome(
            route="board_task_refining",
            chatbot_message=visible_chat_buffer,
            active_board_task_sheet=active_task,
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata=_stream_metadata(
                visible_chat_was_streamed=visible_chat_was_streamed,
                structured_parse_failed=structured_parse_failed,
                board_task_update_skipped=True,
            ),
            board_task_questions=_board_task_questions(active_task),
            changed=False,
        )
    if not isinstance(result, BoardTaskRequirementRefinement):
        return None
    if visible_chat_buffer:
        result = result.model_copy(update={"chatbot_message": visible_chat_buffer})
    if not visible_chat_was_streamed:
        _emit_validated_chatbot_message(result)

    if result.route == "ordinary_chat":
        return BoardTaskRefinementOutcome(
            route="ordinary_chat",
            chatbot_message=_first_text(visible_chat_buffer, result.chatbot_message),
            active_board_task_sheet=active_task,
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata=_stream_metadata(
                visible_chat_was_streamed=visible_chat_was_streamed,
                structured_parse_failed=structured_parse_failed,
                board_task_update_skipped=False,
            ),
            board_task_questions=_board_task_questions(active_task),
            changed=False,
        )

    if result.board_task_sheet is None:
        return None
    sheet = _normalize_board_task_sheet(result.board_task_sheet, selection=selection)
    stamp = recorder.record_update(
        sheet=sheet,
        change_summary=sheet.clarification_question or sheet.question_or_topic or "更新已有板书任务需求清单。",
    )
    questions = _board_task_questions(sheet)
    _emit_board_task_update(sheet=sheet, stamp=stamp, questions=questions)
    metadata = _stream_metadata(
        visible_chat_was_streamed=visible_chat_was_streamed,
        structured_parse_failed=structured_parse_failed,
        board_task_update_skipped=False,
    )
    metadata.update(
        {
            "location_kind": sheet.location_kind,
            "requested_action": sheet.requested_action,
            "missing_items": sheet.missing_items,
            "progress": sheet.progress,
        }
    )
    return BoardTaskRefinementOutcome(
        route="board_task_refining",
        chatbot_message=_first_text(visible_chat_buffer, result.chatbot_message, sheet.clarification_question),
        active_board_task_sheet=sheet,
        history_stamp=stamp,
        history_operations=list(recorder.operations),
        guidance_metadata=metadata,
        board_task_questions=questions,
        changed=bool(recorder.operations),
    )


def _active_board_task_from_state(
    lesson: Lesson,
    history_state: dict[str, Any] | None,
) -> BoardTaskRequirementSheet | None:
    from_history = _model_from_history_json(history_state, "latest_sheet_json")
    if from_history is not None:
        return from_history
    if history_state and history_state.get("status") in {"collecting", "ready", "awaiting_confirmation"}:
        return lesson.board_task_requirements
    return lesson.board_task_requirements


def _model_from_history_json(
    history_state: dict[str, Any] | None,
    key: str,
) -> BoardTaskRequirementSheet | None:
    if not history_state:
        return None
    raw = history_state.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return BoardTaskRequirementSheet.model_validate_json(raw)
    except Exception:
        return None


def _board_summary(lesson: Lesson, *, limit: int = 2200) -> str:
    parts = [
        lesson.board_document.title.strip(),
        _board_outline(lesson),
        (lesson.board_document.content_text or "").strip(),
    ]
    text = "\n\n".join(part for part in parts if part)
    return text[:limit]


def _board_outline(lesson: Lesson, *, limit: int = 1200) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    text = lesson.board_document.content_text or ""
    for match in re.finditer(r"(?m)^\s*(?:#{1,6}\s+)?(?P<title>(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+[、.．])\s+.+?)\s*$", text):
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        lines.append(f"- {title}")
    for match in re.finditer(r"(?m)^\s*(#{1,6})\s+(?P<title>.+?)\s*$", text):
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        lines.append(f"- {title}")
    if not lines:
        return ""
    outline = "板书结构目录：\n" + "\n".join(lines[:24])
    return outline[:limit]


def _normalize_board_task_sheet(
    sheet: BoardTaskRequirementSheet,
    *,
    selection: SelectionRef | None,
) -> BoardTaskRequirementSheet:
    update: dict[str, Any] = {
        "board_workflow": "act_on_existing_board",
        "interaction_rule_draft": None,
        "confirmation_status": sheet.confirmation_status or "none",
    }
    if sheet.requested_action not in {"write", "edit", "explain", "chat", None}:
        update["requested_action"] = None
    if selection and not sheet.target_location:
        update["target_location"] = _focus_from_selection(selection)
    if selection and not sheet.target_hint.strip():
        update["target_hint"] = selection.excerpt.strip()[:240]
    requested_action = update.get("requested_action", sheet.requested_action)
    location_kind = sheet.location_kind
    if selection and selection.location_kind == "target_range":
        location_kind = "target_range"
    if selection and selection.location_kind == "insertion_anchor":
        location_kind = "insertion_anchor"
    if location_kind == "unspecified" and (sheet.target_hint.strip() or selection):
        location_kind = "insertion_anchor" if requested_action == "write" else "target_range"
    update["location_kind"] = location_kind
    target_hint = str(update.get("target_hint", sheet.target_hint) or "").strip()
    target_location = update.get("target_location", sheet.target_location)
    if (target_hint or target_location) and sheet.location_status == "missing":
        update["location_status"] = "selected"

    normalized = sheet.model_copy(update=update)
    if _is_optional_explain_clarification(normalized, normalized.clarification_question):
        normalized = normalized.model_copy(update={"clarification_question": ""})
    missing_items = _missing_items(normalized, declared_missing=sheet.missing_items)
    progress = _progress_from_missing(missing_items)
    return normalized.model_copy(update={"missing_items": missing_items, "progress": progress})


def _focus_from_selection(selection: SelectionRef) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=selection.lesson_id,
        document_id=selection.document_id,
        segment_id=selection.segment_id or selection.block_id,
        heading_path=selection.heading_path,
        excerpt=selection.excerpt,
        before_text=selection.before_text,
        after_text=selection.after_text,
        text_hash=selection.text_hash,
        confidence=0.8,
        reason="用户当前选区提供了位置线索。",
        display_label=_selection_display_label(selection),
    )


def _selection_display_label(selection: SelectionRef) -> str:
    if selection.kind == "board" and selection.location_kind == "target_range":
        return "TargetRange"
    if selection.kind == "board" and selection.location_kind == "insertion_anchor":
        return "InsertionAnchor"
    if selection.kind == "board":
        return "当前选区"
    return "当前上下文"


def _missing_items(
    sheet: BoardTaskRequirementSheet,
    *,
    declared_missing: list[str] | None = None,
) -> list[str]:
    missing: list[str] = []
    if sheet.location_kind == "unspecified" or not (sheet.target_hint.strip() or sheet.target_location):
        missing.append("位置")
    if sheet.requested_action not in {"write", "edit", "explain"}:
        missing.append("动作")
    if not sheet.question_or_topic.strip():
        missing.append("怎么做")
    if sheet.requested_action == "chat":
        missing.append("互动规则执行暂未启用")
    if _has_unresolved_clarification(sheet):
        missing.append("澄清问题")
    for item in declared_missing or []:
        compact = str(item or "").strip()
        if compact and _should_keep_declared_missing(sheet, compact) and compact not in missing:
            missing.append(compact)
    return missing


def _progress_from_missing(missing_items: list[str]) -> int:
    return max(0, min(100, round(((3 - len(missing_items)) / 3) * 100)))


def _has_unresolved_clarification(sheet: BoardTaskRequirementSheet) -> bool:
    question = sheet.clarification_question.strip()
    if question and not _is_optional_explain_clarification(sheet, question):
        return True
    compact = re.sub(r"\s+", "", sheet.question_or_topic)
    if _explain_has_minimum_context(sheet) and _mentions_optional_explain_detail(compact):
        return False
    return bool(re.search(r"(待定|未定|不明确|不清楚|不确定|待确认|需确认|需要澄清|尚未明确)", compact))


def _should_keep_declared_missing(sheet: BoardTaskRequirementSheet, item: str) -> bool:
    if not _explain_has_minimum_context(sheet):
        return True
    if _is_optional_explain_detail(item):
        return False
    return True


def _explain_has_minimum_context(sheet: BoardTaskRequirementSheet) -> bool:
    has_location = sheet.location_kind != "unspecified" and bool(
        sheet.target_hint.strip() or sheet.target_location
    )
    return sheet.requested_action == "explain" and has_location


def _is_optional_explain_clarification(sheet: BoardTaskRequirementSheet, question: str) -> bool:
    if not _explain_has_minimum_context(sheet):
        return False
    return _is_optional_explain_detail(question)


def _is_optional_explain_detail(value: str) -> bool:
    compact = re.sub(r"\s+", "", value or "")
    return _mentions_optional_explain_detail(compact)


def _mentions_optional_explain_detail(compact: str) -> bool:
    if not compact:
        return False
    return bool(
        re.search(
            r"(讲解)?(角度|深度|风格|方式|重点|方面|层次|主题|内容)|怎么讲|怎么做|待定|未指定",
            compact,
        )
    )


def _board_task_questions(sheet: BoardTaskRequirementSheet | None) -> list[str]:
    if sheet is None or sheet.progress >= 100:
        return []
    question = sheet.clarification_question.strip()
    return [question] if question else []


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
    board_task_update_skipped: bool,
) -> dict[str, Any]:
    return {
        "visible_chat_source": "streamed_buffer" if visible_chat_was_streamed else "validated_result",
        "visible_chat_was_streamed": visible_chat_was_streamed,
        "structured_parse_failed": structured_parse_failed,
        "board_task_update_skipped": board_task_update_skipped,
    }


def _emit_validated_chatbot_message(result: BoardTaskRequirementRefinement) -> None:
    message = _first_text(result.chatbot_message, result.board_task_sheet.clarification_question if result.board_task_sheet else "")
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


def _emit_board_task_update(
    *,
    sheet: BoardTaskRequirementSheet,
    stamp: BoardTaskHistoryStamp,
    questions: list[str],
) -> None:
    payload = BoardTaskUpdateStreamPayload(
        board_task_sheet=sheet,
        active_board_task_sheet=sheet,
        board_task_run_id=stamp.run_id,
        board_task_version_id=stamp.version_id,
        board_task_phase=stamp.phase,
        board_task_questions=questions,
    )
    emit_ai_stream_event({"type": "board_task_update", "payload": payload.model_dump(mode="json")})
