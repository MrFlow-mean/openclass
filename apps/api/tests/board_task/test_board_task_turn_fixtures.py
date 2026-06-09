from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardTaskRequirementSheet, ChatRequest, SelectionRef
from app.services.board_task_manager import update_board_task_from_chat
from app.services.chatbot import (
    _infer_board_task_action,
    _requests_collection_explanation_sequence,
    _requests_learning_start,
    _requests_resource_backed_answer,
)
from app.services.learning_requirement_manager import (
    is_explicit_board_generation_request,
    is_generation_control_request,
)
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import build_document


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "board_task_turn_cases.yml"
REQUIRED_CASE_IDS = {
    "existing_explain_selection_meaning",
    "existing_append_next_section",
    "existing_expand_selection",
    "existing_simplify_selection",
    "existing_rewrite_polish_selection",
    "existing_resource_answer",
    "existing_explain_exercise_collection",
    "existing_explain_question_2",
    "blank_learning_topic",
    "blank_start_board_generation",
}
BASELINE_KEYS = {
    "path",
    "board_action",
    "board_task_requested_action",
    "document_write_allowed",
    "resource_reference",
    "collection_sequence_candidate",
    "learning_requirement_path",
    "initial_board_generation_path",
}
DOCUMENT_WRITE_ACTIONS = {
    "generate_board",
    "append_section",
    "rewrite_target",
    "expand_target",
    "simplify_target",
}
EXISTING_BOARD_TEXT = (
    "# 已有板书\n"
    "## 核心概念\n"
    "这一段说明当前概念的含义。\n"
    "## 练习题\n"
    "- 第 1 题：说明现象 A。\n"
    "- 第 2 题：比较方法 B 和方法 C。\n"
    "- 第 3 题：判断结论 D。\n"
)


def _load_cases() -> list[dict[str, Any]]:
    # The fixture is JSON-compatible YAML, so the baseline needs no test-only YAML dependency.
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


CASES = _load_cases()


def _selection(case: dict[str, Any]) -> SelectionRef | None:
    if not case["request"]["has_selection"]:
        return None
    return SelectionRef(
        kind="board",
        excerpt="这一段说明当前概念的含义。",
        heading_path=["已有板书", "核心概念"],
    )


def _chat_request(case: dict[str, Any]) -> ChatRequest:
    request = case["request"]
    return ChatRequest(
        message=case["user_message"],
        selection=_selection(case),
        interaction_mode=request["interaction_mode"],
        board_generation_action=request["board_generation_action"],
    )


def _lesson_for_case(case: dict[str, Any]):
    lesson = create_empty_lesson("测试主题")
    if case["board_state"] == "existing":
        lesson.board_document = build_document(title="已有板书", content_text=EXISTING_BOARD_TEXT)
    return lesson


def _board_task_sheet(case: dict[str, Any]) -> BoardTaskRequirementSheet | None:
    if case["board_state"] != "existing":
        return None
    selection = _selection(case)
    return update_board_task_from_chat(
        lesson=_lesson_for_case(case),
        resources=[],
        conversation=[],
        user_message=case["user_message"],
        selection=selection,
        selection_excerpt=selection.excerpt if selection else None,
        existing=None,
    )


def _collection_sequence_candidate(
    *,
    case: dict[str, Any],
    sheet: BoardTaskRequirementSheet | None,
) -> bool:
    if sheet is None:
        return False
    return _requests_collection_explanation_sequence(
        board_task=sheet,
        request_message=case["user_message"],
    )


def _baseline_for_case(case: dict[str, Any]) -> dict[str, object]:
    request = _chat_request(case)
    document_empty = case["board_state"] == "blank"
    board_action = _infer_board_task_action(
        request,
        has_selection=case["request"]["has_selection"],
        document_empty=document_empty,
    )
    sheet = _board_task_sheet(case)
    resource_reference = _requests_resource_backed_answer(case["user_message"])
    learning_requirement_path = document_empty and _requests_learning_start(case["user_message"])
    initial_board_generation_path = document_empty and (
        request.board_generation_action == "start"
        or is_generation_control_request(case["user_message"])
        or is_explicit_board_generation_request(case["user_message"])
    )
    board_task_requested_action = sheet.requested_action if sheet else None
    collection_sequence_candidate = _collection_sequence_candidate(case=case, sheet=sheet)
    document_write_allowed = board_action in DOCUMENT_WRITE_ACTIONS
    path = "unclassified"
    if initial_board_generation_path:
        path = "initial_board_generation"
    elif learning_requirement_path:
        path = "learning_requirement"
    elif resource_reference and board_action is None:
        path = "resource_reference"
    elif case["board_state"] == "existing" and (board_action is not None or board_task_requested_action is not None):
        path = "existing_board_task"
    return {
        "path": path,
        "board_action": board_action,
        "board_task_requested_action": board_task_requested_action,
        "document_write_allowed": document_write_allowed,
        "resource_reference": resource_reference,
        "collection_sequence_candidate": collection_sequence_candidate,
        "learning_requirement_path": learning_requirement_path,
        "initial_board_generation_path": initial_board_generation_path,
    }


def _negative_case(base: dict[str, Any], negative: dict[str, Any]) -> dict[str, Any]:
    request = dict(base["request"])
    if "has_selection" in negative:
        request["has_selection"] = negative["has_selection"]
    if "interaction_mode" in negative:
        request["interaction_mode"] = negative["interaction_mode"]
    if "board_generation_action" in negative:
        request["board_generation_action"] = negative["board_generation_action"]
    return {
        **base,
        "board_state": negative.get("board_state", base["board_state"]),
        "user_message": negative["message"],
        "request": request,
    }


def _case_param(case: dict[str, Any]) -> pytest.ParameterSet | dict[str, Any]:
    if case.get("xfail_reason"):
        return pytest.param(
            case,
            marks=pytest.mark.xfail(reason=case["xfail_reason"], strict=True),
            id=case["id"],
        )
    return case


def test_fixture_file_schema_is_complete() -> None:
    ids = [case["id"] for case in CASES]
    assert set(ids) == REQUIRED_CASE_IDS
    assert len(ids) == len(set(ids))

    for case in CASES:
        assert case["title"]
        assert case["coverage"]
        assert case["board_state"] in {"existing", "blank"}
        assert case["user_message"]
        assert set(case["expected"]) == BASELINE_KEYS
        assert case["request"]["interaction_mode"] in {"ask", "direct_edit"}
        assert isinstance(case["request"]["has_selection"], bool)
        assert len(case["negative_examples"]) >= 2
        for negative in case["negative_examples"]:
            assert negative["message"]
            assert negative["must_not"]
            assert set(negative["must_not"]).issubset(BASELINE_KEYS)


@pytest.mark.parametrize("case", [_case_param(case) for case in CASES], ids=[case["id"] for case in CASES])
def test_board_task_turn_positive_contract(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    assert _baseline_for_case(case) == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_board_task_turn_negative_examples(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    for negative in case["negative_examples"]:
        actual = _baseline_for_case(_negative_case(case, negative))
        for key, disallowed_value in negative["must_not"].items():
            assert actual[key] != disallowed_value, (
                f"{case['id']} negative example {negative['message']!r} "
                f"unexpectedly matched {key}={disallowed_value!r}"
            )
