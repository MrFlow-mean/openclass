from __future__ import annotations

import hashlib
import html
import json
import math
import re
from difflib import SequenceMatcher
from typing import Any, TypedDict

from app.models import (
    BoardDecision,
    BoardDocument,
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
    ReadingCompanionRule,
    ReadingCompanionTurn,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    ResourceLibraryItem,
    SectionTeachingProgressView,
    TeachingLocationContext,
    TeachingGuide,
)
from app.services.course_runtime import effective_requirements
from app.services.lesson_factory import build_requirements, build_teaching_guide, slugify
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import extract_reference_context
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
READING_FOCUS_LIMIT = 4500
READING_CONTEXT_LIMIT = 1200
READING_COMPANION_MARKERS = (
    "陪我读",
    "陪读",
    "一起读",
    "跟我读",
    "轮流读",
    "分角色",
    "角色扮演",
    "扮演",
    "你一句我一句",
    "轮流念",
    "读对话",
    "读课文",
)


def classify_scope(message: str, lesson: Lesson) -> str:
    _ = message, lesson
    return "in_scope"


def match_resources(*args: Any, **kwargs: Any) -> list[ResourceMatch]:
    package: CoursePackage = kwargs["package"]
    lesson: Lesson = kwargs["lesson"]
    request: ChatRequest = kwargs["request"]
    requirements: LearningRequirementSheet | None = kwargs.get("requirements")
    query = kwargs.get("query") or (
        _requirements_resource_query(requirements, request.message)
        if requirements is not None
        else request.message.strip()
    )
    if not query:
        return []

    query_terms = _resource_terms(query)
    query_compact = _compact(query)
    query_vector = _term_vector(query_terms)
    if not query_terms and len(query_compact) < 4:
        return []

    scored: list[ResourceMatch] = []
    for resource in _accessible_resources(package, lesson.id):
        for chapter in resource.outline:
            score, overlap, matched_chunk_id, matched_excerpt, chunk_locator = _score_resource_chapter(
                resource=resource,
                chapter=chapter,
                query=query,
                query_terms=query_terms,
                query_compact=query_compact,
                query_vector=query_vector,
            )
            if score <= 0:
                continue
            scored.append(
                ResourceMatch(
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    resource_name=resource.name,
                    chapter_title=chapter.title,
                    reason=_resource_match_reason(resource, chapter, overlap),
                    score=score,
                    is_high_overlap=overlap,
                    matched_chunk_id=matched_chunk_id,
                    matched_excerpt=matched_excerpt,
                    chunk_locator=chunk_locator,
                )
            )

    scored.sort(key=lambda item: (item.is_high_overlap, item.score), reverse=True)
    return _rerank_resource_matches_with_catalog_ai(
        package=package,
        lesson_id=lesson.id,
        requirements=requirements,
        matches=scored[:8],
    )[:5]


def _requirements_resource_query(requirements: LearningRequirementSheet | None, fallback_message: str) -> str:
    if requirements is None:
        return fallback_message.strip()
    parts = [
        requirements.theme,
        requirements.learning_goal,
        requirements.level,
        requirements.known_background,
        requirements.target_depth,
        requirements.output_preference,
        requirements.success_criteria,
        *requirements.learning_need_checklist,
        *[
            f"{item.section_path} {item.title} {item.content}"
            for item in requirements.learning_need_catalog
            if item.status == "active"
        ],
        fallback_message,
    ]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _resource_terms(value: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", value.lower()):
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _term_vector(terms: list[str]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for term in terms:
        if not term:
            continue
        weights[term] = weights.get(term, 0.0) + 1.0
    return weights


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _char_ngram_similarity(left: str, right: str, *, n: int = 3) -> float:
    if not left or not right:
        return 0.0
    if len(left) < n or len(right) < n:
        return 1.0 if left in right or right in left else 0.0
    left_ngrams = {left[index : index + n] for index in range(len(left) - n + 1)}
    right_ngrams = {right[index : index + n] for index in range(len(right) - n + 1)}
    if not left_ngrams or not right_ngrams:
        return 0.0
    inter = len(left_ngrams & right_ngrams)
    union = len(left_ngrams | right_ngrams)
    if union == 0:
        return 0.0
    return inter / union


def _accessible_resources(package: CoursePackage, lesson_id: str) -> list[ResourceLibraryItem]:
    return [
        resource
        for resource in package.resources
        if resource.scope_lesson_id is None or resource.scope_lesson_id == lesson_id
    ]


def _chapter_haystack(chapter: LibraryChapter) -> str:
    return " ".join(
        [
            chapter.title,
            chapter.summary,
            " ".join(chapter.keywords),
            " ".join(chapter.path),
            chapter.locator_hint or "",
        ]
    ).lower()


def _score_resource_chapter(
    *,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    query: str,
    query_terms: list[str],
    query_compact: str,
    query_vector: dict[str, float],
) -> tuple[float, bool, str | None, str | None, Any]:
    chapter_text = _chapter_haystack(chapter)
    resource_text = (resource.text_content or "").lower()
    resource_compact = _compact(resource_text)
    score = 0.0

    for term in query_terms:
        if term in chapter_text:
            score += 1.2
        if term in resource_text:
            score += 0.8

    overlap = False
    if query_compact and len(query_compact) >= 6 and query_compact in resource_compact:
        score += 8.0
        overlap = True
    elif len(query_compact) >= 6:
        snippets = [query_compact[i : i + 6] for i in range(max(1, len(query_compact) - 5))]
        if any(snippet and snippet in resource_compact for snippet in snippets):
            score += 4.0
            overlap = True

    if query.lower() in chapter_text:
        score += 3.0
        overlap = True

    if resource.resource_type == "image" and resource.extracted_text_available:
        score += 0.5

    matched_chunk_id: str | None = None
    matched_excerpt: str | None = None
    matched_locator = None
    if resource.ocr_chunks:
        chunk_scores: list[tuple[float, Any]] = []
        for chunk in resource.ocr_chunks:
            chunk_vector = chunk.embedding or _term_vector(list(dict.fromkeys([*chunk.terms, *_resource_terms(chunk.text)])))
            semantic_score = _cosine_similarity(query_vector, chunk_vector)
            lexical_bonus = 0.0
            lowered = chunk.text.lower()
            compact_chunk_text = _compact(chunk.text)
            for term in query_terms:
                if term in lowered:
                    lexical_bonus += 0.25
            chunk_score = semantic_score * 6.0 + lexical_bonus
            if query_compact and len(query_compact) >= 6 and query_compact in _compact(chunk.text):
                chunk_score += 5.0
            elif query_compact and len(query_compact) >= 6:
                chunk_score += _char_ngram_similarity(query_compact, compact_chunk_text) * 4.0
            chunk_scores.append((chunk_score, chunk))
        if chunk_scores:
            chunk_scores.sort(key=lambda item: item[0], reverse=True)
            best_chunk_score, best_chunk = chunk_scores[0]
            if best_chunk_score > 0:
                score += best_chunk_score
                matched_chunk_id = best_chunk.id
                matched_excerpt = best_chunk.text[:240]
                matched_locator = best_chunk.locator
                if best_chunk_score >= 4.2:
                    overlap = True

    return score, overlap, matched_chunk_id, matched_excerpt, matched_locator


def _resource_match_reason(resource: ResourceLibraryItem, chapter: LibraryChapter, overlap: bool) -> str:
    if overlap and resource.resource_type == "image":
        return f"问题文本与图片「{resource.name}」提取文字高度重合，优先定位到该图片。"
    if overlap:
        return f"问题文本与「{resource.name} / {chapter.title}」内容高度重合。"
    return f"「{resource.name} / {chapter.title}」与当前问题关键词最相关。"


def _resource_candidate_payload(
    *,
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    match: ResourceMatch,
) -> dict[str, Any]:
    matched_chunk = next(
        (chunk for chunk in resource.ocr_chunks if chunk.id == match.matched_chunk_id),
        None,
    )
    return {
        "resource_id": resource.id,
        "chapter_id": chapter.id,
        "chunk_id": match.matched_chunk_id,
        "resource_name": resource.name,
        "resource_type": resource.resource_type,
        "chapter_title": chapter.title,
        "chapter_summary": chapter.summary,
        "chapter_keywords": chapter.keywords,
        "matched_excerpt": match.matched_excerpt,
        "ocr_chunk_text": matched_chunk.text if matched_chunk is not None else None,
        "deterministic_score": match.score,
        "deterministic_reason": match.reason,
    }


def _rerank_resource_matches_with_catalog_ai(
    *,
    package: CoursePackage,
    lesson_id: str,
    requirements: LearningRequirementSheet | None,
    matches: list[ResourceMatch],
) -> list[ResourceMatch]:
    if requirements is None or not matches:
        return matches
    candidates: list[dict[str, Any]] = []
    match_by_key: dict[tuple[str, str, str | None], ResourceMatch] = {}
    for match in matches:
        resource = _resource_by_id(package, match.resource_id, lesson_id)
        if resource is None:
            continue
        chapter = next((item for item in resource.outline if item.id == match.chapter_id), None)
        if chapter is None:
            continue
        key = (match.resource_id, match.chapter_id, match.matched_chunk_id)
        match_by_key[key] = match
        candidates.append(_resource_candidate_payload(resource=resource, chapter=chapter, match=match))

    ranked = openai_course_ai.compare_requirements_to_resource_catalog(
        learning_requirement_sheet=requirements,
        resource_candidates=candidates,
    )
    if ranked is None or not ranked.matches:
        return matches

    ai_ranked: list[ResourceMatch] = []
    used_keys: set[tuple[str, str, str | None]] = set()
    for item in ranked.matches:
        key = (item.resource_id, item.chapter_id, item.chunk_id)
        match = match_by_key.get(key) or match_by_key.get((item.resource_id, item.chapter_id, None))
        if match is None:
            continue
        if key in used_keys:
            continue
        used_keys.add(key)
        ai_score = max(0.0, min(float(item.score), 1.0)) * 10.0
        ai_ranked.append(
            match.model_copy(
                update={
                    "score": max(match.score, ai_score),
                    "reason": item.reason.strip() or match.reason,
                    "is_high_overlap": match.is_high_overlap or item.score >= 0.72,
                }
            )
        )
    if not ai_ranked:
        return matches
    remaining = [
        match
        for match in matches
        if (match.resource_id, match.chapter_id, match.matched_chunk_id) not in used_keys
    ]
    return [*ai_ranked, *remaining]


def _resource_by_id(package: CoursePackage, resource_id: str, lesson_id: str) -> ResourceLibraryItem | None:
    for resource in _accessible_resources(package, lesson_id):
        if resource.id == resource_id:
            return resource
    return None


def _extract_teaching_targets(message: str, selection_text: str | None = None) -> list[str]:
    targets: list[str] = []
    if selection_text and selection_text.strip():
        targets.append(selection_text.strip())
    for pattern in (
        r"[“\"']([^“”\"']{1,60})[”\"']",
        r"(?:单词|词语|短语|句子|这段|这一段|这个词|这个单词)(?:是|叫|：|:)?\s*([A-Za-z][A-Za-z'\-]{1,40})",
        r"(?:解释|讲解|朗读|读一下|讲一下)(?:一下)?([^，。,.；;？！?!]{1,40})",
    ):
        for match in re.finditer(pattern, message):
            value = match.group(1).strip(" ：:，,。.!！?？;；")
            if value:
                targets.append(value)
    for word in re.findall(r"\b[A-Za-z][A-Za-z'\-]{2,}\b", message):
        if word.lower() not in {"realtime", "english", "word"}:
            targets.append(word)
    deduped: list[str] = []
    for target in targets:
        if target and target not in deduped:
            deduped.append(target)
    return deduped[:5]


def _snippet_around(text: str, index: int, *, window: int = 160) -> str:
    start = max(0, index - window)
    end = min(len(text), index + window)
    return text[start:end].strip()


def _heading_before(text: str, index: int) -> str | None:
    prefix = text[:index]
    headings = [
        line.strip().lstrip("#").strip()
        for line in prefix.splitlines()
        if line.strip().startswith("#") or len(line.strip()) <= 36
    ]
    return headings[-1] if headings else None


def _locate_in_text(
    *,
    text: str,
    targets: list[str],
    source: str,
    reason_prefix: str,
    heading: str | None = None,
) -> TeachingLocationContext | None:
    if not text.strip():
        return None
    compact_text = text.lower()
    for target in targets:
        normalized = target.strip()
        if not normalized:
            continue
        index = compact_text.find(normalized.lower())
        if index >= 0:
            return TeachingLocationContext(
                source=source,  # type: ignore[arg-type]
                target_text=normalized,
                surrounding_text=_snippet_around(text, index),
                heading=heading or _heading_before(text, index),
                reason=f"{reason_prefix}命中「{normalized}」。",
                score=1.0,
                needs_clarification=False,
            )
    return None


def locate_teaching_target(
    *,
    lesson: Lesson,
    package: CoursePackage,
    request: ChatRequest,
    resource_matches: list[ResourceMatch],
) -> TeachingLocationContext:
    selection_text = request.selection.excerpt if request.selection else None
    targets = _extract_teaching_targets(request.message, selection_text)
    if selection_text and selection_text.strip():
        return TeachingLocationContext(
            source="selection",
            target_text=selection_text.strip()[:120],
            surrounding_text=selection_text.strip(),
            reason="用户当前选区是最强定位信号。",
            score=1.0,
        )

    board_location = _locate_in_text(
        text=lesson.board_document.content_text,
        targets=targets,
        source="board",
        reason_prefix="在当前板书中",
    )
    if board_location is not None:
        return board_location

    for match in resource_matches:
        resource = _resource_by_id(package, match.resource_id, lesson.id)
        if resource is None:
            continue
        chapter = next((item for item in resource.outline if item.id == match.chapter_id), None)
        if match.matched_excerpt:
            location = _locate_in_text(
                text=match.matched_excerpt,
                targets=targets or [match.matched_excerpt[:40]],
                source="ocr" if resource.resource_type == "image" else "resource",
                reason_prefix=f"在资料「{resource.name}」匹配片段中",
                heading=chapter.title if chapter else None,
            )
            if location is not None:
                return location.model_copy(
                    update={
                        "resource_id": resource.id,
                        "chapter_id": chapter.id if chapter else match.chapter_id,
                        "chunk_id": match.matched_chunk_id,
                        "locator": match.chunk_locator,
                        "score": max(location.score, match.score),
                    }
                )
        for chunk in resource.ocr_chunks:
            location = _locate_in_text(
                text=chunk.text,
                targets=targets,
                source="ocr",
                reason_prefix=f"在图片「{resource.name}」OCR 片段中",
                heading=chapter.title if chapter else None,
            )
            if location is not None:
                return location.model_copy(
                    update={
                        "resource_id": resource.id,
                        "chapter_id": chapter.id if chapter else match.chapter_id,
                        "chunk_id": chunk.id,
                        "locator": chunk.locator,
                        "score": max(location.score, match.score),
                    }
                )
        if resource.text_content:
            location = _locate_in_text(
                text=resource.text_content,
                targets=targets,
                source="resource",
                reason_prefix=f"在资料「{resource.name}」全文中",
                heading=chapter.title if chapter else None,
            )
            if location is not None:
                return location.model_copy(
                    update={
                        "resource_id": resource.id,
                        "chapter_id": chapter.id if chapter else match.chapter_id,
                        "score": max(location.score, match.score),
                    }
                )

    if targets:
        return TeachingLocationContext(
            source="unknown",
            target_text=targets[0],
            surrounding_text="",
            reason=f"没有在当前板书或资料库中定位到「{targets[0]}」。",
            score=0.0,
            needs_clarification=True,
        )
    return TeachingLocationContext(
        source="unknown",
        target_text="",
        surrounding_text="",
        reason="用户要求讲解，但没有明确指出要讲哪一段或哪个词。",
        score=0.0,
        needs_clarification=True,
    )


def _build_reference_prompt_from_match(match: ResourceMatch) -> ResourceReferencePrompt:
    return ResourceReferencePrompt(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        chunk_id=match.matched_chunk_id,
        question=f"我定位到你的问题可能来自资料「{match.resource_name} / {match.chapter_title}」，要基于它继续生成吗？",
        reason=match.reason,
        score=match.score,
    )


def _reference_payload(context: ResourceReferenceContext) -> dict[str, Any]:
    payload = context.model_dump(mode="json")
    payload["chapter_text"] = context.full_text
    return payload


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


def _fallback_board_intent(
    *,
    message: str,
    has_selection: bool,
    board_has_content: bool,
) -> str:
    compact = _compact(message)
    explicit_realtime_markers = ("实时语音", "语音讲", "口头讲", "像老师一样讲", "realtime")
    edit_markers = ("改", "重写", "补充", "润色", "扩写", "写进", "生成板书", "编辑")
    if any(marker in compact for marker in explicit_realtime_markers):
        return "teach_realtime"
    if not board_has_content:
        return "edit_board_text"
    if has_selection or any(marker in compact for marker in edit_markers):
        return "edit_board_text"
    return "edit_board_text"


def _teaching_mode_message(theme: str) -> str:
    topic = theme.strip() or "当前主题"
    return f"已切换到讲解模式。我会围绕「{topic}」先做口头讲解，再按你的反馈继续细化。"


def _teaching_mode_message_for_location(theme: str, location: TeachingLocationContext) -> str:
    if location.needs_clarification:
        return f"已切换到讲解模式，但我还没有定位到你想听的具体位置。{location.reason} 你可以选中那段文字，或者直接告诉我要讲哪个词/句子。"
    target = location.target_text or location.heading or theme or "这部分内容"
    source_label = {
        "selection": "你选中的内容",
        "board": "当前板书",
        "resource": "资料库",
        "ocr": "图片文字",
        "unknown": "当前上下文",
    }.get(location.source, "当前上下文")
    return f"已切换到讲解模式，并定位到{source_label}里的「{target}」。我会先围绕这一处讲解，再根据你的反馈继续。"


def _is_reading_companion_request(message: str) -> bool:
    compact = _compact(message)
    return any(marker in compact for marker in READING_COMPANION_MARKERS)


def _reading_source_label(location: TeachingLocationContext) -> str:
    return {
        "selection": "用户选区",
        "board": "当前板书",
        "resource": "资料库",
        "ocr": "图片文字",
        "unknown": "当前上下文",
    }.get(location.source, "当前上下文")


def _reading_companion_location(
    *,
    lesson: Lesson,
    package: CoursePackage,
    request: ChatRequest,
    resource_matches: list[ResourceMatch],
) -> TeachingLocationContext:
    location = locate_teaching_target(
        lesson=lesson,
        package=package,
        request=request,
        resource_matches=resource_matches,
    )
    if not location.needs_clarification:
        return location
    board_text = lesson.board_document.content_text.strip()
    if board_text:
        return TeachingLocationContext(
            source="board",
            target_text=lesson.board_document.title or "当前课文",
            surrounding_text=board_text[:READING_FOCUS_LIMIT],
            heading=lesson.board_document.title,
            reason="用户要求陪读，默认使用当前板书作为陪读文本。",
            score=0.7,
            needs_clarification=False,
        )
    return location


def _reading_context_for_location(document: BoardDocument, location: TeachingLocationContext) -> dict[str, str]:
    document_text = document.content_text.strip()
    focus = (location.surrounding_text or location.target_text or document_text).strip()
    context_before = ""
    context_after = ""
    target = (location.target_text or "").strip()
    if document_text:
        index = document_text.lower().find(target.lower()) if target else -1
        if index < 0 and focus:
            index = document_text.lower().find(focus[: min(len(focus), 80)].lower())
        if index >= 0:
            focus_start = max(0, index - READING_FOCUS_LIMIT // 3)
            focus_end = min(len(document_text), index + max(len(target), 1) + (READING_FOCUS_LIMIT * 2) // 3)
            focus = document_text[focus_start:focus_end].strip()
            context_before = document_text[max(0, focus_start - READING_CONTEXT_LIMIT) : focus_start].strip()
            context_after = document_text[focus_end : focus_end + READING_CONTEXT_LIMIT].strip()
        elif location.source == "board":
            focus = document_text[:READING_FOCUS_LIMIT].strip()
            context_after = document_text[READING_FOCUS_LIMIT : READING_FOCUS_LIMIT + READING_CONTEXT_LIMIT].strip()
    return {
        "source_label": _reading_source_label(location),
        "heading": location.heading or document.title,
        "target_text": target,
        "context_before": context_before[:READING_CONTEXT_LIMIT],
        "focus_excerpt": focus[:READING_FOCUS_LIMIT],
        "context_after": context_after[:READING_CONTEXT_LIMIT],
    }


def _extract_reading_roles(message: str) -> tuple[str, str]:
    def _clean_role(value: str) -> str:
        cleaned = value.strip()
        for marker in ("我们", "一起", "轮流", "来", "开始", "读", "念", "陪"):
            if marker in cleaned:
                cleaned = cleaned.split(marker, 1)[0]
        return cleaned.strip(" ：:，,。.!！?？;；")[:12]

    compact = _compact(message)
    patterns = (
        r"我是([^，。,.；;？！?!]{1,12})你是([^，。,.；;？！?!]{1,12})",
        r"我(?:来)?扮演([^，。,.；;？！?!]{1,12})你(?:来)?扮演([^，。,.；;？！?!]{1,12})",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            user_role = _clean_role(match.group(1))
            assistant_role = _clean_role(match.group(2))
            if user_role and assistant_role:
                return user_role, assistant_role
    return "学习者", "AI"


def _split_dialogue_lines(text: str) -> list[tuple[str, str]]:
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(raw_lines) <= 1:
        raw_lines = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", text) if part.strip()]
    parsed: list[tuple[str, str]] = []
    for raw in raw_lines:
        line = re.sub(r"^[\-*•\d.、\s]+", "", raw).strip()
        if not line:
            continue
        match = re.match(r"^([^：:]{1,16})[：:]\s*(.+)$", line)
        if match:
            parsed.append((match.group(1).strip(), match.group(2).strip()))
        else:
            parsed.append(("", line))
    return parsed


def _fallback_reading_companion_guide(
    *,
    document: BoardDocument,
    location: TeachingLocationContext,
    reading_context: dict[str, str],
    user_message: str,
) -> BoardTeachingGuide:
    user_role, assistant_role = _extract_reading_roles(user_message)
    parsed_lines = _split_dialogue_lines(reading_context.get("focus_excerpt", ""))
    turns: list[ReadingCompanionTurn] = []
    for idx, (speaker, text) in enumerate(parsed_lines[:80]):
        role = "other"
        if user_role and speaker == user_role:
            role = "user"
        elif assistant_role and speaker == assistant_role:
            role = "assistant"
        elif speaker == "":
            role = "user" if idx % 2 == 0 else "assistant"
            speaker = user_role if role == "user" else assistant_role
        turns.append(ReadingCompanionTurn(order_index=idx, speaker=speaker, text=text, role=role))
    if not any(turn.role == "user" for turn in turns) or not any(turn.role == "assistant" for turn in turns):
        turns = [
            turn.model_copy(
                update={
                    "role": "user" if idx % 2 == 0 else "assistant",
                    "speaker": turn.speaker or (user_role if idx % 2 == 0 else assistant_role),
                }
            )
            for idx, turn in enumerate(turns)
        ]
    if not turns:
        turns = [
            ReadingCompanionTurn(
                order_index=0,
                speaker=user_role,
                text=reading_context.get("focus_excerpt", location.target_text or "当前文本")[:500],
                role="user",
            )
        ]
    valid_user_inputs = [turn.text for turn in turns if turn.role == "user"]
    rule = ReadingCompanionRule(
        mode="role_play_dialogue",
        user_role=user_role,
        assistant_role=assistant_role,
        rule_text=user_message.strip(),
        matching_policy="允许轻微漏词、口误和语音转写错误，但语义必须对应当前或相邻的用户角色台词。",
        context_before=reading_context.get("context_before", ""),
        focus_excerpt=reading_context.get("focus_excerpt", ""),
        context_after=reading_context.get("context_after", ""),
        source_label=reading_context.get("source_label", ""),
        valid_user_inputs=valid_user_inputs,
        turns=turns,
    )
    section_plans = [
        BoardSectionTeachingPlan(
            order_index=turn.order_index,
            heading=f"{turn.speaker or turn.role} · 第 {turn.order_index + 1} 句",
            board_excerpt=turn.text[:520],
            spoken_script=turn.text if turn.role == "assistant" else "",
            teaching_steps=["等待学习者朗读", "匹配台词", "按规则输出下一句"],
            teaching_method="陪读状态机：用户台词合规则推进，越界则退出到普通学习工作流。",
            transition_to_next="等待下一句合规台词。",
        )
        for turn in turns
    ]
    snapshot_source = {
        "document": document.model_dump(mode="json", exclude={"id"}),
        "reading_context": reading_context,
        "rule": rule.model_dump(mode="json"),
    }
    snap = hashlib.sha256(json.dumps(snapshot_source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    excerpt = reading_context.get("focus_excerpt") or location.target_text or user_message
    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=snap,
        board_title=document.title or "陪读",
        reading_companion=True,
        reading_rule=rule,
        selected_items=[
            BoardTeachingSelectedItem(
                excerpt=excerpt[:220],
                source_heading=location.heading,
                reason=location.reason or "陪读定位（离线回退规则）",
                mapped_needs=[],
                teaching_role="reading_companion_focus",
                order_index=0,
            )
        ],
        teaching_flow=[plan.heading for plan in section_plans],
        generation_rationale="陪读规则生成 AI 不可用时，从当前文本中抽取台词并按角色/交替顺序整理。",
        teacher_brief=f"陪读规则：用户扮演「{user_role}」，AI 扮演「{assistant_role}」。",
        lecture_handout="\n".join(f"{turn.speaker or turn.role}: {turn.text}" for turn in turns),
        section_plans=section_plans,
    )


def _fallback_realtime_lecture_guide(
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    location: TeachingLocationContext,
    user_message: str,
) -> BoardTeachingGuide:
    tgt = (location.target_text or location.heading or "").strip() or "讲解片段"
    sur = (location.surrounding_text or "").strip()
    checklist = "、".join(item for item in (requirements.learning_need_checklist or [])[:5] if item.strip())
    goal = (requirements.learning_goal or requirements.theme or "当前学习目标").strip()
    level = (requirements.level or requirements.known_background or "").strip()
    s1_script = (
        f"我们先锁定要讲的这一点：{tgt}。"
        f"把它放回上下文里看：{sur if sur else '相关材料里已经框定了这一处的位置'}。"
        f"它在文中的作用，通常是连接前后信息：前面铺垫了什么、这里推进了什么，你听到时要特别抓住这个衔接。"
    )
    need_line = checklist or "整体理解与关键概念"
    level_line = f"结合你目前的水平：{level}。" if level else ""
    s2_script = (
        f"接下来把你的学习需求对齐一下：{goal}。"
        f"{level_line}"
        f"清单里我们重点照顾：{need_line}。"
        f"试着用自己的话复述刚才这一点是什么意思，它如何帮助你达成上面的目标。"
    )
    section_plans = [
        BoardSectionTeachingPlan(
            order_index=0,
            heading="定位与上下文关系",
            board_excerpt=tgt[:520],
            spoken_script=s1_script,
            check_question="你能指出它前后各一句话，说明它怎么把上下文串起来吗？",
        ),
        BoardSectionTeachingPlan(
            order_index=1,
            heading="对齐学习需求",
            board_excerpt=tgt[:520],
            spoken_script=s2_script,
            check_question="用自己的话说：这一点解决了你清单里的哪一条？",
        ),
    ]
    handbook = "\n\n".join(plan.spoken_script for plan in section_plans)
    excerpt = tgt[:220] or user_message.strip()[:220]
    snap = _board_snapshot_hash(document)
    return BoardTeachingGuide(
        board_document_id=document.id,
        board_snapshot_hash=snap,
        board_title=document.title or requirements.theme or "实时讲解",
        realtime_lecture=True,
        selected_items=[
            BoardTeachingSelectedItem(
                excerpt=excerpt or document.content_text[:220],
                source_heading=location.heading,
                reason=location.reason or "讲解定位（离线回退稿）",
                mapped_needs=(requirements.learning_need_checklist or [])[:6],
                teaching_role="realtime_lecture_focus",
                order_index=0,
            )
        ],
        need_mappings=[],
        teaching_flow=[plan.heading for plan in section_plans],
        generation_rationale="讲义生成 AI 不可用时的最小回退稿：仍分两段说明文意与学习需求对齐。",
        teacher_brief=f"回退讲解稿：围绕「{excerpt}」分段朗读。",
        lecture_handout=handbook,
        section_plans=section_plans,
    )


def _realtime_lecture_progress_view(
    guide: BoardTeachingGuide,
    progress: BoardTeachingProgress,
) -> SectionTeachingProgressView:
    plans = guide.section_plans
    n = len(plans)
    if not n:
        return SectionTeachingProgressView(
            section_index=0,
            section_count=0,
            current_section_title="",
            has_next_section=False,
            waiting_for_continue=False,
        )
    idx = max(0, min(progress.current_section_index, n - 1))
    title = plans[idx].heading
    return SectionTeachingProgressView(
        section_index=idx,
        section_count=n,
        current_section_title=title,
        has_next_section=idx < n - 1,
        waiting_for_continue=n > 1 and idx < n - 1,
    )


def _initial_realtime_lecture_progress(lesson: Lesson, guide: BoardTeachingGuide) -> BoardTeachingProgress:
    board_hash = guide.board_snapshot_hash or _board_snapshot_hash(lesson.board_document)
    n = len(guide.section_plans)
    return BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=board_hash,
        current_section_index=0,
        completed_section_indexes=[],
        waiting_for_continue=n > 1,
    )


def _realtime_lecture_teacher_message(*, guide: BoardTeachingGuide, section_index: int) -> str:
    plans = guide.section_plans
    if not plans or section_index < 0 or section_index >= len(plans):
        return "讲解稿缺少有效段落，请重新发起一次讲解模式请求。"
    section = plans[section_index]
    total = len(plans)
    head = f"（第 {section_index + 1}/{total} 段）{section.heading}"
    body = section.spoken_script.strip()
    if section_index < total - 1:
        tail = "\n\n听完本段后可以说「继续」或点「继续下一节」，我再读下一段。"
    else:
        tail = "\n\n这是最后一段；若还想重听，可说「重新开始」或在界面选择重新开始分段。"
    return f"已生成讲解稿。接上实时语音后，请用自然语速朗读下面口播正文。\n\n{head}\n\n{body}{tail}"


def _reading_companion_progress_view(
    guide: BoardTeachingGuide,
    progress: BoardTeachingProgress,
) -> SectionTeachingProgressView:
    turns = guide.reading_rule.turns if guide.reading_rule else []
    n = len(turns)
    idx = max(0, min(progress.current_section_index, n))
    if idx >= n:
        title = "陪读已结束"
    else:
        turn = turns[idx]
        title = f"等待{turn.speaker or turn.role}"
    return SectionTeachingProgressView(
        section_index=min(idx, max(n - 1, 0)),
        section_count=n,
        current_section_title=title,
        has_next_section=idx < n,
        waiting_for_continue=False,
    )


def _initial_reading_companion_progress(lesson: Lesson, guide: BoardTeachingGuide) -> BoardTeachingProgress:
    return BoardTeachingProgress(
        board_document_id=lesson.board_document.id,
        board_snapshot_hash=guide.board_snapshot_hash or _board_snapshot_hash(lesson.board_document),
        current_section_index=0,
        completed_section_indexes=[],
        waiting_for_continue=False,
    )


def _next_user_turn(turns: list[ReadingCompanionTurn], start_index: int) -> ReadingCompanionTurn | None:
    for turn in turns[max(0, start_index) :]:
        if turn.role == "user":
            return turn
    return None


def _collect_assistant_turns(
    turns: list[ReadingCompanionTurn],
    start_index: int,
) -> tuple[list[ReadingCompanionTurn], int]:
    idx = max(0, start_index)
    outputs: list[ReadingCompanionTurn] = []
    while idx < len(turns) and turns[idx].role == "assistant":
        outputs.append(turns[idx])
        idx += 1
    return outputs, idx


def _reading_companion_start_message(
    guide: BoardTeachingGuide,
    progress: BoardTeachingProgress,
) -> tuple[str, BoardTeachingProgress]:
    turns = guide.reading_rule.turns if guide.reading_rule else []
    outputs, next_idx = _collect_assistant_turns(turns, progress.current_section_index)
    if outputs:
        body = "\n".join(f"{turn.speaker or 'AI'}：{turn.text}" for turn in outputs)
        message = f"已进入陪读模式。我先读这一句。\n\n{body}\n\n接下来轮到你读下一句。"
        return message, progress.model_copy(update={"current_section_index": next_idx})
    expected = _next_user_turn(turns, progress.current_section_index)
    if expected is None:
        return "陪读规则已准备好，但没有找到需要你朗读的下一句。你可以重新选中一段对话再开始。", progress
    message = f"已进入陪读模式。你先读「{expected.speaker or '你的角色'}」这句：\n\n{expected.text}"
    return message, progress


def _normalize_reading_line(value: str) -> str:
    lowered = value.lower()
    return re.sub(r"[\s，。,.；;？！?!：:\"'“”‘’、\-—（）()\[\]【】]+", "", lowered)


def _reading_line_matches(transcript: str, expected: str) -> bool:
    normalized_transcript = _normalize_reading_line(transcript)
    normalized_expected = _normalize_reading_line(expected)
    if not normalized_transcript or not normalized_expected:
        return False
    if normalized_transcript in normalized_expected or normalized_expected in normalized_transcript:
        return min(len(normalized_transcript), len(normalized_expected)) >= 4
    ratio = SequenceMatcher(None, normalized_transcript, normalized_expected).ratio()
    threshold = 0.72 if max(len(normalized_expected), len(normalized_transcript)) >= 12 else 0.82
    return ratio >= threshold


def _matched_reading_user_turn(
    turns: list[ReadingCompanionTurn],
    transcript: str,
    current_index: int,
) -> ReadingCompanionTurn | None:
    candidates = [
        turn
        for turn in turns
        if turn.role == "user" and current_index - 1 <= turn.order_index <= current_index + 3
    ]
    if not candidates:
        candidates = [turn for turn in turns if turn.role == "user"]
    for turn in candidates:
        if _reading_line_matches(transcript, turn.text):
            return turn
    return None


def _reading_companion_turn_message(
    guide: BoardTeachingGuide,
    matched_turn: ReadingCompanionTurn,
) -> tuple[str, int]:
    turns = guide.reading_rule.turns if guide.reading_rule else []
    outputs, next_idx = _collect_assistant_turns(turns, matched_turn.order_index + 1)
    if outputs:
        body = "\n".join(f"{turn.speaker or 'AI'}：{turn.text}" for turn in outputs)
        next_user = _next_user_turn(turns, next_idx)
        if next_user is not None:
            return f"{body}\n\n轮到你继续读「{next_user.speaker or '你的角色'}」下一句。", next_idx
        return f"{body}\n\n这段陪读已经读完了。", next_idx
    next_user = _next_user_turn(turns, matched_turn.order_index + 1)
    if next_user is not None:
        return f"收到。继续读「{next_user.speaker or '你的角色'}」这句：\n\n{next_user.text}", next_user.order_index
    return "这段陪读已经读完了。", len(turns)


def _handle_reading_companion_turn(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    status: LearningClarificationStatus,
    teaching_guide: TeachingGuide,
) -> dict[str, object] | None:
    guide = lesson.board_teaching_guide
    if guide is None or not guide.reading_companion or guide.reading_rule is None:
        return None
    if request.teaching_action is not None:
        return None
    turns = guide.reading_rule.turns
    progress = lesson.board_teaching_progress or _initial_reading_companion_progress(lesson, guide)
    matched = _matched_reading_user_turn(turns, request.message, progress.current_section_index)
    if matched is None:
        return {"_exit_reading_companion": True}
    message, next_idx = _reading_companion_turn_message(guide, matched)
    completed = list(progress.completed_section_indexes)
    if matched.order_index not in completed:
        completed.append(matched.order_index)
    new_progress = progress.model_copy(
        update={
            "current_section_index": next_idx,
            "completed_section_indexes": completed,
            "waiting_for_continue": False,
        }
    )
    return {
        "learning_requirement_sheet": requirements,
        "learning_clarification": status,
        "needs_clarification": False,
        "clarification_questions": [],
        "board_decision": BoardDecision(action="reading_companion", reason="用户台词匹配陪读规则，继续按角色轮读。"),
        "teaching_guide": teaching_guide,
        "teacher_message": message,
        "teacher_document": lesson.board_document,
        "document_updated": False,
        "scope_options": [],
        "resource_matches": [],
        "reference_prompt": None,
        "board_edit_prompt": None,
        "selected_reference": None,
        "generated_lesson": None,
        "board_teaching_guide": guide,
        "board_teaching_progress": new_progress,
        "teaching_location": None,
        "teaching_progress": _reading_companion_progress_view(guide, new_progress),
    }


def _start_reading_companion_response(
    *,
    lesson: Lesson,
    package: CoursePackage,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    status: LearningClarificationStatus,
    teaching_guide: TeachingGuide,
    resource_matches: list[ResourceMatch],
    intent_reason: str,
) -> dict[str, object]:
    location = _reading_companion_location(
        lesson=lesson,
        package=package,
        request=request,
        resource_matches=resource_matches,
    )
    if location.needs_clarification:
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="reading_companion", reason=intent_reason),
            "teaching_guide": teaching_guide,
            "teacher_message": (
                f"我可以陪你读，但还没有定位到要读哪一段。{location.reason} "
                "你可以先选中课文或对话，再说“陪我读这一段”。"
            ),
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": resource_matches,
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": lesson.board_teaching_guide,
            "board_teaching_progress": None,
            "teaching_location": location,
            "teaching_progress": None,
        }

    reading_context = _reading_context_for_location(lesson.board_document, location)
    guide = openai_course_ai.generate_reading_companion_guide(
        lesson_title=lesson.title,
        user_message=request.message,
        teaching_location=location,
        document=lesson.board_document,
        reading_context=reading_context,
    )
    if guide is None:
        guide = _fallback_reading_companion_guide(
            document=lesson.board_document,
            location=location,
            reading_context=reading_context,
            user_message=request.message,
        )
    progress = _initial_reading_companion_progress(lesson, guide)
    message, progress = _reading_companion_start_message(guide, progress)
    return {
        "learning_requirement_sheet": requirements,
        "learning_clarification": status,
        "needs_clarification": False,
        "clarification_questions": [],
        "board_decision": BoardDecision(action="reading_companion", reason=intent_reason),
        "teaching_guide": teaching_guide,
        "teacher_message": message,
        "teacher_document": lesson.board_document,
        "document_updated": False,
        "scope_options": [],
        "resource_matches": resource_matches,
        "reference_prompt": None,
        "board_edit_prompt": None,
        "selected_reference": None,
        "generated_lesson": None,
        "board_teaching_guide": guide,
        "board_teaching_progress": progress,
        "teaching_location": location,
        "teaching_progress": _reading_companion_progress_view(guide, progress),
    }


def _handle_teaching_action_shortcut(
    *,
    lesson: Lesson,
    request: ChatRequest,
) -> dict[str, object] | None:
    if request.teaching_action not in {"continue", "restart"}:
        return None
    requirements_src = lesson.learning_requirements or _heuristic_requirements(lesson, request)
    requirements = LearningRequirementSheet.model_validate(requirements_src.model_dump(mode="json"))
    teaching_guide = lesson.teaching_guide or build_teaching_guide(
        lesson.id,
        lesson.title,
        lesson.board_document,
        requirements,
    )
    status = _clarification_status(requirements, request)

    def _missing_flow_response() -> dict[str, object]:
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": True,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="clarify_request", reason="没有可用的分段朗读讲解稿。"),
            "teaching_guide": teaching_guide,
            "teacher_message": "当前没有进行中的分段讲解。请先像往常一样发起「讲解模式」，定位要讲的内容后再分段收听。",
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": lesson.board_teaching_guide,
            "board_teaching_progress": lesson.board_teaching_progress,
            "teaching_location": None,
            "teaching_progress": None,
        }

    guide = lesson.board_teaching_guide
    if guide is not None and guide.reading_companion and guide.reading_rule is not None:
        if request.teaching_action == "restart":
            new_progress = _initial_reading_companion_progress(lesson, guide)
            message, new_progress = _reading_companion_start_message(guide, new_progress)
            return {
                "learning_requirement_sheet": requirements,
                "learning_clarification": status,
                "needs_clarification": False,
                "clarification_questions": [],
                "board_decision": BoardDecision(action="reading_companion", reason="重新开始当前陪读规则。"),
                "teaching_guide": teaching_guide,
                "teacher_message": message,
                "teacher_document": lesson.board_document,
                "document_updated": False,
                "scope_options": [],
                "resource_matches": [],
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "generated_lesson": None,
                "board_teaching_guide": guide,
                "board_teaching_progress": new_progress,
                "teaching_location": None,
                "teaching_progress": _reading_companion_progress_view(guide, new_progress),
            }
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="reading_companion", reason="陪读模式等待学习者朗读下一句。"),
            "teaching_guide": teaching_guide,
            "teacher_message": "当前是陪读模式。请直接读你的下一句台词，我会按规则接下一句。",
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": guide,
            "board_teaching_progress": lesson.board_teaching_progress or _initial_reading_companion_progress(lesson, guide),
            "teaching_location": None,
            "teaching_progress": _reading_companion_progress_view(
                guide,
                lesson.board_teaching_progress or _initial_reading_companion_progress(lesson, guide),
            ),
        }
    if guide is None or not guide.realtime_lecture or not guide.section_plans:
        return _missing_flow_response()

    progress = lesson.board_teaching_progress
    n = len(guide.section_plans)
    if request.teaching_action == "restart":
        new_progress = BoardTeachingProgress(
            board_document_id=lesson.board_document.id,
            board_snapshot_hash=guide.board_snapshot_hash or _board_snapshot_hash(lesson.board_document),
            current_section_index=0,
            completed_section_indexes=[],
            waiting_for_continue=n > 1,
        )
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="teach_realtime", reason="重新开始朗读当前讲解稿。"),
            "teaching_guide": teaching_guide,
            "teacher_message": _realtime_lecture_teacher_message(guide=guide, section_index=0),
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": guide,
            "board_teaching_progress": new_progress,
            "teaching_location": None,
            "teaching_progress": _realtime_lecture_progress_view(guide, new_progress),
        }

    if progress is None:
        progress = _initial_realtime_lecture_progress(lesson, guide)
    idx = progress.current_section_index
    if idx >= n - 1:
        final_progress = progress.model_copy(update={"waiting_for_continue": False})
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": False,
            "clarification_questions": [],
            "board_decision": BoardDecision(action="no_change", reason="分段朗读讲解稿已全部播完。"),
            "teaching_guide": teaching_guide,
            "teacher_message": "这一讲的各段都已读完。需要的话可以说「重新开始」重头分段收听，或直接提出新问题。",
            "teacher_document": lesson.board_document,
            "document_updated": False,
            "scope_options": [],
            "resource_matches": [],
            "reference_prompt": None,
            "board_edit_prompt": None,
            "selected_reference": None,
            "generated_lesson": None,
            "board_teaching_guide": guide,
            "board_teaching_progress": final_progress,
            "teaching_location": None,
            "teaching_progress": _realtime_lecture_progress_view(guide, final_progress),
        }

    next_idx = idx + 1
    completed = list(progress.completed_section_indexes)
    if idx not in completed:
        completed.append(idx)
    new_progress = BoardTeachingProgress(
        board_document_id=progress.board_document_id,
        board_snapshot_hash=progress.board_snapshot_hash,
        current_section_index=next_idx,
        completed_section_indexes=completed,
        waiting_for_continue=next_idx < n - 1,
    )
    return {
        "learning_requirement_sheet": requirements,
        "learning_clarification": status,
        "needs_clarification": False,
        "clarification_questions": [],
        "board_decision": BoardDecision(action="teach_realtime", reason="继续朗读讲解稿的下一段。"),
        "teaching_guide": teaching_guide,
        "teacher_message": _realtime_lecture_teacher_message(guide=guide, section_index=next_idx),
        "teacher_document": lesson.board_document,
        "document_updated": False,
        "scope_options": [],
        "resource_matches": [],
        "reference_prompt": None,
        "board_edit_prompt": None,
        "selected_reference": None,
        "generated_lesson": None,
        "board_teaching_guide": guide,
        "board_teaching_progress": new_progress,
        "teaching_location": None,
        "teaching_progress": _realtime_lecture_progress_view(guide, new_progress),
    }


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
    selected_reference: dict[str, Any] | None,
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
        selected_reference=selected_reference,
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
        package = state["course_package"]
        shortcut = _handle_teaching_action_shortcut(lesson=lesson, request=request)
        if shortcut is not None:
            return shortcut

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
        resource_matches = match_resources(
            package=package,
            lesson=lesson,
            request=request,
            requirements=requirements,
        )
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

        companion_turn = _handle_reading_companion_turn(
            lesson=lesson,
            request=request,
            requirements=requirements,
            status=status,
            teaching_guide=teaching_guide,
        )
        if companion_turn is not None:
            if companion_turn.get("_exit_reading_companion"):
                lesson.board_teaching_guide = None
                lesson.board_teaching_progress = None
            else:
                return companion_turn

        if _is_reading_companion_request(request.message):
            return _start_reading_companion_response(
                lesson=lesson,
                package=package,
                request=request,
                requirements=requirements,
                status=status,
                teaching_guide=teaching_guide,
                resource_matches=resource_matches,
                intent_reason="用户要求陪读、轮读或分角色朗读。",
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
                "resource_matches": resource_matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "generated_lesson": None,
                "board_teaching_guide": lesson.board_teaching_guide,
                "board_teaching_progress": None,
                "teaching_progress": None,
            }

        intent_result = openai_course_ai.decide_board_intent(
            lesson_title=lesson.title,
            user_message=request.message,
            selection_excerpt=request.selection.excerpt if request.selection else None,
            conversation=conversation,
            board_has_content=not board_empty,
            learning_requirement_sheet=requirements,
        )
        intent = (
            intent_result.intent
            if intent_result is not None
            else _fallback_board_intent(
                message=request.message,
                has_selection=request.selection is not None,
                board_has_content=not board_empty,
            )
        )
        intent_reason = (
            intent_result.reason.strip()
            if intent_result is not None and intent_result.reason.strip()
            else "根据本轮请求判断下一步执行模式。"
        )
        if intent == "clarify":
            clarification_question = (
                intent_result.clarification_question.strip()
                if intent_result is not None and intent_result.clarification_question.strip()
                else "你希望我直接改板书文字，还是先进入讲解模式做口头讲解？"
            )
            return {
                "learning_requirement_sheet": requirements,
                "learning_clarification": status,
                "needs_clarification": True,
                "clarification_questions": [clarification_question],
                "board_decision": BoardDecision(action="clarify_request", reason=intent_reason),
                "teaching_guide": teaching_guide,
                "teacher_message": clarification_question,
                "teacher_document": lesson.board_document,
                "document_updated": False,
                "scope_options": [],
                "resource_matches": resource_matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "generated_lesson": None,
                "board_teaching_guide": lesson.board_teaching_guide,
                "board_teaching_progress": None,
                "teaching_progress": None,
            }
        if intent == "reading_companion":
            return _start_reading_companion_response(
                lesson=lesson,
                package=package,
                request=request,
                requirements=requirements,
                status=status,
                teaching_guide=teaching_guide,
                resource_matches=resource_matches,
                intent_reason=intent_reason,
            )
        if intent == "teach_realtime":
            teaching_location = locate_teaching_target(
                lesson=lesson,
                package=package,
                request=request,
                resource_matches=resource_matches,
            )
            if teaching_location.needs_clarification:
                return {
                    "learning_requirement_sheet": requirements,
                    "learning_clarification": status,
                    "needs_clarification": False,
                    "clarification_questions": [],
                    "board_decision": BoardDecision(action="teach_realtime", reason=intent_reason),
                    "teaching_guide": teaching_guide,
                    "teacher_message": _teaching_mode_message_for_location(requirements.theme, teaching_location),
                    "teacher_document": lesson.board_document,
                    "document_updated": False,
                    "scope_options": [],
                    "resource_matches": resource_matches,
                    "reference_prompt": None,
                    "board_edit_prompt": None,
                    "selected_reference": None,
                    "generated_lesson": None,
                    "board_teaching_guide": lesson.board_teaching_guide,
                    "board_teaching_progress": None,
                    "teaching_location": teaching_location,
                    "teaching_progress": None,
                }

            lecture_guide = openai_course_ai.generate_realtime_lecture_guide(
                lesson_title=lesson.title,
                user_message=request.message,
                requirements=requirements,
                teaching_location=teaching_location,
                document=lesson.board_document,
            )
            if lecture_guide is None:
                lecture_guide = _fallback_realtime_lecture_guide(
                    document=lesson.board_document,
                    requirements=requirements,
                    location=teaching_location,
                    user_message=request.message,
                )
            board_progress = _initial_realtime_lecture_progress(lesson, lecture_guide)
            board_progress = board_progress.model_copy(
                update={"board_snapshot_hash": lecture_guide.board_snapshot_hash or board_progress.board_snapshot_hash}
            )
            teacher_message = _realtime_lecture_teacher_message(guide=lecture_guide, section_index=0)
            return {
                "learning_requirement_sheet": requirements,
                "learning_clarification": status,
                "needs_clarification": False,
                "clarification_questions": [],
                "board_decision": BoardDecision(action="teach_realtime", reason=intent_reason),
                "teaching_guide": teaching_guide,
                "teacher_message": teacher_message,
                "teacher_document": lesson.board_document,
                "document_updated": False,
                "scope_options": [],
                "resource_matches": resource_matches,
                "reference_prompt": None,
                "board_edit_prompt": None,
                "selected_reference": None,
                "generated_lesson": None,
                "board_teaching_guide": lecture_guide,
                "board_teaching_progress": board_progress,
                "teaching_location": teaching_location,
                "teaching_progress": _realtime_lecture_progress_view(lecture_guide, board_progress),
            }

        selected_reference_context: ResourceReferenceContext | None = None
        reference_prompt: ResourceReferencePrompt | None = None
        if (
            request.resource_reference_action == "confirm"
            and request.resource_reference_resource_id
            and request.resource_reference_chapter_id
        ):
            confirmed_resource = _resource_by_id(
                package,
                request.resource_reference_resource_id,
                lesson.id,
            )
            if confirmed_resource is not None:
                selected_reference_context = extract_reference_context(
                    confirmed_resource,
                    request.resource_reference_chapter_id,
                    user_query=request.message,
                    preferred_chunk_id=request.resource_reference_chunk_id,
                )
        elif request.resource_reference_action != "skip" and resource_matches:
            top_match = resource_matches[0]
            if top_match.is_high_overlap or top_match.score >= 7.2:
                matched_resource = _resource_by_id(package, top_match.resource_id, lesson.id)
                if matched_resource is not None:
                    selected_reference_context = extract_reference_context(
                        matched_resource,
                        top_match.chapter_id,
                        user_query=_requirements_resource_query(requirements, request.message),
                        preferred_chunk_id=top_match.matched_chunk_id,
                    )
            elif top_match.score >= 3:
                reference_prompt = _build_reference_prompt_from_match(top_match)
                prompt_message = (
                    f"{reference_prompt.reason} "
                    "你希望我直接参考这份资料继续生成，还是先按当前板书思路继续？"
                )
                return {
                    "learning_requirement_sheet": requirements,
                    "learning_clarification": status,
                    "needs_clarification": True,
                    "clarification_questions": [],
                    "board_decision": BoardDecision(action="clarify_request", reason="检测到可用上传资料，等待你确认是否引用。"),
                    "teaching_guide": teaching_guide,
                    "teacher_message": prompt_message,
                    "teacher_document": lesson.board_document,
                    "document_updated": False,
                    "scope_options": [],
                    "resource_matches": resource_matches,
                    "reference_prompt": reference_prompt,
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
            selected_reference=(
                _reference_payload(selected_reference_context)
                if selected_reference_context is not None
                else None
            ),
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
            "resource_matches": resource_matches,
            "reference_prompt": reference_prompt,
            "board_edit_prompt": None,
            "selected_reference": selected_reference_context,
            "generated_lesson": None,
            "board_teaching_guide": board_guide,
            "board_teaching_progress": None,
            "teaching_progress": None,
        }


course_workflow = SimpleCourseWorkflow()
