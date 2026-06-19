from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    InteractionSession,
)
from app.services import chatbot as chatbot_module
from app.services.board_task_history import BoardTaskHistoryStamp
from app.services.chat.paths.interaction_start_success import (
    InteractionStartSuccessDependencies,
    handle_interaction_start_success,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector, record_workflow_step


TEST_USER_ID = "user_interaction_start_success"


def _workspace_inputs():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 原文\n\n## 第一段\n目标原文内容\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    requirements = lesson.learning_requirements.model_copy(
        update={"learning_goal": "围绕现有板书启动规则互动。"}
    )
    lesson.learning_requirements = requirements
    learning_clarification = chatbot_module._latest_learning_clarification(
        lesson,
        requirements=requirements,
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["原文", "第一段"],
        excerpt="目标原文内容",
        confidence=0.95,
        reason="测试已解析位置。",
        display_label="第一段",
    )
    session = InteractionSession(
        status="active",
        rule_text="按规则互动。",
        interaction_goal="围绕选中原文进行规则互动。",
        target_focus=focus,
        reference_context="目标原文内容",
        compliant_input_rule="用户按规则输入。",
        expected_user_behavior="用户按规则输入。",
        assistant_behavior="Chatbot 参考原文回应。",
    )
    focus_resolution = FocusResolution(
        focus=focus,
        candidates=[focus],
        status="resolved",
        question="",
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "request": ChatRequest(
            message="开始规则互动",
            selection={"kind": "board", "excerpt": "目标原文内容", "lesson_id": lesson.id},
        ),
        "requirements": requirements,
        "learning_clarification": learning_clarification,
        "requirement_history": requirement_history,
        "session": session,
        "focus_resolution": focus_resolution,
    }


def _board_task() -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="选中内容",
        location_status="selected",
        requested_action="chat",
        question_or_topic="围绕选中原文进行规则互动。",
        progress=100,
        missing_items=[],
    )


class _BoardTaskHistoryStub:
    def __init__(self, consumed_stamp: BoardTaskHistoryStamp | None = None, *, fail: bool = False) -> None:
        self.consumed_stamp = consumed_stamp or BoardTaskHistoryStamp(
            run_id="run-source",
            version_id="ver-source",
            phase="consumed",
        )
        self.fail = fail
        self.consume_commit_ids: list[str] = []

    def consume(self, *, commit_id: str, change_summary: str | None = None) -> BoardTaskHistoryStamp:
        self.consume_commit_ids.append(commit_id)
        if self.fail:
            raise RuntimeError("consume failed")
        return self.consumed_stamp


def _deps(calls: dict[str, Any]) -> InteractionStartSuccessDependencies:
    def generate_interaction_message(**kwargs):
        calls.setdefault("generate", []).append(kwargs)
        assert kwargs["decision"] is None
        return "AI生成：已按你的规则开始互动。", "chatbot_interaction", {"kind": "directive"}

    def clear_task_requirements(lesson):
        calls.setdefault("clear", []).append(lesson.id)
        lesson.learning_requirements = None

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        return SimpleNamespace(
            active_interaction_session=kwargs["lesson"].active_interaction_session,
            board_task_stamp=kwargs.get("board_task_stamp"),
            focus_candidates=kwargs.get("focus_candidates"),
            requirement_cleared=kwargs.get("requirement_cleared"),
        )

    return InteractionStartSuccessDependencies(
        generate_interaction_message=generate_interaction_message,
        clear_task_requirements=clear_task_requirements,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def test_handler_starts_session_without_board_task_or_resolve_node() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_interaction_start_success(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=inputs["request"],
            requirements=inputs["requirements"],
            learning_clarification=inputs["learning_clarification"],
            resources=[],
            resolved_session=inputs["session"],
            focus_resolution=inputs["focus_resolution"],
            requirement_history=inputs["requirement_history"],
            source_interaction_metadata={},
            deps=_deps(calls),
        )

    commit = inputs["lesson"].history_graph.commits[-1]
    assert response.active_interaction_session == inputs["lesson"].active_interaction_session
    assert response.board_task_stamp is None
    assert response.focus_candidates == inputs["focus_resolution"].candidates
    assert response.requirement_cleared is True
    assert len(calls["generate"]) == 1
    assert calls["generate"][0]["session"] == inputs["lesson"].active_interaction_session
    assert len(calls["clear"]) == 1
    assert len(calls["save"]) == 1
    assert commit.label == "Interaction session start"
    assert commit.message == "Started a rule-based interaction session"
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["board_explanation_directive"] == {"kind": "directive"}
    assert commit.metadata["selection"] == inputs["request"].selection.model_dump(mode="json")
    assert commit.metadata["interaction_session_before"] is None
    assert commit.metadata["interaction_session_after"]["interaction_goal"] == inputs["session"].interaction_goal
    assert commit.metadata["active_interaction_session_after"]["interaction_goal"] == inputs["session"].interaction_goal
    assert NodeId.INTERACTION_START_RESOLVE.value not in _node_values(collector)
    assert _node_values(collector) == [
        NodeId.INTERACTION_START_PERSIST.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].run_id is None
    assert collector.steps[0].version_id is None
    assert collector.steps[0].commit_id == commit.id


def test_handler_links_and_consumes_board_task_after_commit() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    board_task = _board_task()
    source_stamp = BoardTaskHistoryStamp(run_id="run-source", version_id="ver-source", phase="ready")
    consumed_stamp = BoardTaskHistoryStamp(run_id="run-source", version_id="ver-source", phase="consumed")
    board_task_history = _BoardTaskHistoryStub(consumed_stamp)
    inputs["lesson"].board_task_requirements = board_task

    with bind_workflow_trace_collector() as collector:
        record_workflow_step(NodeId.INTERACTION_START_RESOLVE, decision="resolved")
        response = handle_interaction_start_success(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=inputs["request"],
            requirements=inputs["requirements"],
            learning_clarification=inputs["learning_clarification"],
            resources=[],
            resolved_session=inputs["session"],
            focus_resolution=inputs["focus_resolution"],
            requirement_history=inputs["requirement_history"],
            source_interaction_metadata={"source": "interaction_start_test"},
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=source_stamp,
            board_task_decision_metadata={"route": "chat"},
            deps=_deps(calls),
        )

    commit = inputs["lesson"].history_graph.commits[-1]
    session = inputs["lesson"].active_interaction_session
    assert session is not None
    assert session.source_board_task_run_id == "run-source"
    assert session.source_board_task_version_id == "ver-source"
    assert session.source_board_task_route == "chat"
    assert inputs["lesson"].board_task_requirements is None
    assert board_task_history.consume_commit_ids == [commit.id]
    assert response.board_task_stamp == consumed_stamp
    assert commit.metadata["source"] == "interaction_start_test"
    assert commit.metadata["board_task_run_id"] == "run-source"
    assert commit.metadata["board_task_version_id"] == "ver-source"
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_task_route"] == "chat"
    assert commit.metadata["board_task_decision"] == {"route": "chat"}
    assert commit.metadata["board_task_cleared"] is True
    assert _node_values(collector) == [
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.INTERACTION_START_PERSIST.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[1].run_id == "run-source"
    assert collector.steps[1].version_id == "ver-source"
    assert collector.steps[1].commit_id == commit.id


def test_handler_consume_failure_does_not_record_persist_or_response() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    board_task_history = _BoardTaskHistoryStub(fail=True)
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        record_workflow_step(NodeId.INTERACTION_START_RESOLVE, decision="resolved")
        with pytest.raises(RuntimeError, match="consume failed"):
            handle_interaction_start_success(
                workspace=inputs["workspace"],
                package=inputs["package"],
                lesson=inputs["lesson"],
                user_id=TEST_USER_ID,
                request=inputs["request"],
                requirements=inputs["requirements"],
                learning_clarification=inputs["learning_clarification"],
                resources=[],
                resolved_session=inputs["session"],
                focus_resolution=inputs["focus_resolution"],
                requirement_history=inputs["requirement_history"],
                source_interaction_metadata={},
                board_task=_board_task(),
                board_task_history=board_task_history,
                board_task_stamp=BoardTaskHistoryStamp(run_id="run-source", version_id="ver-source", phase="ready"),
                deps=_deps(calls),
            )

    assert len(inputs["lesson"].history_graph.commits) == initial_commit_count + 1
    assert board_task_history.consume_commit_ids == [inputs["lesson"].history_graph.commits[-1].id]
    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
    assert "save" not in calls
    assert "response" not in calls
