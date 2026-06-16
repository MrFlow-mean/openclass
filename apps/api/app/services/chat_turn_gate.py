from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import BoardGenerationAction, ChatInteractionMode, ResourceReferenceAction, TeachingAction
from app.services import turn_intent
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.learning_requirement_manager import (
    is_explicit_board_generation_request,
    is_generation_control_request,
)


ChatTurnRoute = Literal[
    "ordinary_chat",
    "initial_learning",
    "initial_board_generation",
    "existing_board_task",
    "resource_reference",
]


@dataclass(frozen=True)
class ChatTurnGateDecision:
    route: ChatTurnRoute
    reason: str
    should_update_learning_requirement: bool
    should_try_board_task: bool
    requires_resource_reference: bool
    matched_rules: list[str]

    def metadata(self) -> dict[str, object]:
        return {
            "route": self.route,
            "reason": self.reason,
            "should_update_learning_requirement": self.should_update_learning_requirement,
            "should_try_board_task": self.should_try_board_task,
            "requires_resource_reference": self.requires_resource_reference,
            "matched_rules": list(self.matched_rules),
        }


def decide_chat_turn(
    *,
    message: str,
    document_empty: bool,
    has_selection: bool,
    interaction_mode: ChatInteractionMode,
    board_generation_action: BoardGenerationAction | None,
    teaching_action: TeachingAction | None,
    resource_reference_action: ResourceReferenceAction | None,
    board_action_decision: BoardTaskActionDecision | None,
    has_active_board_task: bool = False,
) -> ChatTurnGateDecision:
    signals = turn_intent.extract_intent_signals(message)
    matched_rules = sorted(signals.raw_matches)

    if board_generation_action == "start":
        if not document_empty:
            return _decision(
                "existing_board_task",
                reason="已有板书下的生成控制必须进入第二层板书任务链路。",
                matched_rules=[*matched_rules, "board_generation_action:start", "existing_board"],
            )
        return _decision(
            "initial_board_generation",
            reason="用户明确要求开始生成板书。",
            matched_rules=[*matched_rules, "board_generation_action:start"],
        )

    if resource_reference_action is not None or signals.wants_explicit_resource:
        return _decision(
            "resource_reference",
            reason="用户明确引用资料或正在处理资料确认。",
            matched_rules=[*matched_rules, "resource_reference"],
        )

    if is_generation_control_request(message) or is_explicit_board_generation_request(message):
        if not document_empty:
            return _decision(
                "existing_board_task",
                reason="已有板书下的生成或讲解控制必须进入第二层板书任务链路。",
                matched_rules=[*matched_rules, "generation_control", "existing_board"],
            )
        return _decision(
            "initial_board_generation",
            reason="用户正在把初始需求推进到板书生成。",
            matched_rules=[*matched_rules, "generation_control"],
        )

    if teaching_action is not None:
        return _decision(
            "existing_board_task",
            reason="用户触发已有板书分节讲解动作。",
            matched_rules=[*matched_rules, f"teaching_action:{teaching_action}"],
        )

    if not document_empty:
        if has_active_board_task:
            return _decision(
                "existing_board_task",
                reason="已有未完成板书任务，需要继续第二层任务链路。",
                matched_rules=[*matched_rules, "active_board_task"],
            )
        if _has_existing_board_task_signal(
            has_selection=has_selection,
            interaction_mode=interaction_mode,
            board_action_decision=board_action_decision,
            signals=signals,
        ):
            return _decision(
                "existing_board_task",
                reason="已有板书下识别到写、改、讲、互动或选区任务。",
                matched_rules=matched_rules,
            )
        if signals.wants_resource:
            return _decision(
                "resource_reference",
                reason="已有板书下识别到资料引用信号。",
                matched_rules=[*matched_rules, "resource_reference"],
            )
        return _decision("ordinary_chat", reason="没有足够信号进入已有板书任务。", matched_rules=matched_rules)

    if signals.wants_resource:
        return _decision(
            "resource_reference",
            reason="空白板书下识别到资料引用信号。",
            matched_rules=[*matched_rules, "resource_reference"],
        )

    if _has_initial_learning_signal(signals):
        return _decision(
            "initial_learning",
            reason="空白板书下识别到学习、练习、讲解或可写入学习产物意图。",
            matched_rules=matched_rules,
        )

    return _decision("ordinary_chat", reason="用户本轮没有表达学习工作台任务。", matched_rules=matched_rules)


def _decision(route: ChatTurnRoute, *, reason: str, matched_rules: list[str]) -> ChatTurnGateDecision:
    return ChatTurnGateDecision(
        route=route,
        reason=reason,
        should_update_learning_requirement=route in {"initial_learning", "initial_board_generation"},
        should_try_board_task=route == "existing_board_task",
        requires_resource_reference=route == "resource_reference",
        matched_rules=sorted(set(matched_rules)),
    )


def _has_existing_board_task_signal(
    *,
    has_selection: bool,
    interaction_mode: ChatInteractionMode,
    board_action_decision: BoardTaskActionDecision | None,
    signals: turn_intent.IntentSignals,
) -> bool:
    if has_selection or interaction_mode == "direct_edit":
        return True
    if board_action_decision and board_action_decision.board_action is not None:
        return True
    return bool(signals.wants_chat)


def _has_initial_learning_signal(signals: turn_intent.IntentSignals) -> bool:
    return any(
        [
            signals.wants_learning_start,
            signals.wants_chat,
            signals.wants_document_artifact,
            signals.wants_write,
            signals.wants_edit,
            signals.wants_append,
            signals.wants_expand,
            signals.wants_simplify,
            signals.wants_rewrite,
            signals.wants_explain,
            signals.wants_sequence,
            signals.wants_collection,
            signals.wants_whole_document,
        ]
    )
