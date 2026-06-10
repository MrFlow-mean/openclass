from __future__ import annotations

import pytest

from app.models import BoardTaskRequirementSheet, SelectionRef
from app.services.board_task_manager import (
    BoardTaskIntentPatch,
    is_write_confirmation,
    is_write_decline,
    normalize_board_task_sheet,
    update_board_task_from_chat,
    update_board_task_from_intent,
)
from app.services.lesson_factory import create_lesson
from app.services.openai_course_ai import openai_course_ai


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="第二节：一次函数的图像和性质",
        heading_path=["一次函数", "第二节"],
    )


def test_update_board_task_from_intent_does_not_call_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")),
    )

    sheet = update_board_task_from_intent(
        patch=BoardTaskIntentPatch(
            requested_action="write",
            question_or_topic="补充一个例子",
            source="fallback",
        )
    )

    assert sheet.requested_action == "write"
    assert sheet.question_or_topic == "补充一个例子"
    assert sheet.progress == 100
    assert sheet.missing_items == []


def test_update_board_task_from_chat_falls_back_when_ai_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    lesson = create_lesson("一次函数")
    sheet = update_board_task_from_chat(
        lesson=lesson,
        resources=[],
        conversation=[],
        user_message="补充一个例子",
        selection=None,
        selection_excerpt=None,
    )

    assert sheet.requested_action == "write"
    assert sheet.question_or_topic == "一个例子"
    assert sheet.progress == 100


def test_selection_excerpt_still_wins_over_patch_target_hint() -> None:
    selection = _selection()

    sheet = update_board_task_from_intent(
        patch=BoardTaskIntentPatch(
            requested_action="explain",
            target_hint="AI 返回的位置",
            question_or_topic="讲解选区",
            source="fallback",
        ),
        selection=selection,
        selection_excerpt=selection.excerpt,
    )

    assert sheet.target_hint == selection.excerpt
    assert sheet.location_status == "selected"


def test_missing_items_progress_and_clarification_match_normalize_behavior() -> None:
    expected = normalize_board_task_sheet(BoardTaskRequirementSheet(requested_action="explain"))
    actual = update_board_task_from_intent(
        patch=BoardTaskIntentPatch(
            requested_action="explain",
            source="fallback",
        )
    )

    assert actual.missing_items == expected.missing_items
    assert actual.progress == expected.progress
    assert actual.clarification_question == expected.clarification_question


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("补充一个例子", "write"),
        ("把这段改简单点", "edit"),
        ("讲解这一段", "explain"),
        ("你问我答练习一下", "chat"),
    ],
)
def test_update_board_task_from_chat_fallback_covers_all_requested_actions(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_action: str,
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    lesson = create_lesson("一次函数")
    sheet = update_board_task_from_chat(
        lesson=lesson,
        resources=[],
        conversation=[],
        user_message=message,
        selection=None,
        selection_excerpt=None,
    )

    assert sheet.requested_action == expected_action
    if expected_action == "chat":
        assert sheet.interaction_rule_draft is not None
        assert sheet.interaction_rule_draft.should_start is True


def test_update_board_task_from_chat_uses_ai_sheet_as_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_ai_sheet(**kwargs):
        return BoardTaskRequirementSheet(
            target_hint="AI 定位",
            requested_action="explain",
            question_or_topic="AI 问题",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", _fake_ai_sheet)

    lesson = create_lesson("一次函数")
    sheet = update_board_task_from_chat(
        lesson=lesson,
        resources=[],
        conversation=[],
        user_message="随便问一句",
        selection=None,
        selection_excerpt=None,
    )

    assert sheet.target_hint == "AI 定位"
    assert sheet.requested_action == "explain"
    assert sheet.question_or_topic == "AI 问题"
    assert sheet.progress == 100


def test_write_confirmation_helpers_remain_available() -> None:
    assert is_write_confirmation("确认") is True
    assert is_write_confirmation("继续") is True
    assert is_write_confirmation("先聊聊") is False
    assert is_write_decline("不用写") is True
    assert is_write_decline("取消") is True
    assert is_write_decline("好的") is False
