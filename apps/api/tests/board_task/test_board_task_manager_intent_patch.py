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
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import build_document


def _lesson():
    lesson = create_empty_lesson("测试主题")
    lesson.board_document = build_document(title="已有板书", content_text="# 已有板书\n\n这一段说明当前概念。")
    return lesson


def test_update_board_task_from_intent_does_not_call_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: pytest.fail("update_board_task_from_intent must not call AI"),
    )

    sheet = update_board_task_from_intent(
        patch=BoardTaskIntentPatch(
            requested_action="write",
            target_hint="核心概念",
            question_or_topic="补充一个例子",
            source="fallback",
        )
    )

    assert sheet.requested_action == "write"
    assert sheet.target_hint == "核心概念"
    assert sheet.question_or_topic == "补充一个例子"
    assert sheet.progress == 100


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [
        ("继续写下一节", "write"),
        ("润色这一段", "edit"),
        ("解释这里是什么意思", "explain"),
        ("我们按规则你问我答练习", "chat"),
    ],
)
def test_update_board_task_from_chat_fallback_covers_actions(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_action: str,
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    sheet = update_board_task_from_chat(
        lesson=_lesson(),
        resources=[],
        conversation=[],
        user_message=message,
        selection=None,
        selection_excerpt=None,
        existing=None,
    )

    assert sheet.requested_action == expected_action
    if expected_action == "chat":
        assert sheet.interaction_rule_draft is not None
        assert sheet.interaction_rule_draft.should_start


def test_selection_excerpt_has_priority_for_target_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="AI 选择的目标",
            requested_action="explain",
            question_or_topic="解释含义",
        ),
    )
    selection = SelectionRef(kind="board", excerpt="用户选中的板书片段")

    sheet = update_board_task_from_chat(
        lesson=_lesson(),
        resources=[],
        conversation=[],
        user_message="解释这里是什么意思",
        selection=selection,
        selection_excerpt=selection.excerpt,
        existing=None,
    )

    assert sheet.target_hint == "用户选中的板书片段"
    assert sheet.location_status == "selected"


def test_missing_progress_and_clarification_match_normalize_behavior() -> None:
    expected = normalize_board_task_sheet(BoardTaskRequirementSheet(requested_action="edit"))

    actual = update_board_task_from_intent(
        patch=BoardTaskIntentPatch(requested_action="edit", source="fallback")
    )

    assert actual.missing_items == expected.missing_items
    assert actual.progress == expected.progress
    assert actual.clarification_question == expected.clarification_question


def test_existing_sheet_is_preserved_when_patch_is_partial() -> None:
    existing = BoardTaskRequirementSheet(
        target_hint="核心概念",
        requested_action="explain",
        question_or_topic="为什么这样定义",
    )

    sheet = update_board_task_from_intent(
        existing=existing,
        patch=BoardTaskIntentPatch(question_or_topic="新问题不会覆盖旧问题", source="existing"),
    )

    assert sheet.target_hint == "核心概念"
    assert sheet.requested_action == "explain"
    assert sheet.question_or_topic == "为什么这样定义"


def test_write_confirmation_and_decline_helpers_still_work() -> None:
    assert is_write_confirmation("好的")
    assert is_write_confirmation("继续")
    assert is_write_decline("不用")
    assert is_write_decline("别写")
    assert not is_write_confirmation("不用")
    assert not is_write_decline("好的")
