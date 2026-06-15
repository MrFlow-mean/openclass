from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest, SelectionRef
from app.services import chat_service, turn_intent, workspace_state
from app.services.board_task_decider import decide_board_task_action
from app.services.chat_turn_gate import decide_chat_turn
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_manager import is_explicit_board_generation_request, is_generation_control_request
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, LearningRequirementUpdate, openai_course_ai
from app.services.rich_document import build_document


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "chat_turn_gate_cases.json"
TEST_USER_ID = "user_chat_turn_gate"
EXISTING_BOARD_TEXT = "# 已有板书\n\n## 核心概念\n这一段说明当前概念的含义。\n"
BASELINE_KEYS = {
    "route",
    "should_update_learning_requirement",
    "should_try_board_task",
    "requires_resource_reference",
}


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


CASES = _load_cases()


def _selection(has_selection: bool) -> SelectionRef | None:
    if not has_selection:
        return None
    return SelectionRef(kind="board", excerpt="这一段说明当前概念的含义。", heading_path=["已有板书", "核心概念"])


def _request_for_case(case: dict[str, Any]) -> ChatRequest:
    request = case["request"]
    return ChatRequest(
        message=case["user_message"],
        selection=_selection(request["has_selection"]),
        interaction_mode=request["interaction_mode"],
        board_generation_action=request["board_generation_action"],
        teaching_action=request["teaching_action"],
        resource_reference_action=request["resource_reference_action"],
    )


def _baseline_for_case(case: dict[str, Any]) -> dict[str, object]:
    request = _request_for_case(case)
    document_empty = case["board_state"] == "blank"
    signals = turn_intent.extract_intent_signals(case["user_message"])
    action_decision = decide_board_task_action(
        message=case["user_message"],
        signals=signals,
        has_selection=case["request"]["has_selection"],
        document_empty=document_empty,
        interaction_mode=request.interaction_mode,
        board_generation_action=request.board_generation_action,
        has_explicit_resource_reference=turn_intent.has_explicit_resource_reference(case["user_message"]),
    )
    decision = decide_chat_turn(
        message=case["user_message"],
        document_empty=document_empty,
        has_selection=case["request"]["has_selection"],
        interaction_mode=request.interaction_mode,
        board_generation_action=request.board_generation_action,
        teaching_action=request.teaching_action,
        resource_reference_action=request.resource_reference_action,
        board_action_decision=action_decision,
        has_active_board_task=bool(case.get("has_active_board_task", False)),
    )
    return {
        "route": decision.route,
        "should_update_learning_requirement": decision.should_update_learning_requirement,
        "should_try_board_task": decision.should_try_board_task,
        "requires_resource_reference": decision.requires_resource_reference,
    }


def _negative_case(base: dict[str, Any], negative: dict[str, Any]) -> dict[str, Any]:
    request = dict(base["request"])
    for key in [
        "has_selection",
        "interaction_mode",
        "board_generation_action",
        "teaching_action",
        "resource_reference_action",
    ]:
        if key in negative:
            request[key] = negative[key]
    return {
        **base,
        "board_state": negative.get("board_state", base["board_state"]),
        "user_message": negative["message"],
        "request": request,
    }


def _seed_workspace(store: SqliteCourseStore, *, existing_board: bool = False):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    if existing_board:
        refresh_lesson_runtime(lesson, document=build_document(title="已有板书", content_text=EXISTING_BOARD_TEXT))
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def _requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=35,
        summary="用户表达了学习工作台任务。",
        key_facts=[],
        checklist=[],
        missing_items=["具体学习内容"],
        next_question="你想先围绕哪个问题开始？",
        ready_for_board=False,
    )


def test_fixture_file_schema_is_complete() -> None:
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["title"]
        assert case["board_state"] in {"blank", "existing"}
        assert case["user_message"]
        assert set(case["expected"]) == BASELINE_KEYS
        assert len(case["negative_examples"]) >= 2
        for negative in case["negative_examples"]:
            assert negative["message"]
            assert set(negative["must_not"]).issubset(BASELINE_KEYS)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_chat_turn_gate_positive_contract(case: dict[str, Any]) -> None:
    assert _baseline_for_case(case) == case["expected"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_chat_turn_gate_negative_examples(case: dict[str, Any]) -> None:
    for negative in case["negative_examples"]:
        baseline = _baseline_for_case(_negative_case(case, negative))
        for key, forbidden in negative["must_not"].items():
            assert baseline[key] != forbidden, (
                f"{case['id']} negative {negative['message']} unexpectedly had {key}={forbidden}"
            )


def test_blank_ordinary_chat_does_not_update_learning_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ordinary chat must not update learning requirements")),
    )

    lesson = _seed_workspace(store)
    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="最近有点累，想随便聊聊"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：我们先聊聊。"
    assert commit.metadata["chat_turn_gate"]["route"] == "ordinary_chat"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert response.requirement_phase is None


def test_blank_learning_request_still_updates_learning_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = {"requirement": 0}

    def _update(**kwargs):
        calls["requirement"] += 1
        return _requirement_update(**kwargs)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我先确认学习方向。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _update)

    lesson = _seed_workspace(store)
    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学点东西但没想好"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert calls["requirement"] == 1
    assert commit.metadata["chat_turn_gate"]["route"] == "initial_learning"
    assert response.learning_clarification.summary == "用户表达了学习工作台任务。"


def test_existing_board_ordinary_chat_does_not_enter_board_task_or_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：可以，先聊两句。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ordinary chat must not enter board task")),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ordinary chat must not update learning requirements")),
    )

    lesson = _seed_workspace(store, existing_board=True)
    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="今天先随便聊两句"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：可以，先聊两句。"
    assert commit.metadata["chat_turn_gate"]["route"] == "ordinary_chat"
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet is None


def test_generation_helpers_are_not_reimplemented_in_gate_fixture() -> None:
    assert is_generation_control_request("开始生成")
    assert is_explicit_board_generation_request("请生成一份板书")
