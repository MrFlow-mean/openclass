from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardFocusRef, BoardTaskRequirementSheet, ChatRequest
from app.services import chatbot as chatbot_module
from app.services import workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_explain import (
    BoardTaskExplainDependencies,
    build_board_task_explanation_target_excerpt,
    handle_board_task_explain,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardExplanationDirective, BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution, focus_context
from app.services.sequence_planner import SequencePlan
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_explain_handler"
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


def _focus(*, lesson_id: str, document_id: str, segment_id: str, excerpt: str, label: str) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document_id,
        segment_id=segment_id,
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt=excerpt,
        confidence=1.0,
        reason=f"测试讲解目标：{label}",
        display_label=label,
    )


def _explain_inputs(tmp_path: Path, *, name: str) -> dict[str, Any]:
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
    primary_focus = _focus(
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-primary",
        excerpt="第一段已有内容。",
        label="第一个片段",
    )
    next_focus = _focus(
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-next",
        excerpt="第二段已有内容。",
        label="第二个片段",
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="目标范围",
        target_location=primary_focus,
        location_status="resolved",
        requested_action="explain",
        question_or_topic="解释目标范围里的第一段。",
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
        target_focus=primary_focus,
        candidate_focuses=[primary_focus, next_focus],
        reason="已定位可讲解的板书内容。",
    )
    resolution = FocusResolution(
        focus=primary_focus,
        candidates=[primary_focus, next_focus],
        status="resolved",
        question="",
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "lesson_id": lesson.id,
        "store": store,
        "request": ChatRequest(message="解释目标范围里的第一段"),
        "requirements": requirements,
        "learning_clarification": learning_clarification,
        "resources": package.resources,
        "board_task": board_task,
        "board_task_history": board_task_history,
        "decision": decision,
        "resolution": resolution,
        "requirement_history": requirement_history,
        "primary_focus": primary_focus,
        "next_focus": next_focus,
    }


def _directive_payload(**overrides: object) -> dict[str, object]:
    directive = BoardExplanationDirective(
        status="approved",
        target_summary="目标范围第一段",
        target_excerpt="第一段已有内容。",
        board_feedback="可以围绕目标摘录讲解。",
        teaching_instruction="只依据目标摘录讲解。",
        constraints=["不要越界讲解后续候选"],
    ).model_copy(update=overrides)
    return directive.model_dump(mode="json")


def _deps(calls: dict[str, list[dict[str, Any]]]) -> BoardTaskExplainDependencies:
    def generate_board_directed_explanation_message(**kwargs):
        calls.setdefault("explain", []).append(kwargs)
        return "AI生成：这是目标内容的讲解。", "chatbot_board_directed", _directive_payload()

    return BoardTaskExplainDependencies(
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
    deps: BoardTaskExplainDependencies,
    **overrides,
) -> BoardTaskExplainDependencies:
    return BoardTaskExplainDependencies(
        generate_board_directed_explanation_message=overrides.get(
            "generate_board_directed_explanation_message",
            deps.generate_board_directed_explanation_message,
        ),
        requirements_from_board_task=overrides.get(
            "requirements_from_board_task",
            deps.requirements_from_board_task,
        ),
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


def _run_handler(
    inputs: dict[str, Any],
    deps: BoardTaskExplainDependencies,
    *,
    sequence_plan: SequencePlan | None = None,
):
    return handle_board_task_explain(
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
        action_decision=None,
        decision=inputs["decision"],
        resolution=inputs["resolution"],
        requirement_history=inputs["requirement_history"],
        interaction_metadata={"source": "board_task_explain_handler_test"},
        sequence_plan=sequence_plan,
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


def test_handler_commits_approved_directive_consumes_board_task_and_preserves_focus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _explain_inputs(tmp_path, name="explain_handler_success")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls: dict[str, list[dict[str, Any]]] = {}
    original_board = inputs["lesson"].board_document.model_dump(mode="json")

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(inputs, _deps(calls))

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "chatbot_board_directed"
    assert collector.steps[0].reason == inputs["decision"].reason
    assert collector.steps[0].run_id is not None
    assert collector.steps[0].version_id is not None
    assert collector.steps[1].decision == "committed"
    assert collector.steps[1].commit_id == commit.id
    assert collector.steps[1].run_id == collector.steps[0].run_id
    assert collector.steps[1].version_id == collector.steps[0].version_id
    assert collector.steps[2].decision == "assembled"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.chatbot_message == "AI生成：这是目标内容的讲解。"
    assert response.resolved_focus == inputs["primary_focus"]
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet == inputs["board_task"]
    assert response.board_task_phase == "consumed"
    assert commit.label == "Board task explanation"
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["source"] == "board_task_explain_handler_test"
    assert commit.metadata["assistant_message"] == response.chatbot_message
    assert commit.metadata["assistant_message_source"] == "chatbot_board_directed"
    assert commit.metadata["board_explanation_directive"] == _directive_payload()
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["resolved_focus"] == inputs["primary_focus"].model_dump(mode="json")
    assert commit.metadata["focus_candidates"] == [
        inputs["primary_focus"].model_dump(mode="json"),
        inputs["next_focus"].model_dump(mode="json"),
    ]
    assert len(calls["explain"]) == 1
    assert calls["explain"][0]["requirements"].target_location == inputs["primary_focus"]
    assert calls["explain"][0]["requirements"].location_status == "resolved"
    target_excerpt = calls["explain"][0]["target_excerpt"]
    assert focus_context(inputs["primary_focus"]) in target_excerpt
    assert "第二个片段" in target_excerpt
    assert "第二段已有内容。" not in target_excerpt
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    board_task_events = inputs["store"].list_board_task_events(
        owner_user_id=TEST_USER_ID,
        lesson_id=inputs["lesson_id"],
    )
    assert [event["event_type"] for event in board_task_events] == ["created", "ready", "consumed"]
    assert json.loads(board_task_events[-1]["metadata_json"]) == {"commit_id": commit.id}


def test_handler_commits_non_approved_directive_without_consuming_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _explain_inputs(tmp_path, name="explain_handler_non_approved")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls: dict[str, list[dict[str, Any]]] = {}
    deps = _deps(calls)

    def generate_clarification(**kwargs):
        calls.setdefault("explain", []).append(kwargs)
        return (
            "AI生成：需要先确认目标。",
            "chatbot_board_directed_clarification",
            _directive_payload(
                status="needs_clarification",
                clarification_question="请确认讲解哪一段。",
            ),
        )

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            inputs,
            _with_deps(deps, generate_board_directed_explanation_message=generate_clarification),
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert _node_values(collector) == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert response.chatbot_message == "AI生成：需要先确认目标。"
    assert response.resolved_focus == inputs["primary_focus"]
    assert response.requirement_cleared is True
    assert response.active_board_task_sheet == inputs["board_task"]
    assert response.board_task_phase == "ready"
    assert commit.metadata["board_explanation_directive"]["status"] == "needs_clarification"
    assert commit.metadata["board_task_cleared"] is False
    board_task_events = inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"])
    assert [event["event_type"] for event in board_task_events] == ["created", "ready"]


def test_handler_empty_message_records_failure_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _explain_inputs(tmp_path, name="explain_handler_empty_failure")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    deps = _deps({})
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    def generate_empty(**kwargs):
        return "", "chatbot_board_directed_empty", _directive_payload()

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            inputs,
            _with_deps(deps, generate_board_directed_explanation_message=generate_empty),
        )

    assert _node_values(collector) == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert len(inputs["lesson"].history_graph.commits) == initial_commit_count
    assert response.chatbot_message == ""
    assert response.active_board_task_sheet == inputs["board_task"]
    assert response.board_task_phase == "ready"
    assert response.board_decision.reason == "Board-directed explanation failed because Chatbot returned empty."
    board_task_events = inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"])
    assert [event["event_type"] for event in board_task_events] == ["created", "ready", "execution_failed"]
    failure_metadata = json.loads(board_task_events[-1]["metadata_json"])
    assert failure_metadata["board_explanation_failed"] is True
    assert failure_metadata["board_task_cleared"] is False


@pytest.mark.parametrize("failure_point", ["generate", "save", "response"])
def test_handler_failure_ordering_preserves_response_trace_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    inputs = _explain_inputs(tmp_path, name=f"explain_handler_{failure_point}_failure")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    deps = _deps({})

    if failure_point == "generate":
        def fail_generate(**kwargs):
            raise RuntimeError("generate failed")

        deps = _with_deps(deps, generate_board_directed_explanation_message=fail_generate)
        expected_error = "generate failed"
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
            _run_handler(inputs, deps)

    nodes = _node_values(collector)
    if failure_point == "generate":
        assert nodes == []
    elif failure_point == "save":
        assert nodes == [NodeId.BOARD_EXPLAIN_DIRECTIVE.value]
    else:
        assert nodes == [NodeId.BOARD_EXPLAIN_DIRECTIVE.value, NodeId.BOARD_EXPLAIN_COMMIT.value]
        saved_lesson = inputs["store"].load_for_user(TEST_USER_ID).packages[0].lessons[-1]
        commit = saved_lesson.history_graph.commits[-1]
        assert collector.steps[-1].commit_id == commit.id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    if failure_point in {"generate", "save"}:
        assert inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"]) == []


def test_handler_rejects_sequence_plan_before_directive_generation(tmp_path: Path) -> None:
    inputs = _explain_inputs(tmp_path, name="explain_handler_sequence_guard")
    calls: dict[str, list[dict[str, Any]]] = {}
    sequence_plan = SequencePlan(
        mode=ATOMIC_EXPLANATION_SEQUENCE_MODE,
        items=[inputs["primary_focus"], inputs["next_focus"]],
        start_index=0,
        scope_label="已有板书 / 目标范围",
        reason="应交给 sequence-start handler。",
        planner_name="sequence_planner",
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="sequence plans"):
            _run_handler(inputs, _deps(calls), sequence_plan=sequence_plan)

    assert calls == {}
    assert inputs["board_task_history"].operations == []
    assert collector.steps == ()


def test_target_excerpt_keeps_candidate_body_outside_chatbot_boundary(tmp_path: Path) -> None:
    inputs = _explain_inputs(tmp_path, name="explain_handler_excerpt_boundary")

    target_excerpt = build_board_task_explanation_target_excerpt(
        board_task=inputs["board_task"],
        focus=inputs["primary_focus"],
        decision=inputs["decision"],
        resolution=inputs["resolution"],
    )

    assert "当前允许讲解的目标内容" in target_excerpt
    assert "第一段已有内容。" in target_excerpt
    assert "第二个片段" in target_excerpt
    assert "第二段已有内容。" not in target_excerpt
