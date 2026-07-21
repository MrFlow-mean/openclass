from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models import BoardFocusRef, SelectionRef
from app.services import workspace_state
from app.services.board_segment_index import build_board_segment_index, compact_segment_text


@dataclass(frozen=True)
class RealtimeBoardContext:
    model_output: dict[str, Any]
    focus: BoardFocusRef | None = None


def read_realtime_board_context(
    *,
    lesson_id: str,
    user_id: str,
    arguments: dict[str, Any],
    selection: SelectionRef | None,
) -> RealtimeBoardContext:
    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    document = lesson.board_document
    index = build_board_segment_index(document)
    segments = index.segments
    mode = str(arguments.get("mode") or "target").strip().lower()
    target = compact_segment_text(str(arguments.get("target") or ""), limit=600)
    max_chars = _bounded_int(arguments.get("max_chars"), default=6000, minimum=800, maximum=12000)

    outline = _outline(segments)
    if not segments:
        return RealtimeBoardContext(
            model_output={
                "status": "empty",
                "document_title": document.title,
                "message": "The current board has no readable content.",
                "outline": outline,
            }
        )
    if mode == "outline":
        return RealtimeBoardContext(
            model_output={
                "status": "ok",
                "document_title": document.title,
                "outline": outline,
            }
        )

    if mode == "current_selection":
        if selection is None or selection.kind != "board":
            return RealtimeBoardContext(
                model_output={
                    "status": "selection_missing",
                    "message": "There is no active board selection.",
                    "outline": outline,
                }
            )
        if selection.lesson_id and selection.lesson_id != lesson_id:
            return RealtimeBoardContext(
                model_output={"status": "selection_mismatch", "message": "The selection belongs to another lesson."}
            )
        if selection.document_id and selection.document_id != document.id:
            return RealtimeBoardContext(
                model_output={"status": "selection_mismatch", "message": "The selection belongs to another board."}
            )
        match = _best_selection_match(segments, selection)
        target = selection.excerpt
    else:
        if not target:
            return RealtimeBoardContext(
                model_output={
                    "status": "target_missing",
                    "message": "A board target or outline mode is required.",
                    "outline": outline,
                }
            )
        match = _best_target_match(segments, target)

    if match is None or match[1] < 0.24:
        return RealtimeBoardContext(
            model_output={
                "status": "not_found",
                "document_title": document.title,
                "target": target,
                "message": "No reliable board range matched the requested target.",
                "outline": outline,
                "candidates": _candidate_payloads(segments, target),
            }
        )

    matched_index, confidence = match
    start, end = _context_range(segments, matched_index)
    selected_segments = segments[start : end + 1]
    content = _bounded_context(selected_segments, max_chars=max_chars)
    matched = segments[matched_index]
    before_text = segments[start - 1].text if start > 0 else ""
    after_text = segments[end + 1].text if end + 1 < len(segments) else ""
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document.id,
        segment_id=matched.segment_id,
        kind=matched.kind,
        heading_path=matched.heading_path,
        excerpt=content,
        before_text=compact_segment_text(before_text, limit=500),
        after_text=compact_segment_text(after_text, limit=500),
        text_hash=matched.text_hash,
        confidence=confidence,
        reason="Resolved from the current lesson board within a bounded segment range.",
        display_label=_display_label(matched.heading_path, matched.text),
        source_segment_ids=[segment.segment_id for segment in selected_segments],
        order_start=selected_segments[0].order_index,
        order_end=selected_segments[-1].order_index,
    )
    return RealtimeBoardContext(
        focus=focus,
        model_output={
            "status": "ok",
            "document_title": document.title,
            "target": target,
            "range_label": focus.display_label,
            "content": content,
            "focus": focus.model_dump(mode="json"),
        },
    )


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())


def _ngrams(value: str, size: int = 2) -> set[str]:
    normalized = _normalize(value)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _similarity(target: str, candidate: str) -> float:
    left = _normalize(target)
    right = _normalize(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    containment = 0.92 if left in right else 0.84 if right in left and len(right) >= 3 else 0.0
    left_grams = _ngrams(left)
    right_grams = _ngrams(right)
    overlap = len(left_grams & right_grams) / max(1, len(left_grams | right_grams))
    sequence = SequenceMatcher(None, left, right).ratio()
    return max(containment, overlap * 0.9, sequence * 0.72)


def _best_target_match(segments, target: str) -> tuple[int, float] | None:
    scored: list[tuple[int, float]] = []
    for index, segment in enumerate(segments):
        heading = " / ".join(segment.heading_path)
        score = max(_similarity(target, segment.text), _similarity(target, heading))
        if segment.kind == "heading" and score > 0:
            score = min(1.0, score + 0.05)
        scored.append((index, score))
    return max(scored, key=lambda item: item[1], default=None)


def _best_selection_match(segments, selection: SelectionRef) -> tuple[int, float] | None:
    excerpt = selection.excerpt.strip()
    if not excerpt:
        return None
    normalized_excerpt = _normalize(excerpt)
    for index, segment in enumerate(segments):
        normalized_segment = _normalize(segment.text)
        if normalized_segment and (normalized_segment in normalized_excerpt or normalized_excerpt in normalized_segment):
            return index, 1.0
    return _best_target_match(segments, excerpt)


def _context_range(segments, matched_index: int) -> tuple[int, int]:
    matched = segments[matched_index]
    if matched.kind == "heading" and matched.heading_path:
        path = matched.heading_path
        end = matched_index
        for index in range(matched_index + 1, len(segments)):
            candidate_path = segments[index].heading_path
            if candidate_path[: len(path)] != path:
                break
            end = index
        return matched_index, end
    start = matched_index
    end = matched_index
    while start > 0 and matched_index - start < 2 and segments[start - 1].kind != "heading":
        start -= 1
    while end + 1 < len(segments) and end - matched_index < 2 and segments[end + 1].kind != "heading":
        end += 1
    if matched.heading_path:
        heading_index = matched_index - 1
        while heading_index >= 0:
            if segments[heading_index].kind == "heading" and segments[heading_index].heading_path == matched.heading_path:
                start = heading_index
                break
            heading_index -= 1
    return start, end


def _bounded_context(segments, *, max_chars: int) -> str:
    parts: list[str] = []
    current_length = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        prefix = "# " if segment.kind == "heading" else ""
        part = f"{prefix}{text}"
        remaining = max_chars - current_length
        if remaining <= 0:
            break
        if len(part) > remaining:
            parts.append(part[:remaining])
            break
        parts.append(part)
        current_length += len(part) + 2
    return "\n\n".join(parts).strip()


def _outline(segments) -> list[dict[str, Any]]:
    return [
        {
            "segment_id": segment.segment_id,
            "heading_path": segment.heading_path,
            "heading": segment.text,
            "order_index": segment.order_index,
        }
        for segment in segments
        if segment.kind == "heading"
    ][:120]


def _candidate_payloads(segments, target: str) -> list[dict[str, Any]]:
    scored = []
    for index, segment in enumerate(segments):
        score = max(_similarity(target, segment.text), _similarity(target, " / ".join(segment.heading_path)))
        scored.append((score, index, segment))
    return [
        {
            "segment_id": segment.segment_id,
            "kind": segment.kind,
            "heading_path": segment.heading_path,
            "excerpt": compact_segment_text(segment.text, limit=300),
            "confidence": round(score, 3),
        }
        for score, _index, segment in sorted(scored, reverse=True)[:5]
        if score > 0
    ]


def _display_label(heading_path: list[str], text: str) -> str:
    if heading_path:
        return " / ".join(heading_path)
    return compact_segment_text(text, limit=80) or "当前板书片段"
