from __future__ import annotations

import hashlib
import html
import re
from typing import TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardEditPrompt,
    BoardNeedMapping,
    BoardSectionTeachingPlan,
    BoardTeachingGuide,
    BoardTeachingProgress,
    BoardTeachingSelectedItem,
    ChatRequest,
    CoursePackage,
    LibraryChapter,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ScopeOption,
    SectionTeachingProgressView,
    TeachingGuide,
)
from app.services.course_runtime import (
    effective_requirements,
    normalize_requirements,
)
from app.services.lesson_factory import build_requirements, build_teaching_guide, create_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import extract_reference_context
from app.services.rich_document import (
    append_html_section,
    build_document,
    html_to_text,
    is_document_empty,
    replace_selection_in_document,
)

HIGH_OVERLAP_THRESHOLD = 0.72
TERM_EQUIVALENT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("virtual memory", "虚拟内存"),
    ("address translation", "地址转换"),
    ("page table", "page tables", "页表"),
    ("page fault", "page faults", "缺页", "缺页异常"),
    ("tlb", "快表"),
    ("cache", "caches", "缓存", "高速缓存"),
    ("process", "processes", "进程"),
    ("linking", "链接"),
    ("exceptional control flow", "异常控制流"),
    ("network programming", "网络编程"),
    ("concurrent programming", "并发编程"),
    ("machine-level representation", "机器级表示", "机器级程序表示"),
    ("information storage", "信息存储"),
    ("integer representations", "整数表示"),
    ("integer arithmetic", "整数运算"),
    ("floating point", "浮点数", "浮点"),
    ("the memory hierarchy", "storage devices form a hierarchy", "存储层次结构", "存储器层次结构"),
)
TEACHER_PARAGRAPH_MARKERS: tuple[str, ...] = (
    "核心要点",
    "为什么重要",
    "先抓一件事",
    "先抓主线",
    "主线",
    "定义",
    "直觉",
    "例子",
    "类比",
    "应用",
    "练习题",
    "练习解析",
    "检查问题",
    "关键概念",
    "小结",
)


class WorkflowState(TypedDict, total=False):
    lesson: Lesson
    course_package: CoursePackage
    request: ChatRequest
    learning_requirement_sheet: LearningRequirementSheet
    needs_clarification: bool
    learning_clarification: LearningClarificationStatus
    clarification_questions: list[str]
    pm_reason: str
    board_decision: BoardDecision
    teaching_guide: TeachingGuide
    teacher_message: str
    teacher_document: BoardDocument
    document_updated: bool
    scope_options: list[ScopeOption]
    resource_matches: list[ResourceMatch]
    reference_prompt: ResourceReferencePrompt | None
    selected_reference: ResourceReferenceContext | None
    generated_lesson: Lesson | None
    teacher_talk_track: str | None
    board_teaching_guide: BoardTeachingGuide | None
    board_edit_prompt: BoardEditPrompt | None
    board_teaching_progress: BoardTeachingProgress | None
    teaching_progress: SectionTeachingProgressView | None
    teaching_start_section_index: int


def _lesson_corpus(lesson: Lesson) -> str:
    return " ".join([lesson.title, lesson.summary, *(lesson.tags or []), lesson.board_document.content_text]).lower()


def _extract_focus_terms(message: str) -> list[str]:
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", message)
    if quoted:
        return quoted[:4]
    candidates = re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", message)
    return candidates[:6]


def _query_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for chunk in re.split(r"[\s，。！？?!.、/（）()：:；;,\n]+", text):
        cleaned = chunk.strip().lower()
        if len(cleaned) >= 2:
            phrases.append(cleaned)
    topic_hint = _extract_topic_hint(text)
    if topic_hint:
        phrases.append(topic_hint.strip().lower())
    for term in _extract_focus_terms(text):
        cleaned = term.strip().lower()
        if len(cleaned) >= 2:
            phrases.append(cleaned)
    phrases.extend(_expanded_match_terms(text, *phrases))

    unique: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        if phrase in seen:
            continue
        seen.add(phrase)
        unique.append(phrase)
    return unique[:12]


def _expanded_match_terms(*texts: str) -> list[str]:
    corpus = " ".join(texts).lower()
    expanded: list[str] = []
    for group in TERM_EQUIVALENT_GROUPS:
        if any(term in corpus for term in group):
            expanded.extend(group)

    unique: list[str] = []
    seen: set[str] = set()
    for term in expanded:
        cleaned = term.strip().lower()
        if len(cleaned) < 2 or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def classify_scope(message: str, lesson: Lesson) -> str:
    if any(keyword in message for keyword in ["习题", "练习", "例题", "更易懂", "简单讲", "总结", "整理", "改写", "润色"]):
        return "in_scope"
    if _is_in_place_expansion_request(message):
        return "in_scope"
    if any(keyword in message for keyword in ["新增章节", "补充一节", "单独一节", "新开一节"]):
        return "scope_escalation"
    if "什么是" in message or "what is" in message.lower():
        lesson_text = _lesson_corpus(lesson)
        terms = _extract_focus_terms(message)
        unknown = [term for term in terms if term.lower() not in lesson_text]
        if unknown:
            return "scope_escalation"
    return "in_scope"


def _is_board_generation_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    generation_verbs = ["生成", "写", "编", "创作", "设计", "做", "输出", "整理成", "给我", "来一", "完善"]
    artifacts = ["板书", "课文", "对话", "情景对话", "讲义", "练习", "例题", "章节", "课程", "一篇", "一段", "文档"]
    if any(verb in compact for verb in generation_verbs) and any(artifact in compact for artifact in artifacts):
        return True
    return bool(re.search(r"(生成|写|编|做|给我|来|完善)(一篇|一段|一份)?.*(课文|对话|板书|讲义|练习|例题|文档)", compact))


def _compact_request_text(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _has_explicit_append_intent(message: str) -> bool:
    compact = _compact_request_text(message)
    section_targets = ("页面", "一页", "几页", "多页", "章节", "新章节", "一节", "几节", "整章")
    content_targets = (*section_targets, "内容")
    forward_signals = ("继续写", "续写", "接着写", "再写", "往后写", "继续生成")
    create_signals = ("新增", "追加", "新生成", "再生成")

    if any(signal in compact for signal in forward_signals) and any(target in compact for target in content_targets):
        return True
    if any(signal in compact for signal in create_signals) and any(target in compact for target in content_targets):
        return True
    if "补充" in compact and any(target in compact for target in section_targets):
        return True
    if any(signal in compact for signal in ("加上", "加几", "加一", "加个", "添加")) and any(
        target in compact for target in section_targets
    ):
        return True
    tail_markers = ("在后面补", "在末尾补", "追加到末尾", "接在后面", "放到最后", "另起一节", "另起一章")
    return any(marker in compact for marker in tail_markers)


def _is_append_document_request(message: str) -> bool:
    if _is_full_rewrite_request(message):
        return False
    return _has_explicit_append_intent(message)


def _is_in_place_expansion_request(message: str) -> bool:
    if _is_full_rewrite_request(message) or _has_explicit_append_intent(message):
        return False
    compact = _compact_request_text(message)
    expansion_signals = (
        "扩展",
        "扩写",
        "展开",
        "细化",
        "丰富",
        "补全",
        "完善",
        "补充",
        "讲透",
        "更详细",
        "更细致",
        "细致讲解",
        "详细讲解",
        "详细解析",
        "全面",
    )
    current_targets = (
        "板书",
        "版书",
        "讲义",
        "文档",
        "内容",
        "当前",
        "原有",
        "已有",
        "这一节",
        "这节",
        "这一章",
        "这章",
        "小节",
        "段落",
        "例子",
        "案例",
        "知识点",
    )
    return any(signal in compact for signal in expansion_signals) and any(target in compact for target in current_targets)


def _is_forced_start_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    forced_patterns = [
        "直接开始",
        "直接开讲",
        "开讲",
        "直接讲",
        "开始讲解",
        "开始讲课",
        "开始讲",
        "开始教学",
        "马上开始",
        "马上讲",
        "马上讲解",
        "现在开始",
        "先开始",
        "直接教",
        "先教",
        "不用问",
        "不要问",
        "别问",
        "不用再问",
        "不要再问",
        "别再问",
        "就按当前",
        "按目前",
    ]
    return any(pattern in compact for pattern in forced_patterns)


def _is_full_rewrite_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    return any(keyword in compact for keyword in ["重写整篇", "重写全文", "重写整份", "整篇改写", "整体改写", "整体重写"])


def _is_explanation_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    if _is_board_generation_request(message):
        return False
    if _is_vague_pointer_request(message):
        return False
    explanation_keywords = [
        "解释",
        "讲解",
        "讲一下",
        "讲讲",
        "开讲",
        "直接讲",
        "是什么",
        "什么事",
        "什么是",
        "啥是",
        "怎么理解",
        "为什么",
        "什么意思",
        "不理解",
        "没理解",
        "不懂",
        "没懂",
        "看不懂",
        "不明白",
        "看不明白",
        "用自己的话",
        "通俗",
        "别照着念",
        "换个说法讲",
        "带我理解",
    ]
    return any(keyword in compact for keyword in explanation_keywords)


def _is_selection_enhancement_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    if _is_full_rewrite_request(message):
        return False
    rewrite_keywords = ["替换", "改成", "改为", "换成", "精简", "压缩", "缩短", "删掉", "删除"]
    if any(keyword in compact for keyword in rewrite_keywords):
        return False
    enhancement_keywords = [
        "完善",
        "补充",
        "续写",
        "扩写",
        "展开",
        "细化",
        "丰富",
        "补全",
        "讲透",
        "详细解析",
        "详细讲解",
        "更详细",
        "更全面",
        "更加全面",
        "完善全面",
    ]
    return any(keyword in compact for keyword in enhancement_keywords)


def _is_vague_pointer_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    if compact in {"这里没懂", "这没懂", "这个没懂", "这块没懂", "没懂", "不懂", "看不懂", "不会"}:
        return True
    if len(compact) <= 8 and any(pointer in compact for pointer in ["这里", "这个", "这块", "那句", "它"]) and any(
        keyword in compact for keyword in ["没懂", "不懂", "不会", "什么意思"]
    ):
        return True
    return False


def _is_low_information_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message).lower()
    return compact in {
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "hello",
        "hi",
        "在吗",
        "我想学",
        "想学",
        "教我",
        "讲讲",
        "讲一下",
        "开始",
        "开始吧",
    }


def _has_teachable_subject_signal(text: str, lesson: Lesson, request: ChatRequest) -> bool:
    if request.selection is not None:
        return True
    if lesson.title and lesson.title in text and not _is_vague_pointer_request(text):
        return True
    if _is_low_information_request(text) or _is_vague_pointer_request(text):
        return False
    generic_terms = {
        "你好",
        "您好",
        "我想学",
        "想学",
        "教我",
        "讲讲",
        "讲一下",
        "开始",
        "开始吧",
    }
    terms = _extract_focus_terms(text)
    return any(re.sub(r"\s+", "", term).lower() not in generic_terms for term in terms)


def _clean_topic_hint(value: str) -> str | None:
    cleaned = " ".join(value.split()).strip(" ：:，,。！？!?；;")
    cleaned = re.sub(r"^(?:什么是|什么事|啥是|何为)\s*", "", cleaned).strip(" ：:，,。！？!?；;")
    cleaned = re.sub(r"(?:是什么|的内容|这件事|这个概念)$", "", cleaned).strip(" ：:，,。！？!?；;")
    cleaned = re.sub(r"(?:直接开讲|直接讲|开讲|开始教学|先开始)$", "", cleaned).strip(" ：:，,。！？!?；;")
    if len(cleaned) < 2 or _is_low_information_request(cleaned) or _is_vague_pointer_request(cleaned):
        return None
    return cleaned[:40]


def _extract_topic_hint(text: str) -> str | None:
    patterns = [
        r"(?:我要学|我要学习|我想要学|我想要学习|我想学|想要学|想要学习|想学|教我|学习|学一下|为我讲解|给我讲解|为我讲|给我讲|讲解|请讲|请解释|解释一下)\s*([^，。！？!?；;\n]{2,48})",
        r"什么是\s*([^，。！？!?；;\n]{2,48})",
        r"什么事\s*([^，。！？!?；;\n]{2,48})",
        r"啥是\s*([^，。！？!?；;\n]{2,48})",
        r"([^，。！？!?；;\n]{2,48})是什么",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        topic = _clean_topic_hint(match.group(1))
        if topic:
            return topic
    return None


def _clean_generation_topic_hint(value: str) -> str:
    cleaned = " ".join(value.split()).strip(" ：:，,。！？!?；;")
    cleaned = re.sub(
        r"^(?:一份|一版|一篇|一个|系统的|完整的|高质量的|教材式|教科书式|word\s*式|Word\s*式|专题|关于)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"(?:的一篇|的一份|的一版)$", "", cleaned).strip(" ：:，,。")
    cleaned = re.sub(r"(?:情景对话课文|对话课文|课文|情景对话|板书讲义|专题讲义|讲义|板书|课程|教案)$", "", cleaned).strip(" ：:，,。")
    return cleaned[:96]


def _extract_generation_topic_hint(text: str) -> str | None:
    topic_patterns = [
        r"主题[是为：:]\s*([^，。！？!?；;\n]{2,80})",
        r"(?:生成|整理|制作|创建|写出|写一份|写一版|写一篇|给我一份|给我一篇|给我生成|为我生成|请生成).*?(?:一份|一版|一篇|一个)?\s*(?:系统的|完整的|高质量的|教材式|教科书式|word\s*式|Word\s*式)?\s*([^，。！？!?；;\n]{2,80}?)(?:情景对话课文|对话课文|课文|情景对话|板书讲义|专题讲义|讲义|板书|课程|教案)",
    ]
    topic = None
    for pattern in topic_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            topic = _clean_generation_topic_hint(match.group(1))
            if topic:
                break
    if not topic:
        topic = _extract_topic_hint(text)
    if not topic:
        return None

    coverage_match = re.search(
        r"(?:覆盖|包含|包括|按小节讲|按小节讲[：:])\s*([^。！？!?；;\n]{2,140})",
        text,
    )
    if coverage_match:
        coverage = " ".join(coverage_match.group(1).split()).strip(" ：:，,。")
        coverage = re.sub(
            r"[，,]?\s*(?:生成后|生成完|然后|并且|同时|再)?\s*(?:先只讲|每次只讲|讲完|继续问|不要一次|逐步讲).*",
            "",
            coverage,
        ).strip(" ：:，,。")
        if coverage and coverage not in topic:
            return f"{topic}：{coverage[:120]}"
    return topic


def _extract_level_hint(text: str) -> str | None:
    patterns = [
        r"\b([ABC][12])\b",
        r"(从零开始|零基础|完全没学过|没学过|基础薄弱|初学|入门|进阶|高级|高中生|高中|初中生|初中|小学生|小学|高三|高二|高一|初三|初二|初一|大一|大二|大三|大四|研一|研二|研三|本科(?:一|二|三|四)?年级|本科生|大学生|硕士|博士|考研|本科|研究生)",
        r"法语水平是([ABC][12])",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = next((group for group in match.groups() if group), match.group(0))
        return value.upper() if re.fullmatch(r"[abc][12]", value, flags=re.IGNORECASE) else value
    return None


def _extract_goal_or_scenario_hint(text: str) -> str | None:
    patterns = [
        r"(?:为了|我要(?!学|学习)|我想把|想把|想要(?!学|学习)|用于|用来|准备用在|准备应对|应对|准备)\s*([^，。！？!?；;]{2,48})",
        r"(概念理解|概念|理念|理论|做题|题目|练习|实际应用|应用|都要|全都要|都可以|都行|自己看着办|你自己看着办|你看着办|你来决定|你决定|按你判断|按你安排|法国旅游|出国旅游|高考压轴导数大题|高考压轴题|导数大题|旅游|期末考试|考试|期末|面试|作业|论文阅读|论文|课程展示|课堂展示|展示|汇报|实验|工作|项目|阅读|写作|系统学|系统学习|学扎实|主线学扎实|连接.*主线|贯通|打基础|补基础)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = next((group for group in match.groups() if group), match.group(0))
        return " ".join(value.split()).strip()
    return None


def _learning_clarification_status(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> LearningClarificationStatus:
    message = request.message.strip()
    user_turns = [turn.content for turn in request.conversation if turn.role == "user"][-4:]
    user_context = "\n".join([*user_turns, message]).strip() or requirements.learning_goal
    compact = re.sub(r"\s+", "", user_context.lower())
    missing_items: list[str] = []
    progress = 0

    if _has_teachable_subject_signal(user_context, lesson, request):
        progress += 35
    else:
        missing_items.append("想学的主题")

    profile_patterns = [
        r"\b[a-c][12]\b",
        r"高[一二三](?:学生)?",
        r"初[一二三](?:学生)?",
        r"大[一二三四]",
        r"研[一二三]",
        r"\d+\s*(?:个)?(?:词|词汇|单词)",
        r"(?:零基础|初学|入门|进阶|高级|水平|学习者|基础|b1|b2|c1|高中生|高中|初中生|初中|小学生|小学|高三|考研|本科|本科生|大学生|研究生|硕士|博士|专业|年级)",
        r"(?:从零开始|完全没学过|没学过|基础薄弱)",
    ]
    if any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in profile_patterns):
        progress += 30
    else:
        missing_items.append("当前水平或背景")

    scenario_patterns = [
        "为了",
        "用于",
        "应对",
        "准备",
        "旅游",
        "考试",
        "期末",
        "面试",
        "作业",
        "论文",
        "展示",
        "汇报",
        "实验",
        "写作",
        "阅读",
        "工作",
        "项目",
        "题目",
        "概念理解",
        "概念",
        "理念",
        "理论",
        "做题",
        "练习",
        "实际应用",
        "应用",
        "是什么",
        "什么是",
        "什么事",
        "啥是",
        "都要",
        "全都要",
        "都可以",
        "都行",
        "自己看着办",
        "你自己看着办",
        "你看着办",
        "你来决定",
        "你决定",
        "按你判断",
        "按你安排",
        "场景",
        "情景",
        "高考",
        "竞赛",
        "出国",
        "法国",
        "餐厅",
        "压轴",
        "系统学",
        "系统学习",
        "学扎实",
        "主线",
        "连接",
        "贯通",
        "打基础",
        "补基础",
    ]
    if any(pattern in compact for pattern in scenario_patterns) or request.selection:
        progress += 25
    else:
        missing_items.append("学习目的或应用场景")

    output_patterns = [
        "解释",
        "讲解",
        "板书",
        "课文",
        "对话",
        "练习",
        "例题",
        "总结",
        "讲义",
        "生成",
        "整理",
        "文档",
        "开始教学",
        "开始讲解",
        "开始讲课",
        "开始讲",
        "直接开始",
    ]
    if any(pattern in compact for pattern in output_patterns):
        progress += 10

    progress = max(0, min(progress, 100))
    forced_start = _is_forced_start_request(message)
    can_start = progress >= 35 or forced_start or request.interaction_mode == "direct_edit"
    if progress >= 80:
        label = "需求已清楚"
        reason = "当前主题、水平和应用场景已经足够明确，可以直接进入讲义生成或教学。"
    elif can_start:
        label = "可以先开始"
        reason = "就算信息还不完整，也已经足够先讲起来，缺的部分可以由系统先做合理假设。"
    else:
        label = "建议补一句"
        reason = "当前信息太少，不补一句就容易把讲法和深度带偏。"

    if forced_start and progress < 80:
        reason = "用户明确要求先开始教学，因此系统会按当前信息直接推进，缺的信息由系统先做保守假设。"

    return LearningClarificationStatus(
        progress=progress,
        label=label,
        reason=reason,
        missing_items=missing_items[:2],
        can_start=can_start,
        forced_start=forced_start,
    )


def _should_use_fast_pm_path(
    *,
    lesson: Lesson,
    request: ChatRequest,
    status: LearningClarificationStatus,
) -> bool:
    return True


def _should_use_fast_board_path(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> bool:
    if request.interaction_mode == "direct_edit" or request.scope_action is not None:
        return True
    if request.board_edit_action is not None:
        return True
    if request.resource_reference_action is not None:
        return True
    if is_document_empty(lesson.board_document):
        return True
    if _is_append_document_request(request.message):
        return True
    if _is_board_generation_request(request.message) or _is_explanation_request(request.message):
        return True
    if classify_scope(request.message, lesson) == "scope_escalation":
        return True
    if requirements.output_preference and not is_document_empty(lesson.board_document):
        return True
    compact = re.sub(r"\s+", "", request.message)
    obvious_keywords = [
        "新增章节",
        "补充一节",
        "展开讲",
        "扩展",
        "更易懂",
        "整理",
        "改写",
        "润色",
        "练习",
        "习题",
        "例题",
        "总结",
        "完善",
    ]
    return any(keyword in compact for keyword in obvious_keywords)


def _resource_query_text(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> str:
    parts = [
        lesson.title,
        lesson.board_document.title,
        lesson.board_document.content_text[:3000],
        requirements.theme,
        requirements.learning_goal,
        requirements.target_depth,
        *requirements.board_scope[:8],
        *requirements.current_questions[-2:],
        request.message,
    ]
    if request.selection:
        parts.append(request.selection.excerpt[:120])
    return "\n".join(part for part in parts if part)


def _available_reference_resources(course_package: CoursePackage, lesson: Lesson) -> list[ResourceLibraryItem]:
    return [
        resource
        for resource in course_package.resources
        if resource.scope_lesson_id is None or resource.scope_lesson_id == lesson.id
    ]


def _is_resource_followup_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message).lower()
    if compact in {"你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗"}:
        return False
    if _is_explanation_request(message) or _is_board_generation_request(message) or _is_forced_start_request(message):
        return True
    chapter_no, _ = _extract_requested_outline_reference(message)
    if chapter_no is not None:
        return True
    resource_terms = [
        "资料",
        "文件",
        "文章",
        "教材",
        "讲义",
        "文档",
        "内容",
        "章节",
        "这一章",
        "这一节",
        "这个部分",
        "这部分",
        "第一",
        "第二",
        "第三",
        "第四",
        "第五",
        "chapter",
        "section",
    ]
    teaching_terms = ["讲", "学", "教", "解释", "开始", "开讲", "整理"]
    return any(term in compact for term in resource_terms) and any(term in compact for term in teaching_terms)


def _status_with_resource_context_default(
    status: LearningClarificationStatus,
    *,
    resource_count: int,
) -> LearningClarificationStatus:
    missing_items = [item for item in status.missing_items if item != "想学的主题"]
    reason = "当前追问可以先按已上传资料理解，缺少的学习背景由系统先做保守假设。"
    if resource_count > 1:
        reason = "当前追问看起来指向已上传资料；如果资料不唯一，系统会先确认具体文件。"
    return status.model_copy(
        update={
            "progress": max(status.progress, 35),
            "label": "可以先开始",
            "reason": reason,
            "missing_items": missing_items[:2],
            "can_start": True,
        }
    )


def _should_use_resource_followup_context(
    *,
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
) -> bool:
    if request.interaction_mode == "direct_edit" or request.selection is not None:
        return False
    if request.resource_reference_action is not None:
        return False
    return bool(_available_reference_resources(course_package, lesson)) and _is_resource_followup_request(request.message)


def _chapter_overlap_score(
    query_text: str,
    *,
    chapter_title: str,
    chapter_summary: str,
    keywords: list[str],
    chapter_path: list[str] | None = None,
    chapter_level: int = 1,
    chapter_no: int | None = None,
    section_no: int | None = None,
) -> tuple[float, list[str]]:
    lowered_query = query_text.lower()
    phrases = _query_phrases(query_text)
    requested_chapter_no, requested_section_no = _extract_requested_outline_reference(query_text)
    hits: list[str] = []
    score = 0.0

    title_lower = chapter_title.lower()
    title_terms = [title_lower, *_expanded_match_terms(chapter_title)]
    path_terms = [term.lower() for term in (chapter_path or []) if term.strip()]
    path_corpus = " ".join(path_terms)
    if any(term and term in lowered_query for term in title_terms):
        score += 0.58
        hits.append(chapter_title)
    elif any(len(phrase) >= 4 and any(phrase in term for term in title_terms) for phrase in phrases):
        score += 0.45
        hits.append(chapter_title)
    if path_corpus and any(term and term in lowered_query for term in path_terms):
        score += 0.18
        hits.append(" / ".join(chapter_path or [chapter_title]))
    elif path_corpus and any(phrase in path_corpus for phrase in phrases if len(phrase) >= 2):
        score += 0.12
        hits.append(" / ".join(chapter_path or [chapter_title]))

    summary_lower = chapter_summary.lower()
    expanded_keywords = [
        *keywords,
        *path_terms,
        *_expanded_match_terms(chapter_title, chapter_summary, " ".join(keywords), " ".join(chapter_path or [])),
    ]
    for keyword in expanded_keywords:
        lowered_keyword = keyword.lower().strip()
        if len(lowered_keyword) < 2:
            continue
        if lowered_keyword in lowered_query:
            score += 0.16
            hits.append(keyword)
        elif lowered_keyword in summary_lower:
            score += 0.04

    corpus = f"{chapter_title} {chapter_summary} {' '.join(chapter_path or [])}".lower()
    for phrase in phrases:
        if phrase in corpus:
            score += 0.08
            hits.append(phrase)

    if chapter_level > 1 and any(phrase in title_lower or phrase in path_corpus for phrase in phrases):
        score += min((chapter_level - 1) * 0.03, 0.09)

    if requested_chapter_no is not None and chapter_no == requested_chapter_no:
        if requested_section_no is None:
            score += 0.78 if chapter_level == 1 else 0.18
            hits.append(f"第{requested_chapter_no}章")
        else:
            score += 0.24
            hits.append(f"第{requested_chapter_no}章")
        if requested_section_no is not None and section_no == requested_section_no:
            score += 0.86
            hits.append(f"第{requested_chapter_no}章第{requested_section_no}节")
        elif requested_section_no is not None and chapter_level == 1:
            score += 0.08

    unique_hits: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        lowered_hit = hit.lower()
        if lowered_hit in seen:
            continue
        seen.add(lowered_hit)
        unique_hits.append(hit)
    return min(score, 0.99), unique_hits[:3]


def _resource_name_overlap_score(query_text: str, resource_name: str) -> tuple[float, list[str]]:
    lowered_query = query_text.lower()
    cleaned_name = resource_name.strip().lower()
    stem = re.sub(r"\.[^.]+$", "", cleaned_name).strip()
    candidates = [candidate for candidate in [cleaned_name, stem] if len(candidate) >= 2]
    for candidate in candidates:
        if candidate and candidate in lowered_query:
            return 0.66, [candidate]

    tokens = [
        token
        for token in re.split(r"[\s_\-—–.，。！？?!.、/（）()：:；;,\[\]【】]+", stem)
        if len(token) >= 2
    ]
    hits = [token for token in tokens if token in lowered_query]
    if hits:
        return min(0.42 + 0.08 * len(hits), 0.62), hits[:3]
    return 0.0, []


def _query_mentions_resource_name(message: str, resources: list[ResourceLibraryItem]) -> bool:
    return any(_resource_name_overlap_score(message, resource.name)[0] >= 0.42 for resource in resources)


def _default_chapter_for_resource(resource: ResourceLibraryItem, request: ChatRequest) -> LibraryChapter | None:
    if not resource.outline:
        return None
    requested_chapter_no, requested_section_no = _extract_requested_outline_reference(request.message)
    if requested_chapter_no is not None:
        fallback_in_chapter: LibraryChapter | None = None
        for chapter in resource.outline:
            chapter_no, section_no = _outline_reference_position(resource.outline, chapter.id)
            if chapter_no != requested_chapter_no:
                continue
            if requested_section_no is not None and section_no == requested_section_no:
                return chapter
            if requested_section_no is None and chapter.level == 1:
                return chapter
            if fallback_in_chapter is None:
                fallback_in_chapter = chapter
        if fallback_in_chapter is not None:
            return fallback_in_chapter

    return next(
        (chapter for chapter in resource.outline if chapter.level == 1 and not _is_reference_separator_title(chapter.title)),
        resource.outline[0],
    )


def _default_single_resource_match(
    resource: ResourceLibraryItem,
    request: ChatRequest,
) -> ResourceMatch | None:
    chapter = _default_chapter_for_resource(resource, request)
    if chapter is None:
        return None
    return ResourceMatch(
        resource_id=resource.id,
        chapter_id=chapter.id,
        resource_name=resource.name,
        chapter_title=chapter.title,
        reason="当前课程只有这一份已上传资料，按它作为本轮默认学习资料。",
        score=0.76,
        is_high_overlap=True,
    )


def _chapter_body_quality_score(chapter: LibraryChapter) -> float:
    compact_summary = re.sub(r"\s+", "", chapter.summary or "")
    outline_only = bool(re.fullmatch(r"(?:【[^】]+】)+", compact_summary))
    quality = min(len(compact_summary) / 1000, 0.16)
    meaningful_keywords = [
        keyword
        for keyword in chapter.keywords
        if len(keyword.strip()) >= 2 and keyword.strip() not in {"学习精要", "习题解析", "补充训练"}
    ]
    if meaningful_keywords:
        quality += min(len(meaningful_keywords), 6) * 0.015
    if outline_only:
        quality -= 0.18
    if _is_reference_separator_title(chapter.title):
        quality -= 0.24
    return quality


def match_resources(
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> list[ResourceMatch]:
    query_text = _resource_query_text(lesson, request, requirements)
    primary_query_text = "\n".join(
        part
        for part in [
            request.message,
            request.selection.excerpt[:120] if request.selection else None,
        ]
        if part
    )
    requested_chapter_no, requested_section_no = _extract_requested_outline_reference(primary_query_text or query_text)
    scored_matches: list[tuple[float, float, float, float, float, float, ResourceMatch]] = []
    matches: list[ResourceMatch] = []
    resources = _available_reference_resources(course_package, lesson)
    for resource in resources:
        resource_primary_score, resource_primary_hits = _resource_name_overlap_score(primary_query_text, resource.name)
        resource_context_score, resource_context_hits = _resource_name_overlap_score(query_text, resource.name)
        for chapter in resource.outline:
            chapter_no, section_no = _outline_reference_position(resource.outline, chapter.id)
            primary_score, primary_overlap = _chapter_overlap_score(
                primary_query_text,
                chapter_title=chapter.title,
                chapter_summary=chapter.summary,
                keywords=chapter.keywords,
                chapter_path=chapter.path,
                chapter_level=chapter.level,
                chapter_no=chapter_no,
                section_no=section_no,
            )
            score, overlap = _chapter_overlap_score(
                query_text,
                chapter_title=chapter.title,
                chapter_summary=chapter.summary,
                keywords=chapter.keywords,
                chapter_path=chapter.path,
                chapter_level=chapter.level,
                chapter_no=chapter_no,
                section_no=section_no,
            )
            chapter_specific_score = max(primary_score, score)
            primary_score = min(primary_score + resource_primary_score, 0.99)
            score = min(score + resource_context_score, 0.99)
            effective_score = max(primary_score, score)
            if effective_score > 0.18:
                outline_specificity = 0.0
                if requested_chapter_no is not None and chapter_no == requested_chapter_no:
                    outline_specificity += 0.25
                if requested_section_no is not None and section_no == requested_section_no:
                    outline_specificity += 0.75
                overlap_hits = [
                    *(primary_overlap or overlap),
                    *(resource_primary_hits or resource_context_hits),
                ]
                matches.append(
                    ResourceMatch(
                        resource_id=resource.id,
                        chapter_id=chapter.id,
                        resource_name=resource.name,
                        chapter_title=chapter.title,
                        reason=(
                            f"章节标题与关键词和当前学习目标有明显重合："
                            f"{', '.join(overlap_hits[:3]) or chapter.title}"
                        ),
                        score=round(effective_score, 2),
                        is_high_overlap=effective_score >= HIGH_OVERLAP_THRESHOLD,
                    )
                )
                scored_matches.append(
                    (
                        primary_score,
                        score,
                        chapter_specific_score,
                        outline_specificity,
                        _chapter_body_quality_score(chapter),
                        -float(chapter.order_index),
                        matches[-1],
                    )
                )
    scored_matches.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5]), reverse=True)
    ranked = [item[6] for item in scored_matches[:3]]
    if ranked:
        return ranked
    if len(resources) == 1 and _is_resource_followup_request(request.message):
        default_match = _default_single_resource_match(resources[0], request)
        return [default_match] if default_match is not None else []
    return []


def _resource_file_clarification_question(
    resources: list[ResourceLibraryItem],
    matches: list[ResourceMatch],
) -> str:
    ordered_ids: list[str] = []
    for match in matches:
        if match.resource_id not in ordered_ids:
            ordered_ids.append(match.resource_id)
    for resource in sorted(resources, key=lambda item: item.uploaded_at, reverse=True):
        if resource.id not in ordered_ids:
            ordered_ids.append(resource.id)

    resource_by_id = {resource.id: resource for resource in resources}
    option_names = [resource_by_id[resource_id].name for resource_id in ordered_ids if resource_id in resource_by_id][:4]
    options = "、".join(f"《{name}》" for name in option_names)
    suffix = "等" if len(resources) > len(option_names) else ""
    return f"你想讲哪一份资料？我看到 {options}{suffix}。直接回文件名或资料名，我就按那份资料的对应章节讲。"


def _should_clarify_resource_file(
    *,
    resources: list[ResourceLibraryItem],
    matches: list[ResourceMatch],
    request: ChatRequest,
) -> bool:
    if len(resources) <= 1 or request.resource_reference_action is not None:
        return False
    if not _is_resource_followup_request(request.message):
        return False
    if _query_mentions_resource_name(request.message, resources):
        return False
    if not matches:
        return True

    top_match = matches[0]
    second_match = next((match for match in matches[1:] if match.resource_id != top_match.resource_id), None)
    if second_match is None:
        return False

    score_delta = abs(top_match.score - second_match.score)
    if top_match.is_high_overlap and second_match.is_high_overlap and score_delta <= 0.06:
        return True
    chapter_no, _ = _extract_requested_outline_reference(request.message)
    if chapter_no is not None and score_delta <= 0.18:
        return True
    if not top_match.is_high_overlap and score_delta <= 0.12:
        return True
    return False


def _build_reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=(
            f"我在资料目录里找到一个很贴近当前板书/请求的章节：《{match.resource_name}》的《{match.chapter_title}》。要参考这章正文来生成板书吗？"
        ),
        reason=match.reason,
        confirm_label="参考资料生成板书",
        score=match.score,
    )


def _should_auto_attach_reference_for_direct_teaching(
    *,
    request: ChatRequest,
    decision: BoardDecision,
    top_match: ResourceMatch | None,
) -> bool:
    if top_match is None:
        return False
    _ = decision
    chapter_no, _ = _extract_requested_outline_reference(request.message)
    if chapter_no is not None:
        return True
    return False


def _selected_reference_context(
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> ResourceReferenceContext | None:
    if request.resource_reference_action != "confirm":
        return None
    resource_id = request.resource_reference_resource_id
    chapter_id = request.resource_reference_chapter_id
    if not resource_id or not chapter_id:
        return None
    resource = next((candidate for candidate in course_package.resources if candidate.id == resource_id), None)
    if resource is None:
        return None
    return extract_reference_context(
        resource,
        chapter_id,
        user_query=_resource_query_text(lesson, request, requirements),
    )


def _reference_context_for_match(
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    match: ResourceMatch,
) -> ResourceReferenceContext | None:
    resource = next((candidate for candidate in course_package.resources if candidate.id == match.resource_id), None)
    if resource is None:
        return None
    return extract_reference_context(
        resource,
        match.chapter_id,
        user_query=_resource_query_text(lesson, request, requirements),
    )


def _reference_payload(
    reference: ResourceReferenceContext | None,
    *,
    include_full_text: bool,
) -> dict[str, object] | None:
    if reference is None:
        return None

    payload: dict[str, object] = {
        "resource_id": reference.resource_id,
        "chapter_id": reference.chapter_id,
        "resource_name": reference.resource_name,
        "chapter_title": reference.chapter_title,
        "summary": reference.summary,
        "teaching_points": reference.teaching_points,
        "chunks": [chunk.model_dump(mode="json") for chunk in reference.chunks],
        "chapter_text_length": len(reference.full_text),
    }
    if include_full_text:
        max_prompt_chars = 32000
        payload["chapter_text"] = reference.full_text[:max_prompt_chars]
        if len(reference.full_text) > max_prompt_chars:
            payload["chapter_text_note"] = f"全文较长，已优先提供前 {max_prompt_chars} 字符供生成。"
    return payload


def _learning_need_checklist(lesson: Lesson, request: ChatRequest, requirements: LearningRequirementSheet) -> list[str]:
    candidates: list[str] = []
    supplemental_items = _section_followup_need_items(
        lesson=lesson,
        request=request,
        existing_needs=requirements.learning_need_checklist,
    )
    user_turns = [turn.content for turn in request.conversation if turn.role == "user"][-3:]
    for question in [*user_turns, request.message, *requirements.current_questions[-3:]]:
        cleaned = " ".join(str(question).split()).strip()
        if cleaned:
            candidates.append(f"回应用户当前问题：{cleaned[:80]}")
    if request.selection and request.selection.excerpt.strip():
        candidates.append(f"解释用户框选内容：{request.selection.excerpt.strip()[:80]}")
    if requirements.learning_goal.strip():
        candidates.append(f"服务学习目标：{requirements.learning_goal.strip()[:80]}")
    if requirements.target_depth.strip():
        candidates.append(f"达到讲解深度：{requirements.target_depth.strip()[:80]}")
    for term in _extract_focus_terms(request.message)[:3]:
        candidates.append(f"讲清关键词：{term}")
    if lesson.board_document.content_text.strip():
        for line in _relevant_lines(lesson.board_document, request)[:2]:
            candidates.append(f"对齐当前版书：{line[:80]}")
    else:
        topic = requirements.theme if requirements.theme and requirements.theme != lesson.title else lesson.title
        candidates.append(f"在不写入版书的前提下，先围绕《{topic}》形成可讲主线")

    needs: list[str] = []
    seen: set[str] = set()
    target_limit = 8 if supplemental_items else 6
    for candidate in [*requirements.learning_need_checklist, *supplemental_items, *candidates]:
        cleaned = " ".join(str(candidate).split()).strip(" ：:，,。")
        if len(cleaned) < 4 or cleaned in seen:
            continue
        seen.add(cleaned)
        needs.append(cleaned)
        if len(needs) >= target_limit:
            break
    while len(needs) < 2:
        fallback = f"围绕《{lesson.title}》把当前问题讲清楚" if not needs else "用一个例子或检查问题确认理解"
        if fallback not in seen:
            needs.append(fallback)
            seen.add(fallback)
        else:
            break
    return needs[:target_limit]


def _active_section_followup_context(lesson: Lesson) -> tuple[int, str, str] | None:
    progress = lesson.board_teaching_progress
    if progress is None:
        return None
    document = lesson.board_document
    target_hash = _board_snapshot_hash(document)
    if progress.board_document_id != document.id or progress.board_snapshot_hash != target_hash:
        return None

    guide = _current_board_teaching_guide(lesson, document)
    if guide is not None and guide.section_plans:
        index = max(0, min(progress.current_section_index, len(guide.section_plans) - 1))
        plan = guide.section_plans[index]
        body = plan.board_excerpt or "\n".join(plan.core_points)
        return index + 1, plan.heading, body

    sections = _board_h2_sections(document)
    if not sections:
        return None
    index = max(0, min(progress.current_section_index, len(sections) - 1))
    heading, body = sections[index]
    return index + 1, heading, body


def _is_section_followup_learning_need(lesson: Lesson, request: ChatRequest) -> bool:
    if _active_section_followup_context(lesson) is None:
        return False
    if _is_teaching_control_request(request):
        return False
    if request.interaction_mode == "direct_edit":
        return False
    if request.board_edit_action is not None or request.scope_action is not None:
        return False
    if request.resource_reference_action is not None:
        return False
    message = request.message.strip()
    if _is_low_information_request(message) or _is_vague_pointer_request(message):
        return False
    if _is_explicit_board_edit_request(message):
        return False
    compact = _compact_instruction_text(message)
    question_signals = (
        "如果",
        "为什么",
        "怎么",
        "怎样",
        "是什么",
        "什么意思",
        "会怎么样",
        "会怎样",
        "能不能",
        "有没有",
        "区别",
        "关系",
        "那",
        "?",
        "？",
    )
    return _is_explanation_request(message) or any(signal in compact for signal in question_signals)


def _is_confirmed_section_followup_learning_need(lesson: Lesson, request: ChatRequest) -> bool:
    return (
        request.board_edit_action == "confirm"
        and _active_section_followup_context(lesson) is not None
        and bool((request.board_edit_topic or "").strip())
        and not _is_explicit_board_edit_request(request.message)
    )


def _section_followup_topics(message: str) -> list[str]:
    cleaned = " ".join(message.split()).strip(" ：:，,。！？!?；;")
    if not cleaned:
        return []
    parts = re.split(
        r"[？?；;！!\n]+|(?:另外|还有|以及|同时|顺便|再问一下|再问|还想问|还有就是)",
        cleaned,
    )
    topics: list[str] = []
    seen: set[str] = set()
    for part in parts:
        topic = part.strip(" ：:，,。！？!?；;")
        topic = re.sub(r"^(?:我想问|想问|问一下|请问|那|如果|假如|比如说|例如)\s*", "", topic).strip()
        topic = re.sub(r"^(?:请|帮我|给我|为我)?(?:解释一下|解释|讲解一下|讲一下|讲讲)\s*", "", topic).strip()
        if topic.startswith("是") and len(topic) >= 4:
            topic = topic[1:].strip()
        topic = topic.strip(" ：:，,。！？!?；;")
        if len(topic) < 2:
            continue
        if topic in {"这个", "这里", "这块", "继续", "继续讲"}:
            continue
        if topic in seen:
            continue
        seen.add(topic)
        topics.append(topic[:80])
        if len(topics) >= 3:
            break
    if topics:
        return topics

    fallback = _clean_board_edit_topic(cleaned)
    return [fallback] if fallback else []


def _next_section_child_index(existing_needs: list[str], section_number: int) -> int:
    child_indexes: list[int] = []
    pattern = re.compile(rf"^\s*{section_number}\.(\d+)\b")
    for need in existing_needs:
        match = pattern.search(str(need))
        if match:
            child_indexes.append(int(match.group(1)))
    return (max(child_indexes) + 1) if child_indexes else 1


def _section_followup_need_items(
    *,
    lesson: Lesson,
    request: ChatRequest,
    existing_needs: list[str],
) -> list[str]:
    context = _active_section_followup_context(lesson)
    if context is None or not _is_section_followup_learning_need(lesson, request):
        return []
    section_number, section_title, _ = context
    topics = _section_followup_topics(request.message)
    if not topics:
        return []

    child_index = _next_section_child_index(existing_needs, section_number)
    items: list[str] = []
    for offset, topic in enumerate(topics):
        label = f"{section_number}.{child_index + offset}"
        items.append(f"{label} 追问补充：{topic}（来源：第 {section_number} 小节《{section_title}》）")
    return items


def _draft_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    requirements = effective_requirements(lesson)
    user_turns = [turn.content for turn in request.conversation if turn.role == "user"]
    user_context = "\n".join([*user_turns[-3:], request.message]).strip()
    topic_hint = _extract_topic_hint(request.message) or _extract_topic_hint(user_context)
    if topic_hint and topic_hint != lesson.title:
        requirements = build_requirements(topic_hint)
    requirements.current_questions = [*user_turns[-3:], request.message][-4:]
    if request.selection:
        requirements.current_questions.append(f"用户框选内容：{request.selection.excerpt[:80]}")

    level_hint = _extract_level_hint(user_context)
    if level_hint:
        requirements.level = level_hint
        requirements.known_background = f"用户自述或对话可推断：{level_hint}"

    goal_hint = _extract_goal_or_scenario_hint(user_context)
    if goal_hint:
        requirements.success_criteria = f"用户能把当前内容用于：{goal_hint}"
        if not requirements.target_depth or "入门题" in requirements.target_depth:
            requirements.target_depth = f"优先围绕“{goal_hint}”这个场景，把当前知识点讲明白并能立刻用起来。"

    requirements.boundary = "优先围绕当前 lesson 的整篇文档主线；超出范围时先决定是仅讲解、补充章节还是新开 lesson。"
    normalized = normalize_requirements(requirements, lesson_title=lesson.title, document=lesson.board_document)
    if topic_hint:
        normalized.theme = topic_hint
    normalized.learning_need_checklist = _learning_need_checklist(lesson, request, normalized)
    return normalized


def _is_first_user_exchange(request: ChatRequest) -> bool:
    return not any(turn.role == "user" for turn in request.conversation)


def _should_ask_brief_clarification(
    *,
    request: ChatRequest,
    status: LearningClarificationStatus,
) -> bool:
    if request.interaction_mode == "direct_edit" or request.selection is not None:
        return False
    if status.forced_start:
        return False
    if _is_explanation_request(request.message) or _is_board_generation_request(request.message):
        return False
    missing = set(status.missing_items)
    if request.selection is None and _is_vague_pointer_request(request.message):
        return True
    if "想学的主题" in missing and status.progress < 35:
        return True
    if {"当前水平或背景", "学习目的或应用场景"} <= missing and status.progress < 55:
        if "想学的主题" not in missing and _is_first_user_exchange(request):
            return False
        return True
    return False


def _build_scope_options(matches: list[ResourceMatch]) -> list[ScopeOption]:
    return [
        ScopeOption(
            action="patch_current_lesson",
            label="当前课内简述",
            description="不重写当前讲义结构，只围绕现有内容先把问题讲清楚。",
        ),
        ScopeOption(
            action="append_section",
            label="新增章节",
            description="在当前 lesson 的 Word 式讲义里补一节连续内容。",
        ),
        ScopeOption(
            action="create_new_lesson",
            label="新开详细 lesson",
            description="把这个问题单独开成一节新课，避免覆盖当前主线。",
            resource_chapter_id=matches[0].chapter_id if matches else None,
        ),
    ]


def _is_reference_separator_title(title: str) -> bool:
    cleaned = re.sub(r"\s+", "", title.strip().lower())
    front_or_back_matter = {
        "前言",
        "序",
        "序言",
        "补序",
        "目录",
        "目次",
        "参考文献",
        "后记",
        "索引",
        "致谢",
    }
    return (
        cleaned.startswith("---")
        or cleaned.startswith("part")
        or cleaned in front_or_back_matter
        or cleaned.startswith("附录")
    )


CHINESE_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_UNITS: dict[str, int] = {"十": 10, "百": 100}
OUTLINE_NUMBER_PATTERN = r"([0-9一二三四五六七八九十百〇零两]+)"


def _parse_outline_number(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)

    total = 0
    current = 0
    seen = False
    for char in cleaned:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
            seen = True
            continue
        unit = CHINESE_UNITS.get(char)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
        seen = True
    if not seen:
        return None
    return total + current


def _extract_requested_outline_reference(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        rf"第\s*{OUTLINE_NUMBER_PATTERN}\s*章(?:\s*第\s*{OUTLINE_NUMBER_PATTERN}\s*[节讲部分])?",
        text,
    )
    if match:
        chapter_no = _parse_outline_number(match.group(1))
        section_no = _parse_outline_number(match.group(2)) if match.group(2) else None
        return chapter_no, section_no
    dotted = re.search(r"\bchapter\s*(\d+)\s*(?:section\s*(\d+))?\b", text, flags=re.IGNORECASE)
    if dotted:
        chapter_no = int(dotted.group(1))
        section_no = int(dotted.group(2)) if dotted.group(2) else None
        return chapter_no, section_no
    number_pair = re.search(r"\b(\d+)\.(\d+)\b", text)
    if number_pair:
        return int(number_pair.group(1)), int(number_pair.group(2))
    return None, None


def _extract_numbered_title_reference(title: str) -> tuple[int | None, int | None]:
    cleaned = title.strip()
    chapter_match = re.search(rf"第\s*{OUTLINE_NUMBER_PATTERN}\s*章", cleaned)
    if chapter_match:
        return _parse_outline_number(chapter_match.group(1)), None

    dotted = re.search(r"^\s*(\d+)\s*[.．]\s*(\d+)", cleaned)
    if dotted:
        return int(dotted.group(1)), int(dotted.group(2))

    english_chapter = re.search(r"\bchapter\s*(\d+)\b", cleaned, flags=re.IGNORECASE)
    if english_chapter:
        return int(english_chapter.group(1)), None

    english_section = re.search(r"\bsection\s*(\d+)\s*[.．]\s*(\d+)\b", cleaned, flags=re.IGNORECASE)
    if english_section:
        return int(english_section.group(1)), int(english_section.group(2))

    section_match = re.search(rf"第\s*{OUTLINE_NUMBER_PATTERN}\s*[节讲部分]", cleaned)
    if section_match:
        return None, _parse_outline_number(section_match.group(1))

    return None, None


def _outline_reference_position(
    chapters: list[object],
    chapter_id: str,
) -> tuple[int | None, int | None]:
    chapter_no = 0
    section_no = 0
    current_chapter_no: int | None = None
    current_chapter_id: str | None = None
    for raw in chapters:
        chapter = raw
        title = getattr(chapter, "title", "")
        level = int(getattr(chapter, "level", 1))
        current_id = getattr(chapter, "id", "")
        explicit_chapter_no, explicit_section_no = _extract_numbered_title_reference(str(title))
        if level == 1:
            if _is_reference_separator_title(str(title)) and explicit_chapter_no is None:
                current_chapter_no = None
                current_chapter_id = None
                section_no = 0
                if current_id == chapter_id:
                    return None, None
                continue
            if explicit_chapter_no is not None:
                chapter_no = max(chapter_no, explicit_chapter_no)
                current_chapter_no = explicit_chapter_no
            else:
                chapter_no += 1
                current_chapter_no = chapter_no
            current_chapter_id = current_id
            section_no = explicit_section_no or 0
            if current_id == chapter_id:
                return current_chapter_no, explicit_section_no
            continue
        if level >= 2 and current_chapter_id is not None and current_chapter_no is not None:
            if explicit_chapter_no is not None and explicit_chapter_no != current_chapter_no:
                current_chapter_no = explicit_chapter_no
                section_no = 0
            if explicit_section_no is not None:
                section_no = explicit_section_no
            else:
                section_no += 1
            if current_id == chapter_id:
                return current_chapter_no, section_no
    return None, None


def _fallback_board_decision(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> BoardDecision:
    message = request.message
    scope_mode = classify_scope(message, lesson)
    explicit_generation = _is_board_generation_request(message)

    if request.board_edit_action == "skip":
        return BoardDecision(action="no_change", reason="用户选择暂不把这次扩展内容写入版书。")
    if request.board_edit_action == "confirm":
        if request.scope_action == "create_new_lesson":
            return BoardDecision(action="create_new_lesson", reason="用户确认扩选版书，并明确要求拆成一节新课。")
        if request.scope_action == "append_section":
            return BoardDecision(action="append_section", reason="用户确认扩选版书，并选择在当前 lesson 中新增章节。")
        if _is_section_followup_learning_need(lesson, request) or _is_confirmed_section_followup_learning_need(lesson, request):
            return BoardDecision(action="append_section", reason="用户确认将分节授课中的追问沉淀到版书，默认追加为当前小节的子章节。")
        if _is_append_document_request(message):
            return BoardDecision(action="append_section", reason="用户确认扩选版书，且请求指向新增页面或章节。")
        if _is_in_place_expansion_request(message) or explicit_generation or is_document_empty(lesson.board_document):
            return BoardDecision(action="edit_board", reason="用户确认扩选版书，版书管理 AI 将按当前内容生成可写入版本。")
        if scope_mode == "scope_escalation":
            return BoardDecision(action="append_section", reason="用户确认将新知识纳入当前版书，默认作为扩展章节承接原主线。")
        return BoardDecision(action="edit_board", reason="用户确认为这次问题编辑扩选版书。")
    if request.scope_action == "create_new_lesson":
        return BoardDecision(action="create_new_lesson", reason="用户明确要求把问题拆成一节新课。")
    if request.scope_action == "append_section":
        return BoardDecision(action="append_section", reason="用户选择在当前 lesson 中新增章节。")
    if request.scope_action == "patch_current_lesson":
        return BoardDecision(action="no_change", reason="用户选择先在当前课内简述，不直接改讲义。")
    if is_document_empty(lesson.board_document) and explicit_generation:
        return BoardDecision(action="edit_board", reason="用户明确要求生成讲义/板书，当前版书为空，直接生成可写入版本。")
    if _is_explicit_board_edit_request(message) and _is_in_place_expansion_request(message) and not is_document_empty(lesson.board_document):
        return BoardDecision(action="edit_board", reason="用户明确要求扩展当前板书内容，应在原有章节里就地扩写。")
    if _is_append_document_request(message) and not is_document_empty(lesson.board_document):
        return BoardDecision(action="append_section", reason="用户要求在现有讲义后新增页面或章节内容。")
    if explicit_generation:
        return BoardDecision(action="edit_board", reason="用户明确要求生成讲义/课文/对话内容，应直接产出整篇文档。")
    if scope_mode == "scope_escalation":
        return BoardDecision(action="no_change", reason="问题可能超出当前版书范围，先生成内部讲义讲解，再由用户确认是否扩选版书。")
    if any(keyword in message for keyword in ["新增章节", "补充一节"]):
        return BoardDecision(action="append_section", reason="用户希望把相关内容纳入当前 lesson 的新章节。")
    if _is_explicit_board_edit_request(message) and any(keyword in message for keyword in ["改写", "整理", "补一段", "润色", "完善", "扩展", "扩写", "细化", "丰富"]):
        return BoardDecision(action="edit_board", reason="当前需求更适合先调整整篇讲义，再围绕更新后的结构讲解。")
    if any(keyword in message for keyword in ["解释", "讲解", "开讲", "直接讲", "讲一下", "讲讲", "为什么", "什么意思", "怎么理解"]):
        return BoardDecision(action="no_change", reason="当前更像围绕现有讲义的讲解请求，不必先改文档。")
    if requirements.theme != lesson.title:
        return BoardDecision(action="no_change", reason=f"用户提出了新的学习主题“{requirements.theme}”，先讲解，再确认是否扩选版书。")
    if requirements.output_preference and not is_document_empty(lesson.board_document):
        return BoardDecision(action="no_change", reason="现有讲义已经能支撑这次讲解，先不改文档。")
    return BoardDecision(action="no_change", reason="默认先生成内部讲义供讲师讲解，不改动右侧版书。")


def _is_explicit_board_edit_request(message: str) -> bool:
    if _is_append_document_request(message) or _is_board_generation_request(message):
        return True
    compact = _compact_request_text(message)
    edit_signals = ("生成", "写", "做", "整理", "改写", "润色", "补充", "补全", "完善", "扩展", "扩写", "新增", "追加")
    board_targets = ("板书", "版书", "讲义", "文档", "课文", "章节", "页面", "一节", "整章", "练习", "习题", "例题", "对话")
    return any(signal in compact for signal in edit_signals) and any(target in compact for target in board_targets)


def _clean_board_edit_topic(value: str) -> str:
    cleaned = re.sub(r"\s+", "", value or "")
    for prefix in ("请帮我", "帮我", "为我", "给我", "请", "能不能", "可以"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    for marker in ("我想学", "想学", "什么是", "解释一下", "讲解一下", "讲一下", "讲讲", "怎么理解", "为什么", "直接开讲", "直接讲"):
        cleaned = cleaned.replace(marker, "")
    cleaned = re.sub(r"(内容|问题|这一块|这个部分|一下|吗|呢)$", "", cleaned)
    return cleaned.strip(" ：:，,。？！!?」『』“”\"'")[:36]


def _board_edit_prompt_topic(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> str:
    if request.board_edit_topic and request.board_edit_topic.strip():
        return request.board_edit_topic.strip()[:36]
    if matches and matches[0].is_high_overlap:
        return matches[0].chapter_title[:36]
    for term in _extract_focus_terms(request.message):
        topic = _clean_board_edit_topic(term)
        if len(topic) >= 2 and topic not in {"解释", "讲解", "直接开讲"}:
            return topic
    if requirements.theme and requirements.theme != lesson.title:
        return requirements.theme[:36]
    return _clean_board_edit_topic(request.message) or lesson.title


def _build_board_edit_prompt(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> BoardEditPrompt:
    topic = _board_edit_prompt_topic(lesson=lesson, request=request, requirements=requirements, matches=matches)
    reason = "这次问题引入了当前版书之外的内容，先按内部讲义讲解；如果你想沉淀到右侧版书，可以确认后由版书管理 AI 判断怎么扩写。"
    if is_document_empty(lesson.board_document):
        reason = "当前版书为空，本轮先只生成内部讲义给讲师讲解；确认后再把内容写成右侧版书。"
    return BoardEditPrompt(
        topic=topic,
        question=f"是否为「{topic}」内容编辑扩选板书？",
        reason=reason,
        confirm_label="是",
        skip_label="否",
    )


def _should_offer_board_edit_prompt(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    decision: BoardDecision,
) -> bool:
    if request.interaction_mode == "direct_edit":
        return False
    if is_document_empty(lesson.board_document):
        return False
    if request.board_edit_action is not None or request.scope_action is not None:
        return False
    if _is_explicit_board_edit_request(request.message):
        return False
    if decision.action != "no_change":
        return False
    if _is_section_followup_learning_need(lesson, request):
        return True
    if classify_scope(request.message, lesson) == "scope_escalation":
        return True
    return bool(requirements.theme and requirements.theme != lesson.title)


def _fallback_selection_replacement(request: ChatRequest) -> str:
    message = request.message.strip()
    for prefix in ["改成", "替换为", "改为", "换成"]:
        if message.startswith(prefix) and len(message) > len(prefix):
            return message[len(prefix) :].strip(" ：:，,")
    if _is_selection_enhancement_request(message):
        return "补充解析：保留原有题干和解题方法，在原文基础上补上关键信息梳理、解题思路、关键步骤和易错提醒，让这段板书更完整。"
    if any(keyword in message for keyword in ["更易懂", "通俗", "简单", "没懂", "解释"]):
        return "换一种更好懂的说法：先交代这句话在整篇讲义里的作用，再用更口语的语言把它解释清楚。"
    if any(keyword in message for keyword in ["总结", "概括", "压缩"]):
        return "一句话总结：先说结论，再点明原因和使用场景。"
    if any(keyword in message for keyword in ["润色", "校对", "优化"]):
        return message
    return message


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _merge_selection_edit(
    *,
    selection_text: str,
    generated_text: str,
    request_message: str,
) -> str:
    selected = selection_text.strip()
    generated = generated_text.strip()
    if not generated:
        return selected
    if not _is_selection_enhancement_request(request_message):
        return generated
    if not selected:
        return generated
    if _normalize_for_match(selected) in _normalize_for_match(generated):
        return generated
    return f"{selected}\n\n{generated}"


def _append_section_topic(message: str, requirements: LearningRequirementSheet) -> str:
    cleaned = message.strip()
    for separator in ("：", ":", "，", ","):
        head, found, tail = cleaned.partition(separator)
        if found and tail.strip() and _is_append_document_request(head + found):
            cleaned = tail.strip()
            break
    cleaned = re.sub(
        r"^(?:请|帮我|为我|给我)?(?:再)?"
        r"(?:新增|追加|补充|加上|新生成|再生成|继续生成|继续写|续写|接着写|再写|往后写|扩展|扩写)"
        r"(?:一个|一篇|一段|一份|几)?(?:新)?(?:页面|一页|几页|多页|章节|一节|几节|内容)?",
        "",
        cleaned,
    )
    cleaned = cleaned.strip(" ：:，,。？?！!")
    return cleaned or requirements.theme or "补充内容"


def _fallback_append_section_html(
    *,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    selected_reference: ResourceReferenceContext | None,
) -> str:
    topic = _append_section_topic(request.message, requirements)
    escaped_topic = html.escape(topic)
    if selected_reference:
        chapter_title = html.escape(selected_reference.chapter_title or topic)
        resource_name = html.escape(selected_reference.resource_name)
        summary = html.escape(selected_reference.summary or topic)
        teaching_points = [
            point
            for point in selected_reference.teaching_points
            if point.strip() and "不要照搬原文" not in point
        ][:5]
        if not teaching_points:
            teaching_points = [selected_reference.summary or topic]
        point_items = "\n".join(f"<li>{html.escape(point)}</li>" for point in teaching_points)
        chunks = [chunk for chunk in selected_reference.chunks if chunk.excerpt.strip()][:4]
        chunk_html = "\n".join(
            (
                f"<p><strong>{html.escape(chunk.title)}：</strong>"
                f"{html.escape(chunk.excerpt.strip())}</p>"
            )
            for chunk in chunks
        )
        if not chunk_html:
            chunk_html = f"<p>{summary}</p>"
        return f"""
<h2>补充章节：{escaped_topic}</h2>
<p>这一章接在当前板书末尾，用《{resource_name}》里的《{chapter_title}》作为主要依据。目标不是简单贴一段资料，而是把新章节写成可以直接讲、可以继续做笔记的一整段讲义。</p>
<h3>一、本章先解决什么问题</h3>
<p>{summary}</p>
<p>读这一章时，先不要急着记所有细节。先抓住它在当前课程里的位置：它补上了前文还没有展开的概念、方法或应用场景，让原来的板书可以继续往下讲。</p>
<h3>二、关键概念与讲解顺序</h3>
<ol>
{point_items}
</ol>
<p>课堂上可以按“问题入口 -> 关键概念 -> 例子 -> 检查问题”的顺序推进。每个概念都要回答两个问题：它解决什么困难，以及不用它会在哪里卡住。</p>
<h3>三、关键内容讲解</h3>
{chunk_html}
<p>这些片段要转化成板书语言：先把术语放进一句清楚的话，再用例子说明它如何发生，最后指出它和当前 lesson 前文的连接点。</p>
<h3>四、最小例子</h3>
<p>可以设计一个最小例子来检验理解：只保留本章最关键的对象、条件和结论。先让学生说出“已知什么、要判断什么、使用哪条规则”，再补上推理过程。</p>
<p>如果学生只能背结论，却说不出条件为什么重要，就说明这一章还没有真正接上前面的板书，需要回到关键概念重新解释。</p>
<h3>五、练习与检查</h3>
<ol>
<li>用一句话概括本章最核心的问题。</li>
<li>从上面的关键概念中选一个，说明它在例子里承担什么作用。</li>
<li>写出一个容易误用本章方法的场景，并说明怎样避免。</li>
</ol>
<h3>六、参考答案与小结</h3>
<p>参考答案不要求逐字一致，但必须包含三件事：本章要解决的问题、关键概念之间的关系、以及一个可验证的使用条件。最后把它接回当前板书：这一章让我们从“知道一个概念”往前走到“知道什么时候用、怎么检查是否用对”。</p>
""".strip()

    if "过拟合" in topic or "过拟合" in request.message:
        return """
<h2>补充章节：如何解决过拟合</h2>
<p>过拟合指模型在训练数据上表现很好，但一换到新数据、验证集或真实市场环境就明显变差。它本质上不是“学得太好”，而是把训练样本里的噪声、偶然模式也当成了稳定规律。</p>
<p>这一章接在前面的量化数学工具之后，专门回答一个现实问题：模型、回归、机器学习方法看起来很强，为什么真正交易或换一批数据后会失效？答案往往不是公式错了，而是研究流程没有把“样本内好看”和“样本外可靠”分清楚。</p>
<h3>一、先判断是不是过拟合</h3>
<p>最直接的信号是训练集效果持续变好，但验证集或测试集效果停滞甚至变差。在量化研究里，还要特别警惕回测收益很好、换时间段或换市场后效果迅速消失。</p>
<p>判断时不要只看一个总收益数字。更稳的做法是同时看训练集、验证集、测试集和样本外数据：如果训练集指标不断变漂亮，验证集却开始走弱，就说明模型可能正在记住历史噪声。</p>
<h3>二、常用解决方法</h3>
<ol>
<li><strong>降低模型复杂度：</strong>少用不必要的特征、减少模型层数或参数，让模型先抓稳定主线。</li>
<li><strong>加入正则化：</strong>用 L1、L2、权重衰减等方式惩罚过大的参数，避免模型为了贴合训练集而变得太弯。</li>
<li><strong>做交叉验证：</strong>把数据分成多段反复验证，确认效果不是只在某一次切分里碰巧成立。</li>
<li><strong>增加或清洗数据：</strong>更多样本能降低偶然性；清理异常值和泄漏特征能减少假规律。</li>
<li><strong>早停与独立测试：</strong>验证集开始变差时停止训练，并保留从未参与调参的测试集做最后检查。</li>
</ol>
<h3>三、量化金融里的特别提醒</h3>
<p>量化策略最容易把历史行情里的偶然波动误当成规律。检查一个策略时，不只看回测收益，还要看样本外表现、换时间段表现、交易成本后表现，以及参数轻微变化时结果是否稳定。</p>
<p>还要特别注意数据泄漏：如果未来信息在不知不觉中进入了训练特征，回测会看起来非常优秀，但真实交易时这些信息根本拿不到。常见例子包括用到了未来修正后的财报数据、用全样本标准化、或者在调参时反复偷看测试集。</p>
<h3>四、课堂例题</h3>
<p>假设我们用 80 个技术指标预测明天涨跌，训练集准确率 78%，验证集只有 53%，测试集接近随机。这个结果说明模型很可能不是找到了稳定规律，而是把训练区间里的偶然形态记住了。</p>
<p>一个合理改法是：先减少特征，只保留有解释力且不会泄漏未来信息的变量；再用时间序列交叉验证检查不同市场阶段；最后把完全没参与调参的测试集留到最后，只做一次验收。</p>
<h3>五、练习题</h3>
<ol>
<li>如果训练集收益率很高、样本外收益率很低，第一反应应该检查什么？</li>
<li>L1、L2 正则化分别在直觉上起什么作用？</li>
<li>为什么量化策略不能只看回测收益，还要看交易成本和参数稳定性？</li>
</ol>
<h3>六、参考答案与小结</h3>
<p>第一题先检查数据泄漏、模型复杂度和样本划分方式。第二题可以理解为：L1 更倾向于让部分特征权重变成 0，帮助做特征选择；L2 更倾向于压小过大的权重，让模型平滑一些。第三题的关键在于：真实交易不是历史表演，成本、滑点、换时间段和轻微参数变化都会暴露策略是否只是在拟合过去。</p>
<p>小结一句话：解决过拟合，就是让模型少记偶然噪声，多抓可迁移规律；少追求样本内漂亮，多追求样本外可靠。</p>
""".strip()

    return f"""
<h2>补充章节：{escaped_topic}</h2>
<p>这一章承接当前板书，专门回答“{escaped_topic}”这个新问题。它不是在原文后面补一句提示，而是要把一个新的教学单元完整铺开：先说明问题从哪里来，再给出核心概念、操作方法、例子、练习和小结。</p>
<h3>一、本章要解决的问题</h3>
<p>学习“{escaped_topic}”时，先问一句：前面的板书已经讲到哪里，为什么还需要继续往后写？通常是因为前文只解决了定义或基本直觉，而这一章要补上应用条件、判断方法或更深入的解释。</p>
<p>因此，本章的入口不是堆术语，而是把新内容放回原有主线：它解决前文留下的哪个疑问？它让学生多掌握了哪一种判断或操作能力？</p>
<h3>二、核心概念和前文的连接</h3>
<p>可以把这一章拆成三层：第一层是关键词，也就是必须认识的概念；第二层是关系，也就是这些概念如何互相影响；第三层是使用场景，也就是什么时候该用它、什么时候不该用它。</p>
<p>讲解时要不断提醒学生：新章节不是另起炉灶，而是在当前 lesson 后面继续生长出来的内容。每个新概念都应该能找到一个和前文相连的理由。</p>
<h3>三、最小例子</h3>
<p>为了让“{escaped_topic}”不只停留在抽象描述上，可以设计一个最小例子：只保留一个主要对象、一个限制条件和一个需要判断的问题。先让学生说出已知条件，再让学生选择要用的概念，最后说明为什么这样做是合理的。</p>
<p>如果这个例子讲得通，再逐步加复杂度；如果最小例子都讲不通，就说明这一章还需要回到定义和直觉，不能急着进入综合应用。</p>
<h3>四、方法步骤</h3>
<ol>
<li><strong>先定位问题：</strong>明确这一章补的是定义、方法、例题、应用还是误区。</li>
<li><strong>再给出规则：</strong>用一两句话说清核心判断标准，避免直接堆长段解释。</li>
<li><strong>接着演示例子：</strong>把规则放进具体场景，说明每一步为什么成立。</li>
<li><strong>最后做检查：</strong>用一个反例或变式题确认学生不是只记住了表面词语。</li>
</ol>
<h3>五、常见误区</h3>
<p>第一个误区是只记结论，不看条件。很多知识点只有在特定前提下才成立，离开条件就会用错。第二个误区是把例子当定义，看到不同题型就不会迁移。第三个误区是把新章节和前文割裂，导致板书越写越散。</p>
<h3>六、课堂练习</h3>
<ol>
<li>用一句话说明“{escaped_topic}”和前文内容的连接点。</li>
<li>写出这一章最关键的一个概念，并说明它解决什么问题。</li>
<li>构造一个最小例子：包含已知条件、要判断的问题、使用的方法和结论。</li>
</ol>
<h3>七、参考答案与小结</h3>
<p>参考答案的重点不是措辞一致，而是结构完整：先说连接点，再说核心概念，最后说明使用条件。小结一句话：这一章的价值在于把原来的板书继续往前推进，让学生从“听懂前面的解释”走到“能判断、能使用、能检查”。</p>
""".strip()


def _fallback_section_followup_append_html(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> str:
    context = _active_section_followup_context(lesson)
    if context is None:
        return _fallback_append_section_html(
            request=request,
            requirements=requirements,
            selected_reference=None,
        )
    section_number, section_title, section_excerpt = context
    labels = [
        need
        for need in requirements.learning_need_checklist
        if re.match(rf"^\s*{section_number}\.\d+\b", need)
    ]
    topics = _section_followup_topics(request.message) or [request.board_edit_topic or request.message]
    first_label_match = re.match(r"^\s*(\d+\.\d+)\s*(?:追问补充[：:]?)?\s*(.*?)(?:（|$)", labels[0]) if labels else None
    child_label = first_label_match.group(1) if first_label_match else f"{section_number}.{_next_section_child_index(requirements.learning_need_checklist, section_number)}"
    child_topic = (
        first_label_match.group(2).strip(" ：:，,。")
        if first_label_match and first_label_match.group(2).strip()
        else topics[0]
    )
    escaped_label = html.escape(child_label)
    escaped_topic = html.escape(child_topic)
    escaped_section_title = html.escape(section_title)
    escaped_excerpt = html.escape(section_excerpt[:360])
    brief = html.escape(
        _supplemental_teacher_brief(
            request_message=request.message,
            section_number=section_number,
            section_title=section_title,
            topics=topics,
        )
    )
    return f"""
<h2>{escaped_label} {escaped_topic}</h2>
<p>这一节是从第 {section_number} 小节《{escaped_section_title}》讲解过程中自然长出的追问补充。原小节先讲到：{escaped_excerpt}</p>
<p>{brief}</p>
<h3>一、先回答这个新问题</h3>
<p>先把结论说清楚，再说明它为什么会从原小节里冒出来。讲师应避免只说“这是新知识”，而要把它和前面的定义、规则或例子接起来。</p>
<h3>二、和原小节的连接点</h3>
<p>这类追问适合放成 {escaped_label}，因为它不是完全换课题，而是在原小节基础上补一个边界、反例或更高一级的概念。学习时先记住：新内容服务于理解原小节，不是把主线打散。</p>
<h3>三、检查问题</h3>
<p>你能用一句话说明“{escaped_topic}”和第 {section_number} 小节《{escaped_section_title}》之间的关系吗？如果能说出连接点，就说明这个子章节已经接回主线。</p>
""".strip()


def _plain_text_from_html_fragment(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _fallback_expansion_paragraph(source_text: str) -> str:
    cleaned = " ".join(source_text.split()).strip()
    if any(marker in cleaned for marker in ("例如", "比如", "例子", "案例")):
        detail = (
            "这个例子可以拆成三层来讲：先标出已知条件和要解决的问题，再说明每一步为什么这样推，"
            "最后补一个相近但不完全一样的变式，让学生确认自己理解的是方法而不是记住表面词语。"
        )
    else:
        detail = (
            "这句话可以继续展开：先说明它在本节里回答什么问题，再补出关键条件、推理过程和一个可检查的小例子，"
            "让原来的结论从“看得懂”变成“能讲清、能迁移”。"
        )
    return f"\n<p><strong>展开说明：</strong>{html.escape(detail)}</p>"


def _fallback_expand_existing_document(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> BoardDocument:
    source_html = lesson.board_document.content_html.strip()
    if not source_html:
        source_html = f"<h1>{html.escape(lesson.board_document.title)}</h1><p>{html.escape(lesson.board_document.content_text)}</p>"

    block_re = re.compile(r"<(?P<tag>p)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>", re.IGNORECASE | re.DOTALL)
    parts: list[str] = []
    last_end = 0
    expanded_count = 0
    for match in block_re.finditer(source_html):
        parts.append(source_html[last_end : match.end()])
        last_end = match.end()
        paragraph_text = _plain_text_from_html_fragment(match.group("body"))
        if len(paragraph_text) < 6:
            continue
        parts.append(_fallback_expansion_paragraph(paragraph_text))
        expanded_count += 1

    parts.append(source_html[last_end:])
    if expanded_count == 0:
        parts.append(
            "\n<p><strong>展开说明：</strong>"
            "围绕当前板书继续补充定义、条件、例子拆解和检查问题，把原有内容讲得更细，而不是另起一个补充章节。"
            "</p>"
        )

    return build_document(
        title=lesson.board_document.title or requirements.theme,
        content_html="".join(parts),
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )


def _fallback_document_update(
    *,
    lesson: Lesson,
    request: ChatRequest,
    decision: BoardDecision,
    requirements: LearningRequirementSheet,
    selected_reference: ResourceReferenceContext | None,
) -> BoardDocument:
    if request.selection and request.interaction_mode == "direct_edit" and not _is_full_rewrite_request(request.message):
        replacement_text = _merge_selection_edit(
            selection_text=request.selection.excerpt,
            generated_text=_fallback_selection_replacement(request),
            request_message=request.message,
        )
        return replace_selection_in_document(
            lesson.board_document,
            selection_text=request.selection.excerpt,
            replacement_text=replacement_text,
        )

    if (
        decision.action == "edit_board"
        and _is_in_place_expansion_request(request.message)
        and not is_document_empty(lesson.board_document)
    ):
        return _fallback_expand_existing_document(
            lesson=lesson,
            request=request,
            requirements=requirements,
        )

    if decision.action == "append_section":
        if selected_reference is None and (
            _is_section_followup_learning_need(lesson, request)
            or _is_confirmed_section_followup_learning_need(lesson, request)
        ):
            section_html = _fallback_section_followup_append_html(
                lesson=lesson,
                request=request,
                requirements=requirements,
            )
        else:
            section_html = _fallback_append_section_html(
                request=request,
                requirements=requirements,
                selected_reference=selected_reference,
            )
        return append_html_section(lesson.board_document, section_html)

    topic = (
        selected_reference.chapter_title
        if selected_reference is not None
        else (_extract_generation_topic_hint(request.message) or requirements.theme or lesson.title)
    )
    generated = create_lesson(
        topic,
        requirements=requirements,
        reference_context=selected_reference,
    )
    return generated.board_document.model_copy(update={"id": lesson.board_document.id})


def _board_snapshot_hash(document: BoardDocument) -> str:
    payload = f"{document.id}\n{document.title}\n{document.content_text.strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _requirement_needs(requirements: LearningRequirementSheet) -> list[str]:
    candidates = [
        *requirements.learning_need_checklist,
        *reversed(requirements.current_questions[-2:]),
        requirements.learning_goal,
        requirements.target_depth,
    ]
    needs: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = " ".join(str(candidate).split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        needs.append(cleaned)
    return needs[:4]


def _board_segments(document: BoardDocument) -> list[tuple[str | None, str]]:
    if document.content_html.strip():
        segments: list[tuple[str | None, str]] = []
        current_heading: str | None = document.title
        for match in _TEACHING_BLOCK_RE.finditer(document.content_html):
            tag = match.group("tag").lower()
            text = _plain_text_from_html_fragment(match.group("body"))
            if not text:
                continue
            if tag.startswith("h"):
                current_heading = text
                continue
            segments.append((current_heading, text))
        if segments:
            return segments

    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    segments: list[tuple[str | None, str]] = []
    current_heading: str | None = document.title
    for line in lines:
        is_heading = (
            len(line) <= 32
            and not re.search(r"[。！？.!?：:；;，,]", line)
            and not re.match(r"^[-*]|^\d+[.)）]", line)
        )
        if is_heading:
            current_heading = line
            continue
        segments.append((current_heading, line))
    if not segments and document.content_text.strip():
        segments.append((document.title, document.content_text.strip()))
    return segments


_TEACHING_BLOCK_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|li|blockquote)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


def _compact_teaching_line(value: str, limit: int = 160) -> str:
    cleaned = " ".join(value.split()).strip(" ：:，,。！？!?；;")
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1]}…"


def _dedupe_teaching_lines(values: list[str], *, limit: int = 4) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _compact_teaching_line(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        lines.append(cleaned)
        if len(lines) >= limit:
            break
    return lines


def _is_low_value_section_teaching_line(value: str) -> bool:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return True
    if cleaned.startswith(("适用水平：", "学习定位：", "学习目标：", "讲解节奏：", "内部讲义：", "学习需求：", "讲解方式：")):
        return True
    if "每次只讲一个小节" in cleaned:
        return True
    if "不要按板书顺序念" in cleaned:
        return True
    return False


def _board_h2_sections(document: BoardDocument) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in _TEACHING_BLOCK_RE.finditer(document.content_html or ""):
        text = _plain_text_from_html_fragment(match.group("body"))
        if text:
            blocks.append((match.group("tag").lower(), text))

    sections: list[tuple[str, list[str]]] = []
    prelude: list[str] = []
    current_heading: str | None = None
    current_parts: list[str] = []

    for tag, text in blocks:
        if tag == "h2":
            if current_heading is not None:
                sections.append((current_heading, current_parts))
            current_heading = text
            current_parts = list(prelude)
            prelude = []
            continue
        if current_heading is None:
            if tag != "h1":
                prelude.append(text)
            continue
        current_parts.append(text)

    if current_heading is not None:
        sections.append((current_heading, current_parts))

    if sections:
        return [
            (heading, "\n".join(part for part in parts if part.strip()).strip())
            for heading, parts in sections
        ]

    text = document.content_text.strip() or html_to_text(document.content_html)
    if not text:
        text = document.title
    return [(document.title or "本节内容", text)]


def _fallback_section_plans(
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> list[BoardSectionTeachingPlan]:
    sections = _board_h2_sections(document)
    plans: list[BoardSectionTeachingPlan] = []
    for index, (heading, body) in enumerate(sections):
        candidate_points = [
            line
            for line in body.splitlines()
            if line.strip() and line.strip() != heading and not _is_low_value_section_teaching_line(line)
        ]
        check_line = next((line for line in candidate_points if re.match(r"^\s*(?:检查点|检查问题|练习|思考题)[：:]", line)), "")
        core_candidates = [line for line in candidate_points if line != check_line]
        core_points = _dedupe_teaching_lines(core_candidates, limit=3) or [
            f"这一节围绕“{heading}”建立核心理解。"
        ]
        cleaned_check = re.sub(r"^\s*(?:检查点|检查问题|练习|思考题)[：:]\s*", "", check_line).strip()
        excerpt_source = body or "\n".join(core_points)
        plans.append(
            BoardSectionTeachingPlan(
                order_index=index,
                heading=heading,
                board_excerpt=_compact_teaching_line(excerpt_source, limit=420),
                core_points=core_points,
                teaching_steps=[
                    f"先说明“{heading}”在本课里解决什么问题。",
                    "再按板书顺序拆开核心概念、关键条件和推理关系。",
                    "最后用一个例子或检查问题确认学生是否真的听懂。",
                ],
                teaching_method=(
                    f"用“问题入口 -> 核心解释 -> 小例子 -> 检查问题”的方式讲，不照读板书。"
                    f"学习目标要贴住：{requirements.learning_goal}"
                ),
                example_or_analogy=(
                    f"可以把“{heading}”放进一个最小例子里讲：先给已知条件，再说明怎么判断或使用。"
                ),
                common_pitfalls=["不要只复述标题；要说明条件、边界和容易混淆的点。"],
                check_question=cleaned_check or f"你能用一句话说出“{heading}”这一节最核心的意思吗？",
                transition_to_next="如果这一节能跟上，就继续进入下一小节。",
            )
        )

    for index, plan in enumerate(plans[:-1]):
        next_title = plans[index + 1].heading
        plan.transition_to_next = f"如果这一节能跟上，下一节就接着讲“{next_title}”。"
    if plans:
        plans[-1].transition_to_next = "这是这份板书的最后一个小节，讲完后可以回到具体问题或练习。"
    return plans


def _needs_for_excerpt(excerpt: str, needs: list[str]) -> list[str]:
    excerpt_lower = excerpt.lower()
    scored: list[tuple[int, str]] = []
    for need in needs:
        terms = _query_phrases(need)
        score = sum(1 for term in terms if term in excerpt_lower)
        scored.append((score, need))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [need for score, need in scored if score > 0][:2]
    return selected or needs[:1]


def _compact_instruction_text(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _is_low_value_teaching_excerpt(excerpt: str, request_message: str) -> bool:
    cleaned = " ".join(excerpt.split()).strip()
    compact_excerpt = _compact_instruction_text(cleaned)
    compact_request = _compact_instruction_text(request_message)
    if not compact_excerpt:
        return True
    if compact_request and (
        compact_excerpt == compact_request
        or (compact_excerpt in compact_request and len(compact_excerpt) >= 8)
        or (compact_request in compact_excerpt and len(compact_excerpt) <= len(compact_request) + 16)
    ):
        return True
    if any(marker in cleaned for marker in ("用户当前追问", "当前追问", "原有主线", "新问题接回", "专门承接")):
        return True
    if cleaned.startswith(("学习定位：", "讲解节奏：")) or "每次只讲一个小节" in cleaned:
        return True
    if re.match(r"^(?:请|帮我|为我|给我)?(?:续写|继续写|接着写|新增|追加|补充)", cleaned) and len(cleaned) <= 48:
        return True
    if re.match(r"^(?:补充|新增|追加)?章节[：:]", cleaned) and len(cleaned) <= 28:
        return True
    if cleaned in {"补充章节", "新增章节", "追加章节"}:
        return True
    return False


def _is_statistical_learning_reference_context(reference: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference.chapter_title,
            reference.summary,
            *reference.teaching_points,
            *(chunk.excerpt for chunk in reference.chunks[:4]),
            reference.full_text[:3000],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    return (
        ("统计学习理论" in compact or "statisticallearning" in compact)
        and any(term in compact for term in ("经验风险", "真实风险", "期望风险", "vc", "一致性", "推广能力"))
    )


def _is_density_estimation_reference_context(reference: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference.chapter_title,
            reference.summary,
            *reference.teaching_points,
            *(chunk.excerpt for chunk in reference.chunks[:4]),
            reference.full_text[:4000],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    return any(
        term in compact
        for term in (
            "概率密度函数的估计",
            "概率密度估计",
            "密度函数估计",
            "类条件概率密度",
        )
    ) and any(term in compact for term in ("最大似然", "贝叶斯", "先验概率", "训练样本", "似然函数"))


def _is_humanities_reference_context(reference: ResourceReferenceContext) -> bool:
    corpus = " ".join(
        [
            reference.resource_name,
            reference.chapter_title,
            reference.summary,
            *reference.teaching_points,
            *(chunk.excerpt for chunk in reference.chunks[:4]),
            reference.full_text[:2600],
        ]
    )
    compact = re.sub(r"\s+", "", corpus).lower()
    technical_markers = ("统计学习理论", "模式识别", "机器学习", "算法", "公式", "定理", "计算机", "vc")
    if any(marker.lower() in compact for marker in technical_markers):
        return False
    humanities_markers = (
        "文科",
        "文学",
        "历史",
        "哲学",
        "政治",
        "法律",
        "社会",
        "文化",
        "教育",
        "伦理",
        "艺术",
        "语文",
        "制度",
        "思想",
        "观点",
        "论证",
        "叙事",
        "人物",
        "原因",
        "影响",
        "意义",
        "评价",
        "改革",
        "变法",
        "革命",
        "战争",
    )
    return sum(1 for marker in humanities_markers if marker in compact) >= 2


def _humanities_teacher_brief(reference: ResourceReferenceContext) -> str:
    first_chunk = next((chunk.excerpt for chunk in reference.chunks if chunk.excerpt.strip()), reference.summary)
    excerpt = " ".join(first_chunk.split())[:180]
    return (
        f"《{reference.chapter_title}》不要只讲成提纲，先抓材料里的重要内容细讲。"
        f"可以从这句话入手：{excerpt}。"
        "讲法是：先解释材料原意，再补背景和上下文，然后把原因、过程、影响或论证链条拆开。"
        "如果是文学文本，就抓关键词、叙述角度和细节如何塑造主题；如果是历史/政治/法律材料，就抓事件背景、制度变化、利益关系和长期影响。"
        "最后用一个检查问题收束：学生能不能拿材料里的具体证据，而不是空泛套话，说明这个观点为什么成立。"
    )


def _reference_teacher_brief(reference: ResourceReferenceContext) -> str:
    if _is_statistical_learning_reference_context(reference):
        return (
            f"《{reference.chapter_title}》先抓一件事：训练误差小不等于测试误差小。"
            "经验风险是在训练集上算出来的错误，真实风险是模型面对未来未知样本时的平均错误；"
            "统计学习理论要回答的，就是经验风险什么时候能代表真实风险。"
            "所以讲课顺序可以这样走：先区分经验风险和真实风险，再用过拟合说明只追求训练误差会出问题，"
            "然后引出一致性、函数集容量和 VC 维，最后落到推广能力界、SVM 最大间隔和正则化。"
            "一句话总结：本章不是教某个算法，而是解释机器学习为什么需要控制复杂度，才能有推广能力。"
        )
    if _is_density_estimation_reference_context(reference):
        return (
            f"《{reference.chapter_title}》先抓一条主线：贝叶斯决策需要先验概率和类条件概率密度，"
            "但真实任务里这些概率通常未知，所以要先从训练样本中估计出来，再把估计结果代回分类器。"
            "讲课顺序按十个小章展开：本章定位、先验概率与类条件密度、两步贝叶斯决策、参数估计、"
            "最大似然估计、贝叶斯估计、非参数估计、估计质量、从估计回到分类器、最后用误区和检查题收束。"
            "重点不是复述教材片段，而是把“为什么要估计、怎样估计、估计误差怎样影响分类边界”讲成一条完整链路。"
        )
    if _is_humanities_reference_context(reference):
        return _humanities_teacher_brief(reference)

    corpus = " ".join(
        [
            reference.chapter_title,
            reference.summary,
            *reference.teaching_points,
            *(chunk.excerpt for chunk in reference.chunks[:4]),
        ]
    )
    compact = re.sub(r"\s+", "", corpus)
    compact_title = re.sub(r"\s+", "", reference.chapter_title)
    if (
        ("概论" in compact_title or "第一章" in compact_title or "第1章" in compact_title)
        and "模式识别" in compact
        and any(term in compact for term in ("监督", "非监督", "分类器", "聚类"))
    ):
        return (
            f"《{reference.chapter_title}》先抓一条主线：模式识别不是先讲某个算法，"
            "而是先把现实对象变成可观察的特征，再根据特征做分类或聚类。"
            "有已知类别样本时训练分类器，这是监督模式识别；没有标签时按相似性发现结构，这是非监督模式识别。"
            "换句话说，模式是要识别的对象，特征是描述对象的线索，分类器是根据这些线索做判断的规则。"
            "最后把它放进系统流程：信息获取与预处理、特征提取与选择、分类器设计或聚类分析、分类决策或结果解释。"
        )
    first_chunk = next(
        (
            chunk.excerpt
            for chunk in reference.chunks
            if chunk.excerpt.strip() and "目录顺序" not in chunk.excerpt
        ),
        "",
    )
    if first_chunk:
        return f"《{reference.chapter_title}》先抓这条主线：{first_chunk[:220]}"
    if reference.teaching_points:
        return f"《{reference.chapter_title}》先抓这条主线：{reference.teaching_points[0]}"
    first_chunk = next((chunk.excerpt for chunk in reference.chunks if chunk.excerpt.strip()), reference.summary)
    return f"《{reference.chapter_title}》先抓这条主线：{first_chunk[:140]}"


def _fallback_empty_board_teaching_excerpt(
    *,
    requirements: LearningRequirementSheet,
    request_message: str,
    document_title: str,
) -> str:
    topic = requirements.theme.strip()
    question_sources = [
        question.strip().replace("什么事", "什么是")
        for question in reversed(requirements.current_questions)
        if question.strip() and topic and topic in question
    ]
    question_sources.extend(
        question.strip().replace("什么事", "什么是")
        for question in reversed(requirements.current_questions)
        if question.strip() and not _is_low_information_request(question)
    )
    for question in dict.fromkeys(question_sources):
        if question:
            return question[:240]
    return (request_message.strip().replace("什么事", "什么是") or topic or document_title)[:240]


def _fallback_board_teaching_guide(
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    request_message: str,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    needs = _requirement_needs(requirements)
    if selected_reference is not None:
        brief = _reference_teacher_brief(selected_reference)
        reference_handout_document = create_lesson(
            selected_reference.chapter_title or requirements.theme or document.title,
            requirements=requirements,
            reference_context=selected_reference,
        ).board_document
        reference_handout = reference_handout_document.content_text.strip()
        lecture_handout = "\n".join(
            [
                f"内部讲义：《{selected_reference.chapter_title}》",
                f"学习需求：{'；'.join(needs[:3])}",
                f"讲解主线：{brief}",
                reference_handout,
                "讲解方式：先把材料中的核心问题说清楚，再解释关键概念和关系，最后用一个例子或检查问题收束。",
            ]
        )
        if _is_density_estimation_reference_context(selected_reference):
            selected_items = [
                BoardTeachingSelectedItem(
                    excerpt=brief[:420],
                    source_heading=selected_reference.chapter_title,
                    reason="用户明确指定了资料章节，先把参考章节压缩成可直接开讲的主线。",
                    mapped_needs=needs[:1],
                    teaching_role="main_idea",
                    order_index=1,
                ),
                BoardTeachingSelectedItem(
                    excerpt="贝叶斯分类器需要先验概率 P(w_i) 和类条件概率密度 p(x|w_i)；第三章的核心是从训练样本中估计这些概率，再用于判别。",
                    source_heading="先验概率与类条件概率密度",
                    reason="这能解释本章为什么要从第二章的理论判别规则进入概率估计问题。",
                    mapped_needs=needs[:1],
                    teaching_role="why_it_matters",
                    order_index=2,
                ),
                BoardTeachingSelectedItem(
                    excerpt="最大似然估计的直觉是：样本已经出现了，选择最能解释这批样本的参数；贝叶斯估计则进一步把先验知识放进参数更新。",
                    source_heading="最大似然估计与贝叶斯估计",
                    reason="这能承接本章最重要的两条参数估计路线，避免直接照读 OCR 片段。",
                    mapped_needs=needs[:1],
                    teaching_role="example",
                    order_index=3,
                ),
            ]
        else:
            selected_items = [
                BoardTeachingSelectedItem(
                    excerpt=brief[:420],
                    source_heading=selected_reference.chapter_title,
                    reason="用户明确指定了资料章节，先把参考章节压缩成可直接开讲的主线。",
                    mapped_needs=needs[:1],
                    teaching_role="main_idea",
                    order_index=1,
                ),
                *[
                    BoardTeachingSelectedItem(
                        excerpt=(chunk.excerpt or selected_reference.summary)[:420],
                        source_heading=chunk.title,
                        reason="这段来自已锁定参考章节，适合补充定义、例子或系统流程。",
                        mapped_needs=needs[:1],
                        teaching_role="why_it_matters" if index == 2 else "example",
                        order_index=index,
                    )
                    for index, chunk in enumerate(selected_reference.chunks[:2], start=2)
                ],
            ]
        if len(selected_items) == 1 and selected_reference.summary.strip():
            selected_items.append(
                BoardTeachingSelectedItem(
                    excerpt=selected_reference.summary[:420],
                    source_heading=selected_reference.chapter_title,
                    reason="当前参考章节缺少细分片段，使用章节摘要补足讲解依据。",
                    mapped_needs=needs[:1],
                    teaching_role="why_it_matters",
                    order_index=2,
                )
            )
        selected_items = [
            item
            for item in selected_items
            if item.excerpt.strip() and not _is_low_value_teaching_excerpt(item.excerpt, request_message)
        ] or [
            BoardTeachingSelectedItem(
                excerpt=brief[:420],
                source_heading=selected_reference.chapter_title,
                reason="用户明确指定了资料章节，先用章节主线开讲。",
                mapped_needs=needs[:1],
                teaching_role="main_idea",
                order_index=1,
            )
        ]
        need_mappings = [
            BoardNeedMapping(
                need=need,
                matched_excerpt=selected_items[0].excerpt,
                source_heading=selected_items[0].source_heading,
                rationale="当前优先围绕已锁定的教材章节直接开讲，先满足核心学习需求。",
            )
            for need in needs[:3]
        ]
        return BoardTeachingGuide(
            board_document_id=document.id,
            board_snapshot_hash=_board_snapshot_hash(document),
            board_title=document.title,
            selected_items=selected_items,
            need_mappings=need_mappings,
            teaching_flow=[
                f"先根据《{selected_reference.resource_name}》的《{selected_reference.chapter_title}》讲主线。",
                "再解释这一节为什么重要、它解决什么问题。",
                "最后给一个例子、类比或检查问题。",
            ],
            generation_rationale="用户明确指定了教材章节且要求直接开讲，因此在不改板书正文的前提下，优先使用已锁定参考章节的核心片段组织讲解。",
            teacher_brief=brief,
            lecture_handout=lecture_handout,
            section_plans=_fallback_section_plans(reference_handout_document, requirements),
        )

    focus_terms = {term.lower() for term in _query_phrases(f"{request_message}\n{requirements.learning_goal}")}
    scored_segments: list[tuple[int, str | None, str]] = []
    low_value_segments: list[tuple[int, str | None, str]] = []
    for heading, excerpt in _board_segments(document):
        corpus = f"{heading or ''}\n{excerpt}".lower()
        score = sum(1 for term in focus_terms if term in corpus)
        if _is_low_value_teaching_excerpt(excerpt, request_message):
            low_value_segments.append((score - 4, heading, excerpt))
            continue
        if "过拟合" in request_message and any(
            term in excerpt for term in ("训练数据", "训练集", "验证集", "测试集", "正则", "交叉验证", "早停", "样本外", "数据泄漏")
        ):
            score += 3
        scored_segments.append((score, heading, excerpt))
    scored_segments.sort(key=lambda item: item[0], reverse=True)
    low_value_segments.sort(key=lambda item: item[0], reverse=True)

    if is_document_empty(document) and requirements.theme and requirements.theme != document.title:
        chosen = [
            (
                0,
                requirements.theme,
                _fallback_empty_board_teaching_excerpt(
                    requirements=requirements,
                    request_message=request_message,
                    document_title=document.title,
                ),
            )
        ]
    else:
        chosen = (
            scored_segments[:3]
            if scored_segments
            else (low_value_segments[:3] if low_value_segments else [(0, document.title, document.content_text.strip() or document.title)])
        )
    selected_items: list[BoardTeachingSelectedItem] = []
    for index, (_, heading, excerpt) in enumerate(chosen, start=1):
        mapped_needs = _needs_for_excerpt(excerpt, needs)
        role = ["main_idea", "why_it_matters", "example"][min(index - 1, 2)]
        selected_items.append(
            BoardTeachingSelectedItem(
                excerpt=excerpt[:240],
                source_heading=heading,
                reason=f"这段和用户当前问题及学习目标重合度最高，适合作为第 {index} 个讲解重点。",
                mapped_needs=mapped_needs,
                teaching_role=role,
                order_index=index,
            )
        )

    need_mappings: list[BoardNeedMapping] = []
    for need in needs[:3]:
        matched = next(
            (item for item in selected_items if need in item.mapped_needs),
            selected_items[0],
        )
        need_mappings.append(
            BoardNeedMapping(
                need=need,
                matched_excerpt=matched.excerpt,
                source_heading=matched.source_heading,
                rationale="优先把最能直接回应该学习需求的板书内容拿出来讲。",
            )
        )

    first = selected_items[0]
    flow = [
        f"先用“{first.excerpt[:28]}”带出主线，不照读定义。",
        "再解释这件事为什么重要，和用户当前目标有什么关系。",
    ]
    if len(selected_items) > 1:
        flow.append(f"然后接到“{selected_items[1].excerpt[:24]}”补充原因或关键关系。")
    if len(selected_items) > 2:
        flow.append(f"最后用“{selected_items[2].excerpt[:24]}”做例子、提醒或检查点。")

    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=_board_snapshot_hash(document),
        board_title=document.title,
        selected_items=selected_items,
        need_mappings=need_mappings,
        teaching_flow=flow,
        generation_rationale="优先挑选与用户当前追问、学习目标和板书主线同时重合的内容，先讲主线，再讲原因，最后落到例子或检查点。",
        teacher_brief=(
            f"这次先抓“{first.excerpt[:36]}”这条主线，"
            "不要按板书顺序念，而是先讲它在解决什么问题，再补一个例子或检查问题。"
        ),
        lecture_handout=_fallback_lecture_handout(
            document=document,
            requirements=requirements,
            request_message=request_message,
            selected_items=selected_items,
        ),
        section_plans=_fallback_section_plans(document, requirements),
    )


def _fallback_lecture_handout(
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    request_message: str,
    selected_items: list[BoardTeachingSelectedItem],
) -> str:
    needs = _requirement_needs(requirements)
    excerpts = [item.excerpt.strip() for item in selected_items if item.excerpt.strip()]
    if not excerpts and document.content_text.strip():
        first_segment = _board_segments(document)[:1]
        excerpts = [first_segment[0][1]] if first_segment else []
    if excerpts:
        source_lines = excerpts[:3]
    elif document.content_text.strip():
        source_lines = [document.content_text.strip()[:240]]
    else:
        source_lines = [
            _fallback_empty_board_teaching_excerpt(
                requirements=requirements,
                request_message=request_message,
                document_title=document.title,
            )
        ]
    return "\n".join(
        [
            f"内部讲义：{document.title}",
            f"用户问题：{request_message.strip() or requirements.learning_goal}",
            f"学习需求：{'；'.join(needs[:4])}",
            "讲解依据：",
            *[f"- {line}" for line in source_lines if line],
            "讲解顺序：先用一句话说核心概念，再解释它为什么重要，最后给一个例子、类比或检查问题。",
            "注意：这份讲义只供讲师讲解使用，不写入右侧版书。",
        ]
    )


def _bound_board_teaching_guide(
    *,
    guidance: BoardTeachingGuide | None,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    request_message: str,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    fallback = _fallback_board_teaching_guide(
        document=document,
        requirements=requirements,
        request_message=request_message,
        selected_reference=selected_reference,
    )
    if guidance is None:
        return fallback

    payload = guidance.model_dump(mode="json")
    payload["board_document_id"] = document.id
    payload["board_snapshot_hash"] = _board_snapshot_hash(document)
    payload["board_title"] = document.title
    if not payload.get("selected_items"):
        payload["selected_items"] = fallback.selected_items
    if not payload.get("need_mappings"):
        payload["need_mappings"] = fallback.need_mappings
    if not payload.get("teaching_flow"):
        payload["teaching_flow"] = fallback.teaching_flow
    if not payload.get("generation_rationale"):
        payload["generation_rationale"] = fallback.generation_rationale
    if not payload.get("teacher_brief"):
        payload["teacher_brief"] = fallback.teacher_brief
    if not payload.get("lecture_handout"):
        payload["lecture_handout"] = fallback.lecture_handout
    if not payload.get("section_plans"):
        payload["section_plans"] = fallback.section_plans
    return BoardTeachingGuide.model_validate(payload)


def _current_board_teaching_guide(lesson: Lesson, document: BoardDocument) -> BoardTeachingGuide | None:
    target_hash = _board_snapshot_hash(document)
    guidance = lesson.board_teaching_guide
    if guidance and guidance.board_document_id == document.id and guidance.board_snapshot_hash == target_hash:
        return guidance
    for commit in reversed(lesson.history_graph.commits):
        raw = commit.metadata.get("board_teaching_guide") if isinstance(commit.metadata, dict) else None
        if not raw:
            continue
        try:
            candidate = BoardTeachingGuide.model_validate(raw)
        except Exception:
            continue
        if candidate.board_document_id == document.id and candidate.board_snapshot_hash == target_hash:
            return candidate
    return None


def _supplemental_teacher_brief(
    *,
    request_message: str,
    section_number: int,
    section_title: str,
    topics: list[str],
) -> str:
    compact = _compact_instruction_text(request_message)
    if "负数" in compact and any(term in compact for term in ("开方", "平方根", "根号", "被开方")):
        return (
            "核心说明：在实数范围内，负数不能开平方；为了让 x^2 = -1 这类方程有解，"
            "数学引入虚数单位 i，并规定 i^2 = -1。所以当 a > 0 时，√(-a) 可以写成 i√a，"
            "这就进入了复数的入门内容。讲的时候先把“实数范围内无解”和“扩展到复数后有解”分开，"
            f"再接回第 {section_number} 小节《{section_title}》里的开方规则。"
        )
    topic_text = "；".join(topics) or request_message.strip()
    return (
        f"核心说明：这次追问“{topic_text}”是第 {section_number} 小节《{section_title}》的扩展学习需求。"
        "讲师要先直接回答用户的问题，再说明它和当前板书小节的连接点；如果它已经超出当前板书，"
        "要明确告诉用户这是外延知识，并用一个最小例子或检查问题收束。"
    )


def _supplemental_board_teaching_guide(
    *,
    lesson: Lesson,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    request: ChatRequest,
) -> BoardTeachingGuide:
    context = _active_section_followup_context(lesson)
    if context is None:
        return _fallback_board_teaching_guide(
            document=document,
            requirements=requirements,
            request_message=request.message,
        )
    section_number, section_title, section_excerpt = context
    topics = _section_followup_topics(request.message) or [request.message.strip() or requirements.learning_goal]
    labels = [
        need
        for need in requirements.learning_need_checklist
        if re.match(rf"^\s*{section_number}\.\d+\b", need)
    ]
    if not labels:
        labels = _section_followup_need_items(
            lesson=lesson,
            request=request,
            existing_needs=requirements.learning_need_checklist,
        )
    teacher_brief = _supplemental_teacher_brief(
        request_message=request.message,
        section_number=section_number,
        section_title=section_title,
        topics=topics,
    )
    topic_text = "；".join(topics)
    selected_items = [
        BoardTeachingSelectedItem(
            excerpt=teacher_brief[:420],
            source_heading=f"{section_number}.x 追问补充",
            reason="这条内容是版书 AI 为当前追问临时准备的讲解指导，供讲师先回答用户。",
            mapped_needs=labels[:2] or requirements.learning_need_checklist[:1],
            teaching_role="main_idea",
            order_index=1,
        )
    ]
    if section_excerpt.strip():
        selected_items.append(
            BoardTeachingSelectedItem(
                excerpt=section_excerpt[:360],
                source_heading=section_title,
                reason="当前追问发生在这个小节讲解过程中，讲师需要把新问题接回原小节主线。",
                mapped_needs=requirements.learning_need_checklist[:1],
                teaching_role="why_it_matters",
                order_index=2,
            )
        )

    need_mappings = [
        BoardNeedMapping(
            need=need,
            matched_excerpt=teacher_brief[:240],
            source_heading=f"{section_number}.x 追问补充",
            rationale="PM 已把授课中的新追问登记为当前小节的子学习需求，讲师先按内部讲义回答，是否写入版书由用户确认。",
        )
        for need in (labels or requirements.learning_need_checklist[:2])
    ]
    lecture_handout = "\n".join(
        [
            f"内部讲义：第 {section_number} 小节《{section_title}》追问补充",
            f"用户问题：{request.message.strip()}",
            f"学习需求标签：{'；'.join(labels) if labels else topic_text}",
            teacher_brief,
            f"当前小节依据：{section_excerpt[:260]}",
            "讲解指导：先回答追问，再说明它和当前小节的关系；必要时说明这是外延知识，并提示可确认后扩选到板书子章节。",
            "注意：这份讲义只供讲师讲解使用，不自动写入右侧版书。",
        ]
    )
    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=_board_snapshot_hash(document),
        board_title=document.title,
        selected_items=selected_items,
        need_mappings=need_mappings,
        teaching_flow=[
            f"先回答第 {section_number} 小节讲解中冒出的追问：{topic_text}",
            f"再把答案接回《{section_title}》原来的板书内容。",
            "最后询问是否要把这个扩展沉淀为右侧版书的子章节。",
        ],
        generation_rationale="分节授课过程中出现了新的学习需求，版书 AI 先生成临时讲解指导，不直接改动右侧版书。",
        teacher_brief=teacher_brief,
        lecture_handout=lecture_handout,
        section_plans=_fallback_section_plans(document, requirements),
    )


def _relevant_lines(document: BoardDocument, request: ChatRequest) -> list[str]:
    if request.selection and request.selection.excerpt.strip():
        return [request.selection.excerpt.strip()]
    terms = {term.lower() for term in _extract_focus_terms(request.message)}
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    if not terms:
        return lines[:3]
    scored: list[tuple[int, str]] = []
    for line in lines:
        corpus = line.lower()
        score = sum(1 for term in terms if term in corpus)
        if score:
            scored.append((score, line))
    if not scored:
        return lines[:3]
    return [line for _, line in sorted(scored, key=lambda item: item[0], reverse=True)[:3]]


def _interactive_teaching_guide(
    *,
    lesson_id: str,
    lesson_title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    normalized = normalize_requirements(requirements, lesson_title=lesson_title, document=document)
    return build_teaching_guide(lesson_id, lesson_title, document, normalized)


def _resolve_board_teaching_guide(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    document: BoardDocument,
    prefer_existing: bool,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    existing = _current_board_teaching_guide(lesson, document) if prefer_existing else None
    if existing is not None:
        return _bound_board_teaching_guide(
            guidance=existing,
            document=document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    if selected_reference is None and _is_section_followup_learning_need(lesson, request):
        ai_guidance = openai_course_ai.generate_board_teaching_guide(
            lesson_title=lesson.title,
            request_message=request.message,
            requirements=requirements,
            document=document,
        )
        return _bound_board_teaching_guide(
            guidance=ai_guidance
            or _supplemental_board_teaching_guide(
                lesson=lesson,
                document=document,
                requirements=requirements,
                request=request,
            ),
            document=document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    if selected_reference is not None:
        return _bound_board_teaching_guide(
            guidance=None,
            document=document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    ai_guidance = openai_course_ai.generate_board_teaching_guide(
        lesson_title=lesson.title,
        request_message=request.message,
        requirements=requirements,
        document=document,
    )
    return _bound_board_teaching_guide(
        guidance=ai_guidance,
        document=document,
        requirements=requirements,
        request_message=request.message,
        selected_reference=selected_reference,
    )


def _guide_focus_titles(guide: TeachingGuide) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for mapping in guide.mappings:
        for point in mapping.focus_points:
            cleaned = point.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            titles.append(cleaned)
    return titles[:4]


def _teacher_sentences(block: str) -> list[str]:
    sentences = [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]?", block) if part.strip()]
    if sentences:
        return sentences
    clauses = [part.strip() for part in re.split(r"(?<=，)", block) if part.strip()]
    return clauses or [block.strip()]


def _split_dense_teacher_block(block: str) -> list[str]:
    cleaned = block.strip()
    if len(cleaned) <= 90:
        return [cleaned]
    if re.match(r"^(?:[-*•]|\d+[.、）)])", cleaned):
        return [cleaned]

    sentences = _teacher_sentences(cleaned)
    if len(sentences) <= 2:
        return [cleaned]

    groups = ["".join(sentences[:1]).strip()]
    current: list[str] = []
    current_length = 0
    for sentence in sentences[1:]:
        current.append(sentence)
        current_length += len(sentence)
        if len(current) >= 2 or current_length >= 72:
            groups.append("".join(current).strip())
            current = []
            current_length = 0
    if current:
        groups.append("".join(current).strip())
    return [group for group in groups if group]


def _format_teacher_message(message: str) -> str:
    cleaned = message.replace("\r\n", "\n").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    for marker in TEACHER_PARAGRAPH_MARKERS:
        cleaned = re.sub(
            rf"(?:(?<=^)|(?<=[。！？!?：:\n]))\s*({re.escape(marker)}[：:]?)",
            r"\n\n\1",
            cleaned,
        )
    cleaned = re.sub(r"(?<![\nA-Za-z])(?=(?:\d+[.、）)]|[-*•]))", "\n\n", cleaned)

    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    normalized_blocks: list[str] = []
    for block in blocks:
        compact_block = re.sub(r"\s*\n\s*", " ", block).strip()
        normalized_blocks.extend(_split_dense_teacher_block(compact_block))
    return "\n\n".join(normalized_blocks) if normalized_blocks else cleaned


def _teacher_intro(state: WorkflowState) -> str:
    decision = state["board_decision"]
    lesson_title = (state.get("generated_lesson") or state["lesson"]).title
    if state.get("document_updated"):
        return "直接开讲，先抓主线。"
    if decision.action == "create_new_lesson":
        return f"这个问题我已经拆成新课《{lesson_title}》，我们直接讲主线。"
    return "我们直接抓这次最该讲的重点。"


def _is_broad_learning_goal_request(message: str) -> bool:
    compact = _compact_instruction_text(message)
    if _is_low_information_request(message) or _is_vague_pointer_request(message):
        return False
    learning_intents = (
        "我要学",
        "我要学习",
        "我想学",
        "我想要学",
        "我想要学习",
        "想学",
        "想要学",
        "想要学习",
        "教我",
    )
    return any(intent in compact for intent in learning_intents) or compact.startswith("学习")


def _is_math_learning_topic(text: str) -> bool:
    compact = _compact_instruction_text(text)
    math_terms = (
        "数学",
        "函数",
        "几何",
        "代数",
        "代数几何",
        "抽象代数",
        "线性代数",
        "微积分",
        "数学分析",
        "拓扑",
        "环",
        "群",
        "域",
        "模",
        "理想",
        "素理想",
        "多项式",
        "簇",
        "概形",
    )
    return any(term in compact for term in math_terms)


def _is_advanced_algebra_learning_topic(text: str) -> bool:
    compact = _compact_instruction_text(text)
    advanced_terms = (
        "抽象代数",
        "代数几何",
        "交换代数",
        "环",
        "环论",
        "理想",
        "素理想",
        "商环",
        "域",
        "模",
        "概形",
        "zariski",
        "spec",
    )
    return any(term in compact.lower() for term in advanced_terms)


def _teacher_learning_probe(state: WorkflowState) -> str | None:
    request = state["request"]
    status = state.get("learning_clarification")
    if status is None or status.forced_start:
        return None
    if not _is_first_user_exchange(request):
        return None
    if request.selection is not None or request.interaction_mode == "direct_edit":
        return None
    if not _is_broad_learning_goal_request(request.message):
        return None

    missing = set(status.missing_items)
    if "当前水平或背景" not in missing:
        return None

    lesson = state["lesson"]
    requirements = state["learning_requirement_sheet"]
    topic = _extract_topic_hint(request.message) or (requirements.theme if requirements.theme != lesson.title else "") or request.message
    if _is_math_learning_topic(f"{topic}\n{request.message}"):
        if not _is_advanced_algebra_learning_topic(f"{topic}\n{request.message}"):
            return (
                "为了下一轮不把深度讲偏，我需要先摸一下你的数学背景：你当前是什么水平、几年级？"
                "另外你具体想学数学里的什么内容，比如函数、几何、代数、微积分、概率统计，还是某类题？"
            )
        return (
            f"你想学的是“{topic}”。为了下一轮不把深度讲偏，我需要先摸一下你的数学背景：你现在更接近高中、本科低年级、"
            "数学专业本科，还是研究生阶段？如果已经学过环、理想或素理想，也可以直接说。"
        )
    return "为了下一轮把例子和深度对准，你当前是什么阶段或背景？这次具体想学哪一块，想用于考试、作业、论文、项目还是日常应用？"


def _teacher_message_from_talk_track(state: WorkflowState, talk_track: str) -> str:
    lines = [_teacher_intro(state), talk_track.strip()]
    probe = _teacher_learning_probe(state)
    if probe:
        lines.append(probe)
    return "\n".join(line for line in lines if line.strip())


def _is_continue_teaching_request(message: str) -> bool:
    compact = _compact_instruction_text(message)
    return compact in {
        "继续",
        "继续讲",
        "继续下一节",
        "讲下一节",
        "下一节",
        "下一个小节",
        "继续下一个小节",
        "往下讲",
        "接着讲",
    }


def _is_teaching_control_request(request: ChatRequest) -> bool:
    return request.teaching_action in {"continue", "restart"} or _is_continue_teaching_request(request.message)


def _section_progress_view(
    progress: BoardTeachingProgress,
    guide: BoardTeachingGuide,
) -> SectionTeachingProgressView:
    section_count = len(guide.section_plans)
    section_index = max(0, min(progress.current_section_index, max(section_count - 1, 0)))
    title = guide.section_plans[section_index].heading if section_count else ""
    return SectionTeachingProgressView(
        section_index=section_index,
        section_count=section_count,
        current_section_title=title,
        has_next_section=section_index + 1 < section_count,
        waiting_for_continue=progress.waiting_for_continue,
    )


def _section_teacher_message(
    *,
    state: WorkflowState,
    plan: BoardSectionTeachingPlan,
    progress: SectionTeachingProgressView,
) -> str:
    lead = f"第 {progress.section_index + 1} 小节，我们先讲《{plan.heading}》。"
    core_points = [
        point
        for point in plan.core_points[:3]
        if point.strip() and not _is_low_value_section_teaching_line(point)
    ]
    example = plan.example_or_analogy.strip()
    pitfalls = "；".join(point.strip(" 。！？!?；;") for point in plan.common_pitfalls[:2] if point.strip())
    check = plan.check_question.strip() or f"你能用一句话说出《{plan.heading}》最核心的意思吗？"

    blocks = [lead]
    if core_points:
        blocks.append(f"这一节的核心是：{core_points[0]}。")
        if len(core_points) > 1:
            blocks.append(f"接着看第二层：{core_points[1]}。")
        if len(core_points) > 2:
            blocks.append(f"最后落到一个判断：{core_points[2]}。")
    elif plan.board_excerpt.strip():
        blocks.append(f"这一节的核心是：{plan.board_excerpt.strip()}。")
    if example and "放进一个最小例子里讲" not in example:
        blocks.append(f"可以用这个方式理解：{example}")
    if pitfalls:
        blocks.append(f"容易卡住的地方是：{pitfalls}。")

    if progress.has_next_section:
        blocks.append(f"检查一下：{check} 这一节你能理解吗？可以的话，我继续讲下一个小节。")
    else:
        blocks.append(f"检查一下：{check} 这是这份板书的最后一个小节，后面可以回到练习或具体疑问。")

    return "\n\n".join(block for block in blocks if block.strip())


def _section_teaching_turn(state: WorkflowState) -> WorkflowState | None:
    request = state["request"]
    decision = state["board_decision"]
    guide = state.get("board_teaching_guide")
    if guide is None or not guide.section_plans:
        return None

    source_lesson = state.get("generated_lesson") or state["lesson"]
    document = state.get("teacher_document") or source_lesson.board_document
    target_hash = guide.board_snapshot_hash or _board_snapshot_hash(document)
    previous = source_lesson.board_teaching_progress
    completed: list[int] = []
    if (
        previous is not None
        and previous.board_document_id == document.id
        and previous.board_snapshot_hash == target_hash
    ):
        completed = list(previous.completed_section_indexes)

    is_auto_start = bool(state.get("document_updated")) and decision.action in {
        "edit_board",
        "append_section",
        "create_new_lesson",
    }
    is_continue = request.teaching_action == "continue" or _is_continue_teaching_request(request.message)
    is_restart = request.teaching_action == "restart"
    if not (is_auto_start or is_continue or is_restart):
        return None

    section_count = len(guide.section_plans)
    if is_restart:
        target_index = 0
        completed = []
    elif is_continue:
        target_index = (max(completed) + 1) if completed else 0
    else:
        target_index = int(state.get("teaching_start_section_index") or 0)
        completed = []
    target_index = max(0, min(target_index, section_count - 1))
    completed = sorted({*completed, target_index})

    progress = BoardTeachingProgress(
        board_document_id=document.id,
        board_snapshot_hash=target_hash,
        current_section_index=target_index,
        completed_section_indexes=completed,
        waiting_for_continue=target_index + 1 < section_count,
    )
    progress_view = _section_progress_view(progress, guide)
    plan = guide.section_plans[target_index]
    first_section_briefs = [(state.get("teacher_talk_track") or "").strip()]
    if guide.teacher_brief.strip() and not _is_low_value_section_teaching_line(guide.teacher_brief):
        first_section_briefs.append(guide.teacher_brief.strip())
    if target_index == 0 and any(first_section_briefs):
        plan = plan.model_copy(
            update={
                "core_points": _dedupe_teaching_lines(
                    [*first_section_briefs, *plan.core_points],
                    limit=3,
                )
            }
        )
    message = _section_teacher_message(
        state=state,
        plan=plan,
        progress=progress_view,
    )
    return {
        "teacher_message": _format_teacher_message(message),
        "board_teaching_progress": progress,
        "teaching_progress": progress_view,
    }


def _plain_teaching_from_excerpt(excerpt: str) -> str:
    cleaned = " ".join(excerpt.split()).strip(" ：:，,。！？!?；;")
    cleaned = re.sub(r"^(?:本节主线|参考片段\s*\d+|学习目标)[：:]\s*", "", cleaned).strip()
    topic = _extract_topic_hint(cleaned)
    if topic and _is_generic_school_subject_topic(topic):
        return ""
    if {"模式", "特征", "分类器"} <= set(re.findall(r"模式|特征|分类器", cleaned)):
        return (
            f"{cleaned}。换句话说，模式是要识别的对象，特征是我们拿来描述它的线索，"
            "分类器是根据这些线索做判断的规则。"
        )
    if "监督学习" in cleaned and "无监督学习" in cleaned:
        return (
            f"{cleaned}。监督学习是带着答案样本去学判断规则；无监督学习是先从没有标签的数据里找结构；"
            "分类决策则把模型结果落成一个具体选择。"
        )
    return cleaned


def _is_generic_school_subject_topic(topic: str) -> bool:
    return _compact_instruction_text(topic) in {
        "数学",
        "物理",
        "化学",
        "生物",
        "英语",
        "法语",
        "语文",
        "历史",
        "地理",
        "政治",
        "编程",
        "计算机",
    }


def _fallback_concept_teaching_from_request(text: str) -> str | None:
    topic = _extract_topic_hint(text)
    if not topic:
        return None
    if _is_generic_school_subject_topic(topic):
        return None
    compact_topic = _compact_instruction_text(topic)
    if "库仑力" in compact_topic:
        return (
            "什么是库仑力：它就是两个带电物体之间的相互作用力。"
            "同号电荷相互排斥，异号电荷相互吸引；距离越近、电荷量越大，力通常越明显。"
            "高中阶段先抓这三件事：方向看吸引还是排斥，大小看电荷量和距离，受力分析时把它当成一种力画进受力图。"
        )
    return (
        f"什么是{topic}：先把它当成这节课要抓住的核心对象。"
        "我们按三步来学：先说定义，再解释它为什么重要，最后用一个例子或小题检查你是不是真的会用。"
    )


def _teacher_brief_from_handout(handout: str) -> str:
    lines = []
    for raw in handout.splitlines():
        cleaned = raw.strip(" -•\t")
        if not cleaned or cleaned.startswith(("内部讲义", "用户问题", "学习需求", "讲解依据", "讲解顺序", "注意")):
            continue
        concept_teaching = _fallback_concept_teaching_from_request(cleaned)
        if concept_teaching:
            lines.append(concept_teaching)
        elif _is_generic_school_subject_topic(_extract_topic_hint(cleaned) or ""):
            continue
        else:
            lines.append(cleaned)
        if len(lines) >= 3:
            break
    return "\n".join(lines)


def _overfitting_teacher_message() -> str:
    return (
        "我们直接抓这次最该讲的重点。\n\n"
        "过拟合的核心不是模型“学得太好”，而是它把训练数据里的噪声、偶然行情和历史巧合也当成了规律。所以最典型的信号是：训练集表现很好，但验证集、测试集或样本外市场一换，效果就明显变差。\n\n"
        "解决思路要围绕“提高泛化能力”：先降低模型复杂度，少放不必要的特征和参数；再加入正则化，比如 L1、L2 或权重衰减，让模型别为了贴合历史而变得太弯；同时做交叉验证和样本外测试，确认策略不是只在某一次切分里碰巧好看。\n\n"
        "放到量化金融里，还要特别检查数据泄漏、交易成本、换时间段表现和参数稳定性。一个策略如果只有某组参数、某段行情、某次回测特别漂亮，反而要先怀疑它是不是过拟合。"
    )


def _fallback_clarification_message(state: WorkflowState) -> str:
    request = state["request"]
    status = state.get("learning_clarification")
    requirements = state.get("learning_requirement_sheet")
    lesson = state["lesson"]
    missing = set(status.missing_items) if status is not None else set()
    topic = _extract_topic_hint(request.message) or (
        requirements.theme if requirements is not None and requirements.theme != lesson.title else ""
    )

    if "想学的主题" in missing:
        return "你具体想学什么内容？可以直接说一个主题、章节，或者把卡住的题目发给我。"
    if {"当前水平或背景", "学习目的或应用场景"} <= missing:
        if _is_math_learning_topic(f"{topic}\n{request.message}"):
            return "你当前是什么水平、几年级？另外你具体想学数学里的什么内容，比如函数、几何、代数、微积分、概率统计，还是某类题？"
        return "你当前是什么水平或背景？这次具体想学什么内容，想达到什么目标？"
    if "当前水平或背景" in missing:
        return "你当前是什么水平、几年级，或者之前已经学到哪一部分了？"
    if "学习目的或应用场景" in missing:
        return "你这次具体想学什么内容，想达到什么目标？"
    return "你具体想学什么内容？"


def _fallback_teacher_message(state: WorkflowState) -> str:
    request = state["request"]
    decision = state["board_decision"]
    board_teaching_guide = state.get("board_teaching_guide")
    clarification_questions = state.get("clarification_questions", [])
    reference_prompt = state.get("reference_prompt")
    lesson_title = (state.get("generated_lesson") or state["lesson"]).title

    if decision.action == "clarify_request":
        if clarification_questions:
            return clarification_questions[0]
        return _fallback_clarification_message(state)
    if decision.action == "await_reference_choice" and reference_prompt is not None:
        return reference_prompt.question
    if decision.action == "await_scope_choice":
        return f"这个问题已经超出《{lesson_title}》当前讲义范围。你想先在本课简述，还是单独开一节详细课？"

    probe = _teacher_learning_probe(state)
    if probe:
        return probe

    talk_track = (state.get("teacher_talk_track") or "").strip()
    if talk_track:
        return _teacher_message_from_talk_track(state, talk_track)
    if "过拟合" in request.message:
        return _overfitting_teacher_message()

    lines = [_teacher_intro(state)]
    if board_teaching_guide is not None:
        handout_brief = _teacher_brief_from_handout(board_teaching_guide.lecture_handout)
        if handout_brief:
            lines.append(handout_brief)
            return "\n".join(lines)
        if "已锁定参考章节" in board_teaching_guide.generation_rationale and board_teaching_guide.teacher_brief.strip():
            lines.append(board_teaching_guide.teacher_brief.strip())
            return "\n".join(lines)
        selected_items = board_teaching_guide.selected_items
        if selected_items:
            first = selected_items[0]
            first_line = _plain_teaching_from_excerpt(first.excerpt)
            if first_line:
                lines.append(first_line)
            for item in selected_items[1:3]:
                next_line = _plain_teaching_from_excerpt(item.excerpt)
                if next_line and next_line not in lines:
                    lines.append(next_line)
        elif board_teaching_guide.teacher_brief.strip():
            lines.append(board_teaching_guide.teacher_brief.strip())
    return "\n".join(lines)


def _run_pm(state: WorkflowState) -> WorkflowState:
    from app.services.workflow_roles.pm import run_pm

    return run_pm(state)


def _run_board_manager(state: WorkflowState) -> WorkflowState:
    from app.services.workflow_roles.board_manager import run_board_manager

    return run_board_manager(state)


def _run_board_executor(state: WorkflowState) -> WorkflowState:
    from app.services.workflow_roles.board_executor import run_board_executor

    return run_board_executor(state)


def _run_teacher(state: WorkflowState) -> WorkflowState:
    from app.services.workflow_roles.teacher import run_teacher

    return run_teacher(state)


class SimpleCourseWorkflow:
    def invoke(self, initial_state: WorkflowState) -> WorkflowState:
        state: WorkflowState = dict(initial_state)
        if _is_teaching_control_request(state["request"]):
            lesson = state["lesson"]
            requirements = effective_requirements(lesson)
            learning_clarification = _learning_clarification_status(
                lesson=lesson,
                request=state["request"],
                requirements=requirements,
            )
            state.update(
                {
                    "learning_requirement_sheet": requirements,
                    "learning_clarification": learning_clarification,
                    "needs_clarification": False,
                    "clarification_questions": [],
                    "board_decision": BoardDecision(action="no_change", reason="用户要求按分节讲义继续讲解。"),
                    "teaching_guide": _interactive_teaching_guide(
                        lesson_id=lesson.id,
                        lesson_title=lesson.title,
                        document=lesson.board_document,
                        requirements=requirements,
                    ),
                    "teacher_document": lesson.board_document,
                    "document_updated": False,
                    "scope_options": [],
                    "resource_matches": match_resources(state["course_package"], lesson, state["request"], requirements),
                    "reference_prompt": None,
                    "selected_reference": None,
                    "generated_lesson": None,
                    "teacher_talk_track": None,
                    "board_teaching_guide": _resolve_board_teaching_guide(
                        lesson=lesson,
                        request=state["request"],
                        requirements=requirements,
                        document=lesson.board_document,
                        prefer_existing=True,
                    ),
                }
            )
            state.update(_run_teacher(state))
            return state
        state.update(_run_pm(state))
        state.update(_run_board_manager(state))
        state.update(_run_board_executor(state))
        state.update(_run_teacher(state))
        return state


course_workflow = SimpleCourseWorkflow()
