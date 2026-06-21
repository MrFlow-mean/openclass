from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    InteractionRuleDraft,
    LearningClarificationStatus,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.paths.board_task_chat import (
    BOARD_TASK_CHAT_ACTION,
    BoardTaskChatHandoffDependencies,
    handle_board_task_chat_handoff,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector, record_workflow_step


TEST_USER_ID = "user_board_task_chat_handoff"


def _interaction_draft() -> InteractionRuleDraft:
    return InteractionRuleDraft(
        should_start=True,
        rule_text="Follow the learner's rule against the board excerpt.",
        interaction_goal="Practice against the selected board excerpt.",
        target_hint="selected excerpt",
        expected_user_behavior="The learner replies according to the rule.",
        assistant_behavior="The assistant replies according to the rule and excerpt.",
        reference_instruction="Use the selected board excerpt.",
    )


def _workspace_inputs() -> dict[str, Any]:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("Test lesson")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="Existing board",
            content_text="# Source\n\n## First part\ntarget board content\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    requirements = lesson.learning_requirements.model_copy(
        update={"learning_goal": "Start a rule-based interaction around the existing board."}
    )
    lesson.learning_requirements = requirements
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["Source", "First part"],
        excerpt="target board content",
        confidence=0.95,
        reason="Test focus was resolved.",
        display_label="First part",
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="selected excerpt",
        location_status="selected",
        requested_action="chat",
        question_or_topic="Practice against the selected board excerpt.",
        interaction_rule_draft=_interaction_draft(),
        progress=100,
        missing_items=[],
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "request": ChatRequest(
            message="Start the rule-based interaction",
            selection={"kind": "board", "excerpt": focus.excerpt},
        ),
        "requirements": requirements,
        "learning_clarification": LearningClarificationStatus(
            progress=100,
            label="ready",
            reason="ready",
            can_start=True,
            summary="Start a rule-based interaction around the existing board.",
        ),
        "resources": package.resources,
        "selection_text": focus.excerpt,
        "requirement_history": LearningRequirementHistoryRecorder.from_store_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
            state=None,
        ),
        "board_task_history": BoardTaskHistoryRecorder.from_store_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
            state=None,
        ),
        "board_task_stamp": BoardTaskHistoryStamp(run_id="run-chat", version_id="ver-ready", phase="ready"),
        "focus": focus,
        "resolution": FocusResolution(focus=focus, candidates=[focus], status="selected", question=""),
        "board_task": board_task,
        "decision": BoardTaskRouteDecision(
            route="chat",
            location_status="found",
            target_focus=focus,
            reason="The target is resolved and can start interaction.",
            target_scope="focus",
        ),
    }


def _deps(
    calls: dict[str, Any],
    *,
    start_response: object | None,
    trace_mode: str = "resolve_only",
) -> BoardTaskChatHandoffDependencies:
    def requirements_from_board_task(**kwargs):
        calls.setdefault("requirements", []).append(kwargs)
        return kwargs["base"].model_copy(
            update={
                "action_type": kwargs["action_type"],
                "action_instruction": kwargs["board_task"].question_or_topic,
                "target_location": kwargs["focus"],
                "location_status": "resolved" if kwargs.get("focus") else "missing",
                "interaction_rule_draft": kwargs["board_task"].interaction_rule_draft,
            }
        )

    def start_interaction_session(**kwargs):
        calls.setdefault("start", []).append(kwargs)
        record_workflow_step(
            NodeId.INTERACTION_START_RESOLVE,
            decision="resolved" if start_response is not None else "not_started",
            reason=kwargs["board_task_decision"].reason,
            run_id=kwargs["board_task_stamp"].run_id,
            version_id=kwargs["board_task_stamp"].version_id,
        )
        if trace_mode == "canonical_success" and start_response is not None:
            record_workflow_step(
                NodeId.INTERACTION_START_PERSIST,
                decision="started",
                reason=kwargs["board_task_decision"].reason,
                run_id=kwargs["board_task_stamp"].run_id,
                version_id=kwargs["board_task_stamp"].version_id,
                commit_id="commit-chat",
            )
            record_workflow_step(NodeId.RESPONSE_ASSEMBLE, decision="assembled")
        return start_response

    return BoardTaskChatHandoffDependencies(
        requirements_from_board_task=requirements_from_board_task,
        board_search_evidence_metadata=lambda resolution: {"board_search_evidence": {"status": resolution.status}},
        decision_trace_metadata=lambda **kwargs: {
            "decision_trace": {
                "role_executed": kwargs["role_executed"],
                "document_changed": kwargs["document_changed"],
                "reason": kwargs["reason"],
            }
        },
        start_interaction_session=start_interaction_session,
    )


def _call_handler(inputs, calls, *, start_response: object | None = None, trace_mode: str = "resolve_only"):
    response = start_response if start_response is not None else SimpleNamespace(kind="interaction-started")
    return handle_board_task_chat_handoff(
        workspace=inputs["workspace"],
        package=inputs["package"],
        lesson=inputs["lesson"],
        user_id=TEST_USER_ID,
        request=inputs["request"],
        requirements=inputs["requirements"],
        learning_clarification=inputs["learning_clarification"],
        resources=inputs["resources"],
        selection_text=inputs["selection_text"],
        board_task=inputs["board_task"],
        resolution=inputs["resolution"],
        requirement_history=inputs["requirement_history"],
        board_task_history=inputs["board_task_history"],
        board_task_stamp=inputs["board_task_stamp"],
        action_decision=None,
        decision=inputs["decision"],
        source_interaction_metadata={"source": "board_task_chat_handoff_test"},
        deps=_deps(calls, start_response=response, trace_mode=trace_mode),
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def test_chat_handoff_builds_task_requirements_and_delegates_to_interaction_start() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    start_response = SimpleNamespace(kind="interaction-started")

    with bind_workflow_trace_collector() as collector:
        response = _call_handler(
            inputs,
            calls,
            start_response=start_response,
            trace_mode="canonical_success",
        )

    assert response is start_response
    assert calls["requirements"][0]["action_type"] == BOARD_TASK_CHAT_ACTION
    assert calls["requirements"][0]["focus"] == inputs["focus"]
    start_kwargs = calls["start"][0]
    assert start_kwargs["requirements"] is inputs["lesson"].learning_requirements
    assert start_kwargs["requirements"].interaction_rule_draft == inputs["board_task"].interaction_rule_draft
    assert start_kwargs["action_type"] == BOARD_TASK_CHAT_ACTION
    assert start_kwargs["board_task"] == inputs["board_task"]
    assert start_kwargs["board_task_history"] == inputs["board_task_history"]
    assert start_kwargs["board_task_stamp"] == inputs["board_task_stamp"]
    assert start_kwargs["board_task_decision"] == inputs["decision"]
    assert start_kwargs["resolved_focus"] == inputs["focus"]
    assert start_kwargs["selection_text"] == inputs["selection_text"]
    assert start_kwargs["source_interaction_metadata"]["source"] == "board_task_chat_handoff_test"
    assert start_kwargs["source_interaction_metadata"]["board_search_evidence"] == {"status": "selected"}
    assert start_kwargs["source_interaction_metadata"]["decision_trace"]["role_executed"] == "interaction_session"
    assert start_kwargs["source_interaction_metadata"]["decision_trace"]["document_changed"] is False
    assert _node_values(collector) == [
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.INTERACTION_START_PERSIST.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].run_id == "run-chat"
    assert collector.steps[0].version_id == "ver-ready"
    assert collector.steps[1].run_id == "run-chat"
    assert collector.steps[1].version_id == "ver-ready"
    assert collector.steps[1].commit_id == "commit-chat"


def test_chat_handoff_none_from_interaction_start_returns_none_after_handoff_attempt() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}

    with bind_workflow_trace_collector() as collector:
        response = handle_board_task_chat_handoff(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=inputs["request"],
            requirements=inputs["requirements"],
            learning_clarification=inputs["learning_clarification"],
            resources=inputs["resources"],
            selection_text=inputs["selection_text"],
            board_task=inputs["board_task"],
            resolution=inputs["resolution"],
            requirement_history=inputs["requirement_history"],
            board_task_history=inputs["board_task_history"],
            board_task_stamp=inputs["board_task_stamp"],
            action_decision=None,
            decision=inputs["decision"],
            deps=_deps(calls, start_response=None),
        )

    assert response is None
    assert calls["start"][0]["requirements"] is inputs["lesson"].learning_requirements
    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert collector.steps[0].decision == "not_started"
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_chat_handoff_uses_resolution_focus_when_decision_has_no_target_focus() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    inputs["decision"] = inputs["decision"].model_copy(update={"target_focus": None})

    _call_handler(inputs, calls)

    assert calls["requirements"][0]["focus"] == inputs["focus"]
    assert calls["start"][0]["resolved_focus"] == inputs["focus"]


def test_chat_handoff_rejects_non_chat_route_without_touching_requirements() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    original_requirements = inputs["lesson"].learning_requirements
    inputs["decision"] = inputs["decision"].model_copy(update={"route": "edit"})

    with pytest.raises(ValueError, match="requires route='chat'"):
        _call_handler(inputs, calls)

    assert calls == {}
    assert inputs["lesson"].learning_requirements is original_requirements
