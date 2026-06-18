from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest
from app.services import chat_service, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, InitialLearningWorkModeDecision, openai_course_ai
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
    record_workflow_step,
)


TEST_USER_ID = "user_workflow_trace"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_lesson(*, existing_board: bool = False):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    if existing_board:
        refresh_lesson_runtime(
            lesson,
            document=build_document(title="已有板书", content_text="# 已有板书\n\n这一段已有内容。\n"),
        )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


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


def test_node_ids_match_latest_workflow_graph_document() -> None:
    doc = Path("docs/architecture/chat-workflow-graph.md").read_text(encoding="utf-8")
    table = doc.split("| NodeId | Type | Current source |", 1)[1].split("Current documented NodeId count", 1)[0]
    documented = re.findall(r"\| `([A-Z_]+)` \|", table)

    assert len(documented) == 59
    assert set(documented) == {node.value for node in NodeId}


def test_record_workflow_step_noops_before_timestamp_when_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import workflow_trace

    monkeypatch.setattr(
        workflow_trace,
        "_utc_now_iso",
        lambda: (_ for _ in ()).throw(AssertionError("unbound trace must not create timestamps")),
    )

    assert current_workflow_trace_collector() is None
    assert record_workflow_step(NodeId.CONTEXT_LOAD, decision="loaded") is None


def test_nested_binding_restores_outer_collector() -> None:
    outer = WorkflowTraceCollector()
    inner = WorkflowTraceCollector()

    with bind_workflow_trace_collector(outer):
        record_workflow_step(NodeId.CONTEXT_LOAD)
        with bind_workflow_trace_collector(inner):
            record_workflow_step(NodeId.BOARD_ACTION_DECIDE)
        record_workflow_step(NodeId.CHAT_TURN_GATE)

    assert isinstance(outer.steps, tuple)
    assert isinstance(inner.steps, tuple)
    assert _node_values(outer) == [NodeId.CONTEXT_LOAD.value, NodeId.CHAT_TURN_GATE.value]
    assert _node_values(inner) == [NodeId.BOARD_ACTION_DECIDE.value]
    assert current_workflow_trace_collector() is None


def test_ordinary_chat_trace_records_current_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="ordinary")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：我们先聊聊。"
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.ORDINARY_CHAT_GENERATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "not_handled"
    assert collector.steps[6].decision == "chatbot"
    assert collector.steps[7].commit_id == commit.id


def test_non_ordinary_path_never_records_ordinary_chat_generate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="initial")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="",
            reason="学习方向仍然过宽。",
            next_question="你想先聚焦到哪个具体问题？",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("non-ordinary path must not generate ordinary chat")),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我想学点东西但没想好"),
            user_id=TEST_USER_ID,
        )

    assert response.chatbot_message == "你想先聚焦到哪个具体问题？"
    assert NodeId.ORDINARY_CHAT_GENERATE.value not in _node_values(collector)
    assert _node_values(collector)[:6] == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def test_traced_and_untraced_ordinary_chat_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="traced")
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="最近有点累，想随便聊聊"),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_workflow_trace_does_not_leak_to_response_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
