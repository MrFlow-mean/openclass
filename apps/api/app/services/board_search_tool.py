from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.models import BoardFocusRef, BoardSearchCandidate, BoardSearchEvidence, Lesson
from app.services.board_range_reader import build_board_read_context


class BoardSearchTool:
    def search(
        self,
        *,
        lesson: Lesson,
        resolve_focus: Callable[[], Any],
    ) -> Any:
        resolution = resolve_focus()
        return self._with_read_context(lesson=lesson, resolution=resolution)

    def _with_read_context(self, *, lesson: Lesson, resolution: Any) -> Any:
        evidence = getattr(resolution, "evidence", None)
        focus = getattr(resolution, "focus", None)
        candidates = list(getattr(resolution, "candidates", []) or [])
        status = str(getattr(resolution, "status", "") or "")

        if focus is None:
            return replace(
                resolution,
                evidence=_evidence_with_summary(
                    evidence=evidence,
                    status=status,
                    candidates=candidates,
                    read_focus=None,
                    read_context=None,
                ),
            )

        read_context = build_board_read_context(lesson=lesson, focus=focus)
        read_focus = read_context.target_focus
        selected_match_id = evidence.selected_match_id if evidence else read_focus.match_id
        next_candidates = _replace_matching_focuses(candidates=candidates, read_focus=read_focus)
        next_evidence = _evidence_with_summary(
            evidence=evidence,
            status=status,
            candidates=next_candidates,
            read_focus=read_focus,
            read_context=read_context,
            selected_match_id=selected_match_id,
        )
        return replace(
            resolution,
            focus=read_focus,
            candidates=next_candidates,
            evidence=next_evidence,
        )


def _replace_matching_focuses(*, candidates: list[BoardFocusRef], read_focus: BoardFocusRef) -> list[BoardFocusRef]:
    if not candidates:
        return [read_focus]
    replaced = False
    next_candidates: list[BoardFocusRef] = []
    for candidate in candidates:
        if _same_focus(candidate, read_focus):
            next_candidates.append(read_focus)
            replaced = True
        else:
            next_candidates.append(candidate)
    return next_candidates if replaced else [read_focus, *next_candidates]


def _evidence_with_summary(
    *,
    evidence: BoardSearchEvidence | None,
    status: str,
    candidates: list[BoardFocusRef],
    read_focus: BoardFocusRef | None,
    read_context: Any,
    selected_match_id: str | None = None,
) -> BoardSearchEvidence | None:
    if evidence is None:
        return None

    selected_candidate = _selected_candidate(evidence=evidence, selected_match_id=selected_match_id, read_focus=read_focus)
    next_candidates = [
        _candidate_with_focus(candidate=candidate, read_focus=read_focus)
        for candidate in evidence.candidates
    ]
    source = selected_candidate.source if selected_candidate is not None else (next_candidates[0].source if next_candidates else "")
    confidence = read_focus.confidence if read_focus is not None else (selected_candidate.score if selected_candidate else 0.0)
    failure_reason_code = _failure_reason_code(status=status, evidence=evidence)
    return evidence.model_copy(
        update={
            "candidates": next_candidates,
            "selected_match_id": selected_match_id or evidence.selected_match_id,
            "source": source,
            "confidence": confidence,
            "range_label": read_context.range_label if read_context is not None else "",
            "order_start": read_context.order_start if read_context is not None else None,
            "order_end": read_context.order_end if read_context is not None else None,
            "candidate_count": len(evidence.candidates) or len(candidates),
            "failure_reason_code": failure_reason_code,
            "read_context": read_context,
        }
    )


def _candidate_with_focus(
    *,
    candidate: BoardSearchCandidate,
    read_focus: BoardFocusRef | None,
) -> BoardSearchCandidate:
    if read_focus is None or not _same_focus(candidate.focus, read_focus):
        return candidate
    return candidate.model_copy(
        update={
            "focus": read_focus,
            "source_segment_ids": read_focus.source_segment_ids,
            "score": read_focus.confidence or candidate.score,
        }
    )


def _selected_candidate(
    *,
    evidence: BoardSearchEvidence,
    selected_match_id: str | None,
    read_focus: BoardFocusRef | None,
) -> BoardSearchCandidate | None:
    if selected_match_id:
        for candidate in evidence.candidates:
            if candidate.match_id == selected_match_id:
                return candidate
    if read_focus is not None:
        for candidate in evidence.candidates:
            if _same_focus(candidate.focus, read_focus):
                return candidate
    return evidence.candidates[0] if evidence.candidates else None


def _same_focus(left: BoardFocusRef, right: BoardFocusRef) -> bool:
    if left.match_id and right.match_id and left.match_id == right.match_id:
        return True
    if left.segment_id and right.segment_id and left.segment_id == right.segment_id:
        return True
    if left.text_hash and right.text_hash and left.text_hash == right.text_hash:
        return True
    return False


def _failure_reason_code(*, status: str, evidence: BoardSearchEvidence) -> str:
    if status == "ambiguous" or evidence.status == "ambiguous":
        return "ambiguous_location"
    if status == "content_absent" or evidence.status == "content_absent":
        return "content_absent"
    if status == "missing" or evidence.status == "missing":
        return "missing_location"
    return ""


board_search_tool = BoardSearchTool()
