from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import BoardDecision, ChatRequest, Lesson, SectionTeachingProgressView
from app.services.board_teaching import build_board_teaching_guide, teach_first_section, teach_next_section
from app.services.history import commit_operations, current_head_commit


@dataclass(frozen=True)
class BoardTeachingTurnOutcome:
    chatbot_message: str
    board_decision: BoardDecision
    teaching_progress: SectionTeachingProgressView


def should_start_board_teaching(lesson: Lesson, request: ChatRequest) -> bool:
    if request.teaching_action == "restart":
        return True
    if _is_start_from_beginning_request(request.message):
        return True
    if _last_commit_kind(lesson) == "board_document_generation" and _is_affirmative_teaching_reply(request.message):
        return True
    return False


def should_continue_board_teaching(lesson: Lesson, request: ChatRequest) -> bool:
    progress = lesson.board_teaching_progress
    if request.teaching_action == "continue":
        return True
    if _has_explicit_board_mutation_request(request.message):
        return False
    if not progress or not progress.waiting_for_continue:
        return False
    return _is_continue_teaching_request(request.message) or _is_affirmative_teaching_reply(request.message)


def run_board_teaching_turn(
    *,
    lesson: Lesson,
    request: ChatRequest,
    teaching_action: str,
    conversation_summary: str,
) -> BoardTeachingTurnOutcome:
    if lesson.board_teaching_guide is None or not lesson.board_teaching_guide.section_plans:
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
    lesson.learning_requirements = None
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None
    if teaching_action == "continue":
        result = teach_next_section(
            lesson=lesson,
            resource_summary="",
            conversation_summary=conversation_summary,
        )
    else:
        lesson.board_teaching_progress = None
        result = teach_first_section(
            lesson=lesson,
            resource_summary="",
            conversation_summary=conversation_summary,
        )
    board_decision = BoardDecision(
        action="no_change",
        reason="聊天框按照当前板书顺序讲解，不修改右侧文档。",
    )
    commit_operations(
        lesson,
        [],
        label="Board section teaching",
        message="Taught a board document section from the current teaching guide",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_section_teaching",
            "user_message": request.message,
            "assistant_message": result.chatbot_message,
            "assistant_message_source": result.assistant_message_source,
            "document_changed": False,
            "teaching_action": teaching_action,
            "teaching_progress": result.progress_view.model_dump(mode="json"),
            "teaching_progress_after": result.progress_view.model_dump(mode="json"),
            "board_explanation_directive": result.board_explanation_directive,
            "board_teaching_flow": lesson.board_teaching_guide.teaching_flow,
            "active_requirement_sheet_after": None,
            "active_board_task_sheet_after": None,
            "board_task_cleared": True,
            "requirement_cleared": True,
        },
    )
    return BoardTeachingTurnOutcome(
        chatbot_message=result.chatbot_message,
        board_decision=board_decision,
        teaching_progress=result.progress_view,
    )


def _compact_intent_text(value: str) -> str:
    return re.sub(r"[\s，。！？,.!?、；;：:]+", "", value or "").casefold()


def _is_negative_teaching_reply(message: str) -> bool:
    compact = _compact_intent_text(message)
    return bool(re.search(r"(先不|不用|不要|暂停|等一下|等等|取消|先别|不)", compact))


def _is_affirmative_teaching_reply(message: str) -> bool:
    compact = _compact_intent_text(message)
    if not compact or _is_negative_teaching_reply(message):
        return False
    return bool(re.search(r"(好|可以|行|是|对|嗯|开始|来吧|讲吧|下一步|继续)", compact))


def _is_start_from_beginning_request(message: str) -> bool:
    compact = _compact_intent_text(message)
    if _is_negative_teaching_reply(message):
        return False
    return bool(re.search(r"(从头|从开头|从第一节|按顺序|依次|开始(?:为我|给我|帮我)?讲(?:解)?)", compact))


def _is_continue_teaching_request(message: str) -> bool:
    compact = _compact_intent_text(message)
    if _is_negative_teaching_reply(message):
        return False
    return bool(re.search(r"(继续|下一节|下一部分|往下|接着讲|讲下去|下一步)", compact))


def _has_explicit_board_mutation_request(message: str) -> bool:
    """Keep a typed document request out of the conversational teaching flow."""
    compact = _compact_intent_text(message)
    if not compact:
        return False
    action = r"(?:生成|补写|写入|新增|添加|扩展|完善|修改|改写|重写|删除)"
    target = r"(?:板书|文档|讲义|章节|小节|内容)"
    return bool(
        re.search(rf"{action}.{{0,12}}{target}", compact)
        or re.search(rf"{target}.{{0,12}}{action}", compact)
    )


def _last_commit_kind(lesson: Lesson) -> str:
    try:
        metadata = current_head_commit(lesson).metadata
    except Exception:
        return ""
    return str(metadata.get("kind") or "") if isinstance(metadata, dict) else ""
