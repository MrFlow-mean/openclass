from __future__ import annotations

import re

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    InteractionRuleDraft,
    InteractionRuleStep,
    InteractionSession,
)
from app.services.board_task_history import BoardTaskHistoryStamp


def compile_interaction_session(
    *,
    board_task: BoardTaskRequirementSheet,
    focus: BoardFocusRef,
    target_excerpt: str,
    board_task_stamp: BoardTaskHistoryStamp,
) -> InteractionSession:
    draft = board_task.interaction_rule_draft or InteractionRuleDraft()
    rule_text = _first_text(draft.rule_text, board_task.question_or_topic, "按用户指定规则围绕目标板书互动。")
    interaction_goal = _first_text(draft.interaction_goal, board_task.question_or_topic, board_task.target_hint)
    expected_user_behavior = _first_text(draft.expected_user_behavior, _infer_expected_user_behavior(rule_text))
    assistant_behavior = _first_text(draft.assistant_behavior, _infer_assistant_behavior(rule_text))
    rule_steps = _compile_rule_steps(
        target_excerpt=target_excerpt,
        rule_text=rule_text,
        expected_user_behavior=expected_user_behavior,
        assistant_behavior=assistant_behavior,
    )
    compliant_input_rule = _compliant_input_rule(
        draft=draft,
        expected_user_behavior=expected_user_behavior,
        rule_steps=rule_steps,
    )
    return InteractionSession(
        rule_text=rule_text,
        interaction_goal=interaction_goal,
        target_focus=focus,
        reference_context=target_excerpt.strip(),
        compliant_input_rule=compliant_input_rule,
        expected_user_behavior=expected_user_behavior,
        assistant_behavior=assistant_behavior,
        progress_note=_initial_progress_note(rule_steps),
        turn_count=0,
        source_board_task_run_id=board_task_stamp.run_id,
        source_board_task_version_id=board_task_stamp.version_id,
        source_board_task_route="chat",
        rule_steps=rule_steps,
        current_step_index=0,
        last_violation_reason="",
    )


def _compile_rule_steps(
    *,
    target_excerpt: str,
    rule_text: str,
    expected_user_behavior: str,
    assistant_behavior: str,
) -> list[InteractionRuleStep]:
    dialogue_lines = _extract_dialogue_lines(target_excerpt)
    if len(dialogue_lines) < 2:
        return []
    user_role, assistant_role = _extract_role_pair(
        "\n".join([rule_text, expected_user_behavior, assistant_behavior])
    )
    if user_role and assistant_role:
        return _pair_role_lines(
            dialogue_lines,
            user_role=user_role,
            assistant_role=assistant_role,
        )
    if _looks_like_turn_taking(rule_text):
        return _pair_adjacent_lines(dialogue_lines)
    return []


def _extract_dialogue_lines(text: str) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(
            r"^\s*(?:[-*+]\s*)?(?:\d+[\).、]\s*)?(?:\*\*)?(?P<speaker>[^：:\n]{1,40})(?:\*\*)?\s*[：:]\s*(?P<text>.+?)\s*$",
            line,
        )
        if not match:
            continue
        speaker = _clean_role(match.group("speaker"))
        content = match.group("text").strip()
        if speaker and content:
            lines.append((speaker, content, raw_line.strip()))
    return lines


def _extract_role_pair(text: str) -> tuple[str, str]:
    compact = " ".join((text or "").split())
    patterns = [
        r"(?:^|[\s，,。；;])我(?:来)?(?:扮演|当|是|读|负责)(?P<user>[^，,。；;:\n]{1,32}?)[\s，,。；;]*(?:你|AI|助手)(?:来)?(?:扮演|当|是|读|负责)(?P<assistant>[^，,。；;:\n]{1,32})",
        r"(?:用户|学习者)(?:来)?(?:扮演|当|是|读|负责|输入)(?P<user>[^，,。；;:\n]{1,32}?)[\s，,。；;]*(?:AI|助手|系统)(?:来)?(?:扮演|当|是|读|负责|回应)(?P<assistant>[^，,。；;:\n]{1,32})",
        r"\bI\s+(?:am|play|read as|will be)\s+(?P<user>[^,.;:\n]{1,32})[,.;\s]+(?:you|assistant|AI)\s+(?:are|play|read as|will be)\s+(?P<assistant>[^,.;:\n]{1,32})",
        r"\buser\s+(?:plays|reads as|is)\s+(?P<user>[^,.;:\n]{1,32})[,.;\s]+(?:assistant|AI)\s+(?:plays|reads as|is)\s+(?P<assistant>[^,.;:\n]{1,32})",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        user_role = _clean_role(match.group("user"))
        assistant_role = _clean_role(match.group("assistant"))
        if user_role and assistant_role and user_role != assistant_role:
            return user_role, assistant_role
    return "", ""


def _pair_role_lines(
    dialogue_lines: list[tuple[str, str, str]],
    *,
    user_role: str,
    assistant_role: str,
) -> list[InteractionRuleStep]:
    steps: list[InteractionRuleStep] = []
    for index, (speaker, content, raw_line) in enumerate(dialogue_lines):
        if not _role_matches(speaker, user_role):
            continue
        assistant_response = ""
        source_lines = [raw_line]
        for next_speaker, next_content, next_raw in dialogue_lines[index + 1 :]:
            if _role_matches(next_speaker, assistant_role):
                assistant_response = next_content
                source_lines.append(next_raw)
                break
            if _role_matches(next_speaker, user_role):
                break
        steps.append(
            InteractionRuleStep(
                order_index=len(steps),
                expected_user_input=content,
                assistant_response=assistant_response,
                source_excerpt="\n".join(source_lines),
            )
        )
    return steps


def _pair_adjacent_lines(dialogue_lines: list[tuple[str, str, str]]) -> list[InteractionRuleStep]:
    steps: list[InteractionRuleStep] = []
    for index in range(0, len(dialogue_lines) - 1, 2):
        _, user_content, user_raw = dialogue_lines[index]
        _, assistant_content, assistant_raw = dialogue_lines[index + 1]
        steps.append(
            InteractionRuleStep(
                order_index=len(steps),
                expected_user_input=user_content,
                assistant_response=assistant_content,
                source_excerpt=f"{user_raw}\n{assistant_raw}",
            )
        )
    return steps


def _role_matches(speaker: str, role: str) -> bool:
    left = _normalize_role_text(speaker)
    right = _normalize_role_text(role)
    return bool(left and right and (left == right or left in right or right in left))


def _looks_like_turn_taking(rule_text: str) -> bool:
    return bool(re.search(r"(轮流|依次|下一句|接下一句|turn[-\s]?taking|alternate|role[-\s]?play|read aloud)", rule_text or "", re.IGNORECASE))


def _compliant_input_rule(
    *,
    draft: InteractionRuleDraft,
    expected_user_behavior: str,
    rule_steps: list[InteractionRuleStep],
) -> str:
    if draft.reference_instruction.strip():
        base = draft.reference_instruction.strip()
    elif expected_user_behavior.strip():
        base = expected_user_behavior.strip()
    else:
        base = "用户输入应符合当前互动规则。"
    if rule_steps:
        next_input = rule_steps[0].expected_user_input.strip()
        if next_input:
            return f"{base} 当前步骤期待用户输入：{next_input}"
    return base


def _infer_expected_user_behavior(rule_text: str) -> str:
    return "用户按互动规则输入当前轮内容。"


def _infer_assistant_behavior(rule_text: str) -> str:
    return "AI 按互动规则回应，并在输入不合规时先做规则内纠错。"


def _initial_progress_note(rule_steps: list[InteractionRuleStep]) -> str:
    if rule_steps:
        return f"规则互动已启动，等待用户完成第 1 / {len(rule_steps)} 个规则步骤。"
    return "规则互动已启动，等待用户按规则输入。"


def _clean_role(value: str) -> str:
    value = re.sub(r"[*_`#>\[\]（）()]", "", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ：:，,。；;.-")
    return value[:40]


def _normalize_role_text(value: str) -> str:
    return re.sub(r"[\s：:，,。；;.!?？！（）()_\-*`'\"“”‘’]+", "", value or "").casefold()


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""
