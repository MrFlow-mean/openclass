from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest, LearningClarificationStatus
from app.services import chatbot as chatbot_module, workspace_state
from app.services.chat.paths.requirement_chat import (
    RequirementChatTerminalDependencies,
    handle_requirement_chat_terminal,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
)


TEST_USER_ID = "user_requirement_chat_path"


def _workspace_inputs():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id

    assert lesson.learning_requirements is not None
    requirements = lesson.learning_requirements.model_copy(deep=True)
    requirements.learning_goal = "继续澄清学习目标。"
    requirements.current_questions = ["还需要确认学习目标。"]
    clarification = LearningClarificationStatus(
        progress=35,
        label="继续澄清",
        reason="当前信息还不足以生成板书。",
        missing_items=["学习目标"],
        can_start=False,
        next_question="你希望先解决哪个具体问题？",
        ready_for_board=False,
        work_mode="unknown",
        granularity="unclear",
    )
    return workspace, package, lesson, requirements, clarification


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(TEST_USER_ID, lesson_id),
    )


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


def _node_values(collector: WorkflowTraceCollector | None) -> list[str]:
    if collector is None:
        return []
    return [step.node_id.value for step in collector.steps]


def _deps(calls: dict[str, Any], *, store: SqliteCourseStore) -> RequirementChatTerminalDependencies:
    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)
        calls["trace_before_save"] = _node_values(current_workflow_trace_collector())
        chatbot_module._save_workspace_for_user(**kwargs)
        lesson_id = kwargs["workspace"].packages[0].lessons[-1].id
        calls["versions_after_save"] = store.list_learning_requirement_versions(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        return chatbot_module._response(**kwargs)

    return RequirementChatTerminalDependencies(
        commit_operations=chatbot_module.commit_operations,
        task_metadata=chatbot_module._task_metadata,
        reference_metadata=chatbot_module._reference_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def _record_collecting_requirement_update(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    requirements,
    clarification: LearningClarificationStatus,
):
    return requirement_history.record_update(
        requirements=requirements,
        clarification=clarification,
        change_summary="记录用户需求更新。",
    )


def test_handler_persists_requirement_chat_terminal_and_records_trace_after_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_terminal")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirement_history = _requirement_history(lesson.id)
    stamp = _record_collecting_requirement_update(
        requirement_history,
        requirements=requirements,
        clarification=clarification,
    )
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_requirement_chat_terminal(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="继续说明需求"),
            requirements=requirements,
            learning_clarification=clarification,
            chatbot_message="我需要再确认一个关键点。",
            chatbot_message_source="chatbot",
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
            requirement_history=requirement_history,
            requirement_stamp=stamp,
            chat_turn_gate_metadata={"chat_turn_gate": {"route": "initial_requirement"}},
            solver_metadata={"solver_context_used": False},
            deps=_deps(calls, store=store),
        )

    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)

    assert response.requirement_run_id == stamp.run_id
    assert response.requirement_version_id == stamp.version_id
    assert response.requirement_phase == "collecting"
    assert response.active_requirement_sheet == requirements
    assert response.requirement_cleared is False
    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert calls["trace_before_save"] == []
    assert calls["versions_after_save"][0]["id"] == stamp.version_id
    assert runs[0]["status"] == "collecting"
    assert runs[0]["active_version_id"] == stamp.version_id
    assert versions[0]["status"] == "collecting"
    assert versions[0]["change_kind"] == "created"
    assert [event["event_type"] for event in events] == ["created"]
    assert commit.label == "Chat turn"
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert commit.metadata["chat_turn_gate"] == {"route": "initial_requirement"}
    assert commit.metadata["solver_context_used"] is False
    assert commit.metadata["requirement_cleared"] is False
    assert commit.metadata["active_requirement_sheet_after"] == requirements.model_dump(mode="json")
    assert _node_values(collector) == [
        NodeId.REQUIREMENT_CHAT_UPDATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "collecting"
    assert collector.steps[0].reason == "not_ready_for_board"
    assert collector.steps[0].run_id == stamp.run_id
    assert collector.steps[0].version_id == stamp.version_id
    assert collector.steps[1].commit_id == commit.id


def test_handler_keeps_durable_update_when_response_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirement_history = _requirement_history(lesson.id)
    stamp = _record_collecting_requirement_update(
        requirement_history,
        requirements=requirements,
        clarification=clarification,
    )
    calls: dict[str, Any] = {}
    deps = _deps(calls, store=store)

    def raise_on_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        raise RuntimeError("response failed")

    deps = RequirementChatTerminalDependencies(
        commit_operations=deps.commit_operations,
        task_metadata=deps.task_metadata,
        reference_metadata=deps.reference_metadata,
        save_workspace_for_user=deps.save_workspace_for_user,
        build_response=raise_on_response,
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            handle_requirement_chat_terminal(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="继续说明需求"),
                requirements=requirements,
                learning_clarification=clarification,
                chatbot_message="我需要再确认一个关键点。",
                chatbot_message_source="chatbot",
                resource_resolution=ResourceResolution(matches=[], status="none"),
                selected_reference=None,
                requirement_history=requirement_history,
                requirement_stamp=stamp,
                deps=deps,
            )

    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    persisted = store.load_for_user(TEST_USER_ID)
    persisted_commit = persisted.packages[0].lessons[-1].history_graph.commits[-1]

    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert runs[0]["status"] == "collecting"
    assert versions[0]["id"] == stamp.version_id
    assert [event["event_type"] for event in events] == ["created"]
    assert persisted_commit.metadata["kind"] == "chat_flow"
    assert persisted_commit.metadata["assistant_message_source"] == "chatbot"
    assert _node_values(collector) == [
        NodeId.REQUIREMENT_CHAT_UPDATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
    ]
    assert collector.steps[0].run_id == stamp.run_id
    assert collector.steps[0].version_id == stamp.version_id
    assert collector.steps[1].commit_id == lesson.history_graph.commits[-1].id


def test_handler_rejects_ready_requirement_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson, requirements, clarification = _workspace_inputs()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_ready_guard")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirement_history = _requirement_history(lesson.id)
    ready_clarification = clarification.model_copy(update={"ready_for_board": True})
    stamp = _record_collecting_requirement_update(
        requirement_history,
        requirements=requirements,
        clarification=ready_clarification,
    )
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="not-ready clarification"):
            handle_requirement_chat_terminal(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="继续说明需求"),
                requirements=requirements,
                learning_clarification=ready_clarification,
                chatbot_message="我需要再确认一个关键点。",
                chatbot_message_source="chatbot",
                resource_resolution=ResourceResolution(matches=[], status="none"),
                selected_reference=None,
                requirement_history=requirement_history,
                requirement_stamp=stamp,
                deps=_deps(calls, store=store),
            )

    assert calls == {}
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id) == []
    assert _node_values(collector) == []
