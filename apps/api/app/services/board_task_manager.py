from __future__ import annotations

import re

from app.models import (
    BoardTaskRequirementSheet,
    BoardTaskRequestedAction,
    ConversationTurn,
    InteractionRuleDraft,
    Lesson,
    ResourceLibraryItem,
    SelectionRef,
)
from app.services.openai_course_ai import openai_course_ai


WRITE_PATTERN = re.compile(r"(写|编写|生成|设计|创建|新增|追加|补充|扩写|添加|加一段|加一节|加入)")
EDIT_PATTERN = re.compile(
    r"(改|修改|改写|重写|编辑|润色|优化|简化|扩展|缩短|改短|调整|精简|压缩|太长|篇幅|"
    r"控制.{0,8}(?:以内|以下)|[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下))"
)
EXPLAIN_PATTERN = re.compile(
    r"(讲解|讲述|解释|说明|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
STRONG_EXPLAIN_PATTERN = re.compile(
    r"(讲解|讲述|解释|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
CHAT_PATTERN = re.compile(r"(练习|互动|你问我答|问答|角色|轮流|按.{0,12}规则|对话|测验|检查我)")
CONFIRM_PATTERN = re.compile(r"^(好|好的|可以|确认|扩写|写吧|加吧|开始|继续|就这样|按这个来|是|要)$")
DECLINE_PATTERN = re.compile(r"^(不用|不要|先不|取消|算了|否|不用写|别写)$")
GENERIC_REFERENCE_PATTERN = re.compile(r"(这里|这个|这段|这一段|上述|上面|下面|前面|后面|该部分|选中)")
ORDINAL_HINT_PATTERN = re.compile(r"(第\s*[0-9０-９一二三四五六七八九十两]+|[0-9０-９一二三四五六七八九十两]+\s*[.．、:：)）])")
TARGET_BEFORE_ACTION_PATTERN = re.compile(
    r"(?:在|把|对|给)?(?P<hint>[^，。！？!?；;\n\r]{1,80}?)"
    r"(?:里|中|下|后面|前面|旁边|部分|这一段|这段)"
    r"[^，。！？!?；;\n\r]{0,24}?"
    r"(?:写|编写|生成|设计|创建|新增|追加|补充|扩写|添加|改|修改|改写|重写|编辑|润色|优化|简化|精简|压缩|缩短|改短|讲解|解释|说明)"
)


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
    base = ai_sheet or _fallback_board_task_sheet(
        user_message=user_message,
        selection=selection,
        selection_excerpt=selection_excerpt,
        existing=existing,
    )
    return normalize_board_task_sheet(base, selection=selection, selection_excerpt=selection_excerpt)


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


def _fallback_board_task_sheet(
    *,
    user_message: str,
    selection: SelectionRef | None,
    selection_excerpt: str | None,
    existing: BoardTaskRequirementSheet | None,
) -> BoardTaskRequirementSheet:
    # AI 没有返回任务单时，用通用动作词兜底；这里不能写学科或教材关键词分支。
    sheet = (
        BoardTaskRequirementSheet.model_validate(existing.model_dump(mode="json"))
        if existing
        else BoardTaskRequirementSheet()
    )
    message = _compact_text(user_message, limit=280)
    action = _infer_action(message) or sheet.requested_action
    sheet.requested_action = action
    if selection_excerpt:
        sheet.target_hint = _compact_text(selection_excerpt, limit=240)
        sheet.location_status = "selected"
    else:
        target_hint = _extract_target_hint(message)
        if target_hint:
            sheet.target_hint = sheet.target_hint or target_hint
        elif _has_structured_location_hint(message):
            sheet.target_hint = sheet.target_hint or message
    if not sheet.question_or_topic:
        sheet.question_or_topic = _extract_topic(message)
    if action == "chat":
        sheet.interaction_rule_draft = sheet.interaction_rule_draft or InteractionRuleDraft(
            should_start=True,
            rule_text=message,
            interaction_goal=sheet.question_or_topic or message,
            target_hint=sheet.target_hint,
            expected_user_behavior="用户按自己提出的互动方式回应。",
            assistant_behavior="Chatbot 按用户提出的互动方式推进交流。",
            reference_instruction="只围绕当前板书定位到的内容互动。",
        )
    return sheet


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


def _infer_action(text: str) -> BoardTaskRequestedAction | None:
    # 只把学生话语归类为通用动作：写、改、讲解或互动。
    has_write = bool(WRITE_PATTERN.search(text))
    if EXPLAIN_PATTERN.search(text) and (not has_write or STRONG_EXPLAIN_PATTERN.search(text)):
        return "explain"
    if EDIT_PATTERN.search(text):
        return "edit"
    if has_write:
        return "write"
    if CHAT_PATTERN.search(text):
        return "chat"
    return None


def _has_structured_location_hint(text: str) -> bool:
    return bool(ORDINAL_HINT_PATTERN.search(text))


def _extract_target_hint(text: str) -> str:
    match = TARGET_BEFORE_ACTION_PATTERN.search(text)
    if not match:
        return ""
    hint = _compact_text(match.group("hint"), limit=160)
    hint = re.sub(r"^(请|帮我|你能不能|能不能|可以|可以为我|为我)\s*", "", hint)
    hint = hint.strip(" ：:，,。！？!?；;\"'“”‘’")
    return "" if _is_only_generic_reference(hint) else hint


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
