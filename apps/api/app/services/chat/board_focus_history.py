from __future__ import annotations

import re

from app.models import BoardFocusRef, BoardTaskRequirementSheet, Lesson
from app.services.board_segment_index import build_board_segment_index
from app.services.board_task_manager import normalize_board_task_sheet
from app.services.chat.context import compact_text as _compact_text


RECENT_EDIT_FOLLOWUP_PATTERN = re.compile(
    r"(太长|篇幅|缩短|改短|短(?:一点|点|些)|精简|压缩|控制.{0,8}(?:以内|以下)|"
    r"[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下)|来回|回合)"
)
RECENT_WRITE_FOLLOWUP_PATTERN = re.compile(r"(继续|接着|直接|再|进一步|你自己看|自己看|自行|自己判断)")


def maybe_inherit_recent_board_edit_focus(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
) -> BoardTaskRequirementSheet:
    if board_task.requested_action not in {"edit", "write"}:
        return board_task
    if board_task.target_location is not None and board_task.location_status in {"selected", "resolved"}:
        return board_task
    if board_task.requested_action == "edit":
        if not _looks_like_recent_edit_followup(request_message):
            return board_task
        if board_task.target_hint.strip() and not _looks_like_recent_edit_followup(board_task.target_hint):
            return board_task
    elif board_task.requested_action == "write":
        if not _looks_like_recent_write_followup(request_message):
            return board_task
        if board_task.target_hint.strip() and not _looks_like_recent_write_followup(board_task.target_hint):
            return board_task
        if board_task.location_status not in {"missing", "ambiguous"}:
            return board_task
    else:
        return board_task
    focus = _latest_successful_board_edit_focus(lesson)
    if focus is None:
        return board_task
    if board_task.requested_action == "write" and not _recent_focus_matches_board_task(focus, board_task):
        return board_task
    inherited = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    inherited.target_location = focus
    inherited.target_hint = focus.display_label or "最近一次板书编辑的目标区域"
    inherited.location_status = "resolved"
    inherited.clarification_question = ""
    return normalize_board_task_sheet(inherited)


def recent_board_edit_focus_for_commit(
    *,
    lesson: Lesson,
    fallback_focus: BoardFocusRef | None,
    section_titles: list[str],
) -> BoardFocusRef | None:
    if fallback_focus is not None:
        return fallback_focus
    titles = [title.strip() for title in section_titles if title.strip()]
    for title in reversed(titles):
        focus = _focus_from_section_title(lesson=lesson, title=title)
        if focus is not None:
            return focus
    return None


def implicit_board_search_evidence(
    *,
    route: str,
    target_scope: str | None,
    reason: str,
) -> dict[str, object]:
    return {
        "status": "found" if target_scope in {"append", "whole_document"} else "missing",
        "query_plan": {"source": "workflow", "target_scope": target_scope, "route": route},
        "candidates": [],
        "selected_match_id": None,
        "reason": reason,
    }


def _looks_like_recent_edit_followup(text: str) -> bool:
    compact = _compact_text(text, limit=180)
    return bool(compact and RECENT_EDIT_FOLLOWUP_PATTERN.search(compact))


def _looks_like_recent_write_followup(text: str) -> bool:
    compact = _compact_text(text, limit=180)
    return bool(compact and RECENT_WRITE_FOLLOWUP_PATTERN.search(compact))


def _text_overlap_tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    tokens: set[str] = set(re.findall(r"[a-zÀ-ÿ][a-zÀ-ÿ'’_-]{2,}", compact))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", compact):
        tokens.update(chunk[index : index + 2] for index in range(0, max(0, len(chunk) - 1)))
    return {token for token in tokens if len(token) >= 2}


def _recent_focus_matches_board_task(focus: BoardFocusRef, board_task: BoardTaskRequirementSheet) -> bool:
    query = _compact_text(" ".join([board_task.target_hint, board_task.question_or_topic]), limit=500)
    if not query:
        return True
    focus_text = _compact_text(
        " ".join(
            [
                focus.display_label,
                " ".join(focus.heading_path),
                focus.excerpt,
                focus.before_text,
                focus.after_text,
            ]
        ),
        limit=1200,
    )
    query_tokens = _text_overlap_tokens(query)
    focus_tokens = _text_overlap_tokens(focus_text)
    if not query_tokens or not focus_tokens:
        return False
    return len(query_tokens & focus_tokens) >= 2


def _latest_successful_board_edit_focus(lesson: Lesson) -> BoardFocusRef | None:
    for commit in reversed(lesson.history_graph.commits):
        metadata = commit.metadata if isinstance(commit.metadata, dict) else {}
        if metadata.get("kind") != "board_document_edit":
            continue
        if metadata.get("board_task_cleared") is False:
            continue
        raw_focus = metadata.get("recent_board_edit_focus") or metadata.get("resolved_focus")
        if isinstance(raw_focus, dict):
            try:
                return BoardFocusRef.model_validate(raw_focus)
            except Exception:
                pass
        section_titles = metadata.get("board_section_titles")
        if isinstance(section_titles, list):
            titles = [str(title).strip() for title in section_titles if str(title).strip()]
            for title in reversed(titles):
                focus = _focus_from_section_title(lesson=lesson, title=title)
                if focus is not None:
                    return focus
    return None


def _focus_from_section_title(*, lesson: Lesson, title: str) -> BoardFocusRef | None:
    compact_title = _compact_text(title, limit=120)
    if not compact_title:
        return None
    index = build_board_segment_index(lesson.board_document)
    for idx, segment in enumerate(index.segments):
        if segment.kind != "heading" or compact_title not in _compact_text(segment.text, limit=240):
            continue
        target = segment
        for following in index.segments[idx + 1 :]:
            if following.kind == "heading":
                break
            if following.text.strip():
                target = following
                break
        before = index.segments[target.order_index - 1].text if target.order_index and target.order_index > 0 else ""
        after = (
            index.segments[target.order_index + 1].text
            if target.order_index is not None and target.order_index + 1 < len(index.segments)
            else ""
        )
        return BoardFocusRef(
            source="board",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=target.segment_id,
            kind=target.kind,
            heading_path=target.heading_path,
            excerpt=target.text,
            before_text=before,
            after_text=after,
            text_hash=target.text_hash,
            confidence=0.95,
            reason="根据最近一次板书编辑返回的 section title 定位到新增/编辑区域。",
            display_label=" / ".join(target.heading_path) or compact_title,
            match_id=f"recent:{target.segment_id}",
            source_segment_ids=[target.segment_id],
            order_start=target.order_index,
            order_end=target.order_index,
            score_breakdown={"recent_board_edit_focus": 0.95},
        )
    return None
