from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import InitialLearningWorkModeDecision, openai_course_ai
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_initial_guidance_trace"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _requirement_run_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_requirement_runs
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, id
            """,
            (TEST_USER_ID, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


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


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def _initial_learning_decision(work_mode: str) -> InitialLearningWorkModeDecision:
    if work_mode == "unknown":
        return InitialLearningWorkModeDecision(
            work_mode="unknown",
            granularity="unclear",
            topic="学习方向未定",
            reason="用户表达了学习意愿，但还没有说明学习工作模式。",
            next_question="你想先选一个具体主题学习，还是先做一份练习材料？",
            guided_discovery_reply=(
                "我可以先给两个通用方向：选一个具体主题做知识板书，"
                "或者先做一份可练习材料。你想先走哪一种？"
            ),
        )
    if work_mode == "narrow_topic":
        return InitialLearningWorkModeDecision(
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="一个宽泛主题",
            reason="用户提出的是宽泛新知识学习方向。",
            next_question="你想先从哪个具体问题开始？",
        )
    raise AssertionError(f"unsupported work_mode: {work_mode}")


def _patch_initial_learning_decision(monkeypatch: pytest.MonkeyPatch, *, work_mode: str) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: _initial_learning_decision(work_mode),
    )
    monkeypatch.setattr(openai_course_ai, "generate_chatbot_reply", lambda **kwargs: _fail_if_called("generate_chatbot_reply"))
    monkeypatch.setattr(chatbot_module, "generate_from_requirements", lambda **kwargs: _fail_if_called("generate_from_requirements"))


def test_initial_unknown_guidance_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="initial_unknown_guidance")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_initial_learning_decision(monkeypatch, work_mode="unknown")

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我想开始学习，但还没想好怎么学"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert response.chatbot_message == (
        "我可以先给两个通用方向：选一个具体主题做知识板书，或者先做一份可练习材料。你想先走哪一种？"
    )
    assert response.board_decision.action == "no_change"
    assert response.requirement_cleared is False
    assert response.learning_clarification.work_mode == "unknown"
    assert response.learning_clarification.ready_for_board is False
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "unknown"
    assert response.requirement_run_id is None
    assert response.requirement_version_id is None
    assert response.requirement_phase is None
    assert response.reference_prompt is None
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "initial_learning_guided_discovery"
    assert commit.metadata["initial_learning_work_mode"]["work_mode"] == "unknown"
    assert commit.metadata["requirement_cleared"] is False
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert _requirement_run_rows(store, lesson_id) == []
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert nodes == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INITIAL_MODE_DECIDE.value,
        NodeId.INITIAL_UNKNOWN_GUIDANCE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "unknown"
    assert collector.steps[6].reason == "用户表达了学习意愿，但还没有说明学习工作模式。"
    assert collector.steps[7].decision == "guided_discovery"
    assert collector.steps[7].reason == "用户表达了学习意愿，但还没有说明学习工作模式。"
    assert collector.steps[8].commit_id == commit.id
    assert NodeId.INITIAL_NARROW_TOPIC.value not in nodes
    assert NodeId.INITIAL_BOARD_GENERATE.value not in nodes
    assert NodeId.INITIAL_REQUIREMENT_FREEZE.value not in nodes
    assert NodeId.INITIAL_BOARD_COMMIT.value not in nodes


def test_initial_narrow_topic_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="initial_narrow_topic")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_initial_learning_decision(monkeypatch, work_mode="narrow_topic")

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我想学一个宽泛主题"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert response.chatbot_message == "你想先从哪个具体问题开始？"
    assert response.board_decision.action == "no_change"
    assert response.requirement_cleared is False
    assert response.learning_clarification.work_mode == "narrow_topic"
    assert response.learning_clarification.ready_for_board is False
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "narrow_topic"
    assert response.requirement_run_id is None
    assert response.requirement_version_id is None
    assert response.requirement_phase is None
    assert response.reference_prompt is None
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "initial_learning_work_mode"
    assert commit.metadata["initial_learning_work_mode"]["work_mode"] == "narrow_topic"
    assert commit.metadata["requirement_cleared"] is False
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert _requirement_run_rows(store, lesson_id) == []
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert nodes == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INITIAL_MODE_DECIDE.value,
        NodeId.INITIAL_NARROW_TOPIC.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "narrow_topic"
    assert collector.steps[6].reason == "用户提出的是宽泛新知识学习方向。"
    assert collector.steps[7].decision == "clarify_topic"
    assert collector.steps[7].reason == "用户提出的是宽泛新知识学习方向。"
    assert collector.steps[8].commit_id == commit.id
    assert NodeId.INITIAL_UNKNOWN_GUIDANCE.value not in nodes
    assert NodeId.INITIAL_BOARD_GENERATE.value not in nodes
    assert NodeId.INITIAL_REQUIREMENT_FREEZE.value not in nodes
    assert NodeId.INITIAL_BOARD_COMMIT.value not in nodes


@pytest.mark.parametrize(
    ("work_mode", "guidance_node"),
    [
        ("unknown", NodeId.INITIAL_UNKNOWN_GUIDANCE),
        ("narrow_topic", NodeId.INITIAL_NARROW_TOPIC),
    ],
)
def test_initial_guidance_does_not_record_persist_or_response_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    work_mode: str,
    guidance_node: NodeId,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name=f"initial_{work_mode}_save_failure")
    original_board = lesson.board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_initial_learning_decision(monkeypatch, work_mode=work_mode)
    captured: dict[str, Any] = {}

    def _raise_on_save(**kwargs):
        captured["workspace"] = kwargs["workspace"]
        raise RuntimeError("save failed")

    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="我想开始学习"),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    failed_lesson = captured["workspace"].packages[0].lessons[-1]
    commit = failed_lesson.history_graph.commits[-1]
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert failed_lesson.board_document.model_dump(mode="json") == original_board
    assert _requirement_run_rows(store, lesson_id) == []
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert nodes == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INITIAL_MODE_DECIDE.value,
        guidance_node.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


@pytest.mark.parametrize(
    ("work_mode", "guidance_node"),
    [
        ("unknown", NodeId.INITIAL_UNKNOWN_GUIDANCE),
        ("narrow_topic", NodeId.INITIAL_NARROW_TOPIC),
    ],
)
def test_initial_guidance_does_not_record_response_when_response_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    work_mode: str,
    guidance_node: NodeId,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name=f"initial_{work_mode}_response_failure")
    original_board = lesson.board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_initial_learning_decision(monkeypatch, work_mode=work_mode)

    def _raise_response(**kwargs):
        raise RuntimeError("response failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="我想开始学习"),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    commit = saved_lesson.history_graph.commits[-1]
    assert saved_lesson.board_document.model_dump(mode="json") == original_board
    assert _requirement_run_rows(store, lesson_id) == []
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert nodes == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INITIAL_MODE_DECIDE.value,
        guidance_node.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
    ]
    assert collector.steps[-1].commit_id == commit.id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
