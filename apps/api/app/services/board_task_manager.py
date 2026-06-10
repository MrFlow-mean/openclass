from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models import (
    BoardFocusRef,
    BoardTaskConfirmationStatus,
    BoardTaskLocationStatus,
    BoardTaskRequirementSheet,
    BoardTaskRequestedAction,
    ChatInteractionMode,
    ConversationTurn,
    InteractionRuleDraft,
    Lesson,
    ResourceLibraryItem,
    SelectionRef,
)
from app.services.board_task_decider import (
    BoardTaskActionDecision,
    decide_board_task_action,
    decide_board_task_requested_action,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.turn_intent import (
    extract_target_hint,
    extract_intent_signals,
    has_structured_location_hint,
    has_explicit_resource_reference,
)


CONFIRM_PATTERN = re.compile(r"^(好|好的|可以|确认|扩写|写吧|加吧|开始|继续|就这样|按这个来|是|要)$")
DECLINE_PATTERN = re.compile(r"^(不用|不要|先不|取消|算了|否|不用写|别写)$")
GENERIC_REFERENCE_PATTERN = re.compile(r"(这里|这个|这段|这一段|上述|上面|下面|前面|后面|该部分|选中)")


@dataclass(frozen=True)
class BoardTaskIntentPatch:
    requested_action: BoardTaskRequestedAction | None = None
    target_hint: str = ""
    question_or_topic: str = ""
    interaction_rule_draft: InteractionRuleDraft | None = None
    confirmation_status: BoardTaskConfirmationStatus | None = None
    source: Literal["ai_sheet", "fallback", "selection", "existing"] = "fallback"
    target_location: BoardFocusRef | None = None
    location_status: BoardTaskLocationStatus | None = None
    clarification_question: str = ""


def update_board_task_from_chat(
    *,
    lesson: Lesson,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    user_message: str,
    selection: SelectionRef | None,
    selection_excerpt: str | None,
    existing: BoardTaskRequirementSheet | None = None,
) -> BoardTaskRequirementSheet:
    # 已有板书时先维护四字段任务单，而不是让 Chatbot 或 BoardEditor 直接执行。
    ai_sheet = openai_course_ai.generate_board_task_requirement_sheet(
        lesson_title=lesson.title,
        existing_task=existing.model_dump(mode="json") if existing else None,
        board_summary=_compact_text(lesson.board_document.content_text or lesson.board_document.title, limit=1800),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=user_message,
        selection_excerpt=selection_excerpt,
    )
    patch = _intent_patch_from_ai_sheet(ai_sheet) if ai_sheet is not None else _fallback_intent_patch(
        user_message=user_message,
        selection=selection,
        selection_excerpt=selection_excerpt,
        existing=existing,
        interaction_mode="ask",
        document_empty=False,
    )
    return update_board_task_from_intent(
        patch=patch,
        selection=selection,
        selection_excerpt=selection_excerpt,
        existing=existing,
    )


def update_board_task_from_intent(
    *,
    patch: BoardTaskIntentPatch,
    selection: SelectionRef | None = None,
    selection_excerpt: str | None = None,
    existing: BoardTaskRequirementSheet | None = None,
) -> BoardTaskRequirementSheet:
    sheet = (
        BoardTaskRequirementSheet.model_validate(existing.model_dump(mode="json"))
        if existing
        else BoardTaskRequirementSheet()
    )
    replace_existing = patch.source == "ai_sheet"

    if patch.requested_action is not None or replace_existing:
        sheet.requested_action = patch.requested_action
    if patch.target_location is not None or replace_existing:
        sheet.target_location = patch.target_location
    if patch.location_status is not None:
        sheet.location_status = patch.location_status
    if patch.confirmation_status is not None:
        sheet.confirmation_status = patch.confirmation_status
    if patch.clarification_question or replace_existing:
        sheet.clarification_question = patch.clarification_question

    if selection_excerpt:
        sheet.target_hint = _compact_text(selection_excerpt, limit=240)
        sheet.location_status = "selected"
    elif patch.target_hint:
        sheet.target_hint = patch.target_hint if replace_existing else sheet.target_hint or patch.target_hint
    elif replace_existing:
        sheet.target_hint = ""

    if patch.question_or_topic:
        sheet.question_or_topic = (
            patch.question_or_topic if replace_existing else sheet.question_or_topic or patch.question_or_topic
        )
    elif replace_existing:
        sheet.question_or_topic = ""

    if patch.interaction_rule_draft is not None:
        sheet.interaction_rule_draft = (
            patch.interaction_rule_draft
            if replace_existing
            else sheet.interaction_rule_draft or patch.interaction_rule_draft
        )
    elif replace_existing:
        sheet.interaction_rule_draft = None

    return normalize_board_task_sheet(sheet, selection=selection, selection_excerpt=selection_excerpt)


def normalize_board_task_sheet(
    sheet: BoardTaskRequirementSheet,
    *,
    selection: SelectionRef | None = None,
    selection_excerpt: str | None = None,
) -> BoardTaskRequirementSheet:
    # 规范化会补齐选区、目标线索、缺失字段和进度，决定本轮能不能执行。
    normalized = BoardTaskRequirementSheet.model_validate(sheet.model_dump(mode="json"))
    if selection_excerpt:
        normalized.target_hint = normalized.target_hint or _compact_text(selection_excerpt, limit=240)
        normalized.location_status = "selected"
    elif normalized.target_hint and normalized.location_status == "missing":
        normalized.location_status = "missing"
    if selection and selection.excerpt and not normalized.target_hint:
        normalized.target_hint = _compact_text(selection.excerpt, limit=240)
        normalized.location_status = "selected"
    if normalized.target_location and not (
        normalized.target_location.segment_id or normalized.target_location.text_hash
    ):
        normalized.location_status = "missing"

    missing: list[str] = []
    if not _has_target_signal(normalized):
        missing.append("目标位置")
    if normalized.requested_action is None:
        missing.append("动作类型")
    if not normalized.question_or_topic.strip():
        missing.append("问题内容")
    if normalized.requested_action == "chat" and not (
        normalized.interaction_rule_draft and normalized.interaction_rule_draft.should_start
    ):
        missing.append("互动规则")

    normalized.missing_items = missing
    normalized.progress = max(0, min(100, (4 - len(missing)) * 25))
    if normalized.progress == 100:
        normalized.clarification_question = ""
    elif not normalized.clarification_question:
        normalized.clarification_question = _question_for_missing_item(missing[0] if missing else "")
    if normalized.confirmation_status not in {"awaiting", "confirmed", "declined"}:
        normalized.confirmation_status = "none"
    return normalized


def is_write_confirmation(text: str) -> bool:
    return bool(CONFIRM_PATTERN.search(_compact_text(text, limit=80)))


def is_write_decline(text: str) -> bool:
    return bool(DECLINE_PATTERN.search(_compact_text(text, limit=80)))


def make_write_task_from_topic(topic: str) -> BoardTaskRequirementSheet:
    # 找不到可讲/可编辑内容时，转成“是否先扩写板书”的待确认写入任务。
    return normalize_board_task_sheet(
        BoardTaskRequirementSheet(
            target_hint=_compact_text(topic, limit=240),
            location_status="content_absent",
            requested_action="write",
            question_or_topic=_compact_text(topic, limit=240),
            confirmation_status="awaiting",
        )
    )


def _intent_patch_from_ai_sheet(sheet: BoardTaskRequirementSheet) -> BoardTaskIntentPatch:
    return BoardTaskIntentPatch(
        requested_action=sheet.requested_action,
        target_hint=sheet.target_hint,
        question_or_topic=sheet.question_or_topic,
        interaction_rule_draft=sheet.interaction_rule_draft,
        confirmation_status=sheet.confirmation_status,
        source="ai_sheet",
        target_location=sheet.target_location,
        location_status=sheet.location_status,
        clarification_question=sheet.clarification_question,
    )


def _fallback_intent_patch(
    *,
    user_message: str,
    selection: SelectionRef | None,
    selection_excerpt: str | None,
    existing: BoardTaskRequirementSheet | None,
    interaction_mode: ChatInteractionMode,
    document_empty: bool,
) -> BoardTaskIntentPatch:
    # AI 没有返回任务单时，只把本轮意图整理成 patch；合并和缺项计算交给 update/normalize。
    message = _compact_text(user_message, limit=280)
    signals = extract_intent_signals(message)
    decision = decide_board_task_action(
        message=message,
        signals=signals,
        has_selection=bool(selection_excerpt),
        document_empty=document_empty,
        interaction_mode=interaction_mode,
        board_generation_action=None,
        has_explicit_resource_reference=has_explicit_resource_reference(message),
    )
    action = _infer_action(message, decision=decision)
    target_hint = ""
    if selection_excerpt:
        target_hint = _compact_text(selection_excerpt, limit=240)
    else:
        extracted_hint = _extract_target_hint(message)
        if extracted_hint:
            target_hint = extracted_hint
        elif _has_structured_location_hint(message):
            target_hint = message
    question_or_topic = _extract_topic(message)
    interaction_rule_draft = None
    if action == "chat":
        interaction_rule_draft = InteractionRuleDraft(
            should_start=True,
            rule_text=message,
            interaction_goal=question_or_topic or message,
            target_hint=target_hint,
            expected_user_behavior="用户按自己提出的互动方式回应。",
            assistant_behavior="Chatbot 按用户提出的互动方式推进交流。",
            reference_instruction="只围绕当前板书定位到的内容互动。",
        )
    source: Literal["ai_sheet", "fallback", "selection", "existing"] = "selection" if selection_excerpt else "fallback"
    if existing is not None and not any([action, target_hint, question_or_topic, interaction_rule_draft]):
        source = "existing"
    return BoardTaskIntentPatch(
        requested_action=action,
        target_hint=target_hint,
        question_or_topic=question_or_topic,
        interaction_rule_draft=interaction_rule_draft,
        source=source,
    )


def _fallback_board_task_sheet(
    *,
    user_message: str,
    selection: SelectionRef | None,
    selection_excerpt: str | None,
    existing: BoardTaskRequirementSheet | None,
) -> BoardTaskRequirementSheet:
    # 兼容旧测试/调试入口；实际更新路径已经走 BoardTaskIntentPatch。
    return update_board_task_from_intent(
        patch=_fallback_intent_patch(
            user_message=user_message,
            selection=selection,
            selection_excerpt=selection_excerpt,
            existing=existing,
            interaction_mode="ask",
            document_empty=False,
        ),
        selection=selection,
        selection_excerpt=selection_excerpt,
        existing=existing,
    )


def _has_target_signal(sheet: BoardTaskRequirementSheet) -> bool:
    target_hint = sheet.target_hint.strip()
    has_explicit_target = bool(
        (target_hint and not _is_only_generic_reference(target_hint))
        or sheet.target_location
    )
    if sheet.requested_action == "write":
        if has_explicit_target:
            return True
        if sheet.location_status == "ambiguous":
            return False
        return bool(sheet.question_or_topic.strip())
    return bool(
        has_explicit_target
        or (sheet.question_or_topic.strip() and not _is_only_generic_reference(sheet.question_or_topic))
    )


def _infer_action(text: str, decision: BoardTaskActionDecision | None = None) -> BoardTaskRequestedAction | None:
    # 只把学生话语归类为通用动作：写、改、讲解或互动。
    action_decision = decision or decide_board_task_action(
        message=text,
        signals=extract_intent_signals(text),
        has_selection=False,
        document_empty=False,
        interaction_mode="ask",
        board_generation_action=None,
        has_explicit_resource_reference=has_explicit_resource_reference(text),
    )
    return decide_board_task_requested_action(message=text, decision=action_decision)


def _has_structured_location_hint(text: str) -> bool:
    return has_structured_location_hint(text)


def _extract_target_hint(text: str) -> str:
    return extract_target_hint(text)


def _extract_topic(text: str) -> str:
    compact = _compact_text(text, limit=220)
    compact = re.sub(r"^(请|帮我|你能不能|能不能|可以)?\s*", "", compact)
    compact = re.sub(r"(讲解|讲述|解释|说明|讲一下|解释一下|帮我理解|改写|修改|编辑|新增|追加|扩写|补充|练习|互动)", "", compact)
    compact = compact.strip(" ：:，,。！？!?；;\"'“”‘’")
    if _is_only_generic_reference(compact):
        return ""
    return compact or text.strip()


def _is_only_generic_reference(text: str) -> bool:
    compact = re.sub(r"[\s，,。！？!?；;：:]+", "", text or "")
    return compact in {"这", "这个", "这里", "这段", "这一段", "这部分", "这个内容", "这个地方"}


def _question_for_missing_item(item: str) -> str:
    if item == "目标位置":
        return "你想围绕板书里的哪一段、哪个标题或哪处选区来处理？"
    if item == "动作类型":
        return "你希望我接下来是改板书、写新内容、讲解内容，还是按某种规则和你互动？"
    if item == "问题内容":
        return "你想围绕这个位置问什么问题，或者希望处理哪个主题？"
    if item == "互动规则":
        return "你希望按什么互动方式练习，比如你问我答、我出题你回答，还是角色对话？"
    return "你再补充一个最关键的信息，我就能继续。"


def _resource_summary(resources: list[ResourceLibraryItem]) -> str:
    lines: list[str] = []
    for resource in resources[:6]:
        titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        lines.append(f"{resource.name}: {' / '.join(titles)}" if titles else resource.name)
    return "\n".join(lines) or "暂无已上传资料摘要"


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-8:]
    return "\n".join(f"{turn.role}: {_compact_text(turn.content, limit=500)}" for turn in turns if turn.content.strip())


def _compact_text(value: str | None, *, limit: int = 1200) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."
