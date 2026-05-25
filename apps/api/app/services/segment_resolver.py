from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import BoardFocusRef, BoardSegment, BoardTaskAction, Lesson, SelectionRef
from app.services.board_segment_index import build_board_segment_index, compact_segment_text, segment_text_hash


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

    @property
    def resolved(self) -> bool:
        return self.focus is not None and self.status in {"selected", "resolved"}


@dataclass(frozen=True)
class StructuredTarget:
    number: int
    unit: str


def resolve_board_focus(
    *,
    lesson: Lesson,
    user_message: str,
    selection: SelectionRef | None = None,
    selection_text: str | None = None,
    action_type: BoardTaskAction | None = None,
) -> FocusResolution:
    index = build_board_segment_index(lesson.board_document)
    excerpt = compact_segment_text(selection.excerpt if selection else selection_text, limit=1200)
    if excerpt:
        focus = _focus_from_selection(
            lesson=lesson,
            selection=selection,
            excerpt=excerpt,
            segments=index.segments,
        )
        return FocusResolution(focus=focus, candidates=[focus], status="selected")

    structured_candidates = _structured_candidate_focuses(
        lesson=lesson,
        user_message=user_message,
        segments=index.segments,
    )
    if structured_candidates:
        if len(structured_candidates) == 1:
            return FocusResolution(focus=structured_candidates[0], candidates=structured_candidates, status="resolved")
        return FocusResolution(
            focus=None,
            candidates=structured_candidates[:3],
            status="ambiguous",
            question="我按编号内容找到了几个可能的位置。请确认你要讲解或操作的是哪一处。",
        )

    ordinal_candidates = _ordinal_candidate_focuses(lesson=lesson, user_message=user_message, segments=index.segments)
    if ordinal_candidates:
        if len(ordinal_candidates) == 1:
            return FocusResolution(focus=ordinal_candidates[0], candidates=ordinal_candidates, status="resolved")
        return FocusResolution(
            focus=None,
            candidates=ordinal_candidates[:3],
            status="ambiguous",
            question="我按编号找到了几个可能的位置。请确认你要讲解或操作的是哪一段。",
        )

    candidates = _candidate_focuses(lesson=lesson, user_message=user_message, segments=index.segments)
    if not candidates:
        return FocusResolution(
            focus=None,
            candidates=[],
            status="missing",
            question="我还没有定位到要操作的板书位置。请选中一段内容，或说明标题、前后文字、例子/定义/结论等位置线索。",
        )

    threshold = EDIT_CONFIDENCE_THRESHOLD if action_type in {"rewrite_target", "expand_target", "simplify_target"} else EXPLAIN_CONFIDENCE_THRESHOLD
    best = candidates[0]
    if best.confidence >= threshold and (len(candidates) == 1 or best.confidence - candidates[1].confidence >= 0.08):
        return FocusResolution(focus=best, candidates=candidates[:3], status="resolved")

    return FocusResolution(
        focus=None,
        candidates=candidates[:3],
        status="ambiguous",
        question="我找到了几个可能的位置，但还不能安全确定。请确认你要操作的是哪一段。",
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
        )

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


def _candidate_focuses(
    *,
    lesson: Lesson,
    user_message: str,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    query_terms = _query_terms(user_message)
    if not query_terms:
        return []

    scored: list[tuple[float, BoardSegment, str]] = []
    for segment in segments:
        haystack = " ".join([*segment.heading_path, segment.text])
        score = _similarity_score(query_terms, haystack)
        if score <= 0:
            continue
        if segment.kind == "heading":
            score *= 0.9
        scored.append((score, segment, "根据用户描述与目录/文段内容的相似度定位。"))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []

    max_score = max(scored[0][0], 0.01)
    focuses: list[BoardFocusRef] = []
    for score, segment, reason in scored[:5]:
        ratio = score / max_score
        confidence = min(0.9, 0.5 + ratio * 0.18 + min(score, 0.38))
        focuses.append(
            _focus_from_segment(
                lesson=lesson,
                segment=segment,
                segments=segments,
                confidence=confidence,
                reason=reason,
            )
        )
    return focuses


def _structured_candidate_focuses(
    *,
    lesson: Lesson,
    user_message: str,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    target = _structured_target_from_message(user_message)
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
                )
            )
    return candidates[:5]


def _structured_target_from_message(text: str) -> StructuredTarget | None:
    compact = compact_segment_text(text, limit=160)
    match = STRUCTURED_TARGET_PATTERN.search(compact)
    if not match:
        return None
    number = _parse_ordinal_number(match.group("number"))
    if number is None:
        return None
    return StructuredTarget(number=number, unit=match.group("unit"))


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
        excerpts.append(
            (
                _bounded_excerpt(text, match.start(), match.end()),
                "根据用户给出的编号空格定位到对应内容单元。",
            )
        )
    return excerpts


def _numbered_item_excerpts_for_segment(text: str, ordinal: int) -> list[tuple[str, str]]:
    excerpts: list[tuple[str, str]] = []
    for match in NUMBERED_ITEM_PATTERN.finditer(text):
        if _parse_ordinal_number(match.group("label")) != ordinal:
            continue
        excerpts.append(
            (
                compact_segment_text(match.group(0), limit=500),
                "根据用户给出的编号定位到对应条目。",
            )
        )
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
    user_message: str,
    segments: list[BoardSegment],
) -> list[BoardFocusRef]:
    ordinal = _ordinal_from_message(user_message)
    if ordinal is None:
        return []

    headings = [segment for segment in segments if segment.kind == "heading"]
    if not headings:
        return []

    marker_matches = [
        segment
        for segment in headings
        if _heading_starts_with_ordinal(segment.text, ordinal)
    ]
    if marker_matches:
        return [
            _focus_from_segment(
                lesson=lesson,
                segment=segment,
                segments=segments,
                confidence=0.88,
                reason="根据用户给出的编号与板书标题编号定位。",
            )
            for segment in marker_matches[:5]
        ]

    ordered_headings = [
        segment
        for segment in headings
        if not _looks_like_document_title(lesson=lesson, segment=segment)
    ]
    if 1 <= ordinal <= len(ordered_headings):
        return [
            _focus_from_segment(
                lesson=lesson,
                segment=ordered_headings[ordinal - 1],
                segments=segments,
                confidence=0.82,
                reason="根据板书标题顺序定位到对应位置。",
            )
        ]
    return []


def _ordinal_from_message(text: str) -> int | None:
    compact = compact_segment_text(text, limit=160)
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
) -> BoardFocusRef:
    before = ""
    after = ""
    if segment.before_segment_id:
        before_segment = next((item for item in segments if item.segment_id == segment.before_segment_id), None)
        before = compact_segment_text(before_segment.text if before_segment else "", limit=500)
    if segment.after_segment_id:
        after_segment = next((item for item in segments if item.segment_id == segment.after_segment_id), None)
        after = compact_segment_text(after_segment.text if after_segment else "", limit=500)
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
    )


def _focus_from_segment_excerpt(
    *,
    lesson: Lesson,
    segment: BoardSegment,
    segments: list[BoardSegment],
    excerpt: str,
    confidence: float,
    reason: str,
) -> BoardFocusRef:
    focus = _focus_from_segment(
        lesson=lesson,
        segment=segment,
        segments=segments,
        confidence=confidence,
        reason=reason,
    )
    return focus.model_copy(
        update={
            "excerpt": excerpt,
            "text_hash": segment_text_hash(excerpt),
        }
    )


def _query_terms(text: str) -> set[str]:
    compact = compact_segment_text(text, limit=500)
    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact)}
    for group in GENERIC_CONCEPT_GROUPS:
        if any(item in compact for item in group):
            terms.update(group)
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {term for term in terms if len(term.strip()) >= 2}


def _similarity_score(query_terms: set[str], value: str) -> float:
    compact = compact_segment_text(value, limit=1600).lower()
    if not compact:
        return 0.0
    value_terms = {term.lower() for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact)}
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", compact)
    value_terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    overlap = query_terms & value_terms
    if not overlap:
        return 0.0
    exact_bonus = sum(1.0 for term in query_terms if term in compact and len(term) >= 3)
    return len(overlap) / max(len(query_terms), 1) + exact_bonus * 0.08
