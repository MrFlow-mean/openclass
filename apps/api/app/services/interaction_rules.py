from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    BoardFocusRef,
    InteractionRuleDraft,
    InteractionSession,
    InteractionTurnDecision,
    Lesson,
    SelectionRef,
)
from app.services.board_segment_index import compact_segment_text
from app.services.openai_course_ai import openai_course_ai
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus


@dataclass(frozen=True)
class InteractionStartResolution:
    session: InteractionSession | None
    focus_resolution: FocusResolution | None = None


def should_start_interaction(draft: InteractionRuleDraft | None) -> bool:
    return bool(draft and draft.should_start and draft.rule_text.strip())


def build_interaction_start(
    *,
    lesson: Lesson,
    draft: InteractionRuleDraft,
    user_message: str,
    selection: SelectionRef | None = None,
    selection_text: str | None = None,
    resolved_focus: BoardFocusRef | None = None,
) -> InteractionStartResolution:
    focus_resolution: FocusResolution | None = None
    focus: BoardFocusRef | None = resolved_focus
    target_query = compact_segment_text(draft.target_hint or "", limit=500)
    selected_excerpt = compact_segment_text(selection.excerpt if selection else selection_text, limit=1200)
    if focus is None and (selected_excerpt or target_query):
        focus_resolution = resolve_board_focus(
            lesson=lesson,
            user_message=target_query or user_message,
            selection=selection,
            selection_text=selection_text,
            action_type="explain_target",
        )
        if not focus_resolution.resolved:
            return InteractionStartResolution(session=None, focus_resolution=focus_resolution)
        focus = focus_resolution.focus

    reference_context = _reference_context(
        lesson=lesson,
        focus=focus,
        selection=selection,
        selection_text=selection_text,
    )
    session = InteractionSession(
        status="active",
        rule_text=compact_segment_text(draft.rule_text, limit=1000),
        interaction_goal=compact_segment_text(draft.interaction_goal, limit=500),
        target_focus=focus,
        reference_context=reference_context,
        compliant_input_rule=compact_segment_text(draft.expected_user_behavior, limit=500),
        expected_user_behavior=compact_segment_text(draft.expected_user_behavior, limit=500),
        assistant_behavior=compact_segment_text(draft.assistant_behavior, limit=500),
        progress_note="",
        pause_reason="",
        turn_count=0,
    )
    return InteractionStartResolution(session=session, focus_resolution=focus_resolution)


def decide_interaction_turn(
    *,
    lesson: Lesson,
    session: InteractionSession,
    resource_summary: str,
    conversation_summary: str,
    user_message: str,
    selection_excerpt: str | None = None,
) -> InteractionTurnDecision | None:
    return openai_course_ai.generate_interaction_turn_decision(
        lesson_title=lesson.title,
        session=session,
        board_summary=lesson.board_document.content_text or lesson.board_document.title,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=user_message,
        selection_excerpt=selection_excerpt,
    )


def apply_interaction_decision(
    session: InteractionSession,
    decision: InteractionTurnDecision,
) -> InteractionSession | None:
    if decision.route in {"exit_rule", "new_task"}:
        return None
    updates: dict[str, object] = {
        "progress_note": compact_segment_text(decision.progress_note or session.progress_note, limit=1000),
    }
    if decision.route == "side_learning_request":
        updates["status"] = "paused"
        updates["pause_reason"] = compact_segment_text(decision.reason, limit=500)
    elif decision.route == "resume_rule":
        updates["status"] = "active"
        updates["pause_reason"] = ""
        updates["turn_count"] = session.turn_count + 1
    else:
        updates["status"] = "active"
        updates["pause_reason"] = ""
        updates["turn_count"] = session.turn_count + 1
    return session.model_copy(update=updates)


def interaction_context_payload(
    *,
    session: InteractionSession,
    decision: InteractionTurnDecision | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "rule_text": session.rule_text,
        "interaction_goal": session.interaction_goal,
        "reference_context": session.reference_context,
        "compliant_input_rule": session.compliant_input_rule,
        "expected_user_behavior": session.expected_user_behavior,
        "assistant_behavior": session.assistant_behavior,
        "progress_note": session.progress_note,
        "status": session.status,
        "turn_count": session.turn_count,
    }
    if session.source_board_task_run_id:
        payload["source_board_task_run_id"] = session.source_board_task_run_id
    if session.source_board_task_version_id:
        payload["source_board_task_version_id"] = session.source_board_task_version_id
    if session.source_board_task_route:
        payload["source_board_task_route"] = session.source_board_task_route
    if session.target_focus is not None:
        payload["target_focus"] = session.target_focus.model_dump(mode="json")
    if decision is not None:
        payload["turn_decision"] = decision.model_dump(mode="json")
    return payload


def interaction_session_metadata(
    *,
    before: InteractionSession | None,
    after: InteractionSession | None,
    decision: InteractionTurnDecision | None = None,
) -> dict[str, object]:
    return {
        "interaction_decision": decision.model_dump(mode="json") if decision else None,
        "interaction_session_before": before.model_dump(mode="json") if before else None,
        "interaction_session_after": after.model_dump(mode="json") if after else None,
        "active_interaction_session_after": after.model_dump(mode="json") if after else None,
    }


def _reference_context(
    *,
    lesson: Lesson,
    focus: BoardFocusRef | None,
    selection: SelectionRef | None,
    selection_text: str | None,
) -> str:
    if focus is not None:
        return compact_segment_text(focus_context(focus), limit=1800)
    selected_excerpt = compact_segment_text(selection.excerpt if selection else selection_text, limit=1200)
    if selected_excerpt:
        parts = []
        if selection and selection.heading_path:
            parts.append(" / ".join(selection.heading_path))
        if selection and selection.before_text:
            parts.append(compact_segment_text(selection.before_text, limit=300))
        parts.append(selected_excerpt)
        if selection and selection.after_text:
            parts.append(compact_segment_text(selection.after_text, limit=300))
        return "\n".join(part for part in parts if part)
    return compact_segment_text(lesson.board_document.content_text or lesson.board_document.title, limit=1800)
