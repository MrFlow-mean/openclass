from __future__ import annotations

import re

from app.models import BoardFocusRef, BoardSegment, BoardTaskRequirementSheet, Lesson
from app.services.board_segment_index import build_board_segment_index
from app.services.chat.intent import _compact_text
from app.services.explanation_atoms import build_atomic_explanation_sequence
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.segment_resolver import FocusResolution


SEQUENTIAL_EXPLANATION_REQUEST_PATTERN = re.compile(
    r"(都讲|全都讲|全部讲|都解释|全部解释|逐个|一个个|挨个|依次|按顺序|从头到尾|"
    r"(?:讲解|解释|讲|说明).{0,12}(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个)|"
    r"(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个).{0,12}(?:都)?(?:讲|讲解|解释|说明))"
)
COLLECTION_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目|"
    r"exercise|exercises|question|questions|problem|problems|quiz|quizzes|task|tasks)",
    re.IGNORECASE,
)
SINGLE_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(第\s*[0-9０-９一二三四五六七八九十两]+.{0,8}(?:章|节|小节|部分|段|句|行|题|项|条|步)|"
    r"(?:练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目)"
    r"\s*[0-9０-９一二三四五六七八九十两]+|"
    r"倒数|选中|这里|这(?:一|个)?(?:段|句|行|题|项|条|步|部分)|某(?:段|句|行|题|项|条|步))",
    re.IGNORECASE,
)
OVERVIEW_EXPLANATION_REQUEST_PATTERN = re.compile(r"(概括|总结|总览|整体把握|大意|框架|梳理(?:框架|结构)?)")


def _decision_focus(decision: BoardTaskRouteDecision, resolution: FocusResolution | None) -> BoardFocusRef | None:
    return decision.target_focus or (resolution.focus if resolution else None)


def _requests_sequential_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and SEQUENTIAL_EXPLANATION_REQUEST_PATTERN.search(compact))


def _requests_collection_explanation_sequence(
    *,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
) -> bool:
    if board_task.requested_action != "explain":
        return False
    if _requests_sequential_explanation(request_message):
        return True
    request_compact = _compact_text(request_message, limit=160)
    sheet_compact = _compact_text(
        " ".join(part for part in [board_task.target_hint, board_task.question_or_topic] if part),
        limit=240,
    )
    combined = _compact_text(" ".join(part for part in [request_compact, sheet_compact] if part), limit=360)
    if not combined or not COLLECTION_EXPLANATION_TARGET_PATTERN.search(combined):
        return False
    if SINGLE_EXPLANATION_TARGET_PATTERN.search(combined):
        return False
    return True


def _requests_overview_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and OVERVIEW_EXPLANATION_REQUEST_PATTERN.search(compact))


def _ordered_explanation_candidates(
    *,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> list[BoardFocusRef]:
    candidates = decision.candidate_focuses or (resolution.candidates if resolution else [])
    seen: set[tuple[str | None, str]] = set()
    ordered: list[BoardFocusRef] = []
    for candidate in candidates:
        key = (candidate.segment_id, candidate.excerpt)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def _apply_explicit_sequential_explanation_choice(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    request_message: str,
) -> BoardTaskRouteDecision:
    if board_task.requested_action != "explain":
        return decision
    if decision.route != "clarify_location" or decision.location_status != "ambiguous":
        return decision
    if not _requests_collection_explanation_sequence(board_task=board_task, request_message=request_message):
        return decision
    candidates = _ordered_explanation_candidates(decision=decision, resolution=resolution)
    if not candidates:
        return decision
    segments = build_board_segment_index(lesson.board_document).segments
    scope_heading = _scope_heading_for_explanation_sequence(
        segments=segments,
        focus=None,
        candidates=candidates,
        explicit_sequence=True,
    )
    if scope_heading is None:
        return decision
    return BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=candidates[0],
        candidate_focuses=candidates,
        reason=(
            "用户请求讲解同一父级下的集合型内容；"
            "本轮按最小可讲单元从第一个目标开始讲解，不再反复要求用户选择位置。"
        ),
        write_proposal=decision.write_proposal,
    )


def _path_starts_with(path: list[str], prefix: list[str]) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def _dedupe_focuses(candidates: list[BoardFocusRef]) -> list[BoardFocusRef]:
    seen: set[tuple[str | None, str]] = set()
    deduped: list[BoardFocusRef] = []
    for candidate in candidates:
        key = (candidate.segment_id, candidate.excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _find_heading_segment_by_path(segments: list[BoardSegment], heading_path: list[str]) -> BoardSegment | None:
    if not heading_path:
        return None
    return next(
        (
            segment
            for segment in segments
            if segment.kind == "heading"
            and segment.heading_path == heading_path
            and _compact_text(segment.text, limit=240) == _compact_text(heading_path[-1], limit=240)
        ),
        None,
    )


def _section_bounds(segments: list[BoardSegment], heading: BoardSegment) -> tuple[int, int]:
    start = heading.order_index
    end = start
    level = len(heading.heading_path)
    for segment in segments[start + 1 :]:
        if segment.kind == "heading" and len(segment.heading_path) <= level:
            break
        end = segment.order_index
    return start, end


def _direct_child_section_headings(segments: list[BoardSegment], parent_heading: BoardSegment) -> list[BoardSegment]:
    parent_path = parent_heading.heading_path
    parent_start, parent_end = _section_bounds(segments, parent_heading)
    return [
        segment
        for segment in segments[parent_start + 1 : parent_end + 1]
        if segment.kind == "heading"
        and len(segment.heading_path) == len(parent_path) + 1
        and _path_starts_with(segment.heading_path, parent_path)
    ]


def _parent_heading_for_section_sequence(
    *,
    segments: list[BoardSegment],
    candidates: list[BoardFocusRef],
) -> BoardSegment | None:
    candidates = _dedupe_focuses(candidates)
    for candidate in candidates:
        if candidate.kind != "heading" or not candidate.heading_path:
            continue
        if all(_path_starts_with(other.heading_path, candidate.heading_path) for other in candidates if other.heading_path):
            heading = _find_heading_segment_by_path(segments, candidate.heading_path)
            if heading and _direct_child_section_headings(segments, heading):
                return heading

    if len(candidates) == 1:
        candidate_path = candidates[0].heading_path
        while candidate_path:
            heading = _find_heading_segment_by_path(segments, candidate_path)
            if heading and _direct_child_section_headings(segments, heading):
                return heading
            candidate_path = candidate_path[:-1]
        return None

    direct_parent_paths: list[list[str]] = []
    for candidate in candidates:
        if not candidate.heading_path:
            return None
        direct_parent_path = candidate.heading_path[:-1]
        if not direct_parent_path:
            return None
        direct_parent_paths.append(direct_parent_path)
    if direct_parent_paths and all(path == direct_parent_paths[0] for path in direct_parent_paths):
        heading = _find_heading_segment_by_path(segments, direct_parent_paths[0])
        if heading and _direct_child_section_headings(segments, heading):
            return heading
    return None


def _shared_heading_for_atomic_sequence(
    *,
    segments: list[BoardSegment],
    candidates: list[BoardFocusRef],
) -> BoardSegment | None:
    candidates = _dedupe_focuses(candidates)
    heading_paths = [candidate.heading_path for candidate in candidates if candidate.heading_path]
    if not heading_paths:
        return None
    first_path = heading_paths[0]
    if not all(path == first_path for path in heading_paths):
        return None
    return _find_heading_segment_by_path(segments, first_path)


def _scope_heading_for_explanation_sequence(
    *,
    segments: list[BoardSegment],
    focus: BoardFocusRef | None,
    candidates: list[BoardFocusRef],
    explicit_sequence: bool,
) -> BoardSegment | None:
    if explicit_sequence:
        shared_heading = _shared_heading_for_atomic_sequence(segments=segments, candidates=candidates)
        if shared_heading is not None:
            return shared_heading
        return _parent_heading_for_section_sequence(segments=segments, candidates=candidates)
    if focus is None or focus.kind != "heading" or not focus.heading_path:
        return None
    return _find_heading_segment_by_path(segments, focus.heading_path)


def _section_explanation_sequence(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    request_message: str,
) -> list[BoardFocusRef]:
    if board_task.requested_action != "explain":
        return []
    if _requests_overview_explanation(request_message):
        return []
    focus = _decision_focus(decision, resolution)
    segments = build_board_segment_index(lesson.board_document).segments
    explicit_sequence = _requests_collection_explanation_sequence(
        board_task=board_task,
        request_message=request_message,
    )
    if explicit_sequence:
        candidates = decision.candidate_focuses or (resolution.candidates if resolution else [])
        if focus is not None:
            candidates = [focus, *candidates]
        candidates = _dedupe_focuses(candidates)
        if not candidates:
            return []
    else:
        candidates = [focus] if focus is not None else []
    scope_heading = _scope_heading_for_explanation_sequence(
        segments=segments,
        focus=focus,
        candidates=candidates,
        explicit_sequence=explicit_sequence,
    )
    if scope_heading is None:
        return []
    atomic_items = build_atomic_explanation_sequence(
        lesson=lesson,
        segments=segments,
        scope_heading=scope_heading,
    )
    if len(atomic_items) < 2:
        return []
    return atomic_items
