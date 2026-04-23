from __future__ import annotations

import re
from typing import TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
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
    build_internal_teaching_guide,
    build_lesson_for_topic,
    effective_requirements,
    normalize_requirements,
)
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
        "开始教学",
        "马上开始",
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
        progress += 15
    else:
        missing_items.append("想学的主题")

    profile_patterns = [
        r"\b[a-c][12]\b",
        r"\d+\s*(?:个)?(?:词|词汇|单词)",
        r"(?:零基础|初学|入门|进阶|高级|水平|学习者|基础|b1|b2|c1)",
    ]
    if any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in profile_patterns):
        progress += 20
    else:
        missing_items.append("当前水平或背景")

    scenario_patterns = [
        "旅游",
        "咖啡",
        "点餐",
        "考试",
        "面试",
        "写作",
        "阅读",
        "工作",
        "项目",
        "题目",
        "场景",
        "情景",
        "章节",
        "法国",
        "餐厅",
    ]
    if any(pattern in compact for pattern in scenario_patterns) or request.selection:
        progress += 20
    else:
        missing_items.append("具体使用场景或知识点")

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
        "一篇",
        "一段",
        "整理",
        "文档",
    ]
    if any(pattern in compact for pattern in output_patterns):
        progress += 20
    else:
        missing_items.append("希望得到的输出形式")

    constraint_patterns = [
        "公式",
        "语法",
        "过去",
        "将来",
        "时态",
        "深度",
        "难度",
        "b2",
        "词汇",
        "不能",
        "需要",
        "要用",
        "包含",
        "重点",
        "双语",
    ]
    if any(pattern in compact for pattern in constraint_patterns):
        progress += 15
    else:
        missing_items.append("重点约束或学习深度")

    outcome_patterns = [
        "为了",
        "我要",
        "能够",
        "掌握",
        "学会",
        "复述",
        "完成",
        "旅游",
        "考试",
        "使用",
    ]
    if any(pattern in compact for pattern in outcome_patterns):
        progress += 10
    else:
        missing_items.append("学习目的或成功标准")

    progress = max(0, min(progress, 100))
    forced_start = _is_forced_start_request(message)
    can_start = progress >= 60 or forced_start or request.interaction_mode == "direct_edit"
    if progress >= 90:
        label = "需求已清楚"
        reason = "学习主题、水平、场景、输出形式和重点约束都比较明确，可以直接进入讲义生成或教学。"
    elif can_start:
        label = "可以先开始"
        reason = "需求还可以继续补充，但已经足够先推进一版完整讲义。"
    else:
        label = "还需澄清"
        reason = "当前信息偏粗，继续问清楚后文档生成和讲解会更贴合。"

    if forced_start and progress < 90:
        reason = "用户明确要求先开始教学，因此会依据当前信息先推进，并在过程中继续补齐需求。"

    return LearningClarificationStatus(
        progress=progress,
        label=label,
        reason=reason,
        missing_items=missing_items[:4],
        can_start=can_start,
        forced_start=forced_start,
    )


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


def _chapter_overlap_score(query_text: str, *, chapter_title: str, chapter_summary: str, keywords: list[str]) -> tuple[float, list[str]]:
    lowered_query = query_text.lower()
    phrases = _query_phrases(query_text)
    hits: list[str] = []
    score = 0.0

    title_lower = chapter_title.lower()
    title_terms = [title_lower, *_expanded_match_terms(chapter_title)]
    if any(term and term in lowered_query for term in title_terms):
        score += 0.58
        hits.append(chapter_title)
    elif any(len(phrase) >= 4 and any(phrase in term for term in title_terms) for phrase in phrases):
        score += 0.45
        hits.append(chapter_title)

    summary_lower = chapter_summary.lower()
    expanded_keywords = [*keywords, *_expanded_match_terms(chapter_title, chapter_summary, " ".join(keywords))]
    for keyword in expanded_keywords:
        lowered_keyword = keyword.lower().strip()
        if len(lowered_keyword) < 2:
            continue
        if lowered_keyword in lowered_query:
            score += 0.16
            hits.append(keyword)
        elif lowered_keyword in summary_lower:
            score += 0.04

    corpus = f"{chapter_title} {chapter_summary}".lower()
    for phrase in phrases:
        if phrase in corpus:
            score += 0.08
            hits.append(phrase)

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
            primary_score, primary_overlap = _chapter_overlap_score(
                primary_query_text,
                chapter_title=chapter.title,
                chapter_summary=chapter.summary,
                keywords=chapter.keywords,
            )
            score, overlap = _chapter_overlap_score(
                query_text,
                chapter_title=chapter.title,
                chapter_summary=chapter.summary,
                keywords=chapter.keywords,
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
                scored_matches.append((primary_score, score, matches[-1]))
    scored_matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored_matches[:3]]


def _build_reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=(
            f"我发现你当前想学的内容和《{match.resource_name}》的《{match.chapter_title}》高度相关，"
            "要不要参考这一章节来生成讲义？"
        ),
        reason=match.reason,
        score=match.score,
    )


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
    requirements.current_questions = [*user_turns[-3:], request.message][-4:]
    if request.selection:
        requirements.current_questions.append(f"用户框选内容：{request.selection.excerpt[:80]}")
    requirements.boundary = "优先围绕当前 lesson 的整篇文档主线；超出范围时先决定是仅讲解、补充章节还是新开 lesson。"
    return normalize_requirements(requirements, lesson_title=lesson.title, document=lesson.board_document)


def _clarification_questions_for_status(status: LearningClarificationStatus) -> list[str]:
    question_map = {
        "想学的主题": "你现在最想学的具体主题是什么？",
        "当前水平或背景": "你现在大概是什么水平，或者已经掌握了哪些基础？",
        "具体使用场景或知识点": "这个内容准备用在哪个具体场景或题目里？",
        "希望得到的输出形式": "你希望我输出讲解、讲义、完整对话、例题还是练习？",
        "重点约束或学习深度": "你希望重点讲到什么深度，或者必须包含哪些知识点？",
        "学习目的或成功标准": "学完以后你希望自己能做到什么？",
    }
    return [question_map[item] for item in status.missing_items if item in question_map][:3]


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
    if any(keyword in message for keyword in ["解释", "讲一下", "讲讲", "为什么", "什么意思", "怎么理解"]):
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

    generated = build_lesson_for_topic(
        request.message.strip() or lesson.title,
        requirements=effective_requirements(lesson),
        reference_context=selected_reference,
    )
    return generated.board_document.model_copy(update={"id": lesson.board_document.id})


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


def _fallback_teacher_message(state: WorkflowState) -> str:
    request = state["request"]
    decision = state["board_decision"]
    document = state.get("teacher_document") or state["lesson"].board_document
    clarification_questions = state.get("clarification_questions", [])
    reference_prompt = state.get("reference_prompt")
    selected_reference = state.get("selected_reference")
    lesson_title = (state.get("generated_lesson") or state["lesson"]).title

    if decision.action == "clarify_request":
        numbered = "\n".join(
            f"{index}. {question}" for index, question in enumerate(clarification_questions or ["你最想先解决哪个具体问题？"], start=1)
        )
        return "我先不急着改讲义，想先把你的学习需求确认清楚，这样右侧文档会更贴合你。\n" + numbered
    if decision.action == "await_reference_choice" and reference_prompt is not None:
        return reference_prompt.question
    if decision.action == "await_scope_choice":
        return f"这个问题已经超出《{lesson_title}》当前讲义范围。你可以先决定是只在当前课里简述、在本课新增章节，还是直接新开一节详细 lesson。"

    intro = "这次我先沿着当前讲义给你讲。"
    if state.get("document_updated"):
        intro = "我已经把右侧 Word 式讲义更新好了，下面按新的讲义结构带你看重点。"
    elif decision.action == "create_new_lesson":
        intro = f"我已经把这个问题拆成新课《{lesson_title}》，我们先抓主线。"

    lines = [intro]
    for index, line in enumerate(_relevant_lines(document, request), start=1):
        lines.append(f"{index}. {line}")
    if selected_reference is not None:
        lines.append(
            f"这次我还参考了《{selected_reference.resource_name}》的《{selected_reference.chapter_title}》，"
            "但已经把它改写成更适合教学的讲义表达。"
        )
    lines.append("如果你愿意，我还可以继续把其中一段改写成更适合朗读或课堂讲解的版本。")
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

    needs_clarification = draft_status.progress < 35 and not draft_status.forced_start
    questions = _clarification_questions_for_status(draft_status) if needs_clarification else []
    if draft_status.progress >= 60 or draft_status.forced_start:
        needs_clarification = False
        questions = []
    return {
        "learning_requirement_sheet": draft_requirements,
        "learning_clarification": draft_status,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions[:3],
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

    high_overlap_match = next((match for match in matches if match.is_high_overlap), None)
    if (
        request.resource_reference_action is None
        and decision.action in {"edit_board", "append_section", "create_new_lesson"}
        and high_overlap_match is not None
    ):
        return {
            "board_decision": BoardDecision(
                action="await_reference_choice",
                reason="已发现和当前学习目标高度相关的资料章节，先确认是否将该章节作为讲义参考。",
            ),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": _build_reference_prompt(high_overlap_match),
            "selected_reference": None,
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

    if decision.action in {"clarify_request", "await_scope_choice", "await_reference_choice", "no_change"}:
        guide = build_internal_teaching_guide(
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
        }

    if decision.action == "create_new_lesson":
        topic = _extract_focus_terms(request.message)[0] if _extract_focus_terms(request.message) else request.message
        generated_lesson = build_lesson_for_topic(
            topic,
            requirements=requirements,
            reference_context=selected_reference,
        )
        return {
            "teaching_guide": generated_lesson.teaching_guide,
            "teacher_document": generated_lesson.board_document,
            "document_updated": True,
            "generated_lesson": generated_lesson,
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
    else:
        next_document = _fallback_document_update(
            lesson=lesson,
            request=request,
            decision=decision,
            selected_reference=selected_reference,
        )

    guide = build_internal_teaching_guide(
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
    }


def _run_teacher(state: WorkflowState) -> WorkflowState:
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    decision = state["board_decision"]
    teacher_document = state.get("teacher_document") or state["lesson"].board_document
    reference_prompt = state.get("reference_prompt")
    selected_reference = state.get("selected_reference")

    ai_message = openai_course_ai.generate_teacher_message(
        lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
        request_message=request.message,
        requirements=requirements,
        document=teacher_document,
        guide=state["teaching_guide"],
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
