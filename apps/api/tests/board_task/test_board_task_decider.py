from __future__ import annotations

import pytest

from app.models import BoardTaskAction
from app.services import turn_intent
from app.services.board_task_decider import BoardTaskActionDecision, decide_board_task_action


def _decide(
    message: str,
    *,
    has_selection: bool = False,
    document_empty: bool = False,
    interaction_mode: str | None = "ask",
    board_generation_action: str | None = None,
    explicit_resource: bool | None = None,
) -> BoardTaskActionDecision:
    signals = turn_intent.extract_intent_signals(message)
    return decide_board_task_action(
        message=message,
        signals=signals,
        has_selection=has_selection,
        document_empty=document_empty,
        interaction_mode=interaction_mode,
        board_generation_action=board_generation_action,
        has_explicit_resource_reference=signals.wants_explicit_resource if explicit_resource is None else explicit_resource,
    )


def test_direct_edit_append_takes_priority() -> None:
    decision = _decide("继续写下一节", has_selection=True, interaction_mode="direct_edit")

    assert decision.board_action == "append_section"
    assert decision.write_allowed
    assert not decision.requires_resource_resolution
    assert "interaction_mode:direct_edit" in decision.decision_notes


def test_explicit_resource_reference_without_selection_uses_resource_path() -> None:
    decision = _decide("根据上传资料回答这个问题", has_selection=False)

    assert decision.board_action is None
    assert not decision.write_allowed
    assert decision.requires_resource_resolution
    assert not decision.requires_target_resolution


def test_existing_board_strong_explain_prefers_explain_target() -> None:
    decision = _decide("解释这里是什么意思", has_selection=False, document_empty=False)

    assert decision.board_action == "explain_target"
    assert not decision.write_allowed
    assert decision.requires_target_resolution


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("继续写下一节", "append_section"),
        ("把这里改短一点", "simplify_target"),
        ("扩写这一段", "expand_target"),
        ("润色这一段", "rewrite_target"),
    ],
)
def test_existing_board_edit_and_write_actions(message: str, expected: BoardTaskAction) -> None:
    decision = _decide(message, has_selection=True, document_empty=False)

    assert decision.board_action == expected
    assert decision.write_allowed


def test_selection_defaults_to_explain_on_existing_board() -> None:
    decision = _decide("这里", has_selection=True, document_empty=False)

    assert decision.board_action == "explain_target"
    assert not decision.write_allowed
    assert "selection_default" in decision.decision_notes


@pytest.mark.parametrize(
    "message",
    [
        "解释这里是什么意思",
        "润色这一段",
        "扩写这一段",
        "把这里改短一点",
    ],
)
def test_empty_document_does_not_enter_existing_board_edit_or_explain(message: str) -> None:
    decision = _decide(message, has_selection=False, document_empty=True)

    assert decision.board_action is None
    assert not decision.write_allowed
    assert not decision.requires_target_resolution


def test_board_generation_start_still_returns_generate_board() -> None:
    decision = _decide("开始生成板书", document_empty=True, board_generation_action="start")

    assert decision.board_action == "generate_board"
    assert decision.write_allowed
    assert not decision.requires_target_resolution
