from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import (
    BoardDecision,
    ChatRequest,
    InteractionRuleStep,
    InteractionSession,
    InteractionTurnDecision,
    Lesson,
)
from app.services.history import commit_operations
from app.services.openai_course_ai import openai_course_ai


@dataclass(frozen=True)
class InteractionSessionTurnOutcome:
    chatbot_message: str
    board_decision: BoardDecision
    interaction_decision: InteractionTurnDecision
    active_interaction_session: InteractionSession | None
    reroute_user_message: bool = False


def run_interaction_session_turn(
    *,
    lesson: Lesson,
    request: ChatRequest,
    conversation_summary: str,
) -> InteractionSessionTurnOutcome:
    session = lesson.active_interaction_session
    if session is None:
        decision = InteractionTurnDecision(
            route="new_task",
            reason="没有进行中的互动会话。",
            user_intent="new_task",
        )
        return InteractionSessionTurnOutcome(
            chatbot_message="",
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            active_interaction_session=None,
            reroute_user_message=True,
        )

    decision = openai_course_ai.generate_interaction_turn_decision(
        lesson_title=lesson.title,
        session=session,
        board_summary=session.reference_context,
        resource_summary="",
        conversation_summary=conversation_summary,
        user_message=request.message,
        selection_excerpt=request.selection.excerpt if request.selection else None,
    ) or _fallback_turn_decision(session=session, user_message=request.message)
    decision = _normalize_legacy_decision(decision)

    if decision.route == "new_task":
        lesson.active_interaction_session = None
        chatbot_message = _generate_reply(
            lesson=lesson,
            session=session,
            decision=decision,
            conversation_summary=conversation_summary,
            user_message=request.message,
            fallback="",
        )
        _commit_interaction_turn(
            lesson=lesson,
            request=request,
            chatbot_message=chatbot_message,
            decision=decision,
            session_before=session,
            session_after=None,
            label="Interaction session ended by new task",
        )
        return InteractionSessionTurnOutcome(
            chatbot_message=chatbot_message,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            active_interaction_session=None,
            reroute_user_message=True,
        )

    if decision.route == "exit_rule":
        lesson.active_interaction_session = None
        chatbot_message = _generate_reply(
            lesson=lesson,
            session=session,
            decision=decision,
            conversation_summary=conversation_summary,
            user_message=request.message,
            fallback="这轮规则互动已结束。",
        )
        _commit_interaction_turn(
            lesson=lesson,
            request=request,
            chatbot_message=chatbot_message,
            decision=decision,
            session_before=session,
            session_after=None,
            label="Interaction session ended",
        )
        return InteractionSessionTurnOutcome(
            chatbot_message=chatbot_message,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            active_interaction_session=None,
        )

    if decision.route == "rule_violation":
        next_session = session.model_copy(
            update={
                "last_violation_reason": decision.reason,
                "progress_note": decision.progress_note or session.progress_note,
            }
        )
        lesson.active_interaction_session = next_session
        chatbot_message = _generate_reply(
            lesson=lesson,
            session=next_session,
            decision=decision,
            conversation_summary=conversation_summary,
            user_message=request.message,
            fallback=_violation_fallback(next_session, decision),
        )
        _commit_interaction_turn(
            lesson=lesson,
            request=request,
            chatbot_message=chatbot_message,
            decision=decision,
            session_before=session,
            session_after=next_session,
            label="Interaction session rule violation",
        )
        return InteractionSessionTurnOutcome(
            chatbot_message=chatbot_message,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            active_interaction_session=next_session,
        )

    next_session, deterministic_reply = _continue_session(session=session, user_message=request.message)
    if decision.progress_note.strip():
        next_session = next_session.model_copy(update={"progress_note": decision.progress_note.strip()})
    lesson.active_interaction_session = next_session
    chatbot_message = deterministic_reply or _generate_reply(
        lesson=lesson,
        session=next_session,
        decision=decision,
        conversation_summary=conversation_summary,
        user_message=request.message,
        fallback=_continue_fallback(next_session),
    )
    _commit_interaction_turn(
        lesson=lesson,
        request=request,
        chatbot_message=chatbot_message,
        decision=decision,
        session_before=session,
        session_after=next_session,
        label="Interaction session turn",
    )
    return InteractionSessionTurnOutcome(
        chatbot_message=chatbot_message,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        active_interaction_session=next_session,
    )


def _fallback_turn_decision(*, session: InteractionSession, user_message: str) -> InteractionTurnDecision:
    if _is_exit_message(user_message):
        return InteractionTurnDecision(
            route="exit_rule",
            reason="用户明确表达结束当前互动。",
            user_intent="exit_rule",
        )
    if _is_new_task_message(user_message):
        return InteractionTurnDecision(
            route="new_task",
            reason="用户提出了新的写、改、讲或学习任务。",
            user_intent="new_task",
        )
    step = _current_step(session)
    if step and not _matches_expected_input(user_message, step.expected_user_input):
        return InteractionTurnDecision(
            route="rule_violation",
            reason="用户输入没有匹配当前规则步骤。",
            progress_note=session.progress_note,
            user_intent="rule_violation",
        )
    return InteractionTurnDecision(
        route="continue_rule",
        reason="用户输入仍在当前互动规则内。",
        progress_note=session.progress_note,
        user_intent="continue_rule",
    )


def _normalize_legacy_decision(decision: InteractionTurnDecision) -> InteractionTurnDecision:
    if decision.route == "side_learning_request":
        return decision.model_copy(update={"route": "new_task"})
    if decision.route == "resume_rule":
        return decision.model_copy(update={"route": "continue_rule"})
    return decision


def _continue_session(
    *,
    session: InteractionSession,
    user_message: str,
) -> tuple[InteractionSession, str]:
    step = _current_step(session)
    if step is None:
        return session.model_copy(update={"turn_count": session.turn_count + 1, "last_violation_reason": ""}), ""
    updated_steps = list(session.rule_steps)
    updated_steps[session.current_step_index] = step.model_copy(update={"completed": True})
    next_index = min(session.current_step_index + 1, len(updated_steps))
    progress_note = _progress_note(next_index=next_index, total=len(updated_steps))
    next_session = session.model_copy(
        update={
            "rule_steps": updated_steps,
            "current_step_index": next_index,
            "turn_count": session.turn_count + 1,
            "progress_note": progress_note,
            "last_violation_reason": "",
        }
    )
    return next_session, step.assistant_response.strip()


def _current_step(session: InteractionSession) -> InteractionRuleStep | None:
    if not session.rule_steps:
        return None
    if session.current_step_index < 0 or session.current_step_index >= len(session.rule_steps):
        return None
    return session.rule_steps[session.current_step_index]


def _matches_expected_input(user_message: str, expected_input: str) -> bool:
    expected = _compact(expected_input)
    actual = _compact(user_message)
    if not expected:
        return True
    if actual == expected:
        return True
    if len(actual) >= 4 and actual in expected:
        return True
    if len(expected) >= 4 and expected in actual:
        return True
    return False


def _generate_reply(
    *,
    lesson: Lesson,
    session: InteractionSession,
    decision: InteractionTurnDecision,
    conversation_summary: str,
    user_message: str,
    fallback: str,
) -> str:
    generated = openai_course_ai.generate_interaction_session_reply(
        lesson_title=lesson.title,
        session=session,
        decision=decision,
        conversation_summary=conversation_summary,
        user_message=user_message,
    )
    if generated and generated.chatbot_message.strip():
        return generated.chatbot_message.strip()
    return fallback


def _commit_interaction_turn(
    *,
    lesson: Lesson,
    request: ChatRequest,
    chatbot_message: str,
    decision: InteractionTurnDecision,
    session_before: InteractionSession,
    session_after: InteractionSession | None,
    label: str,
) -> None:
    commit_operations(
        lesson,
        [],
        label=label,
        message="Recorded an interaction session turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_session_turn",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "interaction_session",
            "document_changed": False,
            "interaction_decision": decision.model_dump(mode="json"),
            "active_interaction_session_before": session_before.model_dump(mode="json"),
            "active_interaction_session_after": (
                session_after.model_dump(mode="json") if session_after is not None else None
            ),
            "interaction_session_cleared": session_after is None,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
        },
    )


def _is_exit_message(user_message: str) -> bool:
    return bool(
        re.search(
            r"(结束|退出|停止|终止|取消).{0,8}(互动|规则|练习|角色|会话)?|"
            r"(不继续|先到这里|到此为止)|\b(exit|quit|stop|end)\b",
            user_message or "",
            flags=re.IGNORECASE,
        )
    )


def _is_new_task_message(user_message: str) -> bool:
    return bool(
        re.search(
            r"(写|补写|扩写|改|修改|改写|缩短|润色|讲|讲解|解释|说明|总结|生成|换个任务|新任务|另一个任务)|"
            r"\b(write|edit|rewrite|explain|summarize|generate|new task)\b",
            user_message or "",
            flags=re.IGNORECASE,
        )
    )


def _violation_fallback(session: InteractionSession, decision: InteractionTurnDecision) -> str:
    expected = ""
    step = _current_step(session)
    if step and step.expected_user_input.strip():
        expected = f"当前应输入：{step.expected_user_input.strip()}"
    rule = session.compliant_input_rule or session.expected_user_behavior or session.rule_text
    parts = [decision.reason.strip(), rule.strip(), expected]
    return " ".join(part for part in parts if part).strip()


def _continue_fallback(session: InteractionSession) -> str:
    step = _current_step(session)
    if step and step.expected_user_input.strip():
        return f"继续按当前规则来，下一步等你输入：{step.expected_user_input.strip()}"
    return session.assistant_behavior.strip() or session.progress_note.strip() or "继续按当前规则来。"


def _progress_note(*, next_index: int, total: int) -> str:
    if total <= 0:
        return "规则互动继续进行中。"
    if next_index >= total:
        return f"已完成 {total} / {total} 个规则步骤，等待用户继续或自然结束互动。"
    return f"已完成 {next_index} / {total} 个规则步骤，等待用户完成下一步。"


def _compact(value: str) -> str:
    return re.sub(r"[\s，,。；;：:!！?？.'\"“”‘’`*_()（）-]+", "", value or "").casefold()
