from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any, TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardNeedMapping,
    BoardSectionTeachingPlan,
    BoardTeachingGuide,
    BoardTeachingSelectedItem,
    ChatRequest,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    TeachingGuide,
)
from app.services.course_runtime import effective_requirements
from app.services.lesson_factory import build_requirements, build_teaching_guide, slugify
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import build_document, is_document_empty


class WorkflowState(TypedDict, total=False):
    lesson: Lesson
    course_package: CoursePackage
    request: ChatRequest
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    teaching_guide: TeachingGuide
    teacher_message: str
    teacher_document: BoardDocument
    document_updated: bool
    resource_matches: list[ResourceMatch]


STOP_CHARS = "，。,.；;？！?!\n"
GENERIC_LESSON_TITLES = {"", "新课堂", "空白课堂", "未命名课堂", "Untitled document", "开放课堂"}
GENERATION_KEYWORDS = (
    "生成",
    "开始",
    "讲解",
    "解释",
    "板书",
    "课文",
    "讲义",
    "题目",
    "练习",
    "教案",
)
FORCED_START_KEYWORDS = (
    "直接生成",
    "开始生成",
    "马上生成",
    "立刻生成",
    "现在生成",
    "不用问",
    "别问",
    "你自己决定",
    "先生成",
    "生成板书",
    "讲解一下",
    "讲一下",
    "解释一下",
    "开始吧",
    "就这样",
)


def classify_scope(message: str, lesson: Lesson) -> str:
    _ = message, lesson
    return "in_scope"


def match_resources(*args: Any, **kwargs: Any) -> list[ResourceMatch]:
    _ = args, kwargs
    return []


def _board_snapshot_hash(document: BoardDocument) -> str:
    payload = document.model_dump(mode="json", exclude={"id"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _conversation_payload(request: ChatRequest) -> list[dict[str, str]]:
    turns = [
        {"role": turn.role, "content": turn.content.strip()}
        for turn in request.conversation[-12:]
        if turn.content.strip()
    ]
    message = request.message.strip()
    if message and (not turns or turns[-1]["role"] != "user" or turns[-1]["content"] != message):
        turns.append({"role": "user", "content": message})
    return turns


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _slice_after_keyword(compact_message: str, keyword: str, *, max_len: int = 26) -> str:
    if keyword not in compact_message:
        return ""
    tail = compact_message.split(keyword, 1)[1]
    chars: list[str] = []
    for char in tail:
        if char in STOP_CHARS:
            break
        chars.append(char)
        if len(chars) >= max_len:
            break
    return "".join(chars)


def _clean_topic(value: str) -> str:
    topic = value.strip(" ：:，,。.!！?？;；")
    for marker in ("你能", "能不能", "可不可以", "帮我", "为我", "给我"):
        topic = topic.replace(marker, "")
    topic = re.sub(r"(相关的)?(知识|内容|课程|课文|板书|讲义|题目|练习)$", "", topic)
    topic = re.sub(r"(吗|呢|吧|一下|一点|相关)$", "", topic)
    topic = topic.strip(" 的：:，,。.!！?？;；")
    if len(topic) > 18:
        topic = re.split(r"(?:我要|我想|请|用上|需要|面向|为了|给我)", topic, maxsplit=1)[0].strip()
    return topic[:18]


def _extract_topic(message: str, existing_theme: str) -> tuple[str, list[str]]:
    compact = _compact(message)
    old_topics: list[str] = []
    for old_match in re.finditer(r"(?:不想学|不学|不要学|不是学)([^，。,.；;？！?!]{1,18})", compact):
        old_topic = _clean_topic(old_match.group(1))
        if old_topic:
            old_topics.append(old_topic)
    if old_topics and "想学" in compact:
        return _clean_topic(compact.rsplit("想学", 1)[1]), old_topics

    for keyword in (
        "我想要学习",
        "我想学习",
        "我想要学",
        "我想学",
        "想学习",
        "想学",
        "学习",
        "学一下",
        "讲解一下",
        "讲一下",
        "解释一下",
        "讲讲",
    ):
        topic = _clean_topic(_slice_after_keyword(compact, keyword))
        if topic:
            return topic, old_topics

    for pattern in (
        r"(?:关于|围绕|面向)([^，。,.；;？！?!]{1,24})(?:的|来|做|写|生成)",
        r"(?:老师刚刚给我们讲了|老师刚讲了|刚学了)([^，。,.；;？！?!]{1,24})",
    ):
        match = re.search(pattern, compact)
        if match:
            topic = _clean_topic(match.group(1))
            if topic:
                return topic, old_topics

    return existing_theme.strip(), old_topics


def _extract_level(message: str) -> str:
    parts: list[str] = []
    compact = _compact(message)
    cefr = re.search(r"\b([ABC][12])\b", message, flags=re.IGNORECASE)
    if cefr:
        parts.append(f"CEFR {cefr.group(1).upper()}")
    vocab = re.search(r"(?:词汇量?|词汇)[^\d]{0,4}(\d{3,6})", compact)
    if vocab:
        parts.append(f"词汇量约 {vocab.group(1)}")
    for keyword in ("小学生", "初中生", "高中生", "大学生", "研究生", "零基础", "初学者", "入门", "基础一般", "进阶"):
        if keyword in compact:
            parts.append(keyword)
    return "；".join(dict.fromkeys(parts))


def _extract_background(message: str) -> str:
    compact = _compact(message)
    for pattern in (
        r"(老师刚刚给我们讲了[^，。,.；;？！?!]{1,30})",
        r"(老师刚讲了[^，。,.；;？！?!]{1,30})",
        r"(刚学了[^，。,.；;？！?!]{1,30})",
        r"(已经学过[^，。,.；;？！?!]{1,30})",
        r"(大概知道[^，。,.；;？！?!]{1,30})",
    ):
        match = re.search(pattern, compact)
        if match:
            return match.group(1)
    return ""


def _extract_purpose(message: str) -> str:
    compact = _compact(message)
    for keyword in ("为了", "准备", "要去", "用于", "面向", "应对"):
        value = _slice_after_keyword(compact, keyword, max_len=34)
        if value:
            return f"{keyword}{value}"
    for keyword in ("旅游", "考试", "作业", "面试", "工作", "留学", "课堂", "竞赛", "旅行"):
        if keyword in compact:
            return keyword
    return ""


def _extract_output_preference(message: str) -> str:
    compact = _compact(message)
    outputs: list[str] = []
    if any(keyword in compact for keyword in ("情景对话", "对话课文", "课文")):
        outputs.append("情景对话课文")
    if "板书" in compact:
        outputs.append("板书文档")
    if "讲义" in compact:
        outputs.append("连续讲义")
    if any(keyword in compact for keyword in ("题目", "练习题", "练习")):
        outputs.append("练习题")
    if any(keyword in compact for keyword in ("讲解", "解释", "讲一下")):
        outputs.append("讲解型板书")
    return "；".join(dict.fromkeys(outputs))


def _extract_focus_items(message: str, topic: str) -> list[str]:
    compact = _compact(message)
    items: list[str] = []
    for pattern in (
        r"(?:用上|要用上|包含|包括|重点讲|围绕)([^，。,.；;？！?!]{1,30})",
        r"(?:老师刚刚给我们讲了|老师刚讲了|刚学了)([^，。,.；;？！?!]{1,30})",
    ):
        for match in re.finditer(pattern, compact):
            item = _clean_topic(match.group(1))
            if item and item != topic:
                items.append(item)
    for keyword in ("过去将来时", "平方和开方", "平方", "开方"):
        if keyword in compact and keyword not in items:
            items.append(keyword)
    return list(dict.fromkeys(items))


def _merge_text(existing: str, value: str) -> str:
    if not value:
        return existing
    if not existing:
        return value
    if value in existing:
        return existing
    return f"{existing}；{value}"


def _append_unique(items: list[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _remove_old_topic_needs(requirements: LearningRequirementSheet, old_topics: list[str]) -> None:
    if not old_topics:
        return
    requirements.learning_need_checklist = [
        item
        for item in requirements.learning_need_checklist
        if not any(old_topic and old_topic in item for old_topic in old_topics)
    ]
    requirements.learning_need_catalog = [
        item
        for item in requirements.learning_need_catalog
        if not any(old_topic and old_topic in (item.title + item.content) for old_topic in old_topics)
    ]


def _heuristic_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    try:
        requirements = effective_requirements(lesson)
    except Exception:
        requirements = build_requirements(lesson.title)
    requirements = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    message = request.message.strip()
    topic, old_topics = _extract_topic(message, requirements.theme)
    topic = topic or requirements.theme or lesson.title
    if topic and (topic not in GENERIC_LESSON_TITLES or not requirements.theme):
        requirements.theme = topic
    _remove_old_topic_needs(requirements, old_topics)

    level = _extract_level(message)
    background = _extract_background(message)
    purpose = _extract_purpose(message)
    output = _extract_output_preference(message)
    focus_items = _extract_focus_items(message, requirements.theme)

    requirements.level = _merge_text(requirements.level, level)
    requirements.known_background = _merge_text(requirements.known_background, background)
    requirements.output_preference = _merge_text(requirements.output_preference, output)
    if purpose:
        requirements.success_criteria = _merge_text(requirements.success_criteria, f"面向场景/目的：{purpose}")
    if not requirements.learning_goal and requirements.theme:
        requirements.learning_goal = f"围绕「{requirements.theme}」生成适合当前学习者的讲解与板书。"
    if not requirements.target_depth:
        requirements.target_depth = "先讲清核心概念，再给出例子和检查问题。"
    if not requirements.boundary:
        requirements.boundary = "优先服务当前这堂课；明显偏离主题的新需求先记录为后续扩展。"

    if requirements.theme and requirements.theme not in GENERIC_LESSON_TITLES:
        _append_unique(requirements.learning_need_checklist, f"学习主题：{requirements.theme}")
    if requirements.level:
        _append_unique(requirements.learning_need_checklist, f"学习者水平/背景：{requirements.level}")
    if requirements.known_background:
        _append_unique(requirements.learning_need_checklist, f"已知背景：{requirements.known_background}")
    if purpose:
        _append_unique(requirements.learning_need_checklist, f"学习场景/目的：{purpose}")
    if requirements.output_preference:
        _append_unique(requirements.learning_need_checklist, f"期望产物：{requirements.output_preference}")
    for item in focus_items:
        _append_unique(requirements.learning_need_checklist, f"必须覆盖：{item}")

    user_questions = [
        turn.content.strip()
        for turn in request.conversation[-5:]
        if turn.role == "user" and turn.content.strip()
    ]
    user_questions.append(message)
    requirements.current_questions = list(dict.fromkeys(user_questions))[-6:]
    requirements.learning_need_catalog = []
    return LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))


def _is_forced_start_request(request: ChatRequest) -> bool:
    compact = _compact(request.message)
    return request.board_edit_action == "confirm" or any(keyword in compact for keyword in FORCED_START_KEYWORDS)


def _is_generation_request(request: ChatRequest) -> bool:
    compact = _compact(request.message)
    return any(keyword in compact for keyword in GENERATION_KEYWORDS)


def _missing_items(requirements: LearningRequirementSheet) -> list[str]:
    missing: list[str] = []
    if not requirements.theme or requirements.theme in GENERIC_LESSON_TITLES:
        missing.append("具体学习主题")
    if not requirements.level and not requirements.known_background:
        missing.append("学习者当前水平/已学背景")
    if not requirements.success_criteria:
        missing.append("学习目的或使用场景")
    if not requirements.output_preference:
        missing.append("期望教学文档形态")
    return missing


def _clarification_status(requirements: LearningRequirementSheet, request: ChatRequest) -> LearningClarificationStatus:
    missing = _missing_items(requirements)
    forced_start = _is_forced_start_request(request)
    generation_request = _is_generation_request(request)
    has_theme = "具体学习主题" not in missing
    has_level = "学习者当前水平/已学背景" not in missing
    has_output = "期望教学文档形态" not in missing
    has_goal = "学习目的或使用场景" not in missing

    progress = 0
    progress += 30 if has_theme else 0
    progress += 25 if has_level else 0
    progress += 20 if has_goal else 0
    progress += 15 if has_output else 0
    progress += min(10, len(requirements.learning_need_checklist) * 2)
    progress = min(progress, 100)

    can_start = False
    if has_theme and forced_start:
        can_start = True
        progress = max(progress, 85)
    elif has_theme and generation_request and has_level:
        can_start = True
        progress = max(progress, 80)
    elif has_theme and has_level and has_goal and has_output:
        can_start = True
        progress = max(progress, 90)

    if can_start:
        return LearningClarificationStatus(
            progress=progress,
            label="需求足够充分",
            reason="开始生成板书：用户需求已经足以进入板书生成，缺失项可作为默认假设处理。",
            missing_items=[] if forced_start else missing,
            can_start=True,
            forced_start=forced_start,
        )
    return LearningClarificationStatus(
        progress=progress,
        label="继续澄清",
        reason="学习需求清单还缺少进入板书生成的关键信息。",
        missing_items=missing,
        can_start=False,
        forced_start=False,
    )


def _clarification_question(status: LearningClarificationStatus, requirements: LearningRequirementSheet) -> str:
    if "具体学习主题" in status.missing_items:
        return "你想先学哪一个更具体的主题？"
    if "学习者当前水平/已学背景" in status.missing_items:
        return f"你现在学「{requirements.theme}」大概到什么程度，或者刚学过哪些相关内容？"
    if "学习目的或使用场景" in status.missing_items:
        return "这次学习主要是为了考试/作业、实际应用，还是先把概念理解清楚？"
    if "期望教学文档形态" in status.missing_items:
        return "你希望我生成讲解型板书、对话课文，还是练习题/小测？"
    return "还有没有必须覆盖的例子、题型或应用场景？"


def _fallback_pm_message(status: LearningClarificationStatus, requirements: LearningRequirementSheet) -> str:
    if status.can_start:
        return "需求已经够了，我会开始生成板书，并把当前学习需求清单一起保存下来。"
    return _clarification_question(status, requirements)


def _board_html(requirements: LearningRequirementSheet, request: ChatRequest) -> str:
    theme = html.escape(requirements.theme or "这堂课")
    level = html.escape(requirements.level or requirements.known_background or "暂未说明，按入门友好方式处理")
    goal = html.escape(requirements.learning_goal or f"围绕「{requirements.theme}」建立清晰理解")
    depth = html.escape(requirements.target_depth or "先讲概念，再讲例子，最后做检查")
    output = html.escape(requirements.output_preference or "讲解型板书")
    checklist = requirements.learning_need_checklist or [request.message.strip()]
    checklist_html = "\n".join(f"<li>{html.escape(item)}</li>" for item in checklist if item.strip())
    if not checklist_html:
        checklist_html = "<li>根据当前对话生成基础讲解。</li>"
    return f"""
<h1>{theme}</h1>
<h2>学习需求清单</h2>
<ul>
{checklist_html}
</ul>
<h2>本课目标</h2>
<p>{goal}</p>
<h2>学习者基础</h2>
<p>{level}</p>
<h2>讲解路径</h2>
<ol>
  <li>先用直观语言说明核心概念是什么。</li>
  <li>再说明它为什么有用，以及和学习者已有背景的关系。</li>
  <li>给出一个贴近目标场景的例子。</li>
  <li>最后用一个检查问题确认是否真正理解。</li>
</ol>
<h2>深度与产物</h2>
<p>{depth}</p>
<p>期望产物：{output}</p>
<h2>检查问题</h2>
<p>你能不能用自己的话解释「{theme}」的核心意思，并举出一个使用它的场景？</p>
""".strip()


def _fallback_board_guide(document: BoardDocument, requirements: LearningRequirementSheet) -> BoardTeachingGuide:
    needs = requirements.learning_need_checklist or [requirements.learning_goal or requirements.theme]
    selected_items = [
        BoardTeachingSelectedItem(
            excerpt=document.content_text[:220] or document.title,
            source_heading=document.title,
            reason="作为开场主线，先对齐学习目标和学习者背景。",
            mapped_needs=needs[:3],
            teaching_role="main_idea",
            order_index=0,
        )
    ]
    need_mappings = [
        BoardNeedMapping(
            need=need,
            matched_excerpt=document.title,
            source_heading=document.title,
            rationale="第一版板书根据 PM 需求清单生成，后续可继续细化。",
        )
        for need in needs[:6]
    ]
    section_plans = [
        BoardSectionTeachingPlan(
            order_index=0,
            heading=document.title,
            board_excerpt=document.content_text[:300],
            core_points=needs[:3],
            teaching_steps=["确认目标", "讲清概念", "给出例子", "用问题检查理解"],
            teaching_method="先建立直觉，再进入例子与检查。",
            check_question=f"你能用自己的话说出「{requirements.theme}」的核心意思吗？",
        )
    ]
    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=_board_snapshot_hash(document),
        board_title=document.title,
        selected_items=selected_items,
        need_mappings=need_mappings,
        teaching_flow=["对齐需求", "讲核心概念", "结合场景举例", "检查理解"],
        generation_rationale="根据 PM 阶段沉淀的学习需求清单生成第一版板书。",
        teacher_brief=f"先围绕「{requirements.theme}」讲清核心概念，再结合学习者背景和目标产物推进。",
        lecture_handout=document.content_text,
        section_plans=section_plans,
    )


def _generate_document(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> tuple[BoardDocument, BoardTeachingGuide, str]:
    current_branch = lesson.history_graph.current_branch if lesson.history_graph else "main"
    selection = request.selection.model_dump(mode="json") if request.selection else None
    generated = openai_course_ai.generate_document_edit(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=current_branch,
        request_message=request.message,
        selection=selection,
        interaction_mode=request.interaction_mode,
        scope_action=request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=None,
    )
    if generated:
        document = build_document(
            title=generated.suggested_title or requirements.theme or lesson.title,
            content_html=generated.replacement_html,
            content_text=generated.replacement_text,
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        guide = generated.board_teaching_guide or _fallback_board_guide(document, requirements)
        guide.board_document_id = document.id
        guide.board_snapshot_hash = _board_snapshot_hash(document)
        guide.board_title = document.title
        message = generated.teacher_talk_track.strip() or "已根据学习需求清单生成板书。"
        return document, guide, message

    document = build_document(
        title=requirements.theme or lesson.title,
        content_html=_board_html(requirements, request),
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    guide = openai_course_ai.generate_board_teaching_guide(
        lesson_title=lesson.title,
        request_message=request.message,
        requirements=requirements,
        document=document,
    ) or _fallback_board_guide(document, requirements)
    guide.board_document_id = document.id
    guide.board_snapshot_hash = _board_snapshot_hash(document)
    guide.board_title = document.title
    return document, guide, "已根据学习需求清单生成第一版板书。"


def _maybe_update_blank_lesson_title(lesson: Lesson, requirements: LearningRequirementSheet) -> None:
    if not is_document_empty(lesson.board_document):
        return
    theme = requirements.theme.strip()
    if not theme or theme == lesson.title:
        return
    lesson.title = theme
    lesson.slug = slugify(theme)
    lesson.tags = [theme]


class SimpleCourseWorkflow:
    def invoke(self, state: WorkflowState) -> dict[str, object]:
        lesson = state["lesson"]
        request = state["request"]
        board_empty = is_document_empty(lesson.board_document)
        conversation = _conversation_payload(request)
        requirements = _heuristic_requirements(lesson, request)

        assessment = openai_course_ai.assess_learning_requirements(
            lesson_title=lesson.title,
            lesson_summary=lesson.summary,
            lesson_tags=lesson.tags,
            document_outline=requirements.board_scope,
            board_is_empty=board_empty,
            user_message=request.message,
            selection_excerpt=request.selection.excerpt if request.selection else None,
            conversation=conversation,
        )
        if assessment is not None:
            requirements = assessment.learning_requirement_sheet
        requirements = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
        status = _clarification_status(requirements, request)
        clarification_questions = [] if status.can_start else [_clarification_question(status, requirements)]
        pm_message = (
            assessment.assistant_message.strip()
            if assessment is not None and assessment.assistant_message.strip()
            else _fallback_pm_message(status, requirements)
        )

        teaching_guide = lesson.teaching_guide or build_teaching_guide(
            lesson.id,
            lesson.title,
            lesson.board_document,
            requirements,
        )

        if not status.can_start:
            return {
                "learning_requirement_sheet": requirements,
                "learning_clarification": status,
                "needs_clarification": True,
                "clarification_questions": clarification_questions,
                "board_decision": BoardDecision(action="clarify_request", reason=status.reason),
                "teaching_guide": teaching_guide,
                "teacher_message": pm_message,
                "teacher_document": lesson.board_document,
                "document_updated": False,
                "scope_options": [],
                "resource_matches": [],
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "generated_lesson": None,
                "board_teaching_guide": lesson.board_teaching_guide,
                "board_teaching_progress": None,
                "teaching_progress": None,
            }

        _maybe_update_blank_lesson_title(lesson, requirements)
        document, board_guide, teacher_message = _generate_document(
            lesson=lesson,
            request=request,
            requirements=requirements,
        )
        teaching_guide = openai_course_ai.generate_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            requirements=requirements,
            document=document,
        ) or build_teaching_guide(lesson.id, lesson.title, document, requirements)
        if not teacher_message:
            teacher_message = pm_message or "需求已经够了，我开始生成板书。"

        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="edit_board", reason=status.reason),
            "teaching_guide": teaching_guide,
            "teacher_message": teacher_message,
            "teacher_document": document,
            "document_updated": True,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": board_guide,
            "board_teaching_progress": None,
            "teaching_progress": None,
        }


course_workflow = SimpleCourseWorkflow()
