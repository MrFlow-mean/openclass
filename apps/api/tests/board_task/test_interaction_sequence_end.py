from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.models import BoardFocusRef, ChatRequest, InteractionSession
from app.services import chatbot as chatbot_module
from app.services.chat.paths.interaction_sequence_end import (
    InteractionSequenceEndDependencies,
    SequenceEndOutcome,
    handle_interaction_sequence_end,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_sequence_end"


def _workspace_inputs(*, final_item: bool = False):
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
    sequence_items = [first_focus] if final_item else [first_focus, second_focus]
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
        sequence_items=sequence_items,
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
    }


def _deps(calls: dict[str, Any]) -> InteractionSequenceEndDependencies:
    def generate_sequence_end_message(**kwargs):
        calls.setdefault("generate", []).append(kwargs)
        calls.setdefault("active_session_at_generate", []).append(kwargs["lesson"].active_interaction_session)
        return "AI生成：顺序讲解结束。", "chatbot_interaction"

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        return SimpleNamespace(
            active_interaction_session=kwargs["lesson"].active_interaction_session,
            chatbot_message=kwargs["chatbot_message"],
            interaction_decision=kwargs["interaction_decision"],
        )

    return InteractionSequenceEndDependencies(
        generate_sequence_end_message=generate_sequence_end_message,
        task_metadata=chatbot_module._task_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


@pytest.mark.parametrize(
    ("outcome", "unit_label", "label", "message", "reason", "user_intent"),
    [
        (
            "exit_requested",
            None,
            "Section explanation session ended",
            "Ended a sequential section explanation session",
            "用户结束当前顺序讲解。",
            "结束顺序讲解",
        ),
        (
            "completed",
            "单元",
            "Section explanation session completed",
            "Completed a sequential section explanation session",
            "顺序讲解已经完成。",
            "确认最后一个单元无问题",
        ),
    ],
)
def test_handler_ends_sequence_session_and_preserves_commit_metadata(
    outcome: SequenceEndOutcome,
    unit_label: str | None,
    label: str,
    message: str,
    reason: str,
    user_intent: str,
) -> None:
    inputs = _workspace_inputs(final_item=outcome == "completed")
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_interaction_sequence_end(
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
            requirement_history=inputs["requirement_history"],
            unit_label=unit_label,
            deps=_deps(calls),
        )

    commit = inputs["lesson"].history_graph.commits[-1]
    metadata = commit.metadata
    assert inputs["lesson"].active_interaction_session is None
    assert response.active_interaction_session is None
    assert response.chatbot_message == "AI生成：顺序讲解结束。"
    assert response.interaction_decision.route == "exit_rule"
    assert response.interaction_decision.reason == reason
    assert response.interaction_decision.user_intent == user_intent
    assert len(calls["generate"]) == 1
    assert calls["generate"][0]["session"] == inputs["session"]
    assert calls["active_session_at_generate"] == [None]
    assert len(calls["save"]) == 1
    assert len(calls["response"]) == 1
    assert commit.label == label
    assert commit.message == message
    assert metadata["kind"] == "interaction_flow"
    assert metadata["user_message"] == inputs["request"].message
    assert metadata["assistant_message"] == "AI生成：顺序讲解结束。"
    assert metadata["assistant_message_source"] == "chatbot_interaction"
    assert metadata["interaction_session_before"] == inputs["session"].model_dump(mode="json")
    assert metadata["interaction_session_after"] is None
    assert metadata["active_interaction_session_after"] is None
    assert _node_values(collector) == [
        NodeId.INTERACTION_EXIT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "exit_rule"
    assert collector.steps[1].commit_id == commit.id


@pytest.mark.parametrize(
    ("case", "outcome", "configure", "error"),
    [
        (
            "unsupported_outcome",
            cast(SequenceEndOutcome, "advance"),
            lambda inputs: inputs["session"],
            "unsupported",
        ),
        (
            "missing_session_before",
            "exit_requested",
            lambda inputs: None,
            "previous interaction session",
        ),
        (
            "missing_active_session",
            "exit_requested",
            lambda inputs: _clear_active_session(inputs),
            "active session",
        ),
        (
            "mismatched_active_session",
            "exit_requested",
            lambda inputs: _replace_active_session(inputs),
            "active session",
        ),
        (
            "empty_sequence_items",
            "exit_requested",
            lambda inputs: _empty_sequence_items(inputs),
            "sequence items",
        ),
    ],
)
def test_handler_validation_failures_do_not_mutate_or_commit(
    case: str,
    outcome: SequenceEndOutcome,
    configure,
    error: str,
) -> None:
    inputs = _workspace_inputs()
    lesson = inputs["lesson"]
    original_commit_count = len(lesson.history_graph.commits)
    session_before = configure(inputs)
    active_session_at_entry = lesson.active_interaction_session

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match=error):
            handle_interaction_sequence_end(
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
                requirement_history=inputs["requirement_history"],
                deps=_deps({}),
            )

    assert lesson.active_interaction_session == active_session_at_entry
    assert len(lesson.history_graph.commits) == original_commit_count
    assert _node_values(collector) == []


def _clear_active_session(inputs):
    inputs["lesson"].active_interaction_session = None
    return inputs["session"]


def _replace_active_session(inputs):
    inputs["lesson"].active_interaction_session = inputs["session"].model_copy(
        update={"progress_note": "另一个 active session"}
    )
    return inputs["session"]


def _empty_sequence_items(inputs):
    session = inputs["session"].model_copy(update={"sequence_items": []})
    inputs["lesson"].active_interaction_session = session
    return session


def test_handler_rejects_completion_without_unit_label_without_mutating_or_committing() -> None:
    inputs = _workspace_inputs(final_item=True)
    lesson = inputs["lesson"]
    original_active_session = lesson.active_interaction_session
    original_commit_count = len(lesson.history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="unit label"):
            handle_interaction_sequence_end(
                outcome="completed",
                workspace=inputs["workspace"],
                package=inputs["package"],
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=inputs["request"],
                requirements=inputs["requirements"],
                learning_clarification=inputs["learning_clarification"],
                resources=[],
                session_before=inputs["session"],
                requirement_history=inputs["requirement_history"],
                deps=_deps({}),
            )

    assert lesson.active_interaction_session == original_active_session
    assert len(lesson.history_graph.commits) == original_commit_count
    assert _node_values(collector) == []


def test_generator_failure_preserves_current_clear_before_generator_behavior() -> None:
    inputs = _workspace_inputs()
    lesson = inputs["lesson"]
    original_commit_count = len(lesson.history_graph.commits)

    def fail_generate(**kwargs):
        assert kwargs["lesson"].active_interaction_session is None
        raise RuntimeError("generator failed")

    deps = InteractionSequenceEndDependencies(
        generate_sequence_end_message=fail_generate,
        task_metadata=chatbot_module._task_metadata,
        save_workspace_for_user=lambda **kwargs: None,
        build_response=lambda **kwargs: SimpleNamespace(),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="generator failed"):
            handle_interaction_sequence_end(
                outcome="exit_requested",
                workspace=inputs["workspace"],
                package=inputs["package"],
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=inputs["request"],
                requirements=inputs["requirements"],
                learning_clarification=inputs["learning_clarification"],
                resources=[],
                session_before=inputs["session"],
                requirement_history=inputs["requirement_history"],
                deps=deps,
            )

    assert lesson.active_interaction_session is None
    assert len(lesson.history_graph.commits) == original_commit_count
    assert _node_values(collector) == []
