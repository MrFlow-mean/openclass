from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardFocusRef, BoardTaskRequirementSheet, ChatRequest
from app.services import chatbot as chatbot_module
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.interaction_sequence_start import (
    InteractionSequenceStartDependencies,
    handle_interaction_sequence_start,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import focus_context
from app.services.sequence_planner import SequencePlan
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_sequence_start"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _focus(*, lesson_id: str, document_id: str, excerpt: str, label: str) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document_id,
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt=excerpt,
        confidence=1.0,
        reason=f"测试顺序讲解：{label}",
        display_label=label,
    )


def _sequence_start_inputs(tmp_path: Path, *, name: str) -> dict[str, Any]:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n第一段已有内容。第二段已有内容。\n",
        ),
    )
    first_focus = _focus(
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        excerpt="第一段已有内容。",
        label="第一个单元",
    )
    second_focus = _focus(
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        excerpt="第二段已有内容。",
        label="第二个单元",
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="目标范围",
        target_location=first_focus,
        location_status="resolved",
        requested_action="explain",
        question_or_topic="按顺序讲解目标范围。",
        missing_items=[],
        progress=100,
    )
    lesson.board_task_requirements = board_task
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store = _store_with_workspace(tmp_path, workspace, name=name)
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    board_task_stamp = board_task_history.record_update(
        sheet=board_task,
        status="ready",
        change_summary="Ready to start a sequential board explanation.",
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    requirements = lesson.learning_requirements
    learning_clarification = chatbot_module._latest_learning_clarification(
        lesson,
        requirements=requirements,
    )
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=first_focus,
        candidate_focuses=[first_focus, second_focus],
        reason="已定位可顺序讲解的内容。",
    )
    sequence_plan = SequencePlan(
        mode=ATOMIC_EXPLANATION_SEQUENCE_MODE,
        items=[first_focus, second_focus],
        start_index=0,
        scope_label="已有板书 / 目标范围",
        reason="按板书范围生成最小可讲单元顺序讲解计划。",
        planner_name="sequence_planner",
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "lesson_id": lesson.id,
        "store": store,
        "request": ChatRequest(message="按顺序讲解目标范围"),
        "requirements": requirements,
        "learning_clarification": learning_clarification,
        "resources": package.resources,
        "board_task": board_task,
        "board_task_history": board_task_history,
        "board_task_stamp": board_task_stamp,
        "decision": decision,
        "resolution": None,
        "sequence_plan": sequence_plan,
        "requirement_history": requirement_history,
        "first_focus": first_focus,
        "second_focus": second_focus,
    }


def _deps(calls: dict[str, list[dict[str, Any]]]) -> InteractionSequenceStartDependencies:
    def generate_board_directed_explanation_message(**kwargs):
        calls.setdefault("explain", []).append(kwargs)
        return "AI生成：开始顺序讲解。", "chatbot_interaction", {"directive": "sequence_start"}

    return InteractionSequenceStartDependencies(
        generate_board_directed_explanation_message=generate_board_directed_explanation_message,
        requirements_from_board_task=chatbot_module._requirements_from_board_task,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        board_search_evidence_metadata=chatbot_module._board_search_evidence_metadata,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        commit_operations=chatbot_module.commit_operations,
        save_workspace_for_user=chatbot_module._save_workspace_for_user,
        build_response=chatbot_module._response,
    )


def _with_deps(
    deps: InteractionSequenceStartDependencies,
    **overrides,
) -> InteractionSequenceStartDependencies:
    return InteractionSequenceStartDependencies(
        generate_board_directed_explanation_message=overrides.get(
            "generate_board_directed_explanation_message",
            deps.generate_board_directed_explanation_message,
        ),
        requirements_from_board_task=overrides.get("requirements_from_board_task", deps.requirements_from_board_task),
        clear_task_requirements=overrides.get("clear_task_requirements", deps.clear_task_requirements),
        board_search_evidence_metadata=overrides.get(
            "board_search_evidence_metadata",
            deps.board_search_evidence_metadata,
        ),
        task_metadata=overrides.get("task_metadata", deps.task_metadata),
        board_task_metadata=overrides.get("board_task_metadata", deps.board_task_metadata),
        commit_operations=overrides.get("commit_operations", deps.commit_operations),
        save_workspace_for_user=overrides.get("save_workspace_for_user", deps.save_workspace_for_user),
        build_response=overrides.get("build_response", deps.build_response),
    )


def _start_sequence_with_inputs(
    inputs: dict[str, Any],
    deps: InteractionSequenceStartDependencies,
):
    return handle_interaction_sequence_start(
        workspace=inputs["workspace"],
        package=inputs["package"],
        lesson=inputs["lesson"],
        user_id=TEST_USER_ID,
        request=inputs["request"],
        requirements=inputs["requirements"],
        learning_clarification=inputs["learning_clarification"],
        resources=inputs["resources"],
        board_task=inputs["board_task"],
        board_task_history=inputs["board_task_history"],
        board_task_stamp=inputs["board_task_stamp"],
        action_decision=None,
        decision=inputs["decision"],
        resolution=inputs["resolution"],
        sequence_plan=inputs["sequence_plan"],
        requirement_history=inputs["requirement_history"],
        interaction_metadata={"source": "sequence_start_handler_test"},
        deps=deps,
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_all_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def test_handler_starts_sequence_and_preserves_trace_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _sequence_start_inputs(tmp_path, name="sequence_start_handler")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls: dict[str, list[dict[str, Any]]] = {}
    original_board = inputs["lesson"].board_document.model_dump(mode="json")

    with bind_workflow_trace_collector() as collector:
        response = _start_sequence_with_inputs(inputs, _deps(calls))

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    session = response.active_interaction_session
    assert _node_values(collector) == [
        NodeId.BOARD_SEQUENCE_START.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "started"
    assert collector.steps[0].reason == inputs["sequence_plan"].reason
    assert collector.steps[0].run_id == inputs["board_task_stamp"].run_id
    assert collector.steps[0].version_id == inputs["board_task_stamp"].version_id
    assert collector.steps[0].commit_id == commit.id
    assert collector.steps[1].decision == "committed"
    assert collector.steps[1].run_id == inputs["board_task_stamp"].run_id
    assert collector.steps[1].version_id == inputs["board_task_stamp"].version_id
    assert collector.steps[1].commit_id == commit.id
    assert collector.steps[2].decision == "assembled"
    assert session is not None
    assert session.status == "active"
    assert session.sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert session.sequence_index == 0
    assert session.sequence_items == inputs["sequence_plan"].items
    assert session.target_focus == inputs["first_focus"]
    assert session.reference_context == focus_context(inputs["first_focus"])
    assert session.source_board_task_run_id == inputs["board_task_stamp"].run_id
    assert session.source_board_task_version_id == inputs["board_task_stamp"].version_id
    assert session.source_board_task_route == "explain"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.chatbot_message == "AI生成：开始顺序讲解。"
    assert response.resolved_focus == inputs["first_focus"]
    assert response.focus_candidates == inputs["sequence_plan"].items
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet == inputs["board_task"]
    assert response.board_task_run_id == inputs["board_task_stamp"].run_id
    assert response.board_task_version_id == inputs["board_task_stamp"].version_id
    assert response.board_task_phase == "consumed"
    assert commit.label == "Section explanation session start"
    assert commit.message == "Started a sequential section explanation session"
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["source"] == "sequence_start_handler_test"
    assert commit.metadata["assistant_message"] == response.chatbot_message
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["board_explanation_directive"] == {"directive": "sequence_start"}
    assert commit.metadata["board_task_run_id"] == inputs["board_task_stamp"].run_id
    assert commit.metadata["board_task_version_id"] == inputs["board_task_stamp"].version_id
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["section_explanation_sequence"] == [
        item.model_dump(mode="json") for item in inputs["sequence_plan"].items
    ]
    assert commit.metadata["explanation_sequence"] == [
        item.model_dump(mode="json") for item in inputs["sequence_plan"].items
    ]
    assert commit.metadata["explanation_sequence_mode"] == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert commit.metadata["interaction_session_before"] is None
    assert commit.metadata["interaction_session_after"] == session.model_dump(mode="json")
    assert commit.metadata["active_interaction_session_after"] == session.model_dump(mode="json")
    assert len(calls["explain"]) == 1
    assert calls["explain"][0]["target_excerpt"] == focus_context(inputs["first_focus"])
    assert calls["explain"][0]["interaction_context"]["sequence_index"] == 0
    assert calls["explain"][0]["interaction_context"]["sequence_total"] == 2
    assert "第 1/2" in calls["explain"][0]["request"].message
    assert "讲解单元" in calls["explain"][0]["request"].message
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    board_task_events = inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"])
    assert [event["event_type"] for event in board_task_events] == ["created", "ready", "consumed"]
    assert json.loads(board_task_events[-1]["metadata_json"]) == {"commit_id": commit.id}
    board_task_versions = inputs["store"].list_board_task_versions(
        owner_user_id=TEST_USER_ID,
        lesson_id=inputs["lesson_id"],
    )
    assert len(board_task_versions) == 1
    assert board_task_versions[0]["status"] == "ready"


@pytest.mark.parametrize("failure_point", ["reply", "commit", "consume", "save", "response"])
def test_handler_failure_ordering_records_trace_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    inputs = _sequence_start_inputs(tmp_path, name=f"sequence_start_handler_{failure_point}_failure")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    initial_commit_count = len(inputs["lesson"].history_graph.commits)
    calls: dict[str, list[dict[str, Any]]] = {}
    deps = _deps(calls)

    if failure_point == "reply":
        def fail_generate(**kwargs):
            calls.setdefault("explain", []).append(kwargs)
            raise RuntimeError("reply failed")

        deps = _with_deps(deps, generate_board_directed_explanation_message=fail_generate)
        expected_error = "reply failed"
    elif failure_point == "commit":
        deps = _with_deps(
            deps,
            commit_operations=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
        )
        expected_error = "commit failed"
    elif failure_point == "consume":
        monkeypatch.setattr(
            inputs["board_task_history"],
            "consume",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("consume failed")),
        )
        expected_error = "consume failed"
    elif failure_point == "save":
        deps = _with_deps(
            deps,
            save_workspace_for_user=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
        )
        expected_error = "save failed"
    else:
        deps = _with_deps(
            deps,
            build_response=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
        )
        expected_error = "response failed"

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match=expected_error):
            _start_sequence_with_inputs(inputs, deps)

    nodes = _node_values(collector)
    if failure_point in {"reply", "commit"}:
        assert nodes == []
    elif failure_point in {"consume", "save"}:
        assert nodes == [NodeId.BOARD_SEQUENCE_START.value]
    else:
        assert nodes == [NodeId.BOARD_SEQUENCE_START.value, NodeId.PERSIST_CHAT_COMMIT.value]
        assert collector.steps[1].commit_id == inputs["lesson"].history_graph.commits[-1].id
    if nodes:
        assert collector.steps[0].decision == "started"
        assert collector.steps[0].commit_id == inputs["lesson"].history_graph.commits[-1].id
        assert collector.steps[0].run_id == inputs["board_task_stamp"].run_id
        assert collector.steps[0].version_id == inputs["board_task_stamp"].version_id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    if failure_point in {"reply", "commit"}:
        assert len(inputs["lesson"].history_graph.commits) == initial_commit_count
    if failure_point in {"reply", "commit", "consume", "save"}:
        assert inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"]) == []


def test_handler_rejects_empty_sequence_plan_without_mutation(tmp_path: Path) -> None:
    inputs = _sequence_start_inputs(tmp_path, name="sequence_start_empty_plan")
    original_session = inputs["lesson"].active_interaction_session
    original_commit_count = len(inputs["lesson"].history_graph.commits)
    inputs["sequence_plan"] = SequencePlan(
        mode=ATOMIC_EXPLANATION_SEQUENCE_MODE,
        items=[],
        start_index=0,
        scope_label="已有板书 / 目标范围",
        reason="没有可顺序讲解的单元。",
        planner_name="sequence_planner",
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="sequence items"):
            _start_sequence_with_inputs(inputs, _deps({}))

    assert inputs["lesson"].active_interaction_session == original_session
    assert len(inputs["lesson"].history_graph.commits) == original_commit_count
    assert collector.steps == ()
