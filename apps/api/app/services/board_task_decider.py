from __future__ import annotations

from dataclasses import dataclass

from app.models import BoardTaskAction
from app.services import turn_intent
from app.services.turn_intent import IntentSignals


DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {
    "generate_board",
    "append_section",
    "rewrite_target",
    "expand_target",
    "simplify_target",
}
TARGETED_ACTIONS: set[BoardTaskAction] = {
    "explain_target",
    "rewrite_target",
    "expand_target",
    "simplify_target",
}


@dataclass(frozen=True)
class BoardTaskActionDecision:
    board_action: BoardTaskAction | None
    reason: str
    write_allowed: bool
    requires_resource_resolution: bool
    requires_target_resolution: bool
    decision_notes: list[str]


def decide_board_task_action(
    *,
    message: str,
    signals: IntentSignals,
    has_selection: bool,
    document_empty: bool,
    interaction_mode: str | None,
    board_generation_action: str | None,
    has_explicit_resource_reference: bool,
) -> BoardTaskActionDecision:
    notes: list[str] = []
    if board_generation_action == "start":
        return _decision(
            "generate_board",
            reason="用户明确要求开始生成板书。",
            notes=[*notes, "board_generation_action:start"],
        )

    if interaction_mode == "direct_edit":
        notes.append("interaction_mode:direct_edit")
        if signals.wants_append:
            return _decision("append_section", reason="直接编辑模式下识别到续写/追加。", notes=notes)
        if signals.wants_simplify:
            return _decision("simplify_target", reason="直接编辑模式下识别到简化/缩短。", notes=notes)
        if signals.wants_expand:
            return _decision("expand_target", reason="直接编辑模式下识别到扩写/补充。", notes=notes)
        return _decision("rewrite_target", reason="直接编辑模式下默认按目标改写处理。", notes=notes)

    if not has_selection and has_explicit_resource_reference:
        return _decision(
            None,
            reason="用户明确引用资料且没有板书选区，交给资料引用链路。",
            requires_resource_resolution=True,
            notes=[*notes, "explicit_resource_reference"],
        )

    force_explain = turn_intent.should_force_explain_task(message)
    if not document_empty and force_explain:
        return _decision("explain_target", reason="已有板书中识别到强讲解请求。", notes=[*notes, "strong_explain"])

    if signals.wants_append and not document_empty:
        return _decision("append_section", reason="已有板书中识别到续写/追加请求。", notes=[*notes, "append"])

    if not document_empty and signals.wants_simplify:
        return _decision("simplify_target", reason="已有板书中识别到简化/缩短请求。", notes=[*notes, "simplify"])

    if not document_empty and signals.wants_expand:
        return _decision("expand_target", reason="已有板书中识别到扩写/补充请求。", notes=[*notes, "expand"])

    if not document_empty and signals.wants_rewrite:
        if signals.wants_simplify:
            return _decision("simplify_target", reason="改写请求中包含简化/缩短信号。", notes=[*notes, "rewrite", "simplify"])
        if signals.wants_expand:
            return _decision("expand_target", reason="改写请求中包含扩写/补充信号。", notes=[*notes, "rewrite", "expand"])
        return _decision("rewrite_target", reason="已有板书中识别到改写/润色请求。", notes=[*notes, "rewrite"])

    if has_selection and not document_empty:
        if signals.wants_simplify:
            return _decision("simplify_target", reason="板书选区请求中包含简化/缩短信号。", notes=[*notes, "selection", "simplify"])
        if signals.wants_expand:
            return _decision("expand_target", reason="板书选区请求中包含扩写/补充信号。", notes=[*notes, "selection", "expand"])

    if force_explain and not document_empty and (has_selection or signals.has_target_hint):
        return _decision("explain_target", reason="讲解请求带有选区或目标位置提示。", notes=[*notes, "targeted_explain"])

    if not has_selection and signals.wants_resource:
        return _decision(
            None,
            reason="请求包含资料/章节引用信号且没有板书选区，暂不进入板书任务动作。",
            requires_resource_resolution=True,
            notes=[*notes, "resource_reference"],
        )

    if has_selection and not document_empty:
        return _decision("explain_target", reason="已有板书选区默认进入讲解目标。", notes=[*notes, "selection_default"])

    return _decision(
        None,
        reason="没有足够信号进入已有板书任务动作。",
        notes=[*notes, "no_board_task_action"],
    )


def _decision(
    board_action: BoardTaskAction | None,
    *,
    reason: str,
    notes: list[str],
    requires_resource_resolution: bool = False,
) -> BoardTaskActionDecision:
    return BoardTaskActionDecision(
        board_action=board_action,
        reason=reason,
        write_allowed=board_action in DOCUMENT_WRITE_ACTIONS,
        requires_resource_resolution=requires_resource_resolution,
        requires_target_resolution=board_action in TARGETED_ACTIONS,
        decision_notes=notes,
    )
