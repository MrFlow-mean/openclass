from __future__ import annotations

from typing import Any

import pytest

from app.services import turn_intent
from app.services.board_task_decider import decide_board_task_action
from app.services.chat_turn_gate import decide_chat_turn


SIGNAL_EXAMPLES: dict[str, tuple[list[str], list[str]]] = {
    "wants_write": (
        ["写一段总结", "补充一个例子", "生成一份讲义"],
        ["解释这一段", "把这里改短一点", "根据上传资料回答"],
    ),
    "wants_edit": (
        ["修改这一段", "润色这段", "把这里改短一点"],
        ["继续写下一节", "解释这里", "我们练习一下"],
    ),
    "wants_explain": (
        ["解释这里是什么意思", "讲解第 2 题", "帮我理解这段"],
        ["继续写下一节", "润色这一段", "根据资料生成讲义"],
    ),
    "wants_append": (
        ["继续写下一节", "追加一个小节", "接着写一段"],
        ["扩写这一段", "润色这一段", "讲解第 2 题"],
    ),
    "wants_expand": (
        ["扩写这一段", "补充一个例子", "添加说明"],
        ["继续写下一节", "把这里改短一点", "解释这里"],
    ),
    "wants_simplify": (
        ["把这里改短一点", "简化这一段", "控制在 100 字以内"],
        ["扩写这一段", "继续写下一节", "润色这一段"],
    ),
    "wants_rewrite": (
        ["润色这一段", "换个说法", "提高一点难度"],
        ["扩写这一段", "继续写下一节", "解释第 2 题"],
    ),
    "wants_resource": (
        ["根据上传资料回答", "参考文档解释", "来自 PDF 的内容是什么"],
        ["解释这里", "继续写下一节", "我想学习这个主题"],
    ),
    "wants_sequence": (
        ["讲解所有小节", "逐个解释", "按顺序讲所有题"],
        ["讲解第 2 题", "把练习题补充到板书里", "解释这一段"],
    ),
    "wants_collection": (
        ["为我讲解练习题", "解释这些问题", "讲解 exercises"],
        ["讲解第 2 题", "把这里改短一点", "讲解练习题 2"],
    ),
    "wants_whole_document": (
        ["总结全文", "解释整篇", "优化整个文档"],
        ["解释这里", "改短这一段", "讲解第 2 题"],
    ),
    "has_single_target": (
        ["讲解第 2 题", "解释这段", "练习题 3"],
        ["为我讲解练习题", "讲解所有小节", "总结全文"],
    ),
    "has_target_hint": (
        ["解释这里", "讲解第 2 节", "改写这一段"],
        ["继续写下一节", "为我讲解练习题", "根据资料回答"],
    ),
}


@pytest.mark.parametrize("signal_name", SIGNAL_EXAMPLES)
def test_intent_signal_positive_and_negative_examples(signal_name: str) -> None:
    positives, negatives = SIGNAL_EXAMPLES[signal_name]

    assert len(positives) >= 3
    assert len(negatives) >= 3
    for message in positives:
        assert getattr(turn_intent.extract_intent_signals(message), signal_name), message
    for message in negatives:
        assert not getattr(turn_intent.extract_intent_signals(message), signal_name), message


def test_public_helpers_read_the_same_signals() -> None:
    assert turn_intent.wants_append("继续写下一节")
    assert turn_intent.wants_explain("解释这里是什么意思")
    assert turn_intent.wants_resource_reference("根据上传资料回答")
    assert turn_intent.wants_sequential_explanation("讲解所有小节")
    assert turn_intent.wants_collection_explanation("为我讲解练习题")
    assert turn_intent.wants_whole_document_scope("总结全文")


def test_single_numbered_question_is_not_collection_sequence() -> None:
    signals = turn_intent.extract_intent_signals("讲解第 2 题")

    assert signals.wants_explain
    assert signals.has_single_target
    assert not signals.wants_collection
    assert not signals.wants_sequence
    assert not turn_intent.wants_collection_explanation("讲解第 2 题")


def test_exercise_collection_explain_is_collection_candidate() -> None:
    signals = turn_intent.extract_intent_signals("为我讲解练习题")

    assert signals.wants_explain
    assert signals.wants_collection
    assert not signals.has_single_target
    assert turn_intent.wants_collection_explanation("为我讲解练习题")


def test_exercise_write_request_is_not_explain_sequence() -> None:
    signals = turn_intent.extract_intent_signals("把练习题补充到板书里")

    assert signals.wants_write
    assert not signals.wants_explain
    assert not signals.wants_sequence


def test_raw_matches_record_which_rule_fired() -> None:
    signals = turn_intent.extract_intent_signals("根据上传资料解释这一段")

    assert signals.raw_matches["resource"]
    assert signals.raw_matches["explain"]
    assert signals.raw_matches["single_target"]


GOLDEN_TURN_INTENT_CASES: list[dict[str, Any]] = [
    {
        "id": "ordinary_chat_stays_ordinary",
        "message": "哈哈，先聊两句",
        "document_empty": True,
        "expected_route": "ordinary_chat",
        "expected_board_action": None,
        "expected_true": [],
        "expected_false": ["wants_learning_start", "wants_document_artifact", "wants_explain"],
        "why": "no learning, writing, resource, or board-task signal",
    },
    {
        "id": "initial_learning_request_updates_requirements",
        "message": "我想学习这个概念",
        "document_empty": True,
        "expected_route": "initial_learning",
        "expected_board_action": None,
        "expected_true": ["wants_learning_start"],
        "expected_false": ["wants_document_artifact", "wants_resource"],
        "why": "blank-board learning-start signal enters the initial learning path",
    },
    {
        "id": "explicit_initial_board_generation",
        "message": "帮我生成板书",
        "document_empty": True,
        "expected_route": "initial_board_generation",
        "expected_board_action": None,
        "expected_true": ["wants_write", "wants_document_artifact"],
        "expected_false": ["wants_learning_start", "wants_explicit_resource"],
        "why": "explicit board artifact generation bypasses ordinary chat on a blank board",
    },
    {
        "id": "existing_board_explain_target",
        "message": "解释这里是什么意思",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "explain_target",
        "expected_true": ["wants_explain", "has_single_target", "has_target_hint"],
        "expected_false": ["wants_write", "wants_learning_start"],
        "why": "existing-board explanation requests must stay in the board task chain",
    },
    {
        "id": "existing_board_rewrite_target",
        "message": "润色这一段",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "rewrite_target",
        "expected_true": ["wants_edit", "wants_rewrite", "has_target_hint"],
        "expected_false": ["wants_learning_start", "wants_document_artifact"],
        "why": "existing-board rewrite wording is an edit task, not an initial learning turn",
    },
    {
        "id": "existing_board_simplify_target",
        "message": "把这一段改短一点",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "simplify_target",
        "expected_true": ["wants_edit", "wants_simplify", "has_single_target", "has_target_hint"],
        "expected_false": ["wants_learning_start", "wants_document_artifact"],
        "why": "existing-board simplify wording should resolve to the simplify action",
    },
    {
        "id": "existing_board_expand_target",
        "message": "扩写这一段",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "expand_target",
        "expected_true": ["wants_write", "wants_expand", "has_single_target", "has_target_hint"],
        "expected_false": ["wants_learning_start", "wants_document_artifact"],
        "why": "existing-board expansion is a targeted board task",
    },
    {
        "id": "resource_reference_request",
        "message": "根据上传资料解释这一段",
        "document_empty": False,
        "expected_route": "resource_reference",
        "expected_board_action": None,
        "expected_true": ["wants_resource", "wants_explicit_resource", "wants_explain"],
        "expected_false": ["wants_learning_start", "wants_document_artifact"],
        "why": "explicit uploaded-resource wording routes through resource resolution",
    },
    {
        "id": "continue_teaching_phrase_is_currently_ordinary_chat",
        "message": "继续讲",
        "document_empty": False,
        "expected_route": "ordinary_chat",
        "expected_board_action": None,
        "expected_true": [],
        "expected_false": ["wants_explain", "wants_sequence", "wants_append", "wants_learning_start"],
        "why": "current rules do not treat bare continuation wording as a board task signal",
    },
    {
        "id": "ambiguous_short_ack_does_not_generate_board",
        "message": "行，可以",
        "document_empty": True,
        "expected_route": "ordinary_chat",
        "expected_board_action": None,
        "expected_true": [],
        "expected_false": ["wants_write", "wants_document_artifact", "wants_learning_start"],
        "why": "short acknowledgement has no standalone learning or generation signal",
    },
    {
        "id": "explain_paraphrase_keeps_existing_board_task",
        "message": "能不能说明这部分为什么成立",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "explain_target",
        "expected_true": ["wants_explain", "has_single_target", "has_target_hint"],
        "expected_false": ["wants_write", "wants_document_artifact"],
        "why": "paraphrased explanation requests still enter the existing-board explain path",
    },
    {
        "id": "mixed_casual_message_remains_ordinary_chat",
        "message": "今天状态一般，先聊聊",
        "document_empty": True,
        "expected_route": "ordinary_chat",
        "expected_board_action": None,
        "expected_true": [],
        "expected_false": ["wants_write", "wants_explain", "wants_learning_start"],
        "why": "casual wording should not accidentally trigger learning requirement collection",
    },
    {
        "id": "length_limit_number_is_not_a_numbered_target",
        "message": "控制在 100 字以内",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "simplify_target",
        "expected_true": ["wants_edit", "wants_simplify"],
        "expected_false": ["has_single_target", "wants_learning_start", "wants_document_artifact"],
        "why": "quantity limits are edit constraints, not references to numbered board targets",
    },
    {
        "id": "existing_board_append_is_not_initial_learning",
        "message": "继续写下一节",
        "document_empty": False,
        "expected_route": "existing_board_task",
        "expected_board_action": "append_section",
        "expected_true": ["wants_write", "wants_append"],
        "expected_false": ["wants_learning_start", "wants_document_artifact"],
        "why": "existing-board continuation writing stays in the board task path",
    },
]


def _golden_decision_for(case: dict[str, Any]):
    signals = turn_intent.extract_intent_signals(case["message"])
    board_action_decision = decide_board_task_action(
        message=case["message"],
        signals=signals,
        has_selection=case.get("has_selection", False),
        document_empty=case["document_empty"],
        interaction_mode=case.get("interaction_mode", "ask"),
        board_generation_action=case.get("board_generation_action"),
        has_explicit_resource_reference=signals.wants_explicit_resource,
    )
    turn_decision = decide_chat_turn(
        message=case["message"],
        document_empty=case["document_empty"],
        has_selection=case.get("has_selection", False),
        interaction_mode=case.get("interaction_mode", "ask"),
        board_generation_action=case.get("board_generation_action"),
        teaching_action=None,
        resource_reference_action=None,
        board_action_decision=board_action_decision,
    )
    return signals, board_action_decision, turn_decision


@pytest.mark.parametrize("case", GOLDEN_TURN_INTENT_CASES, ids=lambda case: case["id"])
def test_chat_turn_intent_golden_routes(case: dict[str, Any]) -> None:
    signals, board_action_decision, turn_decision = _golden_decision_for(case)

    assert case["why"]
    assert turn_decision.route == case["expected_route"]
    assert board_action_decision.board_action == case["expected_board_action"]
    for flag in case["expected_true"]:
        assert getattr(signals, flag) is True, flag
    for flag in case["expected_false"]:
        assert getattr(signals, flag) is False, flag
