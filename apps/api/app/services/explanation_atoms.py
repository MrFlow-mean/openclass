from __future__ import annotations

import re

from app.models import BoardFocusRef, BoardSegment, Lesson
from app.services.board_segment_index import compact_segment_text, segment_text_hash


ATOMIC_EXPLANATION_SEQUENCE_MODE = "atomic_explanation"

_ANSWER_MARKER_PATTERN = re.compile(
    r"^(?:答案|参考答案|解答|解析|答案解析|answer|answers|solution|solutions)\s*[:：]?$",
    re.IGNORECASE,
)
_ANSWER_NOTE_PATTERN = re.compile(
    r"(答案|参考答案|解答|解析|answer|answers|solution|solutions)",
    re.IGNORECASE,
)
_EXERCISE_GROUP_PATTERN = re.compile(
    r"(练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目|"
    r"exercise|exercises|question|questions|problem|problems|quiz|quizzes|task|tasks)",
    re.IGNORECASE,
)
_SENTENCE_PATTERN = re.compile(r"[^。！？!?；;.\n]+[。！？!?；;.]?")


def build_atomic_explanation_sequence(
    *,
    lesson: Lesson,
    segments: list[BoardSegment],
    scope_heading: BoardSegment,
) -> list[BoardFocusRef]:
    start, end = _section_bounds(segments, scope_heading)
    content_segments = [
        segment
        for segment in segments[start + 1 : end + 1]
        if segment.kind != "heading" and segment.text.strip()
    ]
    atoms: list[BoardFocusRef] = []
    for group in _contiguous_heading_groups(content_segments):
        atoms.extend(
            _atomic_focuses_for_group(
                lesson=lesson,
                group=group,
                next_index=len(atoms) + 1,
            )
        )
    return _dedupe_atoms(atoms)


def _section_bounds(segments: list[BoardSegment], heading: BoardSegment) -> tuple[int, int]:
    start = heading.order_index
    end = start
    level = len(heading.heading_path)
    for segment in segments[start + 1 :]:
        if segment.kind == "heading" and len(segment.heading_path) <= level:
            break
        end = segment.order_index
    return start, end


def _contiguous_heading_groups(segments: list[BoardSegment]) -> list[list[BoardSegment]]:
    groups: list[list[BoardSegment]] = []
    current: list[BoardSegment] = []
    current_path: tuple[str, ...] | None = None
    for segment in segments:
        path = tuple(segment.heading_path)
        if current and path != current_path:
            groups.append(current)
            current = []
        current.append(segment)
        current_path = path
    if current:
        groups.append(current)
    return groups


def _atomic_focuses_for_group(
    *,
    lesson: Lesson,
    group: list[BoardSegment],
    next_index: int,
) -> list[BoardFocusRef]:
    exercise_atoms = _exercise_item_focuses(lesson=lesson, group=group, next_index=next_index)
    if exercise_atoms:
        return exercise_atoms

    atoms: list[BoardFocusRef] = []
    for segment in group:
        if _is_answer_marker(segment):
            continue
        if segment.kind == "paragraph":
            for sentence in _split_sentences(segment.text):
                atoms.append(
                    _make_focus(
                        lesson=lesson,
                        primary=segment,
                        excerpt=sentence,
                        before_text=_nearest_before_text(group=group, segment=segment, fallback=""),
                        after_text=_nearest_after_text(group=group, segment=segment, fallback=""),
                        source_segments=[segment],
                        index=next_index + len(atoms),
                        reason="按板书段落中的句子拆成最小可讲单元。",
                    )
                )
            continue
        atoms.append(
            _make_focus(
                lesson=lesson,
                primary=segment,
                excerpt=segment.text,
                before_text=_nearest_before_text(group=group, segment=segment, fallback=""),
                after_text=_nearest_after_text(group=group, segment=segment, fallback=""),
                source_segments=[segment],
                index=next_index + len(atoms),
                reason="按板书内容块拆成最小可讲单元。",
            )
        )
    return atoms


def _exercise_item_focuses(
    *,
    lesson: Lesson,
    group: list[BoardSegment],
    next_index: int,
) -> list[BoardFocusRef]:
    marker_index = next((index for index, segment in enumerate(group) if _is_answer_marker(segment)), None)
    if marker_index is None and _looks_like_exercise_group(group):
        return _exercise_list_item_focuses(
            lesson=lesson,
            group=group,
            question_segments=[
                segment
                for segment in group
                if segment.kind == "list" and segment.text.strip() and not _is_answer_note(segment)
            ],
            next_index=next_index,
        )
    if marker_index is None:
        return []

    question_segments = [
        segment for segment in group[:marker_index] if segment.kind == "list" and segment.text.strip()
    ]
    if not question_segments:
        return []

    answer_segments = [
        segment
        for segment in group[marker_index + 1 :]
        if segment.kind in {"list", "paragraph"} and segment.text.strip() and not _is_answer_marker(segment)
    ]
    instruction = " ".join(
        segment.text.strip()
        for segment in group[:marker_index]
        if segment.kind == "paragraph" and segment.text.strip() and not _is_answer_marker(segment)
    )
    atoms: list[BoardFocusRef] = []
    for offset, question in enumerate(question_segments):
        answer = answer_segments[offset] if offset < len(answer_segments) else None
        parts = [f"题目：{question.text.strip()}"]
        if answer is not None:
            parts.append(f"参考答案：{answer.text.strip()}")
        source_segments = [question, *([answer] if answer is not None else [])]
        atoms.append(
            _make_focus(
                lesson=lesson,
                primary=question,
                excerpt="\n".join(parts),
                before_text=instruction or _nearest_before_text(group=group, segment=question, fallback=""),
                after_text=_next_question_text(question_segments=question_segments, offset=offset),
                source_segments=source_segments,
                index=next_index + len(atoms),
                reason="按题目列表与对应答案拆成逐题讲解单元。",
            )
        )
    return atoms


def _exercise_list_item_focuses(
    *,
    lesson: Lesson,
    group: list[BoardSegment],
    question_segments: list[BoardSegment],
    next_index: int,
) -> list[BoardFocusRef]:
    if not question_segments:
        return []
    atoms: list[BoardFocusRef] = []
    for offset, question in enumerate(question_segments):
        atoms.append(
            _make_focus(
                lesson=lesson,
                primary=question,
                excerpt=f"题目：{question.text.strip()}",
                before_text=_nearest_instruction_text(group=group, segment=question),
                after_text=_next_question_text(question_segments=question_segments, offset=offset),
                source_segments=[question],
                index=next_index + len(atoms),
                reason="按题目列表拆成逐题讲解单元。",
            )
        )
    return atoms


def _looks_like_exercise_group(group: list[BoardSegment]) -> bool:
    for segment in group:
        if segment.heading_path and _EXERCISE_GROUP_PATTERN.search(" ".join(segment.heading_path)):
            return True
        if segment.kind in {"paragraph", "list"} and _EXERCISE_GROUP_PATTERN.search(
            compact_segment_text(segment.text, limit=240)
        ):
            return True
    return False


def _make_focus(
    *,
    lesson: Lesson,
    primary: BoardSegment,
    excerpt: str,
    before_text: str,
    after_text: str,
    source_segments: list[BoardSegment],
    index: int,
    reason: str,
) -> BoardFocusRef:
    normalized_excerpt = compact_segment_text(excerpt, limit=1400)
    order_values = [segment.order_index for segment in source_segments]
    source_ids = [segment.segment_id for segment in source_segments]
    display_base = " / ".join(primary.heading_path) or lesson.board_document.title
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=primary.segment_id,
        kind=primary.kind,
        heading_path=primary.heading_path,
        excerpt=normalized_excerpt,
        before_text=compact_segment_text(before_text, limit=500),
        after_text=compact_segment_text(after_text, limit=500),
        text_hash=primary.text_hash,
        excerpt_hash=segment_text_hash(normalized_excerpt),
        confidence=0.94,
        reason=reason,
        display_label=f"{display_base} · 第{index}个讲解单元",
        match_id=f"atomic_sequence:{primary.segment_id}:{index}:{segment_text_hash(normalized_excerpt)}",
        source_segment_ids=source_ids,
        order_start=min(order_values) if order_values else primary.order_index,
        order_end=max(order_values) if order_values else primary.order_index,
        score_breakdown={"atomic_sequence": 0.94},
    )


def _split_sentences(text: str) -> list[str]:
    sentences = [compact_segment_text(match.group(0), limit=700) for match in _SENTENCE_PATTERN.finditer(text)]
    return [sentence for sentence in sentences if sentence] or [compact_segment_text(text, limit=700)]


def _is_answer_marker(segment: BoardSegment) -> bool:
    return bool(_ANSWER_MARKER_PATTERN.match(compact_segment_text(segment.text, limit=80)))


def _is_answer_note(segment: BoardSegment) -> bool:
    compact = compact_segment_text(segment.text, limit=160)
    return bool(compact and _ANSWER_NOTE_PATTERN.search(compact))


def _nearest_instruction_text(*, group: list[BoardSegment], segment: BoardSegment) -> str:
    index = group.index(segment)
    for candidate in reversed(group[:index]):
        if candidate.kind == "paragraph" and candidate.text.strip() and not _is_answer_note(candidate):
            return candidate.text
    return _nearest_before_text(group=group, segment=segment, fallback="")


def _nearest_before_text(*, group: list[BoardSegment], segment: BoardSegment, fallback: str) -> str:
    index = group.index(segment)
    for candidate in reversed(group[:index]):
        if candidate.text.strip() and not _is_answer_marker(candidate) and not _is_answer_note(candidate):
            return candidate.text
    return fallback


def _nearest_after_text(*, group: list[BoardSegment], segment: BoardSegment, fallback: str) -> str:
    index = group.index(segment)
    for candidate in group[index + 1 :]:
        if candidate.text.strip() and not _is_answer_marker(candidate) and not _is_answer_note(candidate):
            return candidate.text
    return fallback


def _next_question_text(*, question_segments: list[BoardSegment], offset: int) -> str:
    if offset + 1 < len(question_segments):
        return question_segments[offset + 1].text
    return ""


def _dedupe_atoms(atoms: list[BoardFocusRef]) -> list[BoardFocusRef]:
    seen: set[tuple[str | None, str]] = set()
    deduped: list[BoardFocusRef] = []
    for atom in atoms:
        key = (atom.match_id, atom.excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(atom)
    return deduped
