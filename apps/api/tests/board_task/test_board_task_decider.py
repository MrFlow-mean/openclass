from __future__ import annotations

import pytest

from app.models import ChatRequest
from app.services.board_task_decider import (
    BoardTaskActionDecision,
    decide_board_task_action,
    decide_board_task_requested_action,
)
from app.services.chat.intent import _infer_board_task_action
from app.services.turn_intent import extract_intent_signals, has_explicit_resource_reference


def _decide(
    message: str,
    *,
    has_selection: bool = False,
    document_empty: bool = False,
    interaction_mode: str = "ask",
    board_generation_action: str | None = None,
) -> BoardTaskActionDecision:
    return decide_board_task_action(
        message=message,
        signals=extract_intent_signals(message),
        has_selection=has_selection,
        document_empty=document_empty,
        interaction_mode=interaction_mode,
        board_generation_action=board_generation_action,
        has_explicit_resource_reference=has_explicit_resource_reference(message),
    )


def test_direct_edit_append_takes_priority() -> None:
    decision = _decide("继续写下一节", interaction_mode="direct_edit")

    assert decision.board_action == "append_section"
    assert decision.write_allowed is True
    assert decision.requires_target_resolution is False
    assert "direct_edit_priority" in decision.decision_notes


def test_explicit_resource_reference_without_selection_waits_for_resource_resolution() -> None:
    decision = _decide("根据上传资料回答这个问题", has_selection=False, document_empty=False)

    assert decision.board_action is None
    assert decision.write_allowed is False
    assert decision.requires_resource_resolution is True
    assert decision.requires_target_resolution is False


def test_existing_board_explain_is_preferred_before_edit_or_selection_fallback() -> None:
    decision = _decide("解释这里是什么意思", has_selection=False, document_empty=False)

    assert decision.board_action == "explain_target"
    assert decision.write_allowed is False
    assert decision.requires_target_resolution is True
    assert "force_explain" in decision.decision_notes


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("继续写下一节", "append_section"),
        ("把这里改短一点", "simplify_target"),
        ("扩写这一段", "expand_target"),
        ("润色这一段", "rewrite_target"),
    ],
)
def test_existing_board_write_and_edit_actions(message: str, expected: str) -> None:
    decision = _decide(message, has_selection=False, document_empty=False)

    assert decision.board_action == expected
    assert decision.write_allowed is True


def test_selection_defaults_to_explain_on_existing_board() -> None:
    decision = _decide("普通聊一下学习计划", has_selection=True, document_empty=False)

    assert decision.board_action == "explain_target"
    assert decision.write_allowed is False
    assert decision.requires_target_resolution is False
    assert "selection_default_explain" in decision.decision_notes


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("补充一个例子", "write"),
        ("把这段改简单点", "edit"),
        ("讲解这一段", "explain"),
        ("你问我答练习一下", "chat"),
    ],
)
def test_requested_action_decision_keeps_board_task_sheet_actions(message: str, expected: str) -> None:
    decision = _decide(message, has_selection=False, document_empty=False)

    assert decide_board_task_requested_action(message=message, decision=decision) == expected


def test_selection_default_explain_does_not_force_sheet_action_without_language_signal() -> None:
    decision = _decide("普通聊一下学习计划", has_selection=True, document_empty=False)

    assert decision.board_action == "explain_target"
    assert decide_board_task_requested_action(message="普通聊一下学习计划", decision=decision) is None


@pytest.mark.parametrize(
    "message",
    [
        "解释这里是什么意思",
        "润色这一段",
        "把这里改短一点",
        "扩写这一段",
    ],
)
def test_empty_document_does_not_enter_existing_board_edit_or_explain(message: str) -> None:
    decision = _decide(message, has_selection=False, document_empty=True)

    assert decision.board_action is None
    assert decision.write_allowed is False
    assert "document_empty" in decision.decision_notes


def test_board_generation_action_still_returns_generate_board_for_empty_document() -> None:
    decision = _decide("开始生成", document_empty=True, board_generation_action="start")

    assert decision.board_action == "generate_board"
    assert decision.write_allowed is True
    assert decision.requires_target_resolution is False


@pytest.mark.parametrize(
    ("chat_request", "has_selection", "document_empty"),
    [
        (ChatRequest(message="解释这里是什么意思"), False, False),
        (ChatRequest(message="继续写下一节"), False, False),
        (ChatRequest(message="扩写这一段"), False, False),
        (ChatRequest(message="把这里改短一点"), False, False),
        (ChatRequest(message="润色这一段"), False, False),
        (ChatRequest(message="根据上传资料回答这个问题"), False, False),
        (ChatRequest(message="开始生成", board_generation_action="start"), False, True),
        (ChatRequest(message="普通聊一下学习计划"), False, False),
        (ChatRequest(message="改短一点", interaction_mode="direct_edit"), False, False),
    ],
)
def test_legacy_infer_board_task_action_is_thin_wrapper(
    chat_request: ChatRequest,
    has_selection: bool,
    document_empty: bool,
) -> None:
    message = chat_request.message
    decision = _decide(
        message,
        has_selection=has_selection,
        document_empty=document_empty,
        interaction_mode=chat_request.interaction_mode,
        board_generation_action=chat_request.board_generation_action,
    )

    assert (
        _infer_board_task_action(chat_request, has_selection=has_selection, document_empty=document_empty)
        == decision.board_action
    )
