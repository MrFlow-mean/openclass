from __future__ import annotations

import hashlib
import re
from typing import TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
    BoardNeedMapping,
    BoardTeachingGuide,
    BoardTeachingSelectedItem,
    ChatRequest,
    CoursePackage,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ScopeOption,
    TeachingGuide,
)
from app.services.course_runtime import (
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
)
from app.services.lesson_factory import build_teaching_guide, create_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import extract_reference_context
from app.services.rich_document import (
    append_html_section,
    build_document,
    document_changed,
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
    if any(keyword in message for keyword in ["新增章节", "补充一节", "展开讲", "单独一节", "新开一节"]):
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


def _is_forced_start_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message)
    forced_patterns = [
        "直接开始",
        "直接开讲",
        "开讲",
        "直接讲",
        "开始教学",
        "马上开始",
        "马上讲",
        "现在开始",
        "先开始",
        "直接教",
        "先教",
        "不用问",
        "不要问",
        "别问",
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
    explanation_keywords = [
        "解释",
        "讲解",
        "讲一下",
        "讲讲",
        "开讲",
        "直接讲",
        "怎么理解",
        "为什么",
        "什么意思",
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


def _extract_level_hint(text: str) -> str | None:
    patterns = [
        r"\b([ABC][12])\b",
        r"(零基础|初学|入门|进阶|高级|高三|高二|高一|初三|初二|初一|考研|本科|研究生)",
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
        r"(?:为了|我要|想要|用于|用来|准备用在|准备应对|应对|准备)\s*([^，。！？!?；;]{2,28})",
        r"(法国旅游|出国旅游|高考压轴导数大题|高考压轴题|导数大题|旅游|考试|面试|工作|项目|阅读|写作)",
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

    subject_terms = _extract_focus_terms(user_context) or _extract_focus_terms(requirements.learning_goal)
    if subject_terms or lesson.title in user_context:
        progress += 35
    else:
        missing_items.append("想学的主题")

    profile_patterns = [
        r"\b[a-c][12]\b",
        r"\b高[一二三]\b",
        r"\b初[一二三]\b",
        r"\d+\s*(?:个)?(?:词|词汇|单词)",
        r"(?:零基础|初学|入门|进阶|高级|水平|学习者|基础|b1|b2|c1|高三|考研|本科|研究生)",
    ]
    if any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in profile_patterns):
        progress += 30
    else:
        missing_items.append("当前水平或背景")

    scenario_patterns = [
        "为了",
        "我要",
        "用于",
        "应对",
        "准备",
        "旅游",
        "考试",
        "面试",
        "写作",
        "阅读",
        "工作",
        "项目",
        "题目",
        "场景",
        "情景",
        "高考",
        "竞赛",
        "出国",
        "法国",
        "餐厅",
        "压轴",
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
    if request.resource_reference_action is not None:
        return True
    if is_document_empty(lesson.board_document):
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
        score += 0.42 if requested_section_no is None else 0.18
        hits.append(f"第{requested_chapter_no}章")
        if requested_section_no is not None and section_no == requested_section_no:
            score += 0.72
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
    scored_matches: list[tuple[float, float, ResourceMatch]] = []
    matches: list[ResourceMatch] = []
    for resource in course_package.resources:
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
            effective_score = max(primary_score, score)
            if effective_score > 0.18:
                matches.append(
                    ResourceMatch(
                        resource_id=resource.id,
                        chapter_id=chapter.id,
                        resource_name=resource.name,
                        chapter_title=chapter.title,
                        reason=(
                            f"章节标题与关键词和当前学习目标有明显重合："
                            f"{', '.join(primary_overlap or overlap) or chapter.title}"
                        ),
                        score=round(effective_score, 2),
                        is_high_overlap=effective_score >= HIGH_OVERLAP_THRESHOLD,
                    )
                )
                scored_matches.append((primary_score, score, float(chapter.level), matches[-1]))
    scored_matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in scored_matches[:3]]


def _build_reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=(
            f"我找到一个很贴近的参考章节：《{match.resource_name}》的《{match.chapter_title}》。要参考它来生成吗？"
        ),
        reason=match.reason,
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
    chapter_no, _ = _extract_requested_outline_reference(request.message)
    if chapter_no is not None:
        return True
    return decision.action == "no_change" and (_is_explanation_request(request.message) or _is_forced_start_request(request.message))


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
        payload["chapter_text"] = reference.full_text
    return payload


def _draft_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    requirements = effective_requirements(lesson)
    user_turns = [turn.content for turn in request.conversation if turn.role == "user"]
    user_context = "\n".join([*user_turns[-3:], request.message]).strip()
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
    return normalize_requirements(requirements, lesson_title=lesson.title, document=lesson.board_document)


def _clarification_questions_for_status(status: LearningClarificationStatus) -> list[str]:
    missing = set(status.missing_items)
    if "想学的主题" in missing:
        return ["你现在最想学的具体内容是什么？"]
    if "当前水平或背景" in missing and "学习目的或应用场景" in missing:
        return ["你现在大概什么水平，准备用在哪种场景里？"]
    if "当前水平或背景" in missing:
        return ["你现在大概什么水平？"]
    if "学习目的或应用场景" in missing:
        return ["你准备把这个内容用在哪种场景里？"]
    return []


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
    if "想学的主题" in missing and status.progress < 35:
        return True
    if {"当前水平或背景", "学习目的或应用场景"} <= missing and status.progress < 55:
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
    cleaned = title.strip()
    return cleaned.startswith("---") or cleaned.lower().startswith("part ")


def _extract_requested_outline_reference(text: str) -> tuple[int | None, int | None]:
    match = re.search(r"第\s*(\d+)\s*章(?:第\s*(\d+)\s*[节讲部分])?", text)
    if match:
        chapter_no = int(match.group(1))
        section_no = int(match.group(2)) if match.group(2) else None
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


def _outline_reference_position(
    chapters: list[object],
    chapter_id: str,
) -> tuple[int | None, int | None]:
    chapter_no = 0
    section_no = 0
    current_chapter_id: str | None = None
    for raw in chapters:
        chapter = raw
        title = getattr(chapter, "title", "")
        level = int(getattr(chapter, "level", 1))
        current_id = getattr(chapter, "id", "")
        if level == 1 and not _is_reference_separator_title(str(title)):
            chapter_no += 1
            current_chapter_id = current_id
            section_no = 0
            if current_id == chapter_id:
                return chapter_no, None
            continue
        if level >= 2 and current_chapter_id is not None:
            section_no += 1
            if current_id == chapter_id:
                return chapter_no or None, section_no
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

    if request.scope_action == "create_new_lesson":
        return BoardDecision(action="create_new_lesson", reason="用户明确要求把问题拆成一节新课。")
    if request.scope_action == "append_section":
        return BoardDecision(action="append_section", reason="用户选择在当前 lesson 中新增章节。")
    if request.scope_action == "patch_current_lesson":
        return BoardDecision(action="no_change", reason="用户选择先在当前课内简述，不直接改讲义。")
    if is_document_empty(lesson.board_document) and explicit_generation:
        return BoardDecision(action="edit_board", reason="当前讲义为空，且用户明确要求生成学习内容。")
    if explicit_generation:
        return BoardDecision(action="edit_board", reason="用户明确要求生成讲义/课文/对话内容，应直接产出整篇文档。")
    if scope_mode == "scope_escalation":
        if matches:
            return BoardDecision(
                action="await_scope_choice",
                reason=f"问题超出当前讲义范围，并且资料库里已有相关入口：{matches[0].resource_name} / {matches[0].chapter_title}。",
            )
        return BoardDecision(action="await_scope_choice", reason="问题已经超出当前 lesson，需要先选择推进方式。")
    if any(keyword in message for keyword in ["新增章节", "补充一节", "展开讲", "扩展"]):
        return BoardDecision(action="append_section", reason="用户希望把相关内容纳入当前 lesson 的新章节。")
    if any(keyword in message for keyword in ["更易懂", "通俗", "改写", "整理", "练习", "习题", "例题", "总结", "补一段", "润色", "完善"]):
        return BoardDecision(action="edit_board", reason="当前需求更适合先调整整篇讲义，再围绕更新后的结构讲解。")
    if any(keyword in message for keyword in ["解释", "讲解", "开讲", "直接讲", "讲一下", "讲讲", "为什么", "什么意思", "怎么理解"]):
        return BoardDecision(action="no_change", reason="当前更像围绕现有讲义的讲解请求，不必先改文档。")
    if requirements.output_preference and not is_document_empty(lesson.board_document):
        return BoardDecision(action="no_change", reason="现有讲义已经能支撑这次讲解，先不改文档。")
    return BoardDecision(action="edit_board", reason="默认先生成一版更完整的连续讲义，便于后续教学。")


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


def _fallback_document_update(
    *,
    lesson: Lesson,
    request: ChatRequest,
    decision: BoardDecision,
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

    if decision.action == "append_section":
        lead = selected_reference.teaching_points[0] if selected_reference and selected_reference.teaching_points else "这一节专门承接用户当前追问，把新问题接回原有主线。"
        section_html = f"<h2>补充章节</h2><p>{lead}</p><p>{request.message}</p>"
        return append_html_section(lesson.board_document, section_html)

    generated = create_lesson(
        request.message.strip() or lesson.title,
        requirements=effective_requirements(lesson),
        reference_context=selected_reference,
    )
    return generated.board_document.model_copy(update={"id": lesson.board_document.id})


def _board_snapshot_hash(document: BoardDocument) -> str:
    payload = f"{document.id}\n{document.title}\n{document.content_text.strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _requirement_needs(requirements: LearningRequirementSheet) -> list[str]:
    candidates = [
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


def _fallback_board_teaching_guide(
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    request_message: str,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    needs = _requirement_needs(requirements)
    if is_document_empty(document) and selected_reference is not None:
        selected_items = [
            BoardTeachingSelectedItem(
                excerpt=(chunk.excerpt or selected_reference.summary)[:240],
                source_heading=chunk.title,
                reason="当前板书还没有可讲内容，因此先直接用已锁定参考章节里的关键片段开讲。",
                mapped_needs=needs[:1],
                teaching_role="main_idea" if index == 1 else ("why_it_matters" if index == 2 else "example"),
                order_index=index,
            )
            for index, chunk in enumerate(selected_reference.chunks[:3], start=1)
        ]
        if not selected_items:
            selected_items = [
                BoardTeachingSelectedItem(
                    excerpt=selected_reference.summary[:240],
                    source_heading=selected_reference.chapter_title,
                    reason="当前板书为空，先用已确认的教材章节摘要起讲。",
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
            teacher_brief=(
                f"直接按《{selected_reference.chapter_title}》开讲："
                "先说这节要解决的问题，再讲关键概念之间的关系，最后给一个例子帮助理解。"
            ),
        )

    focus_terms = {term.lower() for term in _query_phrases(f"{request_message}\n{requirements.learning_goal}")}
    scored_segments: list[tuple[int, str | None, str]] = []
    for heading, excerpt in _board_segments(document):
        corpus = f"{heading or ''}\n{excerpt}".lower()
        score = sum(1 for term in focus_terms if term in corpus)
        if request_message.strip() and excerpt in request_message:
            score += 2
        scored_segments.append((score, heading, excerpt))
    scored_segments.sort(key=lambda item: item[0], reverse=True)

    chosen = scored_segments[:3] if scored_segments else [(0, document.title, document.content_text.strip() or document.title)]
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
        return existing
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


def _teacher_intro(state: WorkflowState) -> str:
    decision = state["board_decision"]
    lesson_title = (state.get("generated_lesson") or state["lesson"]).title
    if state.get("document_updated"):
        return "右侧我先补好一版板书了，我们直接抓重点。"
    if decision.action == "create_new_lesson":
        return f"这个问题我已经拆成新课《{lesson_title}》，我们直接讲主线。"
    return "我们直接抓这次最该讲的重点。"


def _teacher_message_from_talk_track(state: WorkflowState, talk_track: str) -> str:
    selected_reference = state.get("selected_reference")
    lines = [_teacher_intro(state), talk_track.strip()]
    if selected_reference is not None:
        lines.append(
            f"这次还参考了《{selected_reference.resource_name}》的《{selected_reference.chapter_title}》，但我会用课堂口吻讲。"
        )
    return "\n".join(line for line in lines if line.strip())


def _fallback_teacher_message(state: WorkflowState) -> str:
    request = state["request"]
    decision = state["board_decision"]
    board_teaching_guide = state.get("board_teaching_guide")
    clarification_questions = state.get("clarification_questions", [])
    reference_prompt = state.get("reference_prompt")
    selected_reference = state.get("selected_reference")
    lesson_title = (state.get("generated_lesson") or state["lesson"]).title

    if decision.action == "clarify_request":
        return (clarification_questions or ["你现在最想先学的具体内容是什么？"])[0]
    if decision.action == "await_reference_choice" and reference_prompt is not None:
        return reference_prompt.question
    if decision.action == "await_scope_choice":
        return f"这个问题已经超出《{lesson_title}》当前讲义范围。你想先在本课简述，还是单独开一节详细课？"

    talk_track = (state.get("teacher_talk_track") or "").strip()
    if talk_track:
        return _teacher_message_from_talk_track(state, talk_track)

    lines = [_teacher_intro(state)]
    if board_teaching_guide is not None:
        if board_teaching_guide.teacher_brief.strip():
            lines.append(board_teaching_guide.teacher_brief.strip())
        selected_items = board_teaching_guide.selected_items
        need_mappings = board_teaching_guide.need_mappings
        if selected_items:
            first = selected_items[0]
            if need_mappings:
                lines.append(
                    f"先把“{need_mappings[0].need[:28]}”这件事讲清楚，重点落在“{first.excerpt[:28]}”背后的意思，而不是照读原句。"
                )
            elif len(selected_items) > 1:
                lines.append(
                    f"先讲“{first.excerpt[:24]}”，再接“{selected_items[1].excerpt[:24]}”，这样主线会更顺。"
                )
    if selected_reference is not None:
        lines.append(
            f"这次也参考了《{selected_reference.resource_name}》的《{selected_reference.chapter_title}》，但讲法会更口语化。"
        )
    return "\n".join(lines)


def _run_pm(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    draft_requirements = _draft_requirements(lesson, request)
    draft_status = _learning_clarification_status(
        lesson=lesson,
        request=request,
        requirements=draft_requirements,
    )

    if request.interaction_mode == "direct_edit":
        return {
            "learning_requirement_sheet": draft_requirements,
            "learning_clarification": draft_status,
            "needs_clarification": False,
            "clarification_questions": [],
            "pm_reason": "用户通过选区编辑入口直接提交文档修改指令，跳过 PM 澄清。",
        }

    if _should_use_fast_pm_path(lesson=lesson, request=request, status=draft_status):
        needs_clarification = _should_ask_brief_clarification(request=request, status=draft_status)
        questions = _clarification_questions_for_status(draft_status) if needs_clarification else []
        return {
            "learning_requirement_sheet": draft_requirements,
            "learning_clarification": draft_status,
            "needs_clarification": needs_clarification,
            "clarification_questions": questions[:1],
            "pm_reason": "优先走极速澄清策略：能直接讲就不追问，只有明显会讲偏时才补一句。",
        }

    assessment = openai_course_ai.assess_learning_requirements(
        lesson_title=lesson.title,
        lesson_summary=lesson.summary,
        lesson_tags=lesson.tags,
        document_outline=draft_requirements.board_scope,
        user_message=request.message,
        selection_excerpt=request.selection.excerpt if request.selection else None,
        conversation=[turn.model_dump(mode="json") for turn in request.conversation],
    )
    if assessment is not None:
        requirements = normalize_requirements(
            assessment.learning_requirement_sheet,
            lesson_title=lesson.title,
            document=lesson.board_document,
        )
        status = _learning_clarification_status(
            lesson=lesson,
            request=request,
            requirements=requirements,
        )
        needs_clarification = not assessment.ready
        if status.progress < 35 and not status.forced_start:
            needs_clarification = True
        if status.progress >= 80 or status.forced_start:
            needs_clarification = False
        clarification_questions = assessment.clarification_questions[:3]
        if needs_clarification and not clarification_questions:
            clarification_questions = _clarification_questions_for_status(status)
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": needs_clarification,
            "clarification_questions": clarification_questions,
            "pm_reason": assessment.reason,
        }

    needs_clarification = _should_ask_brief_clarification(request=request, status=draft_status)
    questions = _clarification_questions_for_status(draft_status) if needs_clarification else []
    return {
        "learning_requirement_sheet": draft_requirements,
        "learning_clarification": draft_status,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions[:1],
        "pm_reason": draft_status.reason,
    }


def _run_board_manager(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    matches = match_resources(state["course_package"], lesson, request, requirements)

    if request.interaction_mode == "direct_edit":
        return {
            "board_decision": BoardDecision(action="edit_board", reason="用户通过选区编辑入口明确要求直接修改讲义。"),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    if state.get("needs_clarification"):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason=state.get("pm_reason", "当前需求仍需要继续澄清。")),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    if _should_use_fast_board_path(lesson=lesson, request=request, requirements=requirements):
        decision = _fallback_board_decision(lesson, request, requirements, matches)
    else:
        ai_decision = openai_course_ai.generate_board_decision(
            lesson_title=lesson.title,
            request_message=request.message,
            selection=request.selection.model_dump(mode="json") if request.selection else None,
            interaction_mode=request.interaction_mode,
            scope_action=request.scope_action,
            requirements=requirements,
            document=lesson.board_document,
            resource_matches=[match.model_dump(mode="json") for match in matches],
        )
        decision = ai_decision or _fallback_board_decision(lesson, request, requirements, matches)

    if is_document_empty(lesson.board_document) and _is_board_generation_request(request.message):
        decision = BoardDecision(action="edit_board", reason="当前讲义为空，且用户明确要求生成学习内容。")
    elif decision.action == "no_change" and _is_board_generation_request(request.message):
        decision = BoardDecision(action="edit_board", reason="用户明确要求生成讲义/对话内容，应直接产出文档。")

    if decision.action == "await_scope_choice":
        return {
            "board_decision": decision,
            "scope_options": _build_scope_options(matches),
            "resource_matches": matches,
            "reference_prompt": None,
            "selected_reference": None,
        }

    top_match = matches[0] if matches else None
    second_match = matches[1] if len(matches) > 1 else None
    ambiguous_reference = (
        top_match is not None
        and second_match is not None
        and top_match.is_high_overlap
        and abs(top_match.score - second_match.score) <= 0.06
    )
    if request.resource_reference_action is None and decision.action in {"edit_board", "append_section", "create_new_lesson", "no_change"}:
        if ambiguous_reference and top_match is not None:
            return {
                "board_decision": BoardDecision(
                    action="await_reference_choice",
                    reason="资料候选已经缩到很小范围，但前两项还比较接近，短确认一下更稳。",
                ),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": _build_reference_prompt(top_match),
                "selected_reference": None,
            }
        if top_match is not None and (
            top_match.is_high_overlap or _should_auto_attach_reference_for_direct_teaching(request=request, decision=decision, top_match=top_match)
        ):
            return {
                "board_decision": decision,
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "selected_reference": extract_reference_context(
                    next(
                        resource
                        for resource in state["course_package"].resources
                        if resource.id == top_match.resource_id
                    ),
                    top_match.chapter_id,
                    user_query=_resource_query_text(lesson, request, requirements),
                ),
            }

    return {
        "board_decision": decision,
        "scope_options": [],
        "resource_matches": matches,
        "reference_prompt": None,
        "selected_reference": _selected_reference_context(state["course_package"], lesson, request, requirements),
    }


def _run_board_executor(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    selected_reference = state.get("selected_reference")

    if decision.action in {"clarify_request", "await_scope_choice", "await_reference_choice"}:
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": None,
        }

    if decision.action == "no_change":
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": _resolve_board_teaching_guide(
                lesson=lesson,
                request=request,
                requirements=requirements,
                document=lesson.board_document,
                prefer_existing=True,
                selected_reference=selected_reference,
            ),
        }

    if decision.action == "create_new_lesson":
        topic = _extract_focus_terms(request.message)[0] if _extract_focus_terms(request.message) else request.message
        generated_lesson = build_lesson_for_topic(
            topic,
            requirements=requirements,
            reference_context=selected_reference,
        )
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=generated_lesson,
            request=request,
            requirements=requirements,
            document=generated_lesson.board_document,
            prefer_existing=True,
            selected_reference=selected_reference,
        )
        generated_lesson.board_teaching_guide = board_teaching_guide
        if generated_lesson.history_graph.commits:
            generated_lesson.history_graph.commits[-1].metadata["board_teaching_guide"] = board_teaching_guide.model_dump(mode="json")
        return {
            "teaching_guide": generated_lesson.teaching_guide,
            "teacher_document": generated_lesson.board_document,
            "document_updated": True,
            "generated_lesson": generated_lesson,
            "teacher_talk_track": None,
            "board_teaching_guide": board_teaching_guide,
        }

    ai_edit = openai_course_ai.generate_document_edit(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=lesson.history_graph.current_branch,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        interaction_mode=request.interaction_mode,
        scope_action=request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=_reference_payload(selected_reference, include_full_text=True),
    )

    if ai_edit is not None:
        replacement_doc = build_document(
            title=ai_edit.suggested_title or lesson.board_document.title,
            content_html=ai_edit.replacement_html,
            content_text=ai_edit.replacement_text or None,
            document_id=lesson.board_document.id,
        )
        if (
            request.selection
            and request.interaction_mode == "direct_edit"
            and not _is_full_rewrite_request(request.message)
        ):
            replacement_text = _merge_selection_edit(
                selection_text=request.selection.excerpt,
                generated_text=replacement_doc.content_text or html_to_text(ai_edit.replacement_html),
                request_message=request.message,
            )
            next_document = replace_selection_in_document(
                lesson.board_document,
                selection_text=request.selection.excerpt,
                replacement_text=replacement_text,
            )
        elif decision.action == "append_section" and not ai_edit.replace_whole:
            next_document = append_html_section(lesson.board_document, replacement_doc.content_html)
        else:
            next_document = replacement_doc
        teacher_talk_track = ai_edit.teacher_talk_track.strip() or None
        board_teaching_guide = _bound_board_teaching_guide(
            guidance=ai_edit.board_teaching_guide,
            document=next_document,
            requirements=requirements,
            request_message=request.message,
            selected_reference=selected_reference,
        )
    else:
        next_document = _fallback_document_update(
            lesson=lesson,
            request=request,
            decision=decision,
            selected_reference=selected_reference,
        )
        teacher_talk_track = None
        board_teaching_guide = _resolve_board_teaching_guide(
            lesson=lesson,
            request=request,
            requirements=requirements,
            document=next_document,
            prefer_existing=False,
            selected_reference=selected_reference,
        )

    guide = _interactive_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=next_document,
        requirements=requirements,
    )
    return {
        "teaching_guide": guide,
        "teacher_document": next_document,
        "document_updated": document_changed(lesson.board_document, next_document),
        "generated_lesson": None,
        "teacher_talk_track": teacher_talk_track,
        "board_teaching_guide": board_teaching_guide,
    }


def _run_teacher(state: WorkflowState) -> WorkflowState:
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    reference_prompt = state.get("reference_prompt")
    selected_reference = state.get("selected_reference")
    teacher_talk_track = (state.get("teacher_talk_track") or "").strip()
    board_teaching_guide = state.get("board_teaching_guide")

    if decision.action in {"clarify_request", "await_scope_choice", "await_reference_choice"}:
        return {"teacher_message": _fallback_teacher_message(state)}
    if teacher_talk_track and decision.action in {"edit_board", "append_section"}:
        return {"teacher_message": _teacher_message_from_talk_track(state, teacher_talk_track)}
    if decision.action == "create_new_lesson":
        return {"teacher_message": _fallback_teacher_message(state)}
    if board_teaching_guide is None:
        return {"teacher_message": _fallback_teacher_message(state)}

    ai_message = openai_course_ai.generate_teacher_message(
        lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
        request_message=request.message,
        requirements=requirements,
        board_teaching_guide=board_teaching_guide,
        board_decision=decision,
        document_updated=state.get("document_updated", False),
        scope_options=state.get("scope_options", []),
        resource_matches=[match.model_dump(mode="json") for match in state.get("resource_matches", [])],
        clarification_questions=state.get("clarification_questions", []),
        reference_prompt=reference_prompt.model_dump(mode="json") if reference_prompt else None,
        selected_reference=_reference_payload(selected_reference, include_full_text=False),
    )
    return {"teacher_message": ai_message or _fallback_teacher_message(state)}


class SimpleCourseWorkflow:
    def invoke(self, initial_state: WorkflowState) -> WorkflowState:
        state: WorkflowState = dict(initial_state)
        state.update(_run_pm(state))
        state.update(_run_board_manager(state))
        state.update(_run_board_executor(state))
        state.update(_run_teacher(state))
        return state


course_workflow = SimpleCourseWorkflow()
