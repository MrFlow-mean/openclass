from __future__ import annotations

import pytest

from app.services import turn_intent


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
