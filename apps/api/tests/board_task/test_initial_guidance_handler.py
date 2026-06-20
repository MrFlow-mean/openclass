from __future__ import annotations

from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services.chat.paths.initial_guidance import InitialGuidanceDependencies, handle_initial_guidance
from app.services.course_store import build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import InitialLearningWorkModeDecision
from app.services.resource_resolver import ResourceResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_initial_guidance_handler"


def _workspace_with_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="",
        learning_goal="",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _clarification(*, work_mode: str) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=20,
        label="继续澄清",
        reason="需要先明确学习入口。",
        missing_items=["学习入口"],
        can_start=False,
        summary="需要先明确学习入口。",
        next_question="你想先走哪一种？",
        ready_for_board=False,
        work_mode=work_mode,
    )


def _decision(work_mode: str) -> InitialLearningWorkModeDecision:
    if work_mode == "unknown":
        return InitialLearningWorkModeDecision(
            work_mode="unknown",
            granularity="unclear",
            topic="学习方向未定",
            reason="学习目的还不明确。",
            guided_discovery_reply="可以先选一个具体主题，或先做一份练习材料。你想先走哪一种？",
            next_question="你想先走哪一种？",
        )
    if work_mode == "narrow_topic":
        return InitialLearningWorkModeDecision(
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="一个宽泛主题",
            reason="主题还过宽。",
            next_question="你想先从哪个具体问题开始？",
        )
    raise AssertionError(f"unsupported work_mode: {work_mode}")


def _history_recorder(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=None,
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


@pytest.mark.parametrize(
    ("outcome", "expected_message", "expected_source"),
    [
        (
            "unknown",
            "可以先选一个具体主题，或先做一份练习材料。你想先走哪一种？",
            "initial_learning_guided_discovery",
        ),
        (
            "narrow_topic",
            "你想先从哪个具体问题开始？",
            "initial_learning_work_mode",
        ),
    ],
)
def test_handle_initial_guidance_commits_saves_and_assembles_terminal_response(
    outcome: str,
    expected_message: str,
    expected_source: str,
) -> None:
    workspace, package, lesson = _workspace_with_lesson()
    requirements = _requirements()
    calls: dict[str, Any] = {"minimal": [], "save": [], "response": []}
    sentinel = object()

    def _minimal_state(
        base: LearningRequirementSheet,
        *,
        decision: InitialLearningWorkModeDecision,
        user_message: str,
        generate_board: bool,
    ):
        calls["minimal"].append(
            {
                "base": base,
                "decision": decision,
                "user_message": user_message,
                "generate_board": generate_board,
            }
        )
        updated = LearningRequirementSheet.model_validate(base.model_dump(mode="json"))
        updated.work_mode = decision.work_mode
        updated.granularity = decision.granularity
        updated.current_questions = [decision.next_question] if decision.next_question else []
        return updated, _clarification(work_mode=decision.work_mode)

    def _task_metadata(**kwargs):
        return {"task_requirement_sheet": kwargs["requirements"].model_dump(mode="json"), "requirement_cleared": False}

    def _work_mode_metadata(decision: InitialLearningWorkModeDecision):
        return {"initial_learning_work_mode": decision.model_dump(mode="json")}

    def _reference_metadata(**kwargs):
        return {"resource_resolution_status": kwargs["resolution"].status}

    def _save(**kwargs):
        calls["save"].append(kwargs)

    def _response(**kwargs):
        calls["response"].append(kwargs)
        return sentinel

    with bind_workflow_trace_collector() as collector:
        response = handle_initial_guidance(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="我想开始学习"),
            requirements=requirements,
            decision=_decision(outcome),
            outcome=outcome,
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
            requirement_history=_history_recorder(lesson.id),
            deps=InitialGuidanceDependencies(
                minimal_initial_learning_state=_minimal_state,
                task_metadata=_task_metadata,
                initial_learning_work_mode_metadata=_work_mode_metadata,
                reference_metadata=_reference_metadata,
                save_workspace_for_user=_save,
                build_response=_response,
            ),
        )

    commit = lesson.history_graph.commits[-1]
    assert response is sentinel
    assert lesson.learning_requirements is not None
    assert lesson.learning_requirements.work_mode == outcome
    assert commit.metadata["assistant_message"] == expected_message
    assert commit.metadata["assistant_message_source"] == expected_source
    assert commit.metadata["initial_learning_work_mode"]["work_mode"] == outcome
    assert calls["minimal"][0]["generate_board"] is False
    assert calls["minimal"][0]["user_message"] == "我想开始学习"
    assert calls["save"][0]["user_id"] == TEST_USER_ID
    assert calls["save"][0]["workspace"] is workspace
    assert calls["response"][0]["chatbot_message"] == expected_message
    assert isinstance(calls["response"][0]["board_decision"], BoardDecision)
    assert calls["response"][0]["resource_matches"] == []
    assert _node_values(collector) == [
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].commit_id == commit.id


def test_handle_initial_guidance_rejects_empty_visible_message_before_commit_or_save() -> None:
    workspace, package, lesson = _workspace_with_lesson()
    initial_commit_count = len(lesson.history_graph.commits)
    saved = False

    def _fail_minimal(*args, **kwargs):
        raise AssertionError("minimal state should not be built when visible guidance is empty")

    def _mark_saved(**kwargs):
        nonlocal saved
        saved = True

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="requires a visible message"):
            handle_initial_guidance(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="我想开始学习"),
                requirements=_requirements(),
                decision=InitialLearningWorkModeDecision(work_mode="unknown", granularity="unclear"),
                outcome="unknown",
                resource_resolution=ResourceResolution(matches=[], status="none"),
                selected_reference=None,
                requirement_history=_history_recorder(lesson.id),
                deps=InitialGuidanceDependencies(
                    minimal_initial_learning_state=_fail_minimal,
                    task_metadata=lambda **kwargs: {},
                    initial_learning_work_mode_metadata=lambda decision: {},
                    reference_metadata=lambda **kwargs: {},
                    save_workspace_for_user=_mark_saved,
                    build_response=lambda **kwargs: object(),
                ),
            )

    assert saved is False
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert _node_values(collector) == []
