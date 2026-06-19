from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    ChatRequest,
    InteractionSession,
    InteractionTurnDecision,
    LibraryChapter,
    ResourceLibraryItem,
    SelectionRef,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_fallback"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_active_session():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(title="已有板书", content_text="# 已有板书\n\n这一段已有内容。\n"),
    )
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="按当前规则逐轮互动。",
        interaction_goal="继续当前互动。",
        reference_context="这一段已有内容。",
        compliant_input_rule="用户继续按规则输入。",
        expected_user_behavior="用户继续按规则输入。",
        assistant_behavior="Chatbot 按当前规则回应。",
        turn_count=1,
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    package.resources.append(
        ResourceLibraryItem(
            id="resource-fallback",
            name="参考资料",
            mime_type="text/plain",
            resource_type="document",
            size_bytes=128,
            scope_lesson_id=lesson.id,
            outline=[
                LibraryChapter(
                    id="chapter-fallback",
                    title="资料章节",
                    level=1,
                    summary="这一章包含参考内容。",
                    keywords=["参考内容"],
                    path=["资料章节"],
                )
            ],
        )
    )
    return workspace, lesson.id


def _workspace_with_sequence_session():
    workspace, lesson_id = _workspace_with_active_session()
    lesson = workspace.packages[0].lessons[-1]
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书"],
        excerpt="这一段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解。",
        display_label="已有板书",
    )
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=focus,
        reference_context=focus.excerpt,
        compliant_input_rule="用户确认继续。",
        expected_user_behavior="用户确认继续。",
        assistant_behavior="继续讲解下一个单元。",
        turn_count=1,
        sequence_items=[focus],
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    return workspace, lesson_id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _parse_sse(block: str) -> tuple[str, dict[str, Any]]:
    event = "message"
    data_lines: list[str] = []
    for line in block.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    return event, json.loads("\n".join(data_lines))


def _collect_sse_events(stream) -> list[tuple[str, dict[str, Any]]]:
    return [_parse_sse(block) for block in stream]


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


def _normalize_visible_response(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        is_commit = {"label", "message", "branch_name", "snapshot", "metadata"}.issubset(value)
        for key, item in value.items():
            if key in {"created_at", "updated_at"}:
                normalized[key] = "<timestamp>"
            elif key in {"requirement_run_id", "requirement_version_id"}:
                normalized[key] = "<requirement_id>"
            elif key in {"board_task_run_id", "board_task_version_id"}:
                normalized[key] = "<board_task_id>"
            elif is_commit and key == "id":
                normalized[key] = "<commit_id>"
            elif key == "head_commit_id":
                normalized[key] = "<commit_id>"
            else:
                normalized[key] = _normalize_visible_response(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_visible_response(item) for item in value]
    return value


def _interaction_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
        NodeId.INTERACTION_DECIDE.value,
    ]


def _fallback_trace() -> list[str]:
    return [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
        NodeId.INTERACTION_TERMINAL.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def _interaction_decision(
    route: str,
    *,
    reason: str | None = None,
    progress_note: str | None = None,
) -> InteractionTurnDecision:
    return InteractionTurnDecision(
        route=route,
        reason=reason or f"{route} reason",
        progress_note=progress_note or f"{route} progress",
        user_intent=f"{route} intent",
    )


def _patch_interaction_turn(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    *,
    reason: str | None = None,
    chatbot_message: str = "AI生成：暂时没有可执行的板书任务。",
) -> InteractionTurnDecision:
    decision = _interaction_decision(route, reason=reason)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: decision)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=chatbot_message),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_board_directed_explanation_message",
        lambda **kwargs: (
            chatbot_message,
            "chatbot_interaction",
            {"directive": "side learning fallback"},
        ),
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
    return decision


def _patch_board_task_none(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"count": 0}

    def _board_task_none(**kwargs):
        calls["count"] += 1
        calls["kwargs"] = kwargs
        calls["source_interaction_metadata"] = dict(kwargs["source_interaction_metadata"])
        calls["active_session_at_entry"] = kwargs["lesson"].active_interaction_session
        return None

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _board_task_none)
    return calls


def _patch_board_task_success(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"count": 0}

    def _board_task_response(**kwargs):
        calls["count"] += 1
        calls["active_session_at_entry"] = kwargs["lesson"].active_interaction_session
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
    return calls


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="这一段已有内容。",
        heading_path=["已有板书"],
    )


def _request(message: str = "新的板书任务") -> ChatRequest:
    return ChatRequest(
        message=message,
        selection=_selection(),
        interaction_mode="ask",
    )


def _history_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    return [
        *store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
    ]


@pytest.mark.parametrize("route", ["new_task", "side_learning_request"])
def test_handoff_fallback_records_exact_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name=f"handoff_fallback_{route}")
    monkeypatch.setattr(workspace_state, "STORE", store)
    decision = _patch_interaction_turn(monkeypatch, route, reason=f"{route} fallback reason")
    handoff_calls = _patch_board_task_none(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(f"{route} fallback"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert handoff_calls["count"] == 1
    assert handoff_calls["active_session_at_entry"] is None
    assert _node_values(collector) == _fallback_trace()
    assert collector.steps[8].decision == route
    assert collector.steps[9].decision == route
    assert collector.steps[9].reason == decision.reason
    assert collector.steps[10].decision == "committed"
    assert collector.steps[10].commit_id == commit.id
    assert collector.steps[11].decision == "assembled"
    assert response.active_interaction_session is None
    assert response.interaction_decision == decision
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []


def test_handoff_fallback_reuses_source_metadata_and_preserves_commit_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="handoff_fallback_metadata")
    monkeypatch.setattr(workspace_state, "STORE", store)
    decision = _patch_interaction_turn(monkeypatch, "new_task", reason="new task fallback reason")
    handoff_calls = _patch_board_task_none(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    metadata = commit.metadata
    source_metadata = handoff_calls["source_interaction_metadata"]
    assert commit.label == "Interaction session ended"
    assert commit.message == "Exited a rule-based interaction session and found no executable board task in the same turn"
    assert metadata["kind"] == "interaction_flow"
    assert metadata["user_message"] == "新的板书任务"
    assert metadata["assistant_message"] == "AI生成：暂时没有可执行的板书任务。"
    assert metadata["assistant_message_source"] == "chatbot_interaction"
    assert metadata["board_explanation_directive"] is None
    assert metadata["interaction_mode"] == "ask"
    assert metadata["selection"] == _selection().model_dump(mode="json")
    assert metadata["task_requirement_sheet"] == response.learning_requirement_sheet.model_dump(mode="json")
    assert metadata["requirement_cleared"] is False
    for key, value in source_metadata.items():
        assert metadata[key] == value
    assert metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert metadata["interaction_session_before"] is not None
    assert metadata["interaction_session_after"] is None
    assert metadata["active_interaction_session_after"] is None


def test_successful_handoff_does_not_record_fallback_terminal_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="handoff_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="successful handoff")
    handoff_calls = _patch_board_task_success(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    assert handoff_calls["count"] == 1
    assert response.chatbot_message == "转入板书任务。"
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_board_task_exception_keeps_attempt_trace_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="handoff_exception")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="board task raises")

    def _raise_board_task(**kwargs):
        assert kwargs["lesson"].active_interaction_session is None
        raise RuntimeError("board task failed")

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _raise_board_task)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="board task failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_reply_generation_failure_does_not_record_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="reply_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="reply raises")
    _patch_board_task_none(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "_generate_interaction_chatbot_message",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="reply failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]


def test_workspace_save_failure_keeps_terminal_but_not_persist_or_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="save raises")
    _patch_board_task_none(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "_save_workspace_for_user",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
        NodeId.INTERACTION_TERMINAL.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_response_construction_failure_keeps_persist_but_not_response_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="response raises")
    _patch_board_task_none(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "_response",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
        NodeId.INTERACTION_TERMINAL.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


@pytest.mark.parametrize("route", ["continue_rule", "resume_rule", "rule_violation"])
def test_continue_resume_and_violation_do_not_record_handoff_fallback_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name=f"no_terminal_{route}")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, route, reason=f"{route} reason")

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=f"{route} message"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == route
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)


def test_exit_rule_does_not_record_handoff_fallback_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="no_terminal_exit")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="exit reason",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)


def test_empty_decision_does_not_record_handoff_fallback_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="no_terminal_empty")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is None
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)


def test_sequence_session_does_not_record_handoff_fallback_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="no_terminal_sequence")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "_generate_sequence_end_message",
        lambda **kwargs: ("顺序讲解结束。", "chatbot_interaction"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert NodeId.INTERACTION_TERMINAL.value not in _node_values(collector)


def test_traced_and_untraced_fallback_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="fallback_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="fallback_traced")
    _patch_interaction_turn(monkeypatch, "new_task", reason="new task fallback")
    _patch_board_task_none(monkeypatch)

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


def test_handoff_fallback_trace_does_not_leak_to_response_sse_metadata_or_histories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="fallback_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(monkeypatch, "new_task", reason="new task fallback")
    handoff_calls = _patch_board_task_none(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    source_metadata = handoff_calls["source_interaction_metadata"]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(source_metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(source_metadata["interaction_session_before"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_history_rows(store, lesson_id)))

    workspace_for_stream, stream_lesson_id = _workspace_with_active_session()
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="fallback_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            _request(),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))
