from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    InteractionTurnDecision,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.chat.paths import active_interaction_empty as active_interaction_empty_module
from app.services.chat.paths.active_interaction_empty import (
    ActiveInteractionEmptyDependencies,
    handle_active_interaction_empty_decision,
)
from app.services.course_store import SqliteCourseStore
from app.services.openai_course_ai import openai_course_ai
from app.services.workflow_trace import NodeId

from .workflow_test_helpers import (
    TRACE_KEYS,
    active_interaction_session,
    active_interaction_trace_prefix as _interaction_trace_prefix,
    all_keys as _all_keys,
    board_focus,
    collect_sse_events as _collect_sse_events,
    collect_workflow_trace as bind_workflow_trace_collector,
    fail_if_called as _fail_if_called,
    node_values as _node_values,
    normalize_visible_response,
    patch_chatbot_response_failure,
    patch_chatbot_save_failure,
    patch_commit_operations_failure,
    save_workspace_to_store,
    workspace_with_lesson,
)


TEST_USER_ID = "user_interaction_empty_decision"


def _workspace_with_active_session():
    workspace, lesson_id = workspace_with_lesson(existing_board=True)
    lesson = workspace.packages[0].lessons[-1]
    lesson.active_interaction_session = active_interaction_session(
        progress_note="上一轮停在这里。",
        pause_reason="等待用户继续。",
        turn_count=3,
    )
    return workspace, lesson_id


def _workspace_with_sequence_session():
    workspace, lesson_id = _workspace_with_active_session()
    lesson = workspace.packages[0].lessons[-1]
    focus = board_focus(lesson)
    lesson.active_interaction_session = active_interaction_session(
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=focus,
        reference_context=focus.excerpt,
        assistant_behavior="继续讲解下一个单元。",
        progress_note="准备讲解当前单元。",
        turn_count=1,
        sequence_items=[focus],
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    return workspace, lesson_id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    return save_workspace_to_store(tmp_path, workspace, user_id=TEST_USER_ID, name=name)


def _normalize_visible_response(value: Any) -> Any:
    return normalize_visible_response(value, normalize_board_task_ids=True)


def _empty_decision_trace() -> list[str]:
    return [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_TERMINAL.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _request(message: str = "继续互动") -> ChatRequest:
    return ChatRequest(message=message, interaction_mode="ask")


def _interaction_decision(route: str) -> InteractionTurnDecision:
    return InteractionTurnDecision(
        route=route,
        reason=f"{route} reason",
        progress_note=f"{route} progress",
        user_intent=f"{route} intent",
    )


def _patch_empty_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)


def _patch_unexpected_empty_terminal_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _fail_if_called("generate_chatbot_reply"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_interaction_chatbot_message",
        lambda **kwargs: _fail_if_called("_generate_interaction_chatbot_message"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_board_directed_explanation_message",
        lambda **kwargs: _fail_if_called("_generate_board_directed_explanation_message"),
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
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_handoff_fallback",
        lambda **kwargs: _fail_if_called("handle_interaction_handoff_fallback"),
    )


def _count_empty_decision_handler_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}
    original_handler = chatbot_module.handle_active_interaction_empty_decision

    def _counting_handler(**kwargs):
        calls["count"] += 1
        return original_handler(**kwargs)

    monkeypatch.setattr(chatbot_module, "handle_active_interaction_empty_decision", _counting_handler)
    return calls


def _empty_decision_test_deps() -> ActiveInteractionEmptyDependencies:
    return ActiveInteractionEmptyDependencies(
        task_metadata=lambda **kwargs: {},
        save_workspace_for_user=lambda **kwargs: None,
        build_response=lambda **kwargs: _fail_if_called("build_response"),
    )


def _history_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    return [
        *store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
    ]


def test_empty_decision_records_exact_terminal_trace_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    lesson_before = workspace.packages[0].lessons[-1]
    session_before = lesson_before.active_interaction_session
    assert session_before is not None
    session_before_json = session_before.model_dump(mode="json")
    original_board = lesson_before.board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name="empty_decision_trace")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_empty_decision(monkeypatch)
    _patch_unexpected_empty_terminal_calls(monkeypatch)
    calls = _count_empty_decision_handler_calls(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    metadata = commit.metadata
    assert calls["count"] == 1
    assert _node_values(collector) == _empty_decision_trace()
    assert collector.steps[7].decision == "empty"
    assert collector.steps[7].reason is None
    assert collector.steps[8].decision == "empty"
    assert collector.steps[8].reason is None
    assert collector.steps[9].decision == "committed"
    assert collector.steps[9].commit_id == commit.id
    assert collector.steps[10].decision == "assembled"
    assert response.chatbot_message == ""
    assert response.interaction_decision is None
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.model_dump(mode="json") == session_before_json
    assert lesson.active_interaction_session is not None
    assert lesson.active_interaction_session.model_dump(mode="json") == session_before_json
    assert response.active_interaction_session.status == session_before.status
    assert response.active_interaction_session.turn_count == session_before.turn_count
    assert response.active_interaction_session.progress_note == session_before.progress_note
    assert response.active_interaction_session.pause_reason == session_before.pause_reason
    assert response.active_interaction_session.rule_text == session_before.rule_text
    assert response.active_interaction_session.interaction_goal == session_before.interaction_goal
    assert response.active_interaction_session.reference_context == session_before.reference_context
    assert response.active_interaction_session.compliant_input_rule == session_before.compliant_input_rule
    assert response.active_interaction_session.expected_user_behavior == session_before.expected_user_behavior
    assert response.active_interaction_session.assistant_behavior == session_before.assistant_behavior
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.label == "Interaction turn"
    assert commit.message == "Recorded an interaction-rule turn without a route decision"
    assert metadata["kind"] == "interaction_flow"
    assert metadata["assistant_message"] == ""
    assert metadata["assistant_message_source"] == "interaction_decision_empty"
    assert metadata["interaction_decision"] is None
    assert metadata["interaction_session_before"] == session_before_json
    assert metadata["interaction_session_after"] == session_before_json
    assert metadata["active_interaction_session_after"] == session_before_json


def test_empty_decision_traced_and_untraced_responses_and_metadata_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="empty_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="empty_traced")
    _patch_empty_decision(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        _request(),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_empty_decision_trace_does_not_leak_to_response_sse_history_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="empty_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_empty_decision(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_before"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_after"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_history_rows(store, lesson_id)))

    sse_workspace, sse_lesson_id = _workspace_with_active_session()
    sse_store = _store_with_workspace(tmp_path, sse_workspace, name="empty_sse_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", sse_store)
    _patch_empty_decision(monkeypatch)
    events = _collect_sse_events(chat_router._chat_stream_events(sse_lesson_id, _request(), user_id=TEST_USER_ID))
    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


def test_empty_decision_commit_failure_does_not_record_persist_or_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="empty_commit_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_empty_decision(monkeypatch)
    patch_commit_operations_failure(monkeypatch, active_interaction_empty_module)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="commit failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_TERMINAL.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_empty_decision_workspace_save_failure_keeps_terminal_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="empty_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_empty_decision(monkeypatch)
    patch_chatbot_save_failure(monkeypatch, chatbot_module)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_TERMINAL.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_empty_decision_response_failure_keeps_persist_but_not_response_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="empty_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_empty_decision(monkeypatch)
    patch_chatbot_response_failure(monkeypatch, chatbot_module)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_TERMINAL.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_empty_decision_handler_requires_previous_session() -> None:
    workspace, _lesson_id = _workspace_with_active_session()
    package = workspace.packages[0]
    lesson = package.lessons[-1]

    with pytest.raises(ValueError, match="previous interaction session"):
        handle_active_interaction_empty_decision(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=_request(),
            requirements=lesson.learning_requirements,
            learning_clarification=chatbot_module._latest_learning_clarification(
                lesson,
                requirements=lesson.learning_requirements,
            ),
            session_before=None,
            requirement_history=None,
            deps=_empty_decision_test_deps(),
        )


@pytest.mark.parametrize("active_session_kind", ["missing", "different"])
def test_empty_decision_handler_requires_current_active_session_to_match_previous(
    active_session_kind: str,
) -> None:
    workspace, _lesson_id = _workspace_with_active_session()
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    session_before = lesson.active_interaction_session
    assert session_before is not None
    if active_session_kind == "missing":
        lesson.active_interaction_session = None
    else:
        lesson.active_interaction_session = session_before.model_copy(update={"turn_count": session_before.turn_count + 1})

    with pytest.raises(ValueError, match="current active session"):
        handle_active_interaction_empty_decision(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=_request(),
            requirements=lesson.learning_requirements,
            learning_clarification=chatbot_module._latest_learning_clarification(
                lesson,
                requirements=lesson.learning_requirements,
            ),
            session_before=session_before,
            requirement_history=None,
            deps=_empty_decision_test_deps(),
        )


@pytest.mark.parametrize(
    "route",
    [
        "continue_rule",
        "resume_rule",
        "rule_violation",
        "exit_rule",
        "new_task",
        "side_learning_request",
    ],
)
def test_other_interaction_routes_do_not_record_empty_terminal_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name=f"not_empty_{route}")
    monkeypatch.setattr(workspace_state, "STORE", store)
    decision = _interaction_decision(route)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: decision)
    monkeypatch.setattr(
        chatbot_module,
        "_generate_interaction_chatbot_message",
        lambda **kwargs: ("AI生成：继续当前互动。", "chatbot_interaction", None),
    )

    if route in {"new_task", "side_learning_request"}:

        def _board_task_response(**kwargs):
            return chatbot_module._response(
                workspace=kwargs["workspace"],
                package=kwargs["package"],
                lesson=kwargs["lesson"],
                chatbot_message="转入板书任务。",
                learning_clarification=chatbot_module._latest_learning_clarification(
                    kwargs["lesson"],
                    requirements=kwargs["requirements"],
                ),
                requirements=kwargs["requirements"],
                board_decision=BoardDecision(action="no_change", reason="board task handled"),
                requirement_history=kwargs["requirement_history"],
                board_task_history=kwargs["board_task_history"],
            )

        monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _board_task_response)

    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_empty_decision",
        lambda **kwargs: _fail_if_called("handle_active_interaction_empty_decision"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(f"{route} message"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == route
    assert not any(
        step.node_id == NodeId.INTERACTION_TERMINAL and step.decision == "empty" for step in collector.steps
    )


def test_sequence_session_does_not_record_empty_terminal_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_not_empty_terminal")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: _fail_if_called("generate_interaction_turn_decision"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_sequence_end_message",
        lambda **kwargs: ("顺序讲解结束。", "chatbot_interaction"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_empty_decision",
        lambda **kwargs: _fail_if_called("handle_active_interaction_empty_decision"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request("结束互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert NodeId.INTERACTION_DECIDE.value not in _node_values(collector)
    assert not any(
        step.node_id == NodeId.INTERACTION_TERMINAL and step.decision == "empty" for step in collector.steps
    )
