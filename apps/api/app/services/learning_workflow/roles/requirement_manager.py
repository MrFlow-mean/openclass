from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypedDict

from app.models import ChatRequest, LearningClarificationStatus, LearningRequirementSheet, Lesson
from app.services.lesson_factory import build_requirements


class RequirementStateResult(TypedDict):
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    needs_clarification: bool
    clarification_questions: list[str]
    pm_reason: str


ImportantTermsFn = Callable[[str], list[str]]
SectionFollowupNeedsFn = Callable[[Lesson, ChatRequest, LearningRequirementSheet], list[str]]


def _compact_request_text(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _dedupe(values: list[str], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = re.sub(r"\s+", " ", value or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _extract_focus_terms(message: str) -> list[str]:
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", message or "")
    if quoted:
        return _dedupe(quoted, limit=6)
    return _dedupe(re.findall(r"[A-Za-z][A-Za-z0-9_+\-.]*|[\u4e00-\u9fff]{2,}", message or ""), limit=8)


def _is_board_generation_request(message: str) -> bool:
    compact = _compact_request_text(message)
    generation_verbs = ("生成", "写", "编", "创作", "设计", "做", "输出", "整理成", "给我", "完善")
    artifacts = ("板书", "讲义", "文档", "课程", "课文", "对话", "练习", "例题", "章节", "专题", "页面")
    return any(verb in compact for verb in generation_verbs) and any(artifact in compact for artifact in artifacts)


def _is_forced_start_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(
        signal in compact
        for signal in ("直接开始", "直接开讲", "先讲", "马上开始", "不用问", "从零开始", "你自己看着办", "你来安排", "都可以")
    )


def _is_explanation_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(signal in compact for signal in ("什么是", "解释", "讲解", "不理解", "没懂", "为什么", "怎么理解"))


def _is_vague_pointer_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return compact in {"这里没懂", "这个不懂", "没懂", "讲一下", "解释一下", "继续", "你好", "嗨"}


def _is_low_information_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return len(compact) <= 4 or _is_vague_pointer_request(message)


def _is_teaching_control_text(message: str) -> bool:
    compact = _compact_request_text(message)
    return compact in {"继续", "继续下一节", "下一节", "讲下一节", "继续讲"} or "继续讲下一个" in compact


def _is_topicless_board_generation_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return compact in {
        "开始生成板书",
        "生成板书",
        "开始写板书",
        "写板书",
        "开始生成讲义",
        "生成讲义",
        "写讲义",
        "整理成板书",
        "整理成讲义",
        "生成文档",
        "写文档",
    }


def _is_topicless_control_request(message: str) -> bool:
    return _is_teaching_control_text(message) or _is_topicless_board_generation_request(message)


def _clean_topic_hint(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    cleaned = re.sub(r"^(什么是|什么事|何为|一下|一下子|关于)", "", cleaned).strip()
    cleaned = re.split(r"[，。！？?!.；;：:\n]", cleaned)[0].strip()
    cleaned = re.sub(r"(直接开始|直接开讲|先讲|从零开始)$", "", cleaned).strip()
    cleaned = re.sub(r"(的内容|这部分内容|相关内容)$", "", cleaned).strip()
    cleaned = cleaned.strip("“”\"' ")
    if not cleaned or cleaned in {"我", "内容", "这个", "这里", "一下"}:
        return None
    return cleaned[:80]


def _clean_generation_topic_hint(value: str) -> str:
    cleaned = re.split(r"覆盖|包括|生成后|并带|重点|要求|，|。|！|？|\n", value or "")[0]
    cleaned = re.sub(r"^(一份|一版|一篇|一个|系统的|完整的)", "", cleaned).strip()
    for _ in range(5):
        cleaned = re.sub(
            r"(系统的|完整的|Word\s*式|word\s*式|板书|讲义|文档|课程|课文|专题|页面)$",
            "",
            cleaned,
        ).strip()
    cleaned = cleaned.strip("“”\"' ")
    return "" if cleaned in {"系统的", "完整的", "系统", "完整"} else cleaned


def _extract_generation_topic_hint(text: str) -> str | None:
    patterns = (
        r"(?:生成|写|编|设计|输出|整理成|给我)(?:一份|一版|一篇|一个|系统的|完整的)?(.+?)(?:讲义|板书|文档|课程|课文|专题|页面)",
        r"(?:请|帮我|为我)?(?:生成|写|编|设计)(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            continue
        topic = _clean_topic_hint(_clean_generation_topic_hint(match.group(1)))
        if topic:
            return topic
    return None


def _extract_level_hint(text: str) -> str | None:
    patterns = (
        r"(零基础|初学者|从零开始)",
        r"([A-C][12])",
        r"(小学(?:生)?|初中(?:生)?|高中生|高中|高一|高二|高三|大一|大二|大三|大四|本科(?:一|二|三|四)?年级|本科生|研究生|研一|研二|研三|博士)",
        r"我是([^，。！？\n]{1,16}(?:学生|学习者|从业者|老师|工程师|设计师|医生|律师|产品经理|研究者))",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_topic_hint(text: str) -> str | None:
    generated = _extract_generation_topic_hint(text)
    if generated:
        return generated
    if _is_topicless_control_request(text):
        return None
    patterns = (
        r"(?:我想学|我要学|想学习|学习|教我|我要了解)(.+)",
        r"(?:讲解|讲一下|解释一下|为我讲|帮我讲)(.+)",
        r"什么是(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            continue
        topic = _clean_topic_hint(match.group(1))
        if topic:
            return topic
    if _is_board_generation_request(text):
        return None
    if _extract_level_hint(text):
        return None
    terms = _extract_focus_terms(text)
    return terms[0] if len(terms) == 1 and not _is_low_information_request(text) else None


def _dialogue_user_text(request: ChatRequest) -> str:
    parts = [turn.content for turn in request.conversation if turn.role == "user"]
    parts.append(request.message)
    return "\n".join(part for part in parts if part)


def _conversation_topic_hint(request: ChatRequest) -> str | None:
    for turn in reversed(request.conversation):
        if turn.role != "user":
            continue
        topic = _extract_topic_hint(turn.content)
        if topic:
            return topic
    return None


def _extract_goal_or_scenario_hint(text: str) -> str | None:
    goals: list[str] = []
    patterns = (
        r"(?:准备|备考)([^，。！？\n]{2,40})",
        r"(?:为了|准备|用于|用来|目标是)([^，。！？\n]{2,40})",
        r"(?:想|希望|要)(?:会|能|把|理解|知道|掌握|理清|学会)([^，。！？\n]{2,50})",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            goals.append(match.group(1).strip())
    goal_match = re.search(r"([^，。！？\n]{0,24}(?:考试|项目|论文|工作|展示|面试|旅游|研究|作业|实验|复盘|汇报|阅读))", text or "")
    if goal_match:
        goals.insert(0, goal_match.group(1).strip())
    goals = [goal for goal in _dedupe(goals, limit=3) if goal]
    return "；".join(goals) if goals else None


def _learning_need_checklist(
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    *,
    important_terms: ImportantTermsFn,
) -> list[str]:
    base = list(requirements.learning_need_checklist or [])
    topic = _extract_topic_hint(request.message)
    if topic:
        base.append(f"围绕“{topic}”建立清晰主线")
    for term in important_terms(request.message)[:6]:
        base.append(f"解释“{term}”的作用、关系和使用边界")
    return _dedupe(base, limit=12)


def _learning_clarification_status(
    *,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> LearningClarificationStatus:
    message = request.message or ""
    forced = _is_forced_start_request(message)
    topic = _extract_topic_hint(message)
    if topic is None and (_is_topicless_control_request(message) or not _is_low_information_request(message)):
        topic = requirements.theme
    dialogue = _dialogue_user_text(request)
    level = _extract_level_hint(message) or _extract_level_hint(dialogue)
    goal = _extract_goal_or_scenario_hint(message) or _extract_goal_or_scenario_hint(dialogue)
    missing: list[str] = []
    if not topic and _is_low_information_request(message):
        missing.append("想学的主题")
    if topic and not level and not forced and not _is_topicless_control_request(message):
        missing.append("当前水平或背景")
    if topic and not goal and not forced and not level and not _is_topicless_control_request(message):
        missing.append("学习目的或应用场景")

    if _is_teaching_control_text(message):
        progress = 100 if topic else 45
    elif _is_topicless_board_generation_request(message):
        progress = 95 if topic else 45
    elif forced:
        progress = 55 if topic else 45
    elif not topic and _is_low_information_request(message):
        progress = 0
    elif topic and level and goal:
        progress = 100
    elif level and goal:
        progress = 95
    elif topic and (level or goal):
        progress = 95 if level else 80
    elif topic:
        progress = 35
    else:
        progress = 55
    return LearningClarificationStatus(
        progress=progress,
        label="已明确" if progress >= 80 else "需要少量澄清" if progress >= 35 else "需要确认入口",
        reason="根据对话日志中是否包含主题、背景和目标进行领域无关判断。",
        missing_items=missing,
        can_start=bool(topic) or forced,
        forced_start=forced,
    )


def _status_with_resource_context_default(
    status: LearningClarificationStatus,
    *,
    resource_count: int,
) -> LearningClarificationStatus:
    if resource_count <= 0 or status.progress > 0:
        return status
    return status.model_copy(update={"progress": 35, "can_start": True, "missing_items": []})


def _should_ask_brief_clarification(
    *,
    request: ChatRequest,
    status: LearningClarificationStatus,
) -> bool:
    if status.forced_start:
        return False
    if _is_explanation_request(request.message) and not _is_low_information_request(request.message):
        return False
    if status.progress < 20:
        return True
    if request.conversation and status.progress < 60 and not _is_board_generation_request(request.message):
        return True
    return False


def _draft_requirements(
    *,
    lesson: Lesson,
    request: ChatRequest,
    important_terms: ImportantTermsFn,
    section_followup_need_items: SectionFollowupNeedsFn,
) -> LearningRequirementSheet:
    explicit_topic = _extract_topic_hint(request.message)
    conversation_topic = _conversation_topic_hint(request)
    existing = lesson.learning_requirements
    topic = explicit_topic or conversation_topic or (existing.theme if existing else None) or lesson.title
    if existing is not None and existing.theme == topic:
        requirements = LearningRequirementSheet.model_validate(existing.model_dump(mode="json"))
    else:
        requirements = build_requirements(topic)
    dialogue = _dialogue_user_text(request)
    level = _extract_level_hint(request.message) or _extract_level_hint(dialogue)
    goal = _extract_goal_or_scenario_hint(request.message) or _extract_goal_or_scenario_hint(dialogue)
    if level:
        requirements.level = level
        requirements.known_background = f"用户自述背景：{level}"
    if goal:
        requirements.success_criteria = f"围绕用户目标完成可验证学习：{goal}"
    requirements.learning_need_checklist = _learning_need_checklist(
        request,
        requirements,
        important_terms=important_terms,
    )
    requirements.learning_need_checklist = _dedupe(
        [
            *requirements.learning_need_checklist,
            *section_followup_need_items(lesson, request, requirements),
        ],
        limit=16,
    )
    return requirements


def draft_requirement_state(
    *,
    lesson: Lesson,
    request: ChatRequest,
    resource_context_active: bool,
    resource_count: int,
    important_terms: ImportantTermsFn,
    section_followup_need_items: SectionFollowupNeedsFn,
) -> RequirementStateResult:
    requirements = _draft_requirements(
        lesson=lesson,
        request=request,
        important_terms=important_terms,
        section_followup_need_items=section_followup_need_items,
    )
    status = _learning_clarification_status(request=request, requirements=requirements)
    if resource_context_active:
        status = _status_with_resource_context_default(status, resource_count=resource_count)
    needs_clarification = _should_ask_brief_clarification(request=request, status=status)
    return {
        "learning_requirement_sheet": requirements,
        "learning_clarification": status,
        "needs_clarification": needs_clarification,
        "clarification_questions": [],
        "pm_reason": status.reason,
    }
