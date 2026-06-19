from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.models import BoardFocusRef, ChatRequest, InteractionSession
from app.services import chatbot as chatbot_module
from app.services.chat.paths.interaction_sequence_continue import (
    InteractionSequenceContinueDependencies,
    SequenceContinueOutcome,
    handle_interaction_sequence_continue,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.segment_resolver import focus_context
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_sequence_continue"


def _workspace_inputs():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 第一段\n第一段已有内容。\n\n## 第二段\n第二段已有内容。\n",
        ),
    )
    first_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书", "第一段"],
        excerpt="第一段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解当前单元。",
        display_label="第一段",
    )
    second_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书", "第二段"],
        excerpt="第二段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解下一单元。",
        display_label="第二段",
    )
    session = InteractionSession(
        status="active",
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=first_focus,
        reference_context=first_focus.excerpt,
        compliant_input_rule="用户确认继续。",
        expected_user_behavior="用户确认继续。",
        assistant_behavior="继续讲解下一个单元。",
        progress_note="准备讲解第 1 个单元。",
        pause_reason="等待用户确认。",
        turn_count=2,
        sequence_items=[first_focus, second_focus],
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    lesson.active_interaction_session = session
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    requirements = lesson.learning_requirements
    learning_clarification = chatbot_module._latest_learning_clarification(
        lesson,
        requirements=requirements,
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "request": ChatRequest(message="继续", interaction_mode="ask"),
        "requirements": requirements,
        "learning_clarification": learning_clarification,
        "requirement_history": requirement_history,
        "session": session,
        "first_focus": first_focus,
        "second_focus": second_focus,
    }


def _deps(calls: dict[str, Any]) -> InteractionSequenceContinueDependencies:
    def generate_board_directed_explanation_message(**kwargs):
        calls.setdefault("explain", []).append(kwargs)
        sequence_index = kwargs["interaction_context"]["sequence_index"]
        return f"AI生成：继续讲解 {sequence_index}。", "chatbot_interaction", {"directive": "sequence"}

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        return SimpleNamespace(
            active_interaction_session=kwargs["lesson"].active_interaction_session,
            chatbot_message=kwargs["chatbot_message"],
            interaction_decision=kwargs["interaction_decision"],
            resolved_focus=kwargs["resolved_focus"],
        )

    return InteractionSequenceContinueDependencies(
        generate_board_directed_explanation_message=generate_board_directed_explanation_message,
        task_metadata=chatbot_module._task_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


@pytest.mark.parametrize(
    ("outcome", "focus_key", "next_index", "expected_index", "label", "message", "reason", "user_intent"),
    [
        (
            "follow_up_current",
            "first_focus",
            None,
            0,
            "Section explanation follow-up",
            "Answered a follow-up within the current sequential section",
            "用户追问当前小节，继续围绕当前小节讲解。",
            "追问当前小节",
        ),
        (
            "advance",
            "second_focus",
            1,
            1,
            "Section explanation turn",
            "Continued a sequential section explanation session",
            "用户确认当前小节后继续下一个小节。",
            "继续顺序讲解",
        ),
    ],
)
def test_handler_continues_sequence_and_preserves_metadata(
    outcome: SequenceContinueOutcome,
    focus_key: str,
    next_index: int | None,
    expected_index: int,
    label: str,
    message: str,
    reason: str,
    user_intent: str,
) -> None:
    inputs = _workspace_inputs()
    focus = inputs[focus_key]
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_interaction_sequence_continue(
            outcome=outcome,
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=inputs["request"],
            requirements=inputs["requirements"],
            learning_clarification=inputs["learning_clarification"],
            resources=[],
            session_before=inputs["session"],
            focus=focus,
            unit_label="小节",
            requirement_history=inputs["requirement_history"],
            deps=_deps(calls),
            next_index=next_index,
        )

    session_after = inputs["lesson"].active_interaction_session
    commit = inputs["lesson"].history_graph.commits[-1]
    metadata = commit.metadata
    assert session_after is not None
    assert session_after.sequence_index == expected_index
    assert session_after.target_focus == focus
    assert session_after.reference_context == focus_context(focus)
    assert session_after.turn_count == inputs["session"].turn_count + 1
    assert session_after.status == "active"
    assert session_after.pause_reason == ""
    assert response.active_interaction_session == session_after
    assert response.chatbot_message == f"AI生成：继续讲解 {expected_index}。"
    assert response.interaction_decision.route == "continue_rule"
    assert response.interaction_decision.reason == reason
    assert response.interaction_decision.user_intent == user_intent
    assert response.resolved_focus == focus
    assert len(calls["explain"]) == 1
    assert calls["explain"][0]["requirements"].target_location == focus
    assert calls["explain"][0]["requirements"].location_status == "resolved"
    assert calls["explain"][0]["target_excerpt"] == focus_context(focus)
    assert f"{expected_index + 1}/2" in calls["explain"][0]["request"].message
    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert commit.label == label
    assert commit.message == message
    assert metadata["kind"] == "interaction_flow"
    assert metadata["user_message"] == inputs["request"].message
    assert metadata["assistant_message_source"] == "chatbot_interaction"
    assert metadata["board_explanation_directive"] == {"directive": "sequence"}
    assert metadata["resolved_focus"] == focus.model_dump(mode="json")
    assert metadata["interaction_session_before"] == inputs["session"].model_dump(mode="json")
    assert metadata["interaction_session_after"] == session_after.model_dump(mode="json")
    assert _node_values(collector) == [
        NodeId.INTERACTION_CONTINUE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "continue_rule"
    assert collector.steps[1].commit_id == commit.id


@pytest.mark.parametrize(
    ("case", "outcome", "configure", "error"),
    [
        (
            "unsupported_outcome",
            cast(SequenceContinueOutcome, "completed"),
            lambda inputs: (inputs["session"], inputs["first_focus"], None),
            "unsupported",
        ),
        (
            "missing_session_before",
            "follow_up_current",
            lambda inputs: (None, inputs["first_focus"], None),
            "previous interaction session",
        ),
        (
            "missing_active_session",
            "follow_up_current",
            lambda inputs: _clear_active_session(inputs),
            "active session",
        ),
        (
            "advance_missing_next_index",
            "advance",
            lambda inputs: (inputs["session"], inputs["second_focus"], None),
            "next_index",
        ),
        (
            "advance_mismatched_focus",
            "advance",
            lambda inputs: (inputs["session"], inputs["first_focus"], 1),
            "focus",
        ),
    ],
)
def test_handler_validation_failures_do_not_mutate_or_commit(
    case: str,
    outcome: SequenceContinueOutcome,
    configure,
    error: str,
) -> None:
    inputs = _workspace_inputs()
    lesson = inputs["lesson"]
    original_commit_count = len(lesson.history_graph.commits)
    session_before, focus, next_index = configure(inputs)
    active_session_at_entry = lesson.active_interaction_session

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match=error):
            handle_interaction_sequence_continue(
                outcome=outcome,
                workspace=inputs["workspace"],
                package=inputs["package"],
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=inputs["request"],
                requirements=inputs["requirements"],
                learning_clarification=inputs["learning_clarification"],
                resources=[],
                session_before=session_before,
                focus=focus,
                unit_label="小节",
                requirement_history=inputs["requirement_history"],
                deps=_deps({}),
                next_index=next_index,
            )

    assert case
    assert lesson.active_interaction_session == active_session_at_entry
    assert len(lesson.history_graph.commits) == original_commit_count
    assert collector.steps == ()


def test_handler_generator_failure_does_not_record_continue_or_commit() -> None:
    inputs = _workspace_inputs()
    lesson = inputs["lesson"]
    original_commit_count = len(lesson.history_graph.commits)

    def _failing_deps(calls: dict[str, Any]) -> InteractionSequenceContinueDependencies:
        deps = _deps(calls)

        def generate_board_directed_explanation_message(**kwargs):
            calls.setdefault("explain", []).append(kwargs)
            raise RuntimeError("boom")

        return InteractionSequenceContinueDependencies(
            generate_board_directed_explanation_message=generate_board_directed_explanation_message,
            task_metadata=deps.task_metadata,
            save_workspace_for_user=deps.save_workspace_for_user,
            build_response=deps.build_response,
        )

    calls: dict[str, Any] = {}
    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="boom"):
            handle_interaction_sequence_continue(
                outcome="follow_up_current",
                workspace=inputs["workspace"],
                package=inputs["package"],
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=inputs["request"],
                requirements=inputs["requirements"],
                learning_clarification=inputs["learning_clarification"],
                resources=[],
                session_before=inputs["session"],
                focus=inputs["first_focus"],
                unit_label="小节",
                requirement_history=inputs["requirement_history"],
                deps=_failing_deps(calls),
            )

    assert len(calls["explain"]) == 1
    assert lesson.active_interaction_session is not None
    assert lesson.active_interaction_session.turn_count == inputs["session"].turn_count + 1
    assert len(lesson.history_graph.commits) == original_commit_count
    assert collector.steps == ()


def _clear_active_session(inputs):
    inputs["lesson"].active_interaction_session = None
    return inputs["session"], inputs["first_focus"], None
