from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    BoardChunk,
    BoardFocusRef,
    BoardSearchCandidate,
    BoardSearchEvidence,
    BoardSearchQueryPlan,
    BoardSegment,
    BoardTaskAction,
    BoardTaskRequirementSheet,
    Lesson,
    SelectionRef,
)
from app.services.board_segment_index import build_board_segment_index, compact_segment_text, segment_text_hash
from app.services.openai_course_ai import openai_course_ai


EDIT_CONFIDENCE_THRESHOLD = 0.85
EXPLAIN_CONFIDENCE_THRESHOLD = 0.65
ORDINAL_LOCATION_PATTERN = re.compile(
    r"(?:第\s*)?(?P<number>[0-9０-９一二三四五六七八九十两]+)\s*(?P<unit>小节|章节|章|节|部分|段)"
)
STRUCTURED_TARGET_PATTERN = re.compile(
    r"(?:第\s*)?(?P<number>[0-9０-９一二三四五六七八九十两]+)\s*(?:个|道)?\s*"
    r"(?P<unit>空|题|问题|项|条|选项|句|行)"
)
HEADING_ORDINAL_PATTERN = re.compile(
    r"^\s*(?:第\s*)?(?P<number>[0-9０-９一二三四五六七八九十两]+)\s*(?:[.．、:：)）]|章|节|部分|段|小节)"
)
NUMBERED_ITEM_PATTERN = re.compile(
    r"(?:(?<=^)|(?<=[\n\r。；;]))\s*"
    r"(?P<label>[0-9０-９一二三四五六七八九十两]+)"
    r"\s*[.．、:：)）]\s*(?P<body>[^\n\r。；;]+)"
)
BLANK_MARKER_PATTERN = re.compile(
    r"(?:[\(（]\s*(?P<paren>[0-9０-９一二三四五六七八九十两]+)\s*[\)）]"
    r"|(?P<prefix>[0-9０-９一二三四五六七八九十两]+)\s*[.．、:：)）])"
    r"\s*[_＿—-]{2,}"
)
SENTENCE_SPLIT_PATTERN = re.compile(r"[^。！？!?；;.\n\r]+[。！？!?；;.]?")

GENERIC_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("为什么", "原因", "机制", "形成", "影响因素", "来源"),
    ("定义", "概念", "含义", "是什么", "意思"),
    ("例子", "示例", "案例", "举例"),
    ("步骤", "流程", "过程", "方法", "操作"),
    ("结论", "总结", "要点", "重点"),
    ("区别", "对比", "不同", "比较"),
    ("表格", "图表", "数据", "列表"),
)


@dataclass(frozen=True)
class FocusResolution:
    focus: BoardFocusRef | None
    candidates: list[BoardFocusRef]
    status: str
    question: str = ""
    evidence: BoardSearchEvidence | None = None

    @property
    def resolved(self) -> bool:
        return self.focus is not None and self.status in {"selected", "resolved"}


@dataclass(frozen=True)
class StructuredTarget:
    number: int
    unit: str


class BoardDocumentLocator:
    def locate(
        self,
        *,
        lesson: Lesson,
        query_text: str,
        selection: SelectionRef | None = None,
        selection_text: str | None = None,
        action_type: BoardTaskAction | None = None,
        board_task: BoardTaskRequirementSheet | None = None,
    ) -> FocusResolution:
        index = build_board_segment_index(lesson.board_document)
        plan = _query_plan(query_text=query_text, board_task=board_task, action_type=action_type)
        excerpt = compact_segment_text(selection.excerpt if selection else selection_text, limit=1200)
        if excerpt:
            focus = _focus_from_selection(
                lesson=lesson,
                selection=selection,
                excerpt=excerpt,
                segments=index.segments,
            )
            evidence = _evidence(
                status="selected",
                plan=plan,
                candidates=[
                    _candidate(
                        source="selection",
                        focus=focus,
                        score=1.0,
                        reason="用户选区已映射为板书侧目标位置。",
                        score_breakdown={"selection": 1.0},
                    )
                ],
                selected=focus.match_id,
                reason="用户已经选中目标内容。",
            )
            return FocusResolution(focus=focus, candidates=[focus], status="selected", evidence=evidence)

        structured_candidates = _structured_candidate_focuses(
            lesson=lesson,
            query_text=plan.query_text,
            segments=index.segments,
        )
        if structured_candidates:
            return _resolution_from_candidates(
                candidates=structured_candidates,
                plan=plan,
                source_reason="根据编号、题号、空格、句子或行号定位。",
                resolved_question="我按编号内容找到了几个可能的位置。请确认你要讲解或操作的是哪一处。",
                action_type=action_type,
                force_unique=True,
            )

        ordinal_candidates = _ordinal_candidate_focuses(lesson=lesson, query_text=plan.query_text, segments=index.segments)
        if ordinal_candidates:
            return _resolution_from_candidates(
                candidates=ordinal_candidates,
                plan=plan,
                source_reason="根据章节、段落或标题顺序定位。",
                resolved_question="我按编号找到了几个可能的位置。请确认你要讲解或操作的是哪一段。",
                action_type=action_type,
                force_unique=True,
            )

        search_candidates = _search_candidates(
            lesson=lesson,
            plan=plan,
            segments=index.segments,
            chunks=index.chunks,
        )
        if not search_candidates:
            status = "content_absent" if board_task and board_task.requested_action in {"explain", "chat"} else "missing"
            return FocusResolution(
                focus=None,
                candidates=[],
                status=status,
                question="我还没有定位到要操作的板书位置。请选中一段内容，或说明标题、前后文字、例子/定义/结论等位置线索。",
                evidence=_evidence(
                    status=status,
                    plan=plan,
                    candidates=[],
                    selected=None,
                    reason="当前板书检索没有找到相关候选。",
                ),
            )

        threshold = (
            EDIT_CONFIDENCE_THRESHOLD
            if action_type in {"rewrite_target", "expand_target", "simplify_target"}
            else EXPLAIN_CONFIDENCE_THRESHOLD
        )
        if not _has_unique_best(search_candidates, threshold):
            search_candidates = _rerank_candidates(board_task=board_task, plan=plan, candidates=search_candidates)
        best = search_candidates[0]
        candidates = [candidate.focus for candidate in search_candidates[:3]]
        if best.focus.confidence >= threshold and (
            len(search_candidates) == 1 or best.focus.confidence - search_candidates[1].focus.confidence >= 0.08
        ):
            return FocusResolution(
                focus=best.focus,
                candidates=candidates,
                status="resolved",
                evidence=_evidence(
                    status="found",
                    plan=plan,
                    candidates=search_candidates[:5],
                    selected=best.match_id,
                    reason=best.reason,
                ),
            )

        return FocusResolution(
            focus=None,
            candidates=candidates,
            status="ambiguous",
            question="我找到了几个可能的位置，但还不能安全确定。请确认你要操作的是哪一段。",
            evidence=_evidence(
                status="ambiguous",
                plan=plan,
                candidates=search_candidates[:5],
                selected=None,
                reason="多个候选位置分数接近，需要用户确认。",
            ),
        )


def _has_unique_best(candidates: list[BoardSearchCandidate], threshold: float) -> bool:
    if not candidates:
        return False
    best = candidates[0]
    return best.focus.confidence >= threshold and (
        len(candidates) == 1 or best.focus.confidence - candidates[1].focus.confidence >= 0.08
    )


board_document_locator = BoardDocumentLocator()


def resolve_board_focus(
    *,
    lesson: Lesson,
    user_message: str,
    selection: SelectionRef | None = None,
    selection_text: str | None = None,
    action_type: BoardTaskAction | None = None,
    board_task: BoardTaskRequirementSheet | None = None,
) -> FocusResolution:
    return board_document_locator.locate(
        lesson=lesson,
        query_text=user_message,
        selection=selection,
        selection_text=selection_text,
        action_type=action_type,
        board_task=board_task,
    )


def focus_context(focus: BoardFocusRef) -> str:
    parts = []
    if focus.heading_path:
        parts.append(f"所在目录：{' / '.join(focus.heading_path)}")
    if focus.before_text:
        parts.append(f"前文：{focus.before_text}")
    parts.append(f"目标文段：{focus.excerpt}")
    if focus.after_text:
        parts.append(f"后文：{focus.after_text}")
    return "\n".join(parts)


def _query_plan(
    *,
    query_text: str,
    board_task: BoardTaskRequirementSheet | None,
    action_type: BoardTaskAction | None,
) -> BoardSearchQueryPlan:
    parts = []
    if board_task:
        parts.extend([board_task.target_hint, board_task.question_or_topic])
        if board_task.interaction_rule_draft:
            parts.append(board_task.interaction_rule_draft.target_hint)
            parts.append(board_task.interaction_rule_draft.interaction_goal)
    parts.append(query_text)
    unique_parts: list[str] = []
    seen_parts: set[str] = set()
    for part in parts:
        compact_part = compact_segment_text(part or "", limit=260)
        if not compact_part or compact_part in seen_parts:
            continue
        seen_parts.add(compact_part)
        unique_parts.append(compact_part)
    compact = compact_segment_text(" ".join(unique_parts), limit=800)
    return BoardSearchQueryPlan(
        query_text=compact,
        search_terms=sorted(_query_terms(compact)),
        structured_target=_structured_target_label(compact),
        scope_hint=board_task.target_hint if board_task and board_task.target_hint else "",
        action_type=action_type,
    )


def _focus_from_selection(
    *,
    lesson: Lesson,
    selection: SelectionRef | None,
    excerpt: str,
    segments: list[BoardSegment],
) -> BoardFocusRef:
    segment = _matching_segment(selection=selection, excerpt=excerpt, segments=segments)
    if segment:
        return _focus_from_segment(
            lesson=lesson,
            segment=segment,
            segments=segments,
            confidence=1.0 if selection and selection.segment_id == segment.segment_id else 0.92,
            reason="用户已经选中板书内容，后端已映射到对应文档片段。",
            source_segment_ids=[segment.segment_id],
            score_breakdown={"selection": 1.0},
        )

    match_id = _match_id("selection", selection.segment_id if selection else excerpt)
    return BoardFocusRef(
        source="board" if not selection or selection.kind == "board" else selection.kind,
        lesson_id=lesson.id,
        document_id=selection.document_id if selection else lesson.board_document.id,
        segment_id=selection.segment_id if selection else None,
        heading_path=selection.heading_path if selection else [],
        excerpt=excerpt,
        before_text=compact_segment_text(selection.before_text if selection else "", limit=500),
        after_text=compact_segment_text(selection.after_text if selection else "", limit=500),
        text_hash=selection.text_hash or segment_text_hash(excerpt) if selection else segment_text_hash(excerpt),
        confidence=0.9,
        reason="用户已经选中内容；即使暂未映射到片段 ID，也按选区文本作为目标。",
        match_id=match_id,
        source_segment_ids=[selection.segment_id] if selection and selection.segment_id else [],
        score_breakdown={"selection": 0.9},
    )


def _matching_segment(
    *,
    selection: SelectionRef | None,
    excerpt: str,
    segments: list[BoardSegment],
) -> BoardSegment | None:
    if selection and selection.segment_id:
        match = next((segment for segment in segments if segment.segment_id == selection.segment_id), None)
        if match:
            return match
    if selection and selection.text_hash:
        match = next((segment for segment in segments if segment.text_hash == selection.text_hash), None)
        if match:
            return match
    exact_matches = [
        segment
        for segment in segments
        if excerpt in segment.text or _segment_text_is_selection(segment.text, excerpt)
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if selection and selection.heading_path:
        heading_key = " / ".join(selection.heading_path)
        for segment in exact_matches:
            if " / ".join(segment.heading_path) == heading_key:
                return segment
    return exact_matches[0] if exact_matches else None


def _segment_text_is_selection(segment_text: str, excerpt: str) -> bool:
    compact_segment = compact_segment_text(segment_text, limit=1200)
    compact_excerpt = compact_segment_text(excerpt, limit=1200)
    if not compact_segment or compact_segment not in compact_excerpt:
        return False
    minimum_length = min(len(compact_excerpt), max(8, int(len(compact_excerpt) * 0.6)))
    return len(compact_segment) >= minimum_length


def _search_candidates(
    *,
    lesson: Lesson,
    plan: BoardSearchQueryPlan,
    segments: list[BoardSegment],
    chunks: list[BoardChunk],
) -> list[BoardSearchCandidate]:
    if not plan.search_terms:
        return []
    segment_lookup = {segment.segment_id: segment for segment in segments}
    by_segment: dict[str, BoardSearchCandidate] = {}

    for segment in segments:
        score, breakdown = _lexical_score(plan, " ".join([*segment.heading_path, segment.text]))
        if score <= 0:
            continue
        if segment.kind == "heading":
            score *= 0.9
            breakdown["heading_kind_penalty"] = -0.04
        focus = _focus_from_segment(
            lesson=lesson,
            segment=segment,
            segments=segments,
            confidence=_confidence_from_score(score),
            reason="根据用户任务清单与板书片段内容的关键词/标题相似度定位。",
            source_segment_ids=[segment.segment_id],
            score_breakdown=breakdown,
        )
        candidate = _candidate(
            source="segment_lexical",
            focus=focus,
            score=focus.confidence,
            reason=focus.reason,
            score_breakdown=breakdown,
        )
        by_segment[segment.segment_id] = candidate

    for chunk in chunks:
        score, breakdown = _lexical_score(plan, chunk.text)
        if score <= 0:
            continue
        best_segment = _best_segment_for_chunk(plan=plan, chunk=chunk, segment_lookup=segment_lookup)
        if best_segment is None:
            continue
        breakdown["chunk_window"] = min(0.2, max(0.04, len(chunk.source_segment_ids) * 0.025))
        confidence = min(0.94, _confidence_from_score(score + breakdown["chunk_window"]))
        focus = _focus_from_segment(
            lesson=lesson,
            segment=best_segment,
            segments=segments,
            confidence=confidence,
            reason="根据相邻板书 chunk 的标题、正文和上下文相似度定位。",
            source_segment_ids=chunk.source_segment_ids,
            score_breakdown=breakdown,
            order_start=chunk.order_start,
            order_end=chunk.order_end,
        )
        candidate = _candidate(
            source="chunk_lexical",
            focus=focus,
            score=confidence,
            reason=focus.reason,
            score_breakdown=breakdown,
            chunk_id=chunk.chunk_id,
            source_segment_ids=chunk.source_segment_ids,
        )
        existing = by_segment.get(best_segment.segment_id)
        if existing is None or candidate.score > existing.score:
            by_segment[best_segment.segment_id] = candidate

    candidates = list(by_segment.values())
    candidates.sort(key=lambda item: (item.score, -item.focus.order_start if item.focus.order_start is not None else 0), reverse=True)
    return candidates[:8]


def _structured_candidate_focuses(
    *,
    lesson: Lesson,
    query_text: str,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    target = _structured_target_from_message(query_text)
    if target is None:
        return []

    if target.unit in {"项", "条", "选项"}:
        list_candidates = _list_item_candidate_focuses(lesson=lesson, target=target, segments=segments)
        if list_candidates:
            return list_candidates

    candidates: list[BoardFocusRef] = []
    seen: set[tuple[str | None, str]] = set()
    for segment in segments:
        excerpts = _structured_excerpts_for_segment(segment.text, target)
        for excerpt, reason in excerpts:
            key = (segment.segment_id, excerpt)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _focus_from_segment_excerpt(
                    lesson=lesson,
                    segment=segment,
                    segments=segments,
                    excerpt=excerpt,
                    confidence=0.94,
                    reason=reason,
                    score_breakdown={"structured": 0.94},
                )
            )
    return candidates[:5]


def _structured_target_from_message(text: str) -> StructuredTarget | None:
    compact = compact_segment_text(text, limit=300)
    match = STRUCTURED_TARGET_PATTERN.search(compact)
    if not match:
        return None
    number = _parse_ordinal_number(match.group("number"))
    if number is None:
        return None
    return StructuredTarget(number=number, unit=match.group("unit"))


def _structured_target_label(text: str) -> str:
    target = _structured_target_from_message(text)
    return f"{target.number}{target.unit}" if target else ""


def _structured_excerpts_for_segment(text: str, target: StructuredTarget) -> list[tuple[str, str]]:
    if target.unit == "空":
        return _blank_excerpts_for_segment(text, target.number)
    if target.unit in {"题", "问题", "项", "条", "选项"}:
        return _numbered_item_excerpts_for_segment(text, target.number)
    if target.unit == "句":
        return _nth_sentence_excerpt_for_segment(text, target.number)
    if target.unit == "行":
        return _nth_line_excerpt_for_segment(text, target.number)
    return []


def _blank_excerpts_for_segment(text: str, ordinal: int) -> list[tuple[str, str]]:
    excerpts: list[tuple[str, str]] = []
    for match in BLANK_MARKER_PATTERN.finditer(text):
        raw_number = match.group("paren") or match.group("prefix") or ""
        if _parse_ordinal_number(raw_number) != ordinal:
            continue
        excerpts.append((_bounded_excerpt(text, match.start(), match.end()), "根据用户给出的编号空格定位到对应内容单元。"))
    return excerpts


def _numbered_item_excerpts_for_segment(text: str, ordinal: int) -> list[tuple[str, str]]:
    excerpts: list[tuple[str, str]] = []
    for match in NUMBERED_ITEM_PATTERN.finditer(text):
        if _parse_ordinal_number(match.group("label")) != ordinal:
            continue
        excerpts.append((compact_segment_text(match.group(0), limit=500), "根据用户给出的编号定位到对应条目。"))
    return excerpts


def _nth_sentence_excerpt_for_segment(text: str, ordinal: int) -> list[tuple[str, str]]:
    sentences = [match.group(0).strip() for match in SENTENCE_SPLIT_PATTERN.finditer(text) if match.group(0).strip()]
    if 1 <= ordinal <= len(sentences):
        return [(compact_segment_text(sentences[ordinal - 1], limit=500), "根据用户给出的句子序号定位。")]
    return []


def _nth_line_excerpt_for_segment(text: str, ordinal: int) -> list[tuple[str, str]]:
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    if 1 <= ordinal <= len(lines):
        return [(compact_segment_text(lines[ordinal - 1], limit=500), "根据用户给出的行号定位。")]
    return []


def _list_item_candidate_focuses(
    *,
    lesson: Lesson,
    target: StructuredTarget,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    list_segments = [segment for segment in segments if segment.kind == "list" and segment.text.strip()]
    if 1 <= target.number <= len(list_segments):
        segment = list_segments[target.number - 1]
        return [
            _focus_from_segment(
                lesson=lesson,
                segment=segment,
                segments=segments,
                confidence=0.9,
                reason="根据用户给出的条目序号定位到列表项。",
                source_segment_ids=[segment.segment_id],
                score_breakdown={"structured_list": 0.9},
            )
        ]
    return []


def _bounded_excerpt(text: str, start: int, end: int) -> str:
    left = max(
        text.rfind("。", 0, start),
        text.rfind("！", 0, start),
        text.rfind("？", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
        text.rfind(".", 0, start),
        text.rfind(";", 0, start),
        text.rfind("；", 0, start),
        text.rfind("\n", 0, start),
        text.rfind("\r", 0, start),
    )
    right_candidates = [
        index
        for marker in ("。", "！", "？", "!", "?", ".", ";", "；", "\n", "\r")
        for index in [text.find(marker, end)]
        if index >= 0
    ]
    left = left + 1 if left >= 0 else max(0, start - 180)
    right = min(right_candidates) + 1 if right_candidates else min(len(text), end + 220)
    return compact_segment_text(text[left:right], limit=500)


def _ordinal_candidate_focuses(
    *,
    lesson: Lesson,
    query_text: str,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    ordinal = _ordinal_from_message(query_text)
    if ordinal is None:
        return []

    headings = [segment for segment in segments if segment.kind == "heading"]
    if not headings:
        return []

    marker_matches = [segment for segment in headings if _heading_starts_with_ordinal(segment.text, ordinal)]
    if marker_matches:
        return [
            _focus_from_segment(
                lesson=lesson,
                segment=segment,
                segments=segments,
                confidence=0.88,
                reason="根据用户给出的编号与板书标题编号定位。",
                source_segment_ids=[segment.segment_id],
                score_breakdown={"heading_ordinal": 0.88},
            )
            for segment in marker_matches[:5]
        ]

    ordered_headings = [
        segment
        for segment in headings
        if not _looks_like_document_title(lesson=lesson, segment=segment)
    ]
    if 1 <= ordinal <= len(ordered_headings):
        segment = ordered_headings[ordinal - 1]
        return [
            _focus_from_segment(
                lesson=lesson,
                segment=segment,
                segments=segments,
                confidence=0.82,
                reason="根据板书标题顺序定位到对应位置。",
                source_segment_ids=[segment.segment_id],
                score_breakdown={"heading_order": 0.82},
            )
        ]
    return []


def _ordinal_from_message(text: str) -> int | None:
    compact = compact_segment_text(text, limit=300)
    match = ORDINAL_LOCATION_PATTERN.search(compact)
    if not match:
        return None
    return _parse_ordinal_number(match.group("number"))


def _heading_starts_with_ordinal(text: str, ordinal: int) -> bool:
    match = HEADING_ORDINAL_PATTERN.search(text)
    if not match:
        return False
    return _parse_ordinal_number(match.group("number")) == ordinal


def _parse_ordinal_number(value: str) -> int | None:
    normalized = value.translate(str.maketrans("０１２３４５６７８９", "0123456789")).strip()
    if normalized.isdigit():
        number = int(normalized)
        return number if number > 0 else None

    digits = {
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
    if normalized == "十":
        return 10
    if "十" in normalized:
        head, _, tail = normalized.partition("十")
        tens = digits.get(head, 1) if head else 1
        ones = digits.get(tail, 0) if tail else 0
        number = tens * 10 + ones
        return number if number > 0 else None
    return digits.get(normalized)


def _looks_like_document_title(*, lesson: Lesson, segment: BoardSegment) -> bool:
    if segment.order_index != 0:
        return False
    title = compact_segment_text(lesson.board_document.title or lesson.title, limit=120)
    return bool(title and compact_segment_text(segment.text, limit=120) == title)


def _focus_from_segment(
    *,
    lesson: Lesson,
    segment: BoardSegment,
    segments: list[BoardSegment],
    confidence: float,
    reason: str,
    source_segment_ids: list[str],
    score_breakdown: dict[str, float],
    order_start: int | None = None,
    order_end: int | None = None,
) -> BoardFocusRef:
    before = ""
    after = ""
    if segment.before_segment_id:
        before_segment = next((item for item in segments if item.segment_id == segment.before_segment_id), None)
        before = compact_segment_text(before_segment.text if before_segment else "", limit=500)
    if segment.after_segment_id:
        after_segment = next((item for item in segments if item.segment_id == segment.after_segment_id), None)
        after = compact_segment_text(after_segment.text if after_segment else "", limit=500)
    match_id = _match_id("focus", segment.segment_id)
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=segment.document_id,
        segment_id=segment.segment_id,
        kind=segment.kind,
        heading_path=segment.heading_path,
        excerpt=segment.text,
        before_text=before,
        after_text=after,
        text_hash=segment.text_hash,
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        match_id=match_id,
        source_segment_ids=source_segment_ids,
        order_start=segment.order_index if order_start is None else order_start,
        order_end=segment.order_index if order_end is None else order_end,
        score_breakdown=score_breakdown,
    )


def _focus_from_segment_excerpt(
    *,
    lesson: Lesson,
    segment: BoardSegment,
    segments: list[BoardSegment],
    excerpt: str,
    confidence: float,
    reason: str,
    score_breakdown: dict[str, float],
) -> BoardFocusRef:
    focus = _focus_from_segment(
        lesson=lesson,
        segment=segment,
        segments=segments,
        confidence=confidence,
        reason=reason,
        source_segment_ids=[segment.segment_id],
        score_breakdown=score_breakdown,
    )
    return focus.model_copy(update={"excerpt": excerpt, "text_hash": segment_text_hash(excerpt)})


def _best_segment_for_chunk(
    *,
    plan: BoardSearchQueryPlan,
    chunk: BoardChunk,
    segment_lookup: dict[str, BoardSegment],
) -> BoardSegment | None:
    scored: list[tuple[float, BoardSegment]] = []
    for segment_id in chunk.source_segment_ids:
        segment = segment_lookup.get(segment_id)
        if segment is None:
            continue
        score, _ = _lexical_score(plan, " ".join([*segment.heading_path, segment.text]))
        if segment.kind == "heading":
            score *= 0.92
        scored.append((score, segment))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], -item[1].order_index), reverse=True)
    best = scored[0][1]
    if best.kind == "heading":
        following = [
            segment_lookup[item]
            for item in chunk.source_segment_ids
            if item in segment_lookup and segment_lookup[item].order_index > best.order_index
        ]
        return next((segment for segment in following if segment.kind != "heading"), best)
    return best


def _lexical_score(plan: BoardSearchQueryPlan, value: str) -> tuple[float, dict[str, float]]:
    compact = compact_segment_text(value, limit=2400).lower()
    if not compact:
        return 0.0, {}
    value_terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact)}
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    value_terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    query_terms = set(plan.search_terms)
    overlap = query_terms & value_terms
    if not overlap:
        return 0.0, {}
    term_score = len(overlap) / max(len(query_terms), 1)
    exact_bonus = sum(0.08 for term in query_terms if term in compact and len(term) >= 3)
    phrase_bonus = 0.18 if plan.query_text and compact_segment_text(plan.query_text, limit=80).lower() in compact else 0.0
    heading_bonus = 0.12 if any(term in " ".join(plan.search_terms) for term in overlap) else 0.0
    score = min(1.0, term_score + exact_bonus + phrase_bonus + heading_bonus)
    return score, {
        "term_overlap": round(term_score, 4),
        "exact_bonus": round(exact_bonus, 4),
        "phrase_bonus": round(phrase_bonus, 4),
        "heading_bonus": round(heading_bonus, 4),
    }


def _confidence_from_score(score: float) -> float:
    return max(0.0, min(0.94, 0.46 + score * 0.52))


def _query_terms(text: str) -> set[str]:
    compact = compact_segment_text(text, limit=800)
    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact)}
    for group in GENERIC_CONCEPT_GROUPS:
        if any(item in compact for item in group):
            terms.update(group)
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {term for term in terms if len(term.strip()) >= 2}


def _resolution_from_candidates(
    *,
    candidates: list[BoardFocusRef],
    plan: BoardSearchQueryPlan,
    source_reason: str,
    resolved_question: str,
    action_type: BoardTaskAction | None,
    force_unique: bool,
) -> FocusResolution:
    search_candidates = [
        _candidate(
            source="structured",
            focus=focus,
            score=focus.confidence,
            reason=focus.reason,
            score_breakdown=focus.score_breakdown,
            source_segment_ids=focus.source_segment_ids,
        )
        for focus in candidates
    ]
    if len(candidates) == 1 or force_unique:
        if len(candidates) == 1:
            return FocusResolution(
                focus=candidates[0],
                candidates=candidates,
                status="resolved",
                evidence=_evidence(
                    status="found",
                    plan=plan,
                    candidates=search_candidates,
                    selected=candidates[0].match_id,
                    reason=source_reason,
                ),
            )
        threshold = (
            EDIT_CONFIDENCE_THRESHOLD
            if action_type in {"rewrite_target", "expand_target", "simplify_target"}
            else EXPLAIN_CONFIDENCE_THRESHOLD
        )
        if candidates[0].confidence >= threshold and candidates[0].confidence - candidates[1].confidence >= 0.08:
            return FocusResolution(
                focus=candidates[0],
                candidates=candidates[:3],
                status="resolved",
                evidence=_evidence(
                    status="found",
                    plan=plan,
                    candidates=search_candidates,
                    selected=candidates[0].match_id,
                    reason=source_reason,
                ),
            )

    return FocusResolution(
        focus=None,
        candidates=candidates[:3],
        status="ambiguous",
        question=resolved_question,
        evidence=_evidence(
            status="ambiguous",
            plan=plan,
            candidates=search_candidates,
            selected=None,
            reason="结构化定位找到多个候选。",
        ),
    )


def _rerank_candidates(
    *,
    board_task: BoardTaskRequirementSheet | None,
    plan: BoardSearchQueryPlan,
    candidates: list[BoardSearchCandidate],
) -> list[BoardSearchCandidate]:
    if not candidates:
        return candidates
    rerank = openai_course_ai.generate_board_search_rerank(
        board_task=board_task.model_dump(mode="json") if board_task else None,
        query_plan=plan.model_dump(mode="json"),
        candidates=[candidate.model_dump(mode="json") for candidate in candidates[:8]],
    )
    if rerank is None or not rerank.ranked:
        return candidates

    by_id = {candidate.match_id: candidate for candidate in candidates}
    ranked: list[BoardSearchCandidate] = []
    seen: set[str] = set()
    for item in rerank.ranked:
        candidate = by_id.get(item.match_id)
        if candidate is None or item.match_id in seen:
            continue
        seen.add(item.match_id)
        score = max(candidate.score, item.score)
        focus = candidate.focus.model_copy(
            update={
                "confidence": max(candidate.focus.confidence, item.score),
                "reason": item.reason or candidate.reason,
                "score_breakdown": {
                    **candidate.score_breakdown,
                    "board_ai_rerank": round(item.score, 4),
                },
            }
        )
        ranked.append(
            candidate.model_copy(
                update={
                    "focus": focus,
                    "score": score,
                    "reason": item.reason or candidate.reason,
                    "score_breakdown": focus.score_breakdown,
                }
            )
        )
    for candidate in candidates:
        if candidate.match_id not in seen:
            ranked.append(candidate)
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked


def _candidate(
    *,
    source: str,
    focus: BoardFocusRef,
    score: float,
    reason: str,
    score_breakdown: dict[str, float],
    chunk_id: str | None = None,
    source_segment_ids: list[str] | None = None,
) -> BoardSearchCandidate:
    match_id = focus.match_id or _match_id(source, focus.segment_id or focus.excerpt)
    focus = focus.model_copy(
        update={
            "match_id": match_id,
            "source_segment_ids": source_segment_ids if source_segment_ids is not None else focus.source_segment_ids,
            "score_breakdown": score_breakdown,
        }
    )
    return BoardSearchCandidate(
        match_id=match_id,
        source=source,
        chunk_id=chunk_id,
        source_segment_ids=source_segment_ids if source_segment_ids is not None else focus.source_segment_ids,
        focus=focus,
        score=max(0.0, min(1.0, score)),
        score_breakdown=score_breakdown,
        reason=reason,
    )


def _evidence(
    *,
    status: str,
    plan: BoardSearchQueryPlan,
    candidates: list[BoardSearchCandidate],
    selected: str | None,
    reason: str,
) -> BoardSearchEvidence:
    return BoardSearchEvidence(
        status=status,  # type: ignore[arg-type]
        query_plan=plan,
        candidates=candidates,
        selected_match_id=selected,
        reason=reason,
    )


def _match_id(source: str, value: str | None) -> str:
    seed = f"{source}:{value or ''}"
    return f"match_{segment_text_hash(seed)}"
