from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardTaskRequirementSheet, ChatRequest, SelectionRef
from app.services.board_task_manager import update_board_task_from_chat
from app.services.chat.intent import _infer_board_task_action
from app.services.chat.sequence import (
    _is_sequence_continue_message,
    _is_sequence_exit_message,
    _requests_collection_explanation_sequence,
    _requests_sequential_explanation,
)
from app.services.lesson_factory import create_lesson
from app.services.openai_course_ai import openai_course_ai


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "board_task_turn_cases.yml"
REQUIRED_CASE_IDS = {
    "blank_learning_topic",
    "blank_generate_board",
    "existing_explain_selection",
    "existing_explain_section_2",
    "existing_simplify_selection",
    "existing_add_example",
    "existing_explain_all_sections",
    "existing_explain_exercises",
    "existing_explain_question_2",
    "existing_sequence_continue",
    "existing_sequence_exit",
    "existing_resource_explain_selection",
}


def _load_cases() -> list[dict[str, Any]]:
    # The file uses JSON-compatible YAML so the baseline has no test-only dependency.
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


CASES = _load_cases()
CONTRACT_GAP_CASES = [
    pytest.param(
        case,
        marks=pytest.mark.xfail(reason=case["contract_xfail"], strict=True),
        id=case["id"],
    )
    for case in CASES
    if "contract_xfail" in case
]


def _selection(case: dict[str, Any]) -> SelectionRef | None:
    if not case["request"]["has_selection"]:
        return None
    return SelectionRef(
        kind="board",
        excerpt="第二节：一次函数的图像和性质",
        heading_path=["一次函数", "第二节"],
    )


def _chat_request(case: dict[str, Any]) -> ChatRequest:
    return ChatRequest(
        message=case["user_message"],
        selection=_selection(case),
        interaction_mode=case["request"]["interaction_mode"],
        board_generation_action=case["request"]["board_generation_action"],
    )


def _board_task_sheet(case: dict[str, Any]) -> BoardTaskRequirementSheet:
    selection = _selection(case)
    lesson = create_lesson("一次函数")
    return update_board_task_from_chat(
        lesson=lesson,
        resources=[],
        conversation=[],
        user_message=case["user_message"],
        selection=selection,
        selection_excerpt=selection.excerpt if selection else None,
        existing=None,
    )


def _current_chatbot_board_action(case: dict[str, Any]) -> str | None:
    request = _chat_request(case)
    return _infer_board_task_action(
        request,
        has_selection=case["request"]["has_selection"],
        document_empty=case["board_state"] == "blank",
    )


def _current_sequence_requested(case: dict[str, Any], sheet: BoardTaskRequirementSheet | None) -> bool:
    if _requests_sequential_explanation(case["user_message"]):
        return True
    if sheet is None:
        return False
    return _requests_collection_explanation_sequence(
        board_task=sheet,
        request_message=case["user_message"],
    )


def test_board_task_turn_fixture_file_is_complete() -> None:
    ids = [case["id"] for case in CASES]
    assert set(ids) == REQUIRED_CASE_IDS
    assert len(ids) == len(set(ids))

    for case in CASES:
        assert case["title"]
        assert case["coverage"]
        assert case["board_state"] in {"blank", "existing"}
        assert case["user_message"]
        assert "contract" in case
        assert "current_baseline" in case
        assert isinstance(case["contract"]["priority"], list)
        assert "write_gate" in case["contract"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_current_turn_helper_baseline_matches_golden_fixture(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    baseline = case["current_baseline"]
    assert _current_chatbot_board_action(case) == baseline["chatbot_board_action"]

    if case["board_state"] == "blank" or case["request"]["active_sequence"]:
        assert baseline["board_task_requested_action"] is None
        return

    sheet = _board_task_sheet(case)
    assert sheet.requested_action == baseline["board_task_requested_action"]
    assert sheet.progress == baseline["board_task_progress"]
    assert sheet.missing_items == baseline["board_task_missing_items"]
    assert sheet.location_status == baseline["location_status"]

    if "sequence_requested" in baseline:
        assert _current_sequence_requested(case, sheet) is baseline["sequence_requested"]


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["request"]["active_sequence"]],
    ids=[case["id"] for case in CASES if case["request"]["active_sequence"]],
)
def test_active_sequence_turns_do_not_require_board_task_inference(case: dict[str, Any]) -> None:
    baseline = case["current_baseline"]
    assert _is_sequence_continue_message(case["user_message"]) is baseline["sequence_continue"]
    assert _is_sequence_exit_message(case["user_message"]) is baseline["sequence_exit"]
    assert _current_chatbot_board_action(case) is None


@pytest.mark.parametrize("case", CONTRACT_GAP_CASES)
def test_contract_gaps_are_documented_as_expected_failures(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    contract = case["contract"]
    assert _current_chatbot_board_action(case) == contract["chatbot_board_action"]

    if case["board_state"] == "existing" and not case["request"]["active_sequence"]:
        sheet = _board_task_sheet(case)
        assert sheet.requested_action == contract["board_task_requested_action"]
        assert sheet.progress == 100
        assert sheet.missing_items == []
