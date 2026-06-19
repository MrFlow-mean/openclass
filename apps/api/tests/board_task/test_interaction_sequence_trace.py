from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest, InteractionTurnDecision
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.chat.paths import active_interaction_empty as active_interaction_empty_module
from app.services.course_store import SqliteCourseStore
from app.services.openai_course_ai import openai_course_ai
from app.services.segment_resolver import focus_context
from app.services.workflow_trace import NodeId

from .workflow_test_helpers import (
    TRACE_KEYS,
    active_interaction_session,
    all_keys as _all_keys,
    board_focus,
    clone_workspace as _clone_workspace,
    collect_sse_events as _collect_sse_events,
    collect_workflow_trace as bind_workflow_trace_collector,
    fail_if_called as _fail_if_called,
    node_values as _node_values,
    normalize_visible_response,
    patch_chatbot_response_failure,
    patch_chatbot_save_failure,
    patch_commit_operations_failure,
    save_workspace_to_store,
    sequence_interaction_trace_prefix as _sequence_trace_prefix,
    workspace_with_lesson,
)


TEST_USER_ID = "user_interaction_sequence_trace"


def _workspace_with_sequence_session(*, final_item: bool = False):
    workspace, lesson_id = workspace_with_lesson(
        existing_board=True,
        content_text="# 已有板书\n\n## 第一段\n第一段已有内容。\n\n## 第二段\n第二段已有内容。\n",
    )
    lesson = workspace.packages[0].lessons[-1]
    first_focus = board_focus(
        lesson,
        heading_path=["已有板书", "第一段"],
        excerpt="第一段已有内容。",
        reason="测试顺序讲解当前单元。",
        display_label="第一段",
    )
    second_focus = board_focus(
        lesson,
        heading_path=["已有板书", "第二段"],
        excerpt="第二段已有内容。",
        reason="测试顺序讲解下一单元。",
        display_label="第二段",
    )
    sequence_items = [first_focus] if final_item else [first_focus, second_focus]
    lesson.active_interaction_session = active_interaction_session(
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=first_focus,
        reference_context=first_focus.excerpt,
        compliant_input_rule="用户确认继续。",
        expected_user_behavior="用户确认继续。",
        assistant_behavior="继续讲解下一个单元。",
        progress_note="准备讲解第 1 个单元。",
        pause_reason="等待用户确认。",
        turn_count=2,
        sequence_items=sequence_items,
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    return workspace, lesson_id, first_focus, second_focus


def _workspace_with_active_session():
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    lesson = workspace.packages[0].lessons[-1]
    lesson.active_interaction_session = active_interaction_session(
        rule_text="按当前规则互动。",
        interaction_goal="继续当前互动。",
        reference_context="第一段已有内容。",
        progress_note="当前普通互动进度。",
        turn_count=2,
    )
    return workspace, lesson_id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    return save_workspace_to_store(tmp_path, workspace, user_id=TEST_USER_ID, name=name)


def _request(message: str) -> ChatRequest:
    return ChatRequest(message=message, interaction_mode="ask")


def _normalize_visible_response(value: Any) -> Any:
    return normalize_visible_response(
        value,
        normalize_board_task_ids=True,
        normalize_interaction_ids=True,
    )


def _history_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    return [
        *store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
    ]


def _sequence_exit_trace() -> list[str]:
    return [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_EXIT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _sequence_continue_trace() -> list[str]:
    return [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_CONTINUE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _patch_sequence_reply_generators(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {"end": [], "explain": []}

    def _sequence_end(**kwargs):
        calls["end"].append(kwargs)
        return "AI生成：顺序讲解结束。", "chatbot_interaction"

    def _directed_explanation(**kwargs):
        calls["explain"].append(kwargs)
        return "AI生成：继续顺序讲解。", "chatbot_interaction", {"directive": "sequence"}

    monkeypatch.setattr(chatbot_module, "_generate_sequence_end_message", _sequence_end)
    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _directed_explanation)
    return calls


def _patch_handled_sequence_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: _fail_if_called("generate_interaction_turn_decision"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_exit",
        lambda **kwargs: _fail_if_called("handle_active_interaction_exit"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_empty_decision",
        lambda **kwargs: _fail_if_called("handle_active_interaction_empty_decision"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_handoff_fallback",
        lambda **kwargs: _fail_if_called("handle_interaction_handoff_fallback"),
    )


@pytest.mark.parametrize(
    ("case", "message", "final_item", "trace", "sequence_decision", "outcome_node", "commit_label", "commit_message"),
    [
        (
            "explicit_exit",
            "结束",
            False,
            _sequence_exit_trace(),
            "exit_requested",
            NodeId.INTERACTION_EXIT,
            "Section explanation session ended",
            "Ended a sequential section explanation session",
        ),
        (
            "follow_up",
            "为什么这里这样？",
            False,
            _sequence_continue_trace(),
            "follow_up_current",
            NodeId.INTERACTION_CONTINUE,
            "Section explanation follow-up",
            "Answered a follow-up within the current sequential section",
        ),
        (
            "completion",
            "继续",
            True,
            _sequence_exit_trace(),
            "completed",
            NodeId.INTERACTION_EXIT,
            "Section explanation session completed",
            "Completed a sequential section explanation session",
        ),
        (
            "advance",
            "继续",
            False,
            _sequence_continue_trace(),
            "advance",
            NodeId.INTERACTION_CONTINUE,
            "Section explanation turn",
            "Continued a sequential section explanation session",
        ),
    ],
)
def test_sequence_turn_records_exact_trace_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
    trace: list[str],
    sequence_decision: str,
    outcome_node: NodeId,
    commit_label: str,
    commit_message: str,
) -> None:
    workspace, lesson_id, first_focus, second_focus = _workspace_with_sequence_session(final_item=final_item)
    lesson_before = workspace.packages[0].lessons[-1]
    session_before = lesson_before.active_interaction_session
    assert session_before is not None
    session_before_json = session_before.model_dump(mode="json")
    original_board = lesson_before.board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name=f"sequence_{case}")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_sequence_reply_generators(monkeypatch)
    _patch_handled_sequence_guardrails(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(message),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    metadata = commit.metadata
    nodes = _node_values(collector)
    assert nodes == trace
    assert nodes.count(NodeId.INTERACTION_SEQUENCE_CHECK.value) == 1
    assert NodeId.INTERACTION_DECIDE.value not in nodes
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in nodes
    assert NodeId.INTERACTION_NEW_TASK.value not in nodes
    assert NodeId.INTERACTION_TERMINAL.value not in nodes
    assert collector.steps[6].decision == sequence_decision
    assert collector.steps[6].reason is None
    assert collector.steps[7].node_id == outcome_node
    assert collector.steps[7].decision == ("exit_rule" if outcome_node == NodeId.INTERACTION_EXIT else "continue_rule")
    assert collector.steps[8].decision == "committed"
    assert collector.steps[8].commit_id == commit.id
    assert collector.steps[9].decision == "assembled"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.label == commit_label
    assert commit.message == commit_message
    assert metadata["kind"] == "interaction_flow"
    assert metadata["assistant_message"] == response.chatbot_message
    assert metadata["assistant_message_source"] == "chatbot_interaction"
    assert metadata["interaction_session_before"] == session_before_json
    assert response.interaction_decision is not None

    if case == "explicit_exit":
        assert response.active_interaction_session is None
        assert response.interaction_decision.route == "exit_rule"
        assert response.interaction_decision.reason == "用户结束当前顺序讲解。"
        assert metadata["interaction_session_after"] is None
        assert len(calls["end"]) == 1
        assert len(calls["explain"]) == 0
    elif case == "completion":
        assert response.active_interaction_session is None
        assert response.interaction_decision.route == "exit_rule"
        assert response.interaction_decision.reason == "顺序讲解已经完成。"
        assert metadata["interaction_session_after"] is None
        assert len(calls["end"]) == 1
        assert len(calls["explain"]) == 0
    elif case == "follow_up":
        assert response.active_interaction_session is not None
        session_after = response.active_interaction_session
        assert session_after.sequence_index == session_before.sequence_index
        assert session_after.turn_count == session_before.turn_count + 1
        assert session_after.target_focus == first_focus
        assert session_after.reference_context == focus_context(first_focus)
        assert session_after.status == "active"
        assert session_after.pause_reason == ""
        assert response.resolved_focus == first_focus
        assert response.interaction_decision.route == "continue_rule"
        assert metadata["interaction_session_after"] == session_after.model_dump(mode="json")
        assert len(calls["end"]) == 0
        assert len(calls["explain"]) == 1
        explain_request = calls["explain"][0]["request"]
        assert "请只围绕当前" in explain_request.message
        assert "不要推进到下一个" in explain_request.message
        assert calls["explain"][0]["target_excerpt"] == focus_context(first_focus)
    else:
        assert response.active_interaction_session is not None
        session_after = response.active_interaction_session
        assert session_after.sequence_index == session_before.sequence_index + 1
        assert session_after.turn_count == session_before.turn_count + 1
        assert session_after.target_focus == second_focus
        assert session_after.reference_context == focus_context(second_focus)
        assert session_after.status == "active"
        assert session_after.pause_reason == ""
        assert response.resolved_focus == second_focus
        assert response.interaction_decision.route == "continue_rule"
        assert metadata["interaction_session_after"] == session_after.model_dump(mode="json")
        assert len(calls["end"]) == 0
        assert len(calls["explain"]) == 1
        assert calls["explain"][0]["target_excerpt"] == focus_context(second_focus)
        assert "第二段" in calls["explain"][0]["request"].message


def test_unrecognized_sequence_input_records_not_handled_before_generic_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_unrecognized")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request("苹果香蕉"),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    assert nodes[:8] == [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_DECIDE.value,
    ]
    assert collector.steps[6].decision == "not_handled"
    assert collector.steps[7].decision == "empty"
    assert response.interaction_decision is None


def test_non_sequence_active_session_records_one_not_handled_sequence_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="non_sequence_not_handled")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)

    with bind_workflow_trace_collector() as collector:
        chat_service.process_chat_on_lesson(
            lesson_id,
            _request("继续"),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    assert nodes.count(NodeId.INTERACTION_SEQUENCE_CHECK.value) == 1
    assert nodes[:8] == [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_DECIDE.value,
    ]
    assert collector.steps[6].decision == "not_handled"


@pytest.mark.parametrize(
    ("case", "message", "final_item"),
    [
        ("explicit_exit", "结束", False),
        ("follow_up", "为什么这里这样？", False),
        ("completion", "继续", True),
        ("advance", "继续", False),
    ],
)
def test_sequence_traced_and_untraced_responses_and_metadata_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    untraced_store = _store_with_workspace(tmp_path, _clone_workspace(workspace), name=f"{case}_untraced")
    traced_store = _store_with_workspace(tmp_path, _clone_workspace(workspace), name=f"{case}_traced")
    _patch_sequence_reply_generators(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        _request(message),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(message),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_sequence_trace_does_not_leak_to_response_sse_history_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_sequence_reply_generators(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request("继续"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_before"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_after"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_history_rows(store, lesson_id)))

    sse_workspace, sse_lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    sse_store = _store_with_workspace(tmp_path, sse_workspace, name="sequence_sse_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", sse_store)
    _patch_sequence_reply_generators(monkeypatch)
    events = _collect_sse_events(chat_router._chat_stream_events(sse_lesson_id, _request("继续"), user_id=TEST_USER_ID))
    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


@pytest.mark.parametrize(
    ("case", "message", "final_item", "sequence_decision", "outcome_node"),
    [
        ("explicit_exit", "结束", False, "exit_requested", NodeId.INTERACTION_EXIT),
        ("follow_up", "为什么这里这样？", False, "follow_up_current", NodeId.INTERACTION_CONTINUE),
        ("completion", "继续", True, "completed", NodeId.INTERACTION_EXIT),
        ("advance", "继续", False, "advance", NodeId.INTERACTION_CONTINUE),
    ],
)
@pytest.mark.parametrize("failure_point", ["reply", "commit", "save", "response"])
def test_sequence_failure_ordering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
    sequence_decision: str,
    outcome_node: NodeId,
    failure_point: str,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    store = _store_with_workspace(tmp_path, workspace, name=f"{case}_{failure_point}_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)

    if failure_point == "reply":
        if outcome_node == NodeId.INTERACTION_EXIT:
            monkeypatch.setattr(
                chatbot_module,
                "_generate_sequence_end_message",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
            )
        else:
            monkeypatch.setattr(
                chatbot_module,
                "_generate_board_directed_explanation_message",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
            )
        expected_error = "reply failed"
    else:
        _patch_sequence_reply_generators(monkeypatch)
        if failure_point == "commit":
            patch_commit_operations_failure(monkeypatch, chatbot_module)
            expected_error = "commit failed"
        elif failure_point == "save":
            patch_chatbot_save_failure(monkeypatch, chatbot_module)
            expected_error = "save failed"
        else:
            patch_chatbot_response_failure(monkeypatch, chatbot_module)
            expected_error = "response failed"

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match=expected_error):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(message),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    assert nodes[:7] == _sequence_trace_prefix()
    assert collector.steps[6].decision == sequence_decision
    if failure_point == "reply":
        assert outcome_node.value not in nodes
        assert NodeId.PERSIST_CHAT_COMMIT.value not in nodes
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    elif failure_point in {"commit", "save"}:
        assert nodes == [*_sequence_trace_prefix(), outcome_node.value]
        assert NodeId.PERSIST_CHAT_COMMIT.value not in nodes
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    else:
        assert nodes == [*_sequence_trace_prefix(), outcome_node.value, NodeId.PERSIST_CHAT_COMMIT.value]
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
