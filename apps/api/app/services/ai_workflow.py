from __future__ import annotations

import hashlib
import re
from typing import Any, TypedDict

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
from app.services.chart_generation import augment_document_with_generated_charts
from app.services.fallback_generator import reference_document_fallback_html
from app.services.learning_workflow.roles.pm_interviewer import generate_pm_interview_message
from app.services.learning_workflow.roles.requirement_manager import draft_requirement_state
from app.services.lesson_factory import build_requirements, build_teaching_guide
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
OUTLINE_NUMBER_PATTERN = r"([0-9一二三四五六七八九十百〇零两]+)"


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
    teacher_message_source: str
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


def _compact_request_text(value: str) -> str:
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’]+", "", value or "")


def _tokenize(value: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+\-.]*|[\u4e00-\u9fff]{2,}", value or "")
        if len(token.strip()) >= 2
    ]


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


def _lesson_corpus(lesson: Lesson) -> str:
    return " ".join([lesson.title, lesson.summary, *(lesson.tags or []), lesson.board_document.content_text]).lower()


def _extract_focus_terms(message: str) -> list[str]:
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", message or "")
    if quoted:
        return _dedupe(quoted, limit=6)
    return _dedupe(re.findall(r"[A-Za-z][A-Za-z0-9_+\-.]*|[\u4e00-\u9fff]{2,}", message or ""), limit=8)


def _query_phrases(text: str) -> list[str]:
    chunks = re.split(r"[\s，。！？?!.、/（）()：:；;,\n]+", text or "")
    phrases = [chunk.strip().lower() for chunk in chunks if len(chunk.strip()) >= 2]
    phrases.extend(term.lower() for term in _extract_focus_terms(text))
    topic = _extract_topic_hint(text)
    if topic:
        phrases.append(topic.lower())
    return _dedupe(phrases, limit=16)


def classify_scope(message: str, lesson: Lesson) -> str:
    if _is_append_document_request(message):
        return "scope_escalation"
    if _is_in_place_expansion_request(message):
        return "in_scope"
    compact = _compact_request_text(message)
    if any(signal in compact for signal in ("习题", "练习", "例题", "总结", "整理", "改写", "润色", "解释", "讲解")):
        return "in_scope"
    if "什么是" in compact or "what is" in (message or "").lower():
        lesson_text = _lesson_corpus(lesson)
        unknown_terms = [term for term in _extract_focus_terms(message) if term.lower() not in lesson_text]
        if unknown_terms:
            return "scope_escalation"
    return "in_scope"


def _is_board_generation_request(message: str) -> bool:
    compact = _compact_request_text(message)
    generation_verbs = ("生成", "写", "编", "创作", "设计", "做", "输出", "整理成", "给我", "完善")
    artifacts = ("板书", "讲义", "文档", "课程", "课文", "对话", "练习", "例题", "章节", "专题", "页面")
    return any(verb in compact for verb in generation_verbs) and any(artifact in compact for artifact in artifacts)


def _has_explicit_append_intent(message: str) -> bool:
    compact = _compact_request_text(message)
    targets = ("页面", "一页", "几页", "多页", "章节", "新章节", "一节", "几节", "整章", "内容")
    forward_signals = ("继续写", "续写", "接着写", "再写", "往后写", "继续生成")
    create_signals = ("新增", "追加", "新生成", "添加")
    if any(signal in compact for signal in forward_signals) and any(target in compact for target in targets):
        return True
    if any(signal in compact for signal in create_signals) and any(target in compact for target in targets):
        return True
    if "补充" in compact and any(target in compact for target in targets[:-1]):
        return True
    return any(marker in compact for marker in ("在后面补", "在末尾补", "追加到末尾", "接在后面", "放到最后"))


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
        "讲透",
        "更详细",
        "更细致",
        "详细讲解",
        "详细解析",
        "全面",
    )
    current_targets = ("板书", "版书", "讲义", "文档", "内容", "当前", "原有", "已有", "这一节", "这节", "这一章", "这章")
    return any(signal in compact for signal in expansion_signals) and any(target in compact for target in current_targets)


def _is_forced_start_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(
        signal in compact
        for signal in ("直接开始", "直接开讲", "先讲", "马上开始", "不用问", "从零开始", "你自己看着办", "你来安排", "都可以")
    )


def _is_explanation_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(signal in compact for signal in ("什么是", "解释", "讲解", "不理解", "没懂", "为什么", "怎么理解"))


def _is_full_rewrite_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(signal in compact for signal in ("重写全文", "重新生成全文", "整篇重写", "全部重写", "完整重写"))


def _is_explicit_board_edit_request(message: str) -> bool:
    return _is_board_generation_request(message) or _is_append_document_request(message) or _is_in_place_expansion_request(message)


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

def _available_reference_resources(course_package: CoursePackage, lesson: Lesson) -> list[ResourceLibraryItem]:
    return [
        resource
        for resource in course_package.resources
        if resource.scope_lesson_id in {None, lesson.id} and resource.outline
    ]


def _resource_query_text(request: ChatRequest, requirements: LearningRequirementSheet, lesson: Lesson) -> str:
    document_text = "\n".join([lesson.board_document.title, lesson.board_document.content_text[:1200]])
    return "\n".join([request.message, requirements.theme, " ".join(requirements.current_questions), document_text])


def _is_resource_followup_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(signal in compact for signal in ("资料", "教材", "文件", "章节", "第一章", "第1章", "这章", "本章", "这一节", "本节", "讲一下"))


def _status_with_resource_context_default(
    status: LearningClarificationStatus,
    *,
    resource_count: int,
) -> LearningClarificationStatus:
    if resource_count <= 0 or status.progress > 0:
        return status
    return status.model_copy(update={"progress": 35, "can_start": True, "missing_items": []})


def _should_use_resource_followup_context(
    *,
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
) -> bool:
    return bool(_available_reference_resources(course_package, lesson)) and _is_resource_followup_request(request.message)


def _parse_outline_number(value: str | None) -> int | None:
    if not value:
        return None
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw in digits:
        return digits[raw]
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def _extract_requested_outline_reference(text: str) -> tuple[int | None, int | None]:
    chapter_match = re.search(rf"第{OUTLINE_NUMBER_PATTERN}章", text or "")
    section_match = re.search(rf"第{OUTLINE_NUMBER_PATTERN}(?:节|小节)", text or "")
    return (
        _parse_outline_number(chapter_match.group(1)) if chapter_match else None,
        _parse_outline_number(section_match.group(1)) if section_match else None,
    )


def _extract_numbered_title_reference(title: str) -> tuple[int | None, int | None]:
    return _extract_requested_outline_reference(title)


def _is_reference_separator_title(title: str) -> bool:
    compact = _compact_request_text(title).lower()
    return compact in {"前言", "目录", "contents", "toc", "preface", "index", "参考文献", "出版说明"}


def _outline_reference_position(resource: ResourceLibraryItem, chapter: LibraryChapter) -> tuple[int | None, int | None]:
    chapter_no, section_no = _extract_numbered_title_reference(chapter.title)
    if chapter_no is not None or section_no is not None:
        return chapter_no, section_no
    if chapter.parent_title:
        parent_chapter_no, _ = _extract_numbered_title_reference(chapter.parent_title)
        siblings = [
            candidate
            for candidate in resource.outline
            if candidate.parent_id == chapter.parent_id and not _is_reference_separator_title(candidate.title)
        ]
        if parent_chapter_no is not None and chapter in siblings:
            return parent_chapter_no, siblings.index(chapter) + 1
    return None, None


def _resource_name_overlap_score(query_text: str, resource_name: str) -> tuple[float, list[str]]:
    query_compact = _compact_request_text(query_text).lower()
    name_compact = re.sub(r"\.[a-z0-9]+$", "", _compact_request_text(resource_name).lower())
    if name_compact and name_compact in query_compact:
        return 1.0, [resource_name]
    name_tokens = _tokenize(resource_name)
    matched = [token for token in name_tokens if token in query_compact]
    if not name_tokens:
        return 0.0, []
    return min(1.0, len(matched) / max(1, len(name_tokens))), matched


def _query_mentions_resource_name(message: str, resources: list[ResourceLibraryItem]) -> bool:
    compact = _compact_request_text(message).lower()
    return any(_compact_request_text(resource.name).lower().replace("md", "")[:4] in compact for resource in resources)


def _chapter_body_quality_score(chapter: LibraryChapter) -> float:
    score = 0.0
    if chapter.summary.strip():
        score += 0.2
    if chapter.keywords:
        score += 0.2
    if chapter.scan_strategy in {"heading_section", "page_window", "fulltext_match"}:
        score += 0.2
    if not _is_reference_separator_title(chapter.title):
        score += 0.4
    return score


def _default_chapter_for_resource(resource: ResourceLibraryItem, request: ChatRequest) -> LibraryChapter | None:
    chapter_no, section_no = _extract_requested_outline_reference(request.message)
    candidates = [chapter for chapter in resource.outline if not _is_reference_separator_title(chapter.title)]
    if not candidates:
        return None
    top_level = [chapter for chapter in candidates if chapter.level <= 1 or chapter.parent_id is None]
    if chapter_no is not None and 1 <= chapter_no <= len(top_level):
        parent = top_level[chapter_no - 1]
        if section_no is None:
            return parent
        descendants: list[LibraryChapter] = []
        started = False
        for candidate in resource.outline:
            if candidate.id == parent.id:
                started = True
                continue
            if not started:
                continue
            if candidate.level <= parent.level:
                break
            if not _is_reference_separator_title(candidate.title):
                descendants.append(candidate)
        if 1 <= section_no <= len(descendants):
            return descendants[section_no - 1]
        return parent
    for chapter in candidates:
        found_chapter, found_section = _outline_reference_position(resource, chapter)
        if chapter_no is not None and found_chapter == chapter_no and (section_no is None or found_section == section_no):
            return chapter
    return max(candidates[:5], key=_chapter_body_quality_score)


def _chapter_overlap_score(query_text: str, resource: ResourceLibraryItem, chapter: LibraryChapter) -> tuple[float, list[str]]:
    query_terms = set(_query_phrases(query_text))
    chapter_terms = set(_query_phrases(" ".join([chapter.title, chapter.summary, " ".join(chapter.keywords), resource.name])))
    if not query_terms or not chapter_terms:
        return 0.0, []
    matched = sorted(
        query
        for query in query_terms
        if any(query in chapter or chapter in query for chapter in chapter_terms)
    )
    score = len(matched) / max(3, min(len(query_terms), len(chapter_terms)))
    query_compact = _compact_request_text(query_text).lower()
    title_compact = _compact_request_text(chapter.title).lower()
    if title_compact and title_compact in query_compact:
        score = max(score, 0.9)
        matched.append(chapter.title)
    name_score, name_matches = _resource_name_overlap_score(query_text, resource.name)
    return min(1.0, score + name_score * 0.25), _dedupe([*matched, *name_matches], limit=8)


def match_resources(
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> list[ResourceMatch]:
    resources = _available_reference_resources(course_package, lesson)
    if not resources:
        return []
    query_text = _resource_query_text(request, requirements, lesson)
    requested_chapter, _ = _extract_requested_outline_reference(request.message)
    matches: list[ResourceMatch] = []
    for resource in resources:
        named_score, named_terms = _resource_name_overlap_score(request.message, resource.name)
        chapter_candidates = resource.outline
        if requested_chapter is not None or (len(resources) == 1 and _is_resource_followup_request(request.message)):
            default = _default_chapter_for_resource(resource, request)
            chapter_candidates = [default] if default is not None else []
        best: ResourceMatch | None = None
        for chapter in chapter_candidates:
            if chapter is None or _is_reference_separator_title(chapter.title):
                continue
            score, matched_terms = _chapter_overlap_score(query_text, resource, chapter)
            if requested_chapter is not None:
                chapter_no, section_no = _outline_reference_position(resource, chapter)
                requested_section = _extract_requested_outline_reference(request.message)[1]
                if chapter_no == requested_chapter and (requested_section is None or section_no == requested_section):
                    score = max(score, 0.95)
                elif chapter == _default_chapter_for_resource(resource, request):
                    score = max(score, 0.88)
            if named_score > 0:
                score = max(score, 1.0 if named_score >= 1 else 0.7 + named_score * 0.2)
                matched_terms.extend(named_terms)
            if len(resources) == 1 and _is_resource_followup_request(request.message):
                score = max(score, 0.78)
            if score <= 0 and not _is_resource_followup_request(request.message):
                continue
            candidate = ResourceMatch(
                resource_id=resource.id,
                chapter_id=chapter.id,
                resource_name=resource.name,
                chapter_title=chapter.title,
                reason="根据请求文本、资料名、章节标题和目录位置进行通用匹配。",
                score=round(score, 4),
                is_high_overlap=score >= HIGH_OVERLAP_THRESHOLD,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        if best is not None:
            matches.append(best)
    return sorted(matches, key=lambda match: match.score, reverse=True)


def _resource_file_clarification_question(resources: list[ResourceLibraryItem], matches: list[ResourceMatch]) -> str:
    names = _dedupe([match.resource_name for match in matches] or [resource.name for resource in resources], limit=4)
    return f"你想参考哪一份资料？可选：{'、'.join(names)}。"


def _should_clarify_resource_file(
    *,
    resources: list[ResourceLibraryItem],
    matches: list[ResourceMatch],
    request: ChatRequest,
) -> bool:
    if len(resources) <= 1 or not _is_resource_followup_request(request.message):
        return False
    if _query_mentions_resource_name(request.message, resources):
        return False
    if not matches:
        return False
    return len({match.resource_id for match in matches[:3]}) > 1


def _build_reference_prompt(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        question=f"我找到了《{match.resource_name}》里的“{match.chapter_title}”。要参考这章正文来生成或讲解吗？",
        reason=match.reason,
        score=match.score,
    )


def _build_scope_options(matches: list[ResourceMatch]) -> list[ScopeOption]:
    return [
        ScopeOption(
            action="patch_current_lesson",
            label="更新当前讲义",
            description="在当前 lesson 内完成这次修改。",
            resource_chapter_id=matches[0].chapter_id if matches else None,
        ),
        ScopeOption(
            action="create_child_lesson",
            label="新建子 lesson",
            description="把这次内容作为当前 lesson 的延伸。",
            resource_chapter_id=matches[0].chapter_id if matches else None,
        ),
    ]


def _should_auto_attach_reference_for_direct_teaching(
    *,
    request: ChatRequest,
    decision: BoardDecision,
    top_match: ResourceMatch | None,
) -> bool:
    return top_match is not None and (
        request.resource_reference_action == "confirm"
        or (_is_forced_start_request(request.message) and top_match.is_high_overlap)
        or (decision.action == "no_change" and _is_resource_followup_request(request.message))
    )


def _reference_context_for_match(
    course_package: CoursePackage,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    match: ResourceMatch,
) -> ResourceReferenceContext | None:
    for resource in _available_reference_resources(course_package, lesson):
        if resource.id == match.resource_id:
            return extract_reference_context(resource, match.chapter_id, user_query=request.message)
    return None


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
    for resource in _available_reference_resources(course_package, lesson):
        if resource.id == resource_id:
            return extract_reference_context(resource, chapter_id, user_query=request.message)
    return None


def _reference_payload(reference: ResourceReferenceContext | None, *, include_full_text: bool) -> dict[str, Any] | None:
    if reference is None:
        return None
    payload: dict[str, Any] = {
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


def _build_board_edit_prompt(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> BoardEditPrompt:
    topic = _board_edit_prompt_topic(request, requirements)
    return BoardEditPrompt(
        topic=topic,
        question=f"要不要把“{topic}”整理成一份可编辑讲义？",
        reason="用户已经给出可学习主题，但还没有明确要求写入板书。",
    )


def _board_edit_prompt_topic(request: ChatRequest, requirements: LearningRequirementSheet) -> str:
    return request.board_edit_topic or _extract_topic_hint(request.message) or requirements.theme


def _should_offer_board_edit_prompt(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    decision: BoardDecision,
) -> bool:
    return (
        decision.action == "no_change"
        and bool(_extract_topic_hint(request.message))
        and not _is_board_generation_request(request.message)
        and request.board_edit_action is None
    )


def _fallback_board_decision(
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    matches: list[ResourceMatch],
) -> BoardDecision:
    if request.interaction_mode == "direct_edit":
        return BoardDecision(action="append_section" if _is_append_document_request(request.message) else "edit_board", reason="用户在编辑入口提交修改。")
    if request.board_edit_action == "confirm" and _is_confirmed_section_followup_learning_need(lesson, request):
        return BoardDecision(action="append_section", reason="用户确认把当前分节追问补入讲义。")
    if request.board_edit_action == "confirm":
        return BoardDecision(action="append_section" if _is_append_document_request(request.message) else "edit_board", reason="用户确认写入板书。")
    if request.board_edit_action == "skip":
        return BoardDecision(action="no_change", reason="用户选择暂不写入板书。")
    if _is_append_document_request(request.message):
        return BoardDecision(action="append_section", reason="用户要求追加新内容。")
    if _is_in_place_expansion_request(request.message):
        return BoardDecision(action="edit_board", reason="用户要求扩展当前讲义内容。")
    if _is_board_generation_request(request.message):
        return BoardDecision(action="edit_board", reason="用户明确要求生成或整理讲义。")
    if is_document_empty(lesson.board_document) and matches and _is_forced_start_request(request.message):
        return BoardDecision(action="edit_board", reason="空白讲义且用户要求直接开始。")
    return BoardDecision(action="no_change", reason="普通追问默认先讲解，不直接改动板书。")


def _should_use_fast_board_path(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> bool:
    return _is_explicit_board_edit_request(request.message) or _is_forced_start_request(request.message)


def _important_terms_from_request(message: str) -> list[str]:
    if _is_topicless_control_request(message):
        return []
    terms: list[str] = []
    coverage = re.search(r"(?:覆盖|包括|重点讲|围绕)(.+)", message or "")
    if coverage:
        tail = re.split(r"生成后|并且|要求|。|！|？", coverage.group(1))[0]
        terms.extend(re.split(r"[、,，和与及/]", tail))
    topic = _append_section_topic(message, build_requirements("补充内容")) if _is_append_document_request(message) else _extract_topic_hint(message)
    if topic:
        terms.insert(0, topic)
    if not coverage:
        terms.extend(_extract_focus_terms(message))
    cleaned = [
        re.sub(r"^(覆盖|包括|重点讲|关于)", "", term).strip()
        for term in terms
        if 2 <= len(term.strip()) <= 40
        and not any(signal in term for signal in ("请生成", "生成一份", "生成一版", "生成后", "讲义", "板书", "续写", "新章节"))
    ]
    return _dedupe(cleaned, limit=12)


def _requested_section_count(message: str) -> int:
    match = re.search(r"至少\s*([0-9]{1,2})\s*(?:个)?(?:小节|节|部分)", message or "")
    if match:
        return max(3, min(30, int(match.group(1))))
    if "整章" in (message or "") or "完整章节" in (message or ""):
        return 10
    return 8


def _fallback_selection_replacement(request: ChatRequest) -> str:
    excerpt = request.selection.excerpt if request.selection else ""
    if _is_selection_enhancement_request(request.message):
        return f"{excerpt}\n\n补充解析：先保留原有表述，再补充关键条件、推理步骤、例子和检查问题。"
    return "换一种更好懂的说法：先说明这句话要解决的问题，再用更直接的语言讲清核心关系。"


def _is_selection_enhancement_request(message: str) -> bool:
    compact = _compact_request_text(message)
    return any(signal in compact for signal in ("完善", "补充", "全面", "更详细", "展开", "细化", "丰富"))


def _merge_selection_edit(document: BoardDocument, request: ChatRequest, ai_edit: Any | None) -> BoardDocument:
    replacement_text = ai_edit.replacement_text if ai_edit and ai_edit.replacement_text else _fallback_selection_replacement(request)
    replacement_html = ai_edit.replacement_html if ai_edit else None
    return replace_selection_in_document(
        document,
        selection_text=request.selection.excerpt if request.selection else "",
        replacement_text=replacement_text,
        replacement_html=replacement_html,
    )


def _append_section_topic(message: str, requirements: LearningRequirementSheet) -> str:
    split_message = re.split(r"[，,：:]", message or "", maxsplit=1)
    if len(split_message) > 1 and any(signal in split_message[0] for signal in ("续写", "继续写", "新增", "追加", "补充")):
        topic = _clean_topic_hint(split_message[1])
        if topic:
            return topic
    topic = _extract_topic_hint(message)
    if topic:
        return topic
    compact = re.sub(r"(续写|继续写|新增|追加|补充|一个|新章节|章节|一节|内容|页面)", "", message or "")
    return _clean_topic_hint(compact) or requirements.theme


def _append_request_already_applied(document: BoardDocument, message: str, requirements: LearningRequirementSheet) -> bool:
    if not _is_append_document_request(message):
        return False
    topic = _append_section_topic(message, requirements)
    compact_topic = _compact_request_text(topic)
    if len(compact_topic) < 3:
        return False
    compact_doc = _compact_request_text(document.content_text)
    heading_markers = (
        f"补充章节{compact_topic}",
        f"新增章节{compact_topic}",
        f"追加章节{compact_topic}",
        f"补充内容{compact_topic}",
    )
    return any(marker in compact_doc for marker in heading_markers)


def _document_edit_has_content(ai_edit: Any | None) -> bool:
    if ai_edit is None:
        return False
    return bool(html_to_text(ai_edit.replacement_html).strip() or ai_edit.replacement_text.strip())


def _document_generation_failure_message(decision: BoardDecision) -> str:
    return ""


def _reference_document_update(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    selected_reference: ResourceReferenceContext,
) -> BoardDocument:
    title = selected_reference.chapter_title or requirements.theme
    content_html = reference_document_fallback_html(requirements.theme, selected_reference)
    return build_document(title=title, content_html=content_html, document_id=lesson.board_document.id)


def _failed_document_generation_result(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    decision: BoardDecision,
    selected_reference: ResourceReferenceContext | None,
) -> WorkflowState:
    guide = _interactive_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=lesson.board_document,
        requirements=requirements,
    )
    board_guide = _resolve_board_teaching_guide(
        lesson=lesson,
        request=request,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=selected_reference,
    )
    return {
        "teaching_guide": guide,
        "teacher_document": lesson.board_document,
        "document_updated": False,
        "generated_lesson": None,
        "teacher_talk_track": _document_generation_failure_message(decision),
        "board_teaching_guide": board_guide,
        "board_teaching_progress": lesson.board_teaching_progress,
        "teaching_progress": _section_progress_view(lesson.board_teaching_progress, board_guide),
    }


def _board_snapshot_hash(document: BoardDocument) -> str:
    raw = "\n".join([document.id, document.title, document.content_html, document.content_text])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _board_h2_sections(document: BoardDocument) -> list[tuple[str, str]]:
    html_content = document.content_html or ""
    matches = list(re.finditer(r"<h2\b[^>]*>(.*?)</h2>", html_content, flags=re.IGNORECASE | re.DOTALL))
    if not matches:
        lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
        if not lines:
            return []
        return [(line, lines[index + 1] if index + 1 < len(lines) else "") for index, line in enumerate(lines[:8])]
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(html_content)
        heading = html_to_text(match.group(0))
        body = html_to_text(html_content[start:end])
        sections.append((heading, body))
    return sections


def _compact_teaching_line(value: str, limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _fallback_section_plans(document: BoardDocument, requirements: LearningRequirementSheet) -> list[BoardSectionTeachingPlan]:
    sections = _board_h2_sections(document)
    if not sections and document.content_text.strip():
        sections = [(document.title, document.content_text)]
    plans: list[BoardSectionTeachingPlan] = []
    for index, (heading, body) in enumerate(sections, start=0):
        excerpt = _compact_teaching_line(body or heading)
        plans.append(
            BoardSectionTeachingPlan(
                order_index=index,
                heading=heading,
                board_excerpt=excerpt,
                core_points=[heading],
                teaching_steps=["先讲本节主线", "解释关键关系", "用例子或检查问题收束"],
                teaching_method="从问题出发，结合板书内容逐步解释。",
                example_or_analogy="选择一个与本节概念结构相似的最小场景。",
                common_pitfalls=["只记结论而忽略适用条件", "不能把概念迁移到新问题"],
                check_question=f"你能说明“{heading}”解决什么问题吗？",
                transition_to_next="把本节结论连接到下一节的新问题。",
            )
        )
    return plans


def _fallback_board_teaching_guide(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    document: BoardDocument,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    section_plans = _fallback_section_plans(document, requirements)
    selected_items = [
        BoardTeachingSelectedItem(
            excerpt=plan.board_excerpt or plan.heading,
            source_heading=plan.heading,
            reason="根据当前板书结构选择可讲解片段。",
            mapped_needs=requirements.learning_need_checklist[:3],
            order_index=index,
        )
        for index, plan in enumerate(section_plans[:5], start=1)
    ]
    if selected_reference is not None and not selected_items:
        excerpt = selected_reference.chunks[0].excerpt if selected_reference.chunks else selected_reference.summary
        selected_items.append(
            BoardTeachingSelectedItem(
                excerpt=excerpt,
                source_heading=selected_reference.chapter_title,
                reason="根据用户确认的资料章节生成内部讲解依据。",
                mapped_needs=requirements.learning_need_checklist[:3],
                order_index=1,
            )
        )
    lecture_parts = [requirements.learning_goal]
    if selected_reference is not None:
        lecture_parts.extend([selected_reference.summary, *selected_reference.teaching_points[:5]])
        lecture_parts.extend(chunk.excerpt for chunk in selected_reference.chunks[:4])
    else:
        lecture_parts.extend(item.excerpt for item in selected_items)
    lecture_handout = "\n\n".join(part for part in lecture_parts if part)
    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=_board_snapshot_hash(document),
        board_title=document.title,
        selected_items=selected_items,
        need_mappings=[
            BoardNeedMapping(
                need=need,
                matched_excerpt=selected_items[0].excerpt if selected_items else document.title,
                source_heading=selected_items[0].source_heading if selected_items else document.title,
                rationale="学习需求与当前板书或资料上下文相关。",
            )
            for need in requirements.learning_need_checklist[:5]
        ],
        teaching_flow=["确认学习入口", "讲清主线", "展开关键关系", "用检查问题收束"],
        generation_rationale="领域无关 fallback：仅依据用户目标、板书结构和资料上下文生成。",
        teacher_brief=_compact_teaching_line(lecture_handout, limit=240),
        lecture_handout=lecture_handout,
        section_plans=section_plans,
    )


def _bound_board_teaching_guide(
    guide: BoardTeachingGuide | None,
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    document: BoardDocument,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    fallback = _fallback_board_teaching_guide(
        lesson=lesson,
        request=request,
        requirements=requirements,
        document=document,
        selected_reference=selected_reference,
    )
    if guide is None:
        return fallback
    updates: dict[str, Any] = {
        "board_document_id": document.id,
        "board_snapshot_hash": _board_snapshot_hash(document),
        "board_title": document.title,
    }
    if not guide.section_plans:
        updates["section_plans"] = fallback.section_plans
    if not guide.lecture_handout:
        updates["lecture_handout"] = fallback.lecture_handout
    if not guide.selected_items:
        updates["selected_items"] = fallback.selected_items
    return guide.model_copy(update=updates)


def _current_board_teaching_guide(lesson: Lesson, document: BoardDocument) -> BoardTeachingGuide | None:
    guide = lesson.board_teaching_guide
    if guide and guide.board_document_id == document.id and guide.board_snapshot_hash == _board_snapshot_hash(document):
        return guide
    return None


def _interactive_teaching_guide(
    *,
    lesson_id: str,
    lesson_title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    return build_teaching_guide(lesson_id, lesson_title, document, requirements)


def _resolve_board_teaching_guide(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    document: BoardDocument,
    prefer_existing: bool = True,
    selected_reference: ResourceReferenceContext | None = None,
) -> BoardTeachingGuide:
    if prefer_existing:
        existing = _current_board_teaching_guide(lesson, document)
        if existing is not None:
            if not existing.section_plans:
                return _bound_board_teaching_guide(
                    existing,
                    lesson=lesson,
                    request=request,
                    requirements=requirements,
                    document=document,
                    selected_reference=selected_reference,
                )
            return existing
    generated = openai_course_ai.generate_board_teaching_guide(
        lesson_title=lesson.title,
        request_message=request.message,
        requirements=requirements,
        document=document,
    )
    return _bound_board_teaching_guide(
        generated,
        lesson=lesson,
        request=request,
        requirements=requirements,
        document=document,
        selected_reference=selected_reference,
    )


def _requirement_needs(requirements: LearningRequirementSheet) -> list[str]:
    return requirements.learning_need_checklist or requirements.current_questions


def _active_section_followup_context(lesson: Lesson) -> tuple[int, str, str] | None:
    progress = lesson.board_teaching_progress
    if not progress:
        return None
    plans = lesson.board_teaching_guide.section_plans if lesson.board_teaching_guide else []
    if not plans:
        requirements = lesson.learning_requirements or build_requirements(lesson.title)
        plans = _fallback_section_plans(lesson.board_document, requirements)
    if not plans:
        return None
    index = max(0, min(progress.current_section_index, len(plans) - 1))
    plan = plans[index]
    return index, plan.heading, plan.board_excerpt


def _is_section_followup_learning_need(lesson: Lesson, request: ChatRequest) -> bool:
    if request.teaching_action == "continue":
        return False
    return _active_section_followup_context(lesson) is not None and bool(_extract_focus_terms(request.message))


def _is_confirmed_section_followup_learning_need(lesson: Lesson, request: ChatRequest) -> bool:
    return request.board_edit_action == "confirm" and _is_section_followup_learning_need(lesson, request)


def _section_followup_topics(message: str) -> list[str]:
    return _dedupe([term for term in _extract_focus_terms(message) if term not in {"如果", "还有", "怎么样"}], limit=4)


def _next_section_child_index(existing_needs: list[str], section_number: int) -> int:
    prefix = f"{section_number}."
    indexes = []
    for need in existing_needs:
        match = re.match(rf"{re.escape(prefix)}([0-9]+)", need)
        if match:
            indexes.append(int(match.group(1)))
    return max(indexes, default=0) + 1


def _section_followup_need_items(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
) -> list[str]:
    context = _active_section_followup_context(lesson)
    if context is None:
        return []
    section_index, heading, _ = context
    section_number = section_index + 1
    next_index = _next_section_child_index(requirements.learning_need_checklist, section_number)
    items = []
    for offset, topic in enumerate(_section_followup_topics(request.message), start=0):
        items.append(f"{section_number}.{next_index + offset} {topic}（承接：{heading}）")
    return items


def _supplemental_board_teaching_guide(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    document: BoardDocument,
) -> BoardTeachingGuide:
    guide = _fallback_board_teaching_guide(
        lesson=lesson,
        request=request,
        requirements=requirements,
        document=document,
    )
    topics = "、".join(_section_followup_topics(request.message)) or request.message
    guide.lecture_handout = f"{guide.lecture_handout}\n\n追问补充：围绕“{topics}”给出定义、边界、例子和检查问题。"
    guide.teacher_brief = _compact_teaching_line(guide.lecture_handout, limit=240)
    return guide


def _format_teacher_message(message: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (message or "").strip())
    if not text:
        return ""
    if "\n\n" in text or len(text) < 90:
        return text
    sentences = re.split(r"(?<=[。！？!?])", text)
    paragraphs: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(current) + len(sentence) > 80 and current:
            paragraphs.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        paragraphs.append(current.strip())
    return "\n\n".join(paragraphs)


def _teacher_message_result(message: str | None, *, source: str = "ai", extra: WorkflowState | None = None) -> WorkflowState:
    formatted = _format_teacher_message(message or "")
    result: WorkflowState = dict(extra or {})
    result["teacher_message"] = formatted
    result["teacher_message_source"] = source if formatted else "none"
    return result


def _teacher_learning_probe(state: WorkflowState) -> str | None:
    status = state["learning_clarification"]
    request = state["request"]
    if state["board_decision"].action == "no_change" and _append_request_already_applied(
        state["lesson"].board_document,
        request.message,
        state["learning_requirement_sheet"],
    ):
        return None
    if _is_section_followup_learning_need(state["lesson"], request):
        return None
    if status.progress >= 80 or status.forced_start or state["board_decision"].action in {"edit_board", "append_section"}:
        return None
    if _is_explanation_request(request.message) and not _is_low_information_request(request.message):
        return None
    topic = _extract_topic_hint(request.message) or state["learning_requirement_sheet"].theme
    if status.progress >= 35:
        return f"关于“{topic}”，请补一句你的起点或这次最想先解决的具体问题。"
    return None


def _clarification_hint_questions(state: WorkflowState, *extra: str | None) -> list[str]:
    hints: list[str] = []
    hints.extend(question for question in state.get("clarification_questions", []) if question)
    reference_prompt = state.get("reference_prompt")
    if reference_prompt is not None:
        hints.append(reference_prompt.question)
    board_edit_prompt = state.get("board_edit_prompt")
    if board_edit_prompt is not None:
        hints.append(board_edit_prompt.question)
    hints.extend(value for value in extra if value)
    return _dedupe(hints, limit=6)


def _generate_ai_clarification_message(state: WorkflowState, *extra_hints: str | None) -> str | None:
    request = state["request"]
    return generate_pm_interview_message(
        lesson_title=(state.get("generated_lesson") or state["lesson"]).title,
        request_message=request.message,
        requirements=state["learning_requirement_sheet"],
        learning_clarification=state["learning_clarification"].model_dump(mode="json"),
        clarification_questions=_clarification_hint_questions(state, *extra_hints),
        conversation=request.conversation,
    )


def _teacher_message_from_talk_track(state: WorkflowState, talk_track: str) -> str:
    return talk_track.strip()


def _is_continue_teaching_request(message: str) -> bool:
    return _is_teaching_control_text(message)


def _is_teaching_control_request(request: ChatRequest) -> bool:
    return request.teaching_action == "continue" or _is_continue_teaching_request(request.message)


def _section_progress_view(progress: BoardTeachingProgress | None, guide: BoardTeachingGuide | None) -> SectionTeachingProgressView:
    plans = guide.section_plans if guide else []
    index = progress.current_section_index if progress else 0
    title = plans[index].heading if plans and 0 <= index < len(plans) else ""
    return SectionTeachingProgressView(
        section_index=index,
        section_count=len(plans),
        current_section_title=title,
        has_next_section=bool(plans and index < len(plans) - 1),
        waiting_for_continue=progress.waiting_for_continue if progress else False,
    )


def _section_teaching_turn(state: WorkflowState) -> WorkflowState | None:
    guide = state.get("board_teaching_guide")
    if guide is None or not guide.section_plans:
        return None
    lesson = state["lesson"]
    request = state["request"]
    if state["board_decision"].action == "no_change" and _is_section_followup_learning_need(lesson, request):
        return None
    existing = lesson.board_teaching_progress
    has_existing_progress = bool(existing and existing.board_snapshot_hash == guide.board_snapshot_hash)
    if has_existing_progress:
        current_index = existing.current_section_index
        completed = list(existing.completed_section_indexes)
    else:
        current_index = 0
        completed = []
    if _is_teaching_control_request(request):
        if has_existing_progress and current_index not in completed:
            completed.append(current_index)
        if has_existing_progress:
            current_index = min(current_index + 1, len(guide.section_plans) - 1)
    elif state["board_decision"].action not in {"edit_board", "append_section"} and not (existing and existing.waiting_for_continue):
        return None
    progress = BoardTeachingProgress(
        board_document_id=guide.board_document_id,
        board_snapshot_hash=guide.board_snapshot_hash,
        current_section_index=current_index,
        completed_section_indexes=sorted(set(completed)),
        waiting_for_continue=True,
    )
    return {
        "board_teaching_progress": progress,
        "teaching_progress": _section_progress_view(progress, guide),
    }


def _concept_hint_from_request(text: str) -> str | None:
    topic = _append_section_topic(text, build_requirements("补充内容")) if _is_append_document_request(text) else _extract_topic_hint(text)
    return topic


def _run_requirement_state_draft(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    resource_context_active = _should_use_resource_followup_context(
        course_package=state["course_package"],
        lesson=lesson,
        request=request,
    )
    return draft_requirement_state(
        lesson=lesson,
        request=request,
        resource_context_active=resource_context_active,
        resource_count=len(_available_reference_resources(state["course_package"], lesson)),
        important_terms=_important_terms_from_request,
        section_followup_need_items=lambda lesson, request, requirements: _section_followup_need_items(
            lesson=lesson,
            request=request,
            requirements=requirements,
        ),
    )


def _run_board_manager(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    requirements = state["learning_requirement_sheet"]
    matches = match_resources(state["course_package"], lesson, request, requirements)
    resources = _available_reference_resources(state["course_package"], lesson)
    decision = _fallback_board_decision(lesson, request, requirements, matches)
    if decision.action == "append_section" and _append_request_already_applied(lesson.board_document, request.message, requirements):
        decision = BoardDecision(action="no_change", reason="目标追加内容已经存在于当前讲义中。")

    if state.get("needs_clarification"):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason=state.get("pm_reason", "当前需求需要澄清。")),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
        }

    confirmed_reference = _selected_reference_context(state["course_package"], lesson, request, requirements)
    if request.resource_reference_action == "confirm":
        if confirmed_reference is None:
            return {
                "board_decision": BoardDecision(action="clarify_request", reason="用户确认了资料，但未提取到可用正文。"),
                "scope_options": [],
                "resource_matches": matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "needs_clarification": True,
                "clarification_questions": ["这份资料的目标章节没有抽到可读正文。请换一份可复制文本或更清晰的资料。"],
            }
        if decision.action == "no_change" and is_document_empty(lesson.board_document) and _is_resource_followup_request(request.message):
            decision = BoardDecision(action="edit_board", reason="空白讲义中确认了资料章节，生成初始讲义。")
        elif decision.action == "no_change" and _extract_requested_outline_reference(request.message)[0] is not None:
            decision = BoardDecision(action="edit_board", reason="用户确认了资料章节，按该章节生成讲义。")
        return {
            "board_decision": decision,
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": confirmed_reference,
        }

    if _should_clarify_resource_file(resources=resources, matches=matches, request=request):
        return {
            "board_decision": BoardDecision(action="clarify_request", reason="多份资料都可能相关，需要先确认文件。"),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "needs_clarification": True,
            "clarification_questions": [_resource_file_clarification_question(resources, matches)],
        }

    top_match = matches[0] if matches else None
    selected_reference = None
    if top_match and _should_auto_attach_reference_for_direct_teaching(request=request, decision=decision, top_match=top_match):
        selected_reference = _reference_context_for_match(state["course_package"], lesson, request, requirements, top_match)
        requested_chapter, _ = _extract_requested_outline_reference(request.message)
        if (
            selected_reference is not None
            and is_document_empty(lesson.board_document)
            and _is_resource_followup_request(request.message)
            and (requested_chapter is not None or _is_forced_start_request(request.message) or _is_board_generation_request(request.message))
        ):
            decision = BoardDecision(action="edit_board", reason="空白讲义中匹配到资料章节，直接生成初始讲义。")
    elif top_match and decision.action in {"edit_board", "append_section"} and top_match.is_high_overlap:
        return {
            "board_decision": BoardDecision(action="await_reference_choice", reason="请求和资料章节高度相关，先确认是否参考资料正文。"),
            "scope_options": [],
            "resource_matches": matches,
            "reference_prompt": _build_reference_prompt(top_match),
            "board_edit_prompt": None,
            "selected_reference": None,
        }

    board_edit_prompt = (
        _build_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, matches=matches)
        if _should_offer_board_edit_prompt(lesson=lesson, request=request, requirements=requirements, decision=decision)
        else None
    )
    return {
        "board_decision": decision,
        "scope_options": _build_scope_options(matches) if decision.action == "await_scope_choice" else [],
        "resource_matches": matches,
        "reference_prompt": None,
        "board_edit_prompt": board_edit_prompt,
        "selected_reference": selected_reference,
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
            "board_teaching_progress": lesson.board_teaching_progress,
            "teaching_progress": _section_progress_view(lesson.board_teaching_progress, lesson.board_teaching_guide),
        }

    if decision.action == "no_change":
        guide = _interactive_teaching_guide(
            lesson_id=lesson.id,
            lesson_title=lesson.title,
            document=lesson.board_document,
            requirements=requirements,
        )
        board_guide = (
            _supplemental_board_teaching_guide(lesson=lesson, request=request, requirements=requirements, document=lesson.board_document)
            if _is_section_followup_learning_need(lesson, request)
            else _resolve_board_teaching_guide(
                lesson=lesson,
                request=request,
                requirements=requirements,
                document=lesson.board_document,
                selected_reference=selected_reference,
            )
        )
        return {
            "teaching_guide": guide,
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "generated_lesson": None,
            "teacher_talk_track": None,
            "board_teaching_guide": board_guide,
            "board_teaching_progress": lesson.board_teaching_progress,
            "teaching_progress": _section_progress_view(lesson.board_teaching_progress, board_guide),
        }

    ai_edit = openai_course_ai.generate_document_edit(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        current_branch=lesson.history_graph.current_branch,
        request_message=request.message,
        selection=request.selection.model_dump(mode="json") if request.selection else None,
        interaction_mode=request.interaction_mode,
        scope_action="append_section" if decision.action == "append_section" else request.scope_action,
        requirements=requirements,
        document=lesson.board_document,
        selected_reference=_reference_payload(selected_reference, include_full_text=True),
    )

    if ai_edit is not None and not _document_edit_has_content(ai_edit):
        ai_edit = None

    if ai_edit is None and selected_reference is not None and decision.action == "edit_board" and request.selection is None:
        next_document = _reference_document_update(
            lesson=lesson,
            requirements=requirements,
            selected_reference=selected_reference,
        )
    elif request.selection and decision.action == "edit_board" and ai_edit is None:
        return _failed_document_generation_result(
            lesson=lesson,
            request=request,
            requirements=requirements,
            decision=decision,
            selected_reference=selected_reference,
        )
    elif request.selection and decision.action == "edit_board":
        next_document = _merge_selection_edit(lesson.board_document, request, ai_edit)
    elif decision.action == "append_section":
        if ai_edit is None:
            return _failed_document_generation_result(
                lesson=lesson,
                request=request,
                requirements=requirements,
                decision=decision,
                selected_reference=selected_reference,
            )
        section_html = ai_edit.replacement_html
        section_text = html_to_text(section_html)
        if (
            not section_text.strip()
            or request.message.strip() in section_text
            or (request.interaction_mode != "direct_edit" and len(_compact_request_text(section_text)) < 500)
        ):
            return _failed_document_generation_result(
                lesson=lesson,
                request=request,
                requirements=requirements,
                decision=decision,
                selected_reference=selected_reference,
            )
        next_document = append_html_section(lesson.board_document, section_html)
    elif _is_in_place_expansion_request(request.message) and (
        ai_edit is None
        or ai_edit.target_action == "append_section"
        or "补充章节" in html_to_text(ai_edit.replacement_html)[:80]
    ):
        return _failed_document_generation_result(
            lesson=lesson,
            request=request,
            requirements=requirements,
            decision=decision,
            selected_reference=selected_reference,
        )
    elif ai_edit is not None:
        next_document = build_document(
            title=ai_edit.suggested_title or lesson.board_document.title,
            content_html=ai_edit.replacement_html,
            content_text=ai_edit.replacement_text or "",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
    else:
        return _failed_document_generation_result(
            lesson=lesson,
            request=request,
            requirements=requirements,
            decision=decision,
            selected_reference=selected_reference,
        )

    next_document = augment_document_with_generated_charts(next_document, request_message=request.message)
    updated = document_changed(lesson.board_document, next_document)
    guide = _interactive_teaching_guide(
        lesson_id=lesson.id,
        lesson_title=lesson.title,
        document=next_document,
        requirements=requirements,
    )
    board_guide = _bound_board_teaching_guide(
        ai_edit.board_teaching_guide if ai_edit else None,
        lesson=lesson,
        request=request,
        requirements=requirements,
        document=next_document,
        selected_reference=selected_reference,
    )
    progress = BoardTeachingProgress(
        board_document_id=next_document.id,
        board_snapshot_hash=_board_snapshot_hash(next_document),
        current_section_index=0,
        completed_section_indexes=[],
        waiting_for_continue=True,
    )
    return {
        "teaching_guide": guide,
        "teacher_document": next_document,
        "document_updated": updated,
        "generated_lesson": None,
        "teacher_talk_track": ai_edit.teacher_talk_track if ai_edit else None,
        "board_teaching_guide": board_guide,
        "board_teaching_progress": progress,
        "teaching_progress": _section_progress_view(progress, board_guide),
    }


def _run_teacher(state: WorkflowState) -> WorkflowState:
    decision = state["board_decision"]
    lesson = state["lesson"]
    request = state["request"]
    talk_track = (state.get("teacher_talk_track") or "").strip()

    if decision.action == "clarify_request":
        return _teacher_message_result(_generate_ai_clarification_message(state), source="ai")
    if decision.action in {"await_scope_choice", "await_reference_choice"}:
        return _teacher_message_result(_generate_ai_clarification_message(state), source="ai")

    if talk_track and state.get("document_updated") and decision.action in {"edit_board", "append_section"}:
        return _teacher_message_result(_teacher_message_from_talk_track(state, talk_track), source="ai")

    section_turn = _section_teaching_turn(state)
    if section_turn is not None:
        state = {**state, **section_turn}

    probe = _teacher_learning_probe(state)

    if decision.action == "no_change" and _append_request_already_applied(
        lesson.board_document,
        request.message,
        state["learning_requirement_sheet"],
    ):
        probe = probe or _concept_hint_from_request(request.message)

    guide = state.get("board_teaching_guide")
    if guide is None:
        return _teacher_message_result(_generate_ai_clarification_message(state, probe), source="ai", extra=section_turn)

    ai_message = openai_course_ai.generate_teacher_message(
        lesson_title=state["lesson"].title,
        request_message=state["request"].message,
        requirements=state["learning_requirement_sheet"],
        board_teaching_guide=guide,
        board_decision=decision,
        document_updated=state.get("document_updated", False),
        scope_options=state.get("scope_options", []),
        resource_matches=[match.model_dump(mode="json") for match in state.get("resource_matches", [])],
        learning_clarification=state["learning_clarification"].model_dump(mode="json"),
        clarification_questions=state.get("clarification_questions", []),
        reference_prompt=state["reference_prompt"].model_dump(mode="json") if state.get("reference_prompt") else None,
        selected_reference=_reference_payload(state.get("selected_reference"), include_full_text=False),
        teaching_progress=state["teaching_progress"].model_dump(mode="json") if state.get("teaching_progress") else None,
    )
    if ai_message:
        return _teacher_message_result(ai_message, source="ai", extra=section_turn)
    return _teacher_message_result(_generate_ai_clarification_message(state, probe), source="ai", extra=section_turn)


class SimpleCourseWorkflow:
    def invoke(self, state: WorkflowState) -> WorkflowState:
        current: WorkflowState = dict(state)
        for step in (_run_requirement_state_draft, _run_board_manager, _run_board_executor, _run_teacher):
            current.update(step(current))
        current.setdefault("scope_options", [])
        current.setdefault("resource_matches", [])
        current.setdefault("reference_prompt", None)
        current.setdefault("selected_reference", None)
        current.setdefault("board_edit_prompt", None)
        current.setdefault("board_teaching_progress", current["lesson"].board_teaching_progress)
        current.setdefault("teaching_progress", _section_progress_view(current.get("board_teaching_progress"), current.get("board_teaching_guide")))
        return current


course_workflow = SimpleCourseWorkflow()
