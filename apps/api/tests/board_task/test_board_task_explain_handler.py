from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    SelectionRef,
)
from app.services import chatbot as chatbot_module
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_explain import (
    BoardTaskExplainDependencies,
    handle_board_task_explain_terminal,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_explain_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("讲解处理器测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n第一句已有内容。第二句已有内容。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="已有板书任务",
        learning_goal="围绕已有板书完成用户指定动作",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100,
        label="可执行",
        reason="已有板书任务已经完整。",
        ready_for_board=False,
        summary="已有板书任务已经完整。",
    )


def _board_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="目标范围",
        location_status="selected",
        requested_action="explain",
        question_or_topic="解释目标范围",
        confirmation_status="none",
        progress=100,
        missing_items=[],
    )


def _focus(lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-target",
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="第一句已有内容。第二句已有内容。",
        confidence=0.95,
        reason="选区已经定位到目标范围。",
        display_label="目标范围",
    )


def _other_focus(lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-other",
        kind="paragraph",
        heading_path=["已有板书", "其他范围"],
        excerpt="其他候选内容。",
        confidence=0.72,
        reason="同一任务中的后续候选。",
        display_label="其他范围",
    )


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="第一句已有内容。第二句已有内容。",
        heading_path=["已有板书", "目标范围"],
    )


def _route_decision(lesson) -> BoardTaskRouteDecision:
    focus = _focus(lesson)
    return BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=focus,
        candidate_focuses=[focus, _other_focus(lesson)],
        reason="已定位可讲解的板书内容。",
    )


def _resolution(lesson) -> FocusResolution:
    focus = _focus(lesson)
    return FocusResolution(
        focus=focus,
        candidates=[focus, _other_focus(lesson)],
        status="selected",
        question="",
    )


def _histories(lesson):
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    return requirement_history, board_task_history


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _make_deps(
    *,
    lesson,
    directive_result: tuple[str, str, dict[str, object] | None] | None = None,
    directive_error: Exception | None = None,
    save_error: Exception | None = None,
    response_error: Exception | None = None,
) -> tuple[BoardTaskExplainDependencies, dict[str, Any]]:
    calls: list[str] = []
    commit_metadata_at_creation: dict[str, Any] = {}
    directive_call: dict[str, Any] = {}
    save_snapshots: list[dict[str, Any]] = []

    def _directive(**kwargs):
        calls.append("directive")
        directive_call.clear()
        directive_call.update(kwargs)
        if directive_error is not None:
            raise directive_error
        return directive_result or (
            "AI生成：这是目标内容的讲解。",
            "chatbot_board_directed",
            {
                "status": "approved",
                "target_excerpt": kwargs["target_excerpt"],
                "teaching_instruction": "只依据目标摘录讲解。",
            },
        )

    def _commit(*args, **kwargs):
        calls.append("commit")
        result = commit_operations(*args, **kwargs)
        commit_metadata_at_creation.clear()
        commit_metadata_at_creation.update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    def _normalize(package):
        calls.append("normalize")
        chatbot_module.workspace_state.normalize_package_state(package)

    def _save(**kwargs):
        calls.append("save")
        if save_error is not None:
            raise save_error
        board_task_history = kwargs["board_task_history"]
        save_snapshots.append(
            {
                "latest_commit_id": lesson.history_graph.commits[-1].id,
                "latest_label": lesson.history_graph.commits[-1].label,
                "operations": [dict(operation) for operation in board_task_history.operations],
            }
        )

    def _response(**kwargs):
        calls.append("response")
        if response_error is not None:
            raise response_error
        return chatbot_module._response(**kwargs)

    deps = BoardTaskExplainDependencies(
        generate_board_directed_explanation_message=_directive,
        requirements_from_board_task=chatbot_module._requirements_from_board_task,
        board_search_evidence_metadata=chatbot_module._board_search_evidence_metadata,
        decision_trace_metadata=chatbot_module.decision_trace_metadata,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
        commit_operations=_commit,
        build_response=_response,
    )
    return deps, {
        "calls": calls,
        "commit_metadata_at_creation": commit_metadata_at_creation,
        "directive_call": directive_call,
        "save_snapshots": save_snapshots,
    }


def _run_handler(
    *,
    workspace,
    package,
    lesson,
    deps: BoardTaskExplainDependencies,
    board_task_history: BoardTaskHistoryRecorder,
):
    requirement_history, _ = _histories(lesson)
    board_task = _board_task()
    lesson.board_task_requirements = board_task
    return handle_board_task_explain_terminal(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="请解释这段是什么意思", selection=_selection()),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        route_decision=_route_decision(lesson),
        action_decision=None,
        resolution=_resolution(lesson),
        source_interaction_metadata={"source_marker": "unit"},
        deps=deps,
    )


def test_direct_explain_success_preserves_directive_commit_consume_save_and_response_order() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson)

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            deps=deps,
            board_task_history=board_task_history,
        )

    commit = lesson.history_graph.commits[-1]
    assert _node_values(collector) == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert captured["calls"] == ["directive", "commit", "normalize", "save", "response"]
    assert commit.metadata == captured["commit_metadata_at_creation"]
    assert commit.label == "Board task explanation"
    assert commit.metadata["assistant_message"] == "AI生成：这是目标内容的讲解。"
    assert commit.metadata["assistant_message_source"] == "chatbot_board_directed"
    assert commit.metadata["board_explanation_directive"]["status"] == "approved"
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["decision_trace"]["role_executed"] == "chatbot_board_directed"
    assert commit.metadata["decision_trace"]["document_changed"] is False
    assert commit.metadata["source_marker"] == "unit"
    assert "当前允许讲解的目标内容" in captured["directive_call"]["target_excerpt"]
    assert "第一句已有内容。第二句已有内容。" in captured["directive_call"]["target_excerpt"]
    assert "同一任务中还存在的后续候选目标" in captured["directive_call"]["target_excerpt"]
    assert response.chatbot_message == "AI生成：这是目标内容的讲解。"
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    assert response.board_task_sheet == _board_task()
    saved_operations = captured["save_snapshots"][0]["operations"]
    assert captured["save_snapshots"][0]["latest_label"] == "Board task explanation"
    assert any(
        operation.get("type") == "update_board_task_run"
        and operation.get("status") == "consumed"
        and operation.get("consumed_commit_id") == commit.id
        for operation in saved_operations
    )
    assert collector.steps[-2].decision == "committed"
    assert collector.steps[-2].commit_id == commit.id
    assert collector.steps[-2].run_id == collector.steps[-3].run_id
    assert collector.steps[-2].version_id == collector.steps[-3].version_id


def test_direct_explain_empty_message_records_failure_without_commit() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(
        lesson=lesson,
        directive_result=("", "chatbot_board_directed_empty", {"status": "approved"}),
    )

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            deps=deps,
            board_task_history=board_task_history,
        )

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert lesson.history_graph.commits[-1].id == initial_commit_id
    assert captured["calls"] == ["directive", "normalize", "save", "response"]
    assert response.chatbot_message == ""
    assert response.active_board_task_sheet is not None
    assert response.board_task_phase == "ready"
    saved_operations = captured["save_snapshots"][0]["operations"]
    failure_metadata = [
        json.loads(operation["metadata_json"])
        for operation in saved_operations
        if operation.get("type") == "insert_board_task_event" and operation.get("event_type") == "execution_failed"
    ][-1]
    assert failure_metadata["assistant_message_source"] == "chatbot_board_directed_empty"
    assert failure_metadata["board_explanation_failed"] is True
    assert failure_metadata["board_task_route"] == "explain"
    assert failure_metadata["board_task_cleared"] is False
    assert failure_metadata["board_explanation_directive"] == {"status": "approved"}
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in nodes


def test_direct_explain_directive_generation_failure_records_no_persistence_or_success_nodes() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, directive_error=RuntimeError("directive failed"))

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="directive failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                deps=deps,
                board_task_history=board_task_history,
            )

    assert _node_values(collector) == []
    assert lesson.history_graph.commits[-1].id == initial_commit_id
    assert board_task_history.operations == []
    assert captured["calls"] == ["directive"]


def test_direct_explain_save_failure_does_not_record_commit_or_response_trace() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, save_error=RuntimeError("save failed"))

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                deps=deps,
                board_task_history=board_task_history,
            )

    assert _node_values(collector) == [NodeId.BOARD_EXPLAIN_DIRECTIVE.value]
    assert captured["calls"] == ["directive", "commit", "normalize", "save"]
    assert lesson.history_graph.commits[-1].label == "Board task explanation"
    assert any(
        operation.get("type") == "update_board_task_run" and operation.get("status") == "consumed"
        for operation in board_task_history.operations
    )
    assert NodeId.BOARD_EXPLAIN_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)

def test_direct_explain_response_failure_keeps_consumed_commit_without_response_trace() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, response_error=RuntimeError("response failed"))

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                deps=deps,
                board_task_history=board_task_history,
            )

    commit = lesson.history_graph.commits[-1]
    assert _node_values(collector) == [
        NodeId.BOARD_EXPLAIN_DIRECTIVE.value,
        NodeId.BOARD_EXPLAIN_COMMIT.value,
    ]
    assert captured["calls"] == ["directive", "commit", "normalize", "save", "response"]
    assert captured["save_snapshots"][0]["latest_label"] == "Board task explanation"
    assert collector.steps[-1].commit_id == commit.id
    assert any(
        operation.get("type") == "update_board_task_run"
        and operation.get("status") == "consumed"
        and operation.get("consumed_commit_id") == commit.id
        for operation in captured["save_snapshots"][0]["operations"]
    )
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
