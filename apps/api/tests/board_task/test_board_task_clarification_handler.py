from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardTaskRequirementSheet,
    ChatRequest,
    LearningClarificationStatus,
)
from app.services.board_task_decider import BoardTaskActionDecision
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.paths.board_task_clarification import (
    BoardTaskClarificationDependencies,
    handle_board_task_clarification,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_clarification_handler"


def _workspace_inputs():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 一段内容\n这是一段已有内容。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    assert lesson.learning_requirements is not None
    requirements = lesson.learning_requirements.model_copy(
        update={"learning_goal": "围绕已有板书处理一个具体任务。"}
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="已有板书任务",
        reason="本轮进入已有板书任务链路。",
        missing_items=[],
        can_start=True,
        next_question="",
        ready_for_board=True,
    )
    return workspace, package, lesson, requirements, clarification


def _board_task(*, progress: int = 75) -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        requested_action="explain",
        question_or_topic="解释用户指定的内容。",
        missing_items=[] if progress >= 100 else ["目标位置"],
        progress=progress,
        clarification_question="" if progress >= 100 else "你想处理板书中的哪一段？",
    )


def _board_task_history(lesson_id: str) -> BoardTaskHistoryRecorder:
    return BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=None,
    )


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=None,
    )


def _action_decision() -> BoardTaskActionDecision:
    return BoardTaskActionDecision(
        board_action="explain_target",
        reason="识别到已有板书讲解请求。",
        write_allowed=False,
        requires_resource_resolution=False,
        requires_target_resolution=True,
        decision_notes=["direct-test"],
    )


def _board_task_metadata(
    *,
    board_task: BoardTaskRequirementSheet | None,
    stamp: BoardTaskHistoryStamp | None,
    route: str | None = None,
    decision: dict[str, object] | None = None,
    cleared: bool = False,
) -> dict[str, object]:
    return {
        "board_task_sheet": board_task.model_dump(mode="json") if board_task else None,
        "board_task_run_id": stamp.run_id if stamp else None,
        "board_task_version_id": stamp.version_id if stamp else None,
        "board_task_phase": stamp.phase if stamp else None,
        "board_task_route": route,
        "board_task_decision": decision,
        "board_task_cleared": cleared,
    }


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _deps(
    calls: dict[str, Any],
    *,
    collector: WorkflowTraceCollector | None = None,
) -> BoardTaskClarificationDependencies:
    def generate_message(**kwargs):
        calls.setdefault("generate", []).append(kwargs)
        return "请先指出要处理的板书位置。", "chatbot_board_task_clarification"

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)
        if collector is not None:
            calls["trace_before_save"] = _node_values(collector)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        return SimpleNamespace(
            chatbot_message=kwargs["chatbot_message"],
            board_decision=kwargs["board_decision"],
            board_task_history=kwargs["board_task_history"],
        )

    return BoardTaskClarificationDependencies(
        generate_board_task_clarification_message=generate_message,
        board_task_metadata=_board_task_metadata,
        commit_operations=commit_operations,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def _failing_deps(calls: dict[str, Any]) -> BoardTaskClarificationDependencies:
    def fail(name: str):
        def _inner(**kwargs):
            calls.setdefault(name, []).append(kwargs)
            raise AssertionError(f"{name} should not be called")

        return _inner

    return BoardTaskClarificationDependencies(
        generate_board_task_clarification_message=fail("generate"),
        board_task_metadata=fail("metadata"),
        commit_operations=fail("commit"),
        save_workspace_for_user=fail("save"),
        build_response=fail("response"),
    )


def test_handler_commits_saves_and_builds_response_for_incomplete_board_task() -> None:
    workspace, package, lesson, requirements, learning_clarification = _workspace_inputs()
    board_task = _board_task()
    board_task_history = _board_task_history(lesson.id)
    stamp = board_task_history.record_update(sheet=board_task)
    requirement_history = _requirement_history(lesson.id)
    request = ChatRequest(message="请解释一下", interaction_mode="ask")
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_board_task_clarification(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=[],
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=stamp,
            action_decision=_action_decision(),
            requirement_history=requirement_history,
            source_interaction_metadata={"source_interaction_route": "new_task"},
            deps=_deps(calls, collector=collector),
        )

    commit = lesson.history_graph.commits[-1]
    assert response.chatbot_message == "请先指出要处理的板书位置。"
    assert response.board_decision.action == "no_change"
    assert response.board_task_history is board_task_history
    assert lesson.board_task_requirements == board_task
    assert calls["generate"][0]["context"] == board_task.clarification_question
    assert calls["generate"][0]["board_task"] == board_task
    assert calls["save"][0]["user_id"] == TEST_USER_ID
    assert calls["save"][0]["workspace"] is workspace
    assert calls["save"][0]["requirement_history"] is requirement_history
    assert calls["save"][0]["board_task_history"] is board_task_history
    assert calls["trace_before_save"] == [NodeId.BOARD_TASK_COLLECT.value]
    assert calls["response"][0]["board_task_history"] is board_task_history
    assert commit.label == "Board task clarification"
    assert commit.message == "Asked for a missing field in the existing-board task sheet"
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot_board_task_clarification"
    assert commit.metadata["source_interaction_route"] == "new_task"
    assert commit.metadata["decision_trace"]["role_executed"] == "board_task_manager"
    assert commit.metadata["decision_trace"]["selected_board_action"] == "explain_target"
    assert commit.metadata["decision_trace"]["document_changed"] is False
    assert commit.metadata["board_task_sheet"] == board_task.model_dump(mode="json")
    assert commit.metadata["board_task_run_id"] == stamp.run_id
    assert commit.metadata["board_task_version_id"] == stamp.version_id
    assert commit.metadata["board_task_phase"] == "collecting"
    assert commit.metadata["board_task_route"] == "clarify_location"
    assert commit.metadata["board_task_cleared"] is False
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TASK_CLARIFY_FIELDS.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].run_id == stamp.run_id
    assert collector.steps[1].commit_id == commit.id


def test_handler_saves_commit_before_response_build_failure() -> None:
    workspace, package, lesson, requirements, learning_clarification = _workspace_inputs()
    board_task = _board_task()
    board_task_history = _board_task_history(lesson.id)
    stamp = board_task_history.record_update(sheet=board_task)
    requirement_history = _requirement_history(lesson.id)
    calls: dict[str, Any] = {}

    def raise_on_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        raise RuntimeError("response failed")

    with bind_workflow_trace_collector() as collector:
        deps = _deps(calls, collector=collector)
        deps = BoardTaskClarificationDependencies(
            generate_board_task_clarification_message=deps.generate_board_task_clarification_message,
            board_task_metadata=deps.board_task_metadata,
            commit_operations=deps.commit_operations,
            save_workspace_for_user=deps.save_workspace_for_user,
            build_response=raise_on_response,
        )
        with pytest.raises(RuntimeError, match="response failed"):
            handle_board_task_clarification(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="请解释一下"),
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=[],
                board_task=board_task,
                board_task_history=board_task_history,
                board_task_stamp=stamp,
                action_decision=None,
                requirement_history=requirement_history,
                deps=deps,
            )

    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert lesson.history_graph.commits[-1].metadata["board_task_run_id"] == stamp.run_id
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TASK_CLARIFY_FIELDS.value,
    ]


def test_handler_rejects_ready_board_task_before_side_effects() -> None:
    workspace, package, lesson, requirements, learning_clarification = _workspace_inputs()
    board_task = _board_task(progress=100)
    calls: dict[str, Any] = {}
    initial_commit_count = len(lesson.history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="requires an incomplete board task"):
            handle_board_task_clarification(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="请解释一下"),
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=[],
                board_task=board_task,
                board_task_history=_board_task_history(lesson.id),
                board_task_stamp=BoardTaskHistoryStamp(run_id="run", version_id="version", phase="ready"),
                action_decision=None,
                requirement_history=_requirement_history(lesson.id),
                deps=_failing_deps(calls),
            )

    assert calls == {}
    assert lesson.board_task_requirements is None
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert _node_values(collector) == []


def test_handler_requires_recorded_board_task_version_before_side_effects() -> None:
    workspace, package, lesson, requirements, learning_clarification = _workspace_inputs()
    board_task = _board_task()
    calls: dict[str, Any] = {}
    initial_commit_count = len(lesson.history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="requires a recorded board task version"):
            handle_board_task_clarification(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="请解释一下"),
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=[],
                board_task=board_task,
                board_task_history=_board_task_history(lesson.id),
                board_task_stamp=BoardTaskHistoryStamp(),
                action_decision=None,
                requirement_history=_requirement_history(lesson.id),
                deps=_failing_deps(calls),
            )

    assert calls == {}
    assert lesson.board_task_requirements is None
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert _node_values(collector) == []
