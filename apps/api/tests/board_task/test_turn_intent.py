from __future__ import annotations

import pytest

from app.services.board_task_manager import _infer_action
from app.services.chat.intent import (
    _requests_append_section,
    _requests_explanation,
    _requests_resource_backed_answer,
)
from app.services.chat.sequence import _requests_sequential_explanation
from app.services.turn_intent import (
    extract_intent_signals,
    infer_board_task_requested_action,
    wants_append,
    wants_collection_explanation,
    wants_explain,
    wants_resource_reference,
    wants_sequential_explanation,
    wants_whole_document_scope,
)


SIGNAL_CASES = {
    "wants_write": {
        "positive": ["补充一个例子", "生成练习题", "把练习题补充到板书里"],
        "negative": ["讲解这一段", "把这段改简单点", "你问我答"],
    },
    "wants_edit": {
        "positive": ["润色这一段", "把这段改简单点", "控制在三句以内"],
        "negative": ["补充一个例子", "讲解这一段", "你问我答"],
    },
    "wants_explain": {
        "positive": ["讲解这一段", "解释第 2 节", "为什么会这样"],
        "negative": ["补充一个例子", "把这段改简单点", "你问我答"],
    },
    "wants_append": {
        "positive": ["继续写下一节", "请新增一段练习", "往后写一段总结"],
        "negative": ["补充一个例子", "讲解这一段", "把这段改简单点"],
    },
    "wants_expand": {
        "positive": ["扩写这一段", "补充一个例子", "添加一个案例"],
        "negative": ["简化这一段", "讲解这一段", "改写这一段"],
    },
    "wants_simplify": {
        "positive": ["简化这一段", "改短一点", "精简这段"],
        "negative": ["扩写这一段", "润色这一段", "讲解这一段"],
    },
    "wants_rewrite": {
        "positive": ["改写这一段", "润色这一段", "换个说法"],
        "negative": ["扩写这一段", "讲解这一段", "继续写下一节"],
    },
    "wants_resource": {
        "positive": ["根据资料解释这段", "参考上传 PDF 回答", "来自教材原文的说法"],
        "negative": ["讲解这一段", "把这段改简单点", "继续写下一节"],
    },
    "wants_sequence": {
        "positive": ["讲解所有小节", "逐个讲这些问题", "按顺序解释每道题"],
        "negative": ["讲解第 2 题", "为我讲解练习题", "把练习题补充到板书里"],
    },
    "wants_collection": {
        "positive": ["为我讲解练习题", "逐个讲这些问题", "把练习题补充到板书里"],
        "negative": ["讲解第 2 题", "解释这一段", "生成板书"],
    },
    "wants_whole_document": {
        "positive": ["讲解全文", "整篇都说明一下", "整个文档帮我总结"],
        "negative": ["讲解第 2 题", "为我讲解练习题", "把这段改简单点"],
    },
    "has_single_target": {
        "positive": ["讲解第 2 题", "解释这一段", "看选中部分"],
        "negative": ["为我讲解练习题", "讲解所有小节", "生成板书"],
    },
    "has_target_hint": {
        "positive": ["解释第 2 节", "讲解这一段", "说明定义部分"],
        "negative": ["普通聊一下", "请新增一段练习", "生成板书"],
    },
}


@pytest.mark.parametrize("signal_name", SIGNAL_CASES.keys())
def test_extract_intent_signals_has_positive_and_negative_examples(signal_name: str) -> None:
    cases = SIGNAL_CASES[signal_name]
    for message in cases["positive"]:
        signals = extract_intent_signals(message)
        assert getattr(signals, signal_name) is True, message
        raw_key = signal_name.replace("wants_", "").replace("has_", "")
        assert any(signals.raw_matches.values())
        if raw_key in signals.raw_matches:
            assert signals.raw_matches[raw_key], message

    for message in cases["negative"]:
        assert getattr(extract_intent_signals(message), signal_name) is False, message


def test_question_2_is_single_explain_not_collection_sequence() -> None:
    signals = extract_intent_signals("讲解第 2 题")

    assert signals.wants_explain is True
    assert signals.has_single_target is True
    assert signals.wants_collection is False
    assert wants_collection_explanation("讲解第 2 题") is False
    assert wants_sequential_explanation("讲解第 2 题") is False


def test_explain_exercises_is_collection_explanation() -> None:
    signals = extract_intent_signals("为我讲解练习题")

    assert signals.wants_explain is True
    assert signals.wants_collection is True
    assert signals.has_single_target is False
    assert wants_collection_explanation("为我讲解练习题") is True


def test_supplement_exercises_is_write_not_explain_sequence() -> None:
    signals = extract_intent_signals("把练习题补充到板书里")

    assert signals.wants_write is True
    assert infer_board_task_requested_action("把练习题补充到板书里") == "write"
    assert signals.wants_append is False
    assert signals.wants_explain is False
    assert signals.wants_sequence is False
    assert wants_collection_explanation("把练习题补充到板书里") is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("补充一个例子", "write"),
        ("讲解这一段", "explain"),
        ("把这段改简单点", "edit"),
        ("你问我答练习一下", "chat"),
        ("普通聊一下学习安排", None),
    ],
)
def test_board_task_manager_action_wrapper_keeps_existing_result(message: str, expected: str | None) -> None:
    assert infer_board_task_requested_action(message) == expected
    assert _infer_action(message) == expected


@pytest.mark.parametrize(
    "message",
    ["讲解这一段", "解释第 2 节", "为什么会这样"],
)
def test_legacy_explain_helper_delegates_to_turn_intent(message: str) -> None:
    assert _requests_explanation(message) is wants_explain(message)


@pytest.mark.parametrize(
    "message",
    ["继续写下一节", "请新增一段练习", "往后写一段总结"],
)
def test_legacy_append_helper_delegates_to_turn_intent(message: str) -> None:
    assert _requests_append_section(message) is wants_append(message)


@pytest.mark.parametrize(
    "message",
    ["根据资料解释这段", "参考上传 PDF 回答", "来自教材原文的说法"],
)
def test_legacy_resource_helper_delegates_to_turn_intent(message: str) -> None:
    assert _requests_resource_backed_answer(message) is wants_resource_reference(message)


@pytest.mark.parametrize(
    "message",
    ["讲解所有小节", "逐个讲这些问题", "按顺序解释每道题"],
)
def test_legacy_sequence_helper_delegates_to_turn_intent(message: str) -> None:
    assert _requests_sequential_explanation(message) is wants_sequential_explanation(message)


@pytest.mark.parametrize(
    "message",
    ["讲解全文", "整篇都说明一下", "整个文档帮我总结"],
)
def test_public_whole_document_helper(message: str) -> None:
    assert wants_whole_document_scope(message) is True
