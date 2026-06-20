from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import ChatRequest, SelectionRef
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_explain_trace"


def _workspace_with_existing_board(*, content_text: str | None = None):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text=content_text
            or "# 已有板书\n\n## 目标范围\n第一句已有内容。第二句已有内容。\n",
        ),
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


def _selection(*, excerpt: str = "第一句已有内容。第二句已有内容。") -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt=excerpt,
        heading_path=["已有板书", "目标范围"],
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {"directive": [], "route": []}
    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_sheet", lambda **kwargs: None)

    def _route(**kwargs):
        calls["route"].append(kwargs)
        return BoardTaskRouteDecision(
            route="explain",
            location_status="found",
            reason="已定位可讲解的板书内容。",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_route_decision", _route)

    def _directive(**kwargs):
        calls["directive"].append(kwargs)
        return (
            "AI生成：这是目标内容的讲解。",
            "chatbot_board_directed",
            {
                "status": "approved",
                "target_excerpt": kwargs["target_excerpt"],
                "teaching_instruction": "只依据目标摘录讲解。",
            },
        )

    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _directive)
    return calls


def _process(
    lesson_id: str,
    *,
    message: str = "请解释这段是什么意思",
    selection: SelectionRef | None = None,
):
    return chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=message, selection=selection or _selection()),
        user_id=TEST_USER_ID,
    )


def test_board_task_explain_trace_records_directive_commit_and_response_after_consume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="explain_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_common(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id)

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-3].decision == "chatbot_board_directed"
    assert collector.steps[-3].run_id is not None
    assert collector.steps[-3].version_id is not None
    assert collector.steps[-2].decision == "committed"
    assert collector.steps[-2].commit_id == commit.id
    assert collector.steps[-2].run_id == collector.steps[-3].run_id
    assert collector.steps[-2].version_id == collector.steps[-3].version_id
    assert response.chatbot_message == "AI生成：这是目标内容的讲解。"
    assert response.requirement_cleared is True
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    assert commit.label == "Board task explanation"
    assert commit.metadata["board_explanation_directive"]["status"] == "approved"
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_cleared"] is True
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert len(calls["directive"]) == 1


def test_board_task_explain_failure_records_failure_only_after_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="explain_empty_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common(monkeypatch)

    def _empty_directive(**kwargs):
        return "", "chatbot_board_directed_empty", {"status": "approved"}

    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _empty_directive)

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id)

    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[-2].decision == "execution_failed"
    assert response.chatbot_message == ""
    assert response.active_board_task_sheet is not None
    assert response.board_task_phase == "ready"
    assert events[-1]["event_type"] == "execution_failed"
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in nodes


def test_board_task_explain_directive_generation_failure_records_no_success_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="explain_directive_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common(monkeypatch)

    def _raise_directive(**kwargs):
        raise RuntimeError("directive failed")

    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _raise_directive)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="directive failed"):
            _process(lesson_id)

    nodes = _node_values(collector)
    assert nodes == _trace_prefix()
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert NodeId.BOARD_EXPLAIN_DIRECTIVE.value not in nodes
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in nodes
    assert NodeId.BOARD_TASK_FAILURE.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_board_task_explain_save_failure_does_not_record_commit_or_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="explain_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common(monkeypatch)

    def _raise_on_save(**kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _process(lesson_id)

    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
    ]
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_board_task_explain_response_failure_keeps_durable_consumed_commit_without_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="explain_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common(monkeypatch)

    def _raise_response(**kwargs):
        raise RuntimeError("response failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _process(lesson_id)

    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    commit = saved_lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
    ]
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert collector.steps[-1].commit_id == commit.id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_board_task_sequence_plan_records_existing_sequence_start_without_duplicate_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_plan")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = _process(lesson_id, message="请按顺序逐个讲解这段")

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        *_trace_prefix(),
        NodeId.BOARD_SEQUENCE_PLAN.value,
        NodeId.BOARD_SEQUENCE_START.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert nodes.count(NodeId.RESPONSE_ASSEMBLE.value) == 1
    assert collector.steps[-4].decision == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert collector.steps[-4].run_id == collector.steps[-3].run_id
    assert collector.steps[-4].version_id == collector.steps[-3].version_id
    assert collector.steps[-3].commit_id == commit.id
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert response.board_task_phase == "consumed"
    assert commit.label == "Section explanation session start"
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in nodes
