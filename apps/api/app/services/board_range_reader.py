from __future__ import annotations

from app.models import BoardFocusRef, BoardReadContext, BoardSegment, Lesson
from app.services.board_segment_index import build_board_segment_index, compact_segment_text, segment_text_hash


TARGET_EXCERPT_LIMIT = 6000
SURROUNDING_CONTEXT_LIMIT = 7600
NEIGHBOR_CONTEXT_LIMIT = 900


def build_board_read_context(*, lesson: Lesson, focus: BoardFocusRef) -> BoardReadContext:
    index = build_board_segment_index(lesson.board_document)
    base_segment = _base_segment_for_focus(focus=focus, segments=index.segments)
    selected_segments = _selected_range_segments(focus=focus, base_segment=base_segment, segments=index.segments)
    if not selected_segments and base_segment is not None:
        selected_segments = [base_segment]

    if not selected_segments:
        target_excerpt = compact_segment_text(focus.excerpt, limit=TARGET_EXCERPT_LIMIT)
        range_label = focus.display_label or "当前板书"
        read_focus = focus.model_copy(
            update={
                "excerpt": target_excerpt,
                "excerpt_hash": segment_text_hash(target_excerpt) if target_excerpt else focus.excerpt_hash,
                "source_segment_ids": focus.source_segment_ids,
            }
        )
        return BoardReadContext(
            target_focus=read_focus,
            target_excerpt=target_excerpt,
            surrounding_context=target_excerpt,
            before_text=focus.before_text,
            after_text=focus.after_text,
            range_label=range_label,
            source_segment_ids=focus.source_segment_ids,
            order_start=focus.order_start,
            order_end=focus.order_end,
            confidence=focus.confidence,
        )

    order_start = selected_segments[0].order_index
    order_end = selected_segments[-1].order_index
    source_segment_ids = [segment.segment_id for segment in selected_segments]
    target_excerpt = _target_excerpt(focus=focus, selected_segments=selected_segments)
    before_text = _neighbor_text(segments=index.segments, start=order_start, end=order_end, side="before")
    after_text = _neighbor_text(segments=index.segments, start=order_start, end=order_end, side="after")
    range_label = _range_label(focus=focus, selected_segments=selected_segments)
    surrounding_context = _surrounding_context(
        range_label=range_label,
        target_excerpt=target_excerpt,
        before_text=before_text,
        after_text=after_text,
    )
    read_focus = focus.model_copy(
        update={
            "segment_id": base_segment.segment_id if base_segment is not None else selected_segments[0].segment_id,
            "document_id": selected_segments[0].document_id,
            "kind": base_segment.kind if base_segment is not None else selected_segments[0].kind,
            "heading_path": base_segment.heading_path if base_segment is not None else selected_segments[0].heading_path,
            "excerpt": target_excerpt,
            "before_text": before_text,
            "after_text": after_text,
            "text_hash": base_segment.text_hash if base_segment is not None else selected_segments[0].text_hash,
            "excerpt_hash": segment_text_hash(target_excerpt) if target_excerpt else focus.excerpt_hash,
            "display_label": range_label,
            "source_segment_ids": source_segment_ids,
            "order_start": order_start,
            "order_end": order_end,
        }
    )
    return BoardReadContext(
        target_focus=read_focus,
        target_excerpt=target_excerpt,
        surrounding_context=surrounding_context,
        before_text=before_text,
        after_text=after_text,
        range_label=range_label,
        source_segment_ids=source_segment_ids,
        order_start=order_start,
        order_end=order_end,
        confidence=focus.confidence,
    )


def _base_segment_for_focus(*, focus: BoardFocusRef, segments: list[BoardSegment]) -> BoardSegment | None:
    if focus.segment_id:
        matched = next((segment for segment in segments if segment.segment_id == focus.segment_id), None)
        if matched is not None:
            return matched
    if focus.text_hash:
        matched = next((segment for segment in segments if segment.text_hash == focus.text_hash), None)
        if matched is not None:
            return matched
    if focus.order_start is not None:
        matched = next((segment for segment in segments if segment.order_index == focus.order_start), None)
        if matched is not None:
            return matched
    excerpt_key = _text_key(focus.excerpt)
    if excerpt_key:
        for segment in segments:
            segment_key = _text_key(segment.text)
            if excerpt_key in segment_key or segment_key in excerpt_key:
                return segment
    return None


def _selected_range_segments(
    *,
    focus: BoardFocusRef,
    base_segment: BoardSegment | None,
    segments: list[BoardSegment],
) -> list[BoardSegment]:
    if focus.order_start is not None and focus.order_end is not None and focus.order_end > focus.order_start:
        source_ids = set(focus.source_segment_ids)
        selected = [
            segment
            for segment in segments
            if focus.order_start <= segment.order_index <= focus.order_end
            and (not source_ids or segment.segment_id in source_ids)
            and segment.text.strip()
        ]
        if selected:
            return selected

    if base_segment is not None and (focus.kind == "heading" or base_segment.kind == "heading"):
        return _heading_section_segments(base_segment=base_segment, segments=segments)

    source_ids = set(focus.source_segment_ids)
    if source_ids:
        selected = [segment for segment in segments if segment.segment_id in source_ids and segment.text.strip()]
        if selected:
            return selected

    return [base_segment] if base_segment is not None and base_segment.text.strip() else []


def _heading_section_segments(*, base_segment: BoardSegment, segments: list[BoardSegment]) -> list[BoardSegment]:
    if base_segment.kind != "heading":
        return [base_segment]
    start = base_segment.order_index
    depth = len(base_segment.heading_path) or 1
    end = segments[-1].order_index if segments else start
    for segment in segments:
        if segment.order_index <= start or segment.kind != "heading":
            continue
        next_depth = len(segment.heading_path) or 1
        if next_depth <= depth:
            end = segment.order_index - 1
            break
    return [segment for segment in segments if start <= segment.order_index <= end and segment.text.strip()]


def _neighbor_text(*, segments: list[BoardSegment], start: int, end: int, side: str) -> str:
    if side == "before":
        neighbors = [segment for segment in segments if segment.order_index < start and segment.text.strip()][-2:]
    else:
        neighbors = [segment for segment in segments if segment.order_index > end and segment.text.strip()][:2]
    return compact_segment_text(_join_segment_text(neighbors), limit=NEIGHBOR_CONTEXT_LIMIT)


def _surrounding_context(*, range_label: str, target_excerpt: str, before_text: str, after_text: str) -> str:
    parts: list[str] = []
    if before_text:
        parts.append(f"前文：\n{before_text}")
    if range_label:
        parts.append(f"目标范围：{range_label}\n{target_excerpt}")
    else:
        parts.append(f"目标范围：\n{target_excerpt}")
    if after_text:
        parts.append(f"后文：\n{after_text}")
    return _limit_text("\n\n".join(parts), limit=SURROUNDING_CONTEXT_LIMIT)


def _range_label(*, focus: BoardFocusRef, selected_segments: list[BoardSegment]) -> str:
    base_label = focus.display_label.strip()
    if not base_label:
        path = " / ".join(selected_segments[0].heading_path)
        base_label = path or "当前板书"
    start = selected_segments[0].order_index + 1
    end = selected_segments[-1].order_index + 1
    if start == end:
        return f"{base_label} · 第{start}段"
    return f"{base_label} · 第{start}-{end}段"


def _join_segment_text(segments: list[BoardSegment]) -> str:
    return "\n\n".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


def _target_excerpt(*, focus: BoardFocusRef, selected_segments: list[BoardSegment]) -> str:
    selected_text = _join_segment_text(selected_segments)
    if len(selected_segments) == 1 and selected_segments[0].kind != "heading":
        focus_excerpt = _limit_text(focus.excerpt, limit=TARGET_EXCERPT_LIMIT)
        focus_key = _text_key(focus_excerpt)
        selected_key = _text_key(selected_text)
        if focus_key and focus_key in selected_key and focus_key != selected_key:
            return focus_excerpt
        if focus_key and focus_key != selected_key and focus.confidence >= 0.85:
            return focus_excerpt
    return _limit_text(selected_text, limit=TARGET_EXCERPT_LIMIT)


def _limit_text(value: str, *, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def _text_key(value: str) -> str:
    return "".join((value or "").split()).casefold()
