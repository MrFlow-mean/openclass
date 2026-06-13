import json

from app.models import ConversationTurn
from app.services import initial_learning_intent
from app.services.decision_trace import initial_learning_intent_trace_metadata
from app.services.initial_learning_intent import (
    InitialLearningIntentDecision,
    decide_initial_learning_intent,
    fallback_initial_learning_intent_decision,
)


def test_initial_learning_intent_uses_ai_schema_decision(monkeypatch) -> None:
    expected = InitialLearningIntentDecision(
        learning_mode="learn_concept",
        target_granularity="specific_concept",
        next_action="freeze_minimal_and_generate_board",
        trace_reason="bounded target",
    )
    monkeypatch.setattr(
        initial_learning_intent.openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: expected,
    )

    decision = decide_initial_learning_intent(
        lesson_title="空白页",
        existing_summary="",
        existing_checklist=[],
        conversation=[ConversationTurn(role="user", content="请解释一个明确概念")],
        user_message="请解释一个明确概念",
    )

    assert decision == expected
    assert decision.next_action == "freeze_minimal_and_generate_board"


def test_initial_learning_intent_downgrades_underbounded_process_goal(monkeypatch) -> None:
    model_decision = InitialLearningIntentDecision(
        learning_mode="learn_concept",
        target_granularity="specific_concept",
        next_action="freeze_minimal_and_generate_board",
        trace_reason="model treated the process goal as bounded",
    )
    monkeypatch.setattr(
        initial_learning_intent.openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: model_decision,
    )

    decision = decide_initial_learning_intent(
        lesson_title="空白页",
        existing_summary="",
        existing_checklist=[],
        conversation=[],
        user_message="我想学一个领域里怎么做优化流程",
    )

    assert decision.learning_mode == "learn_concept"
    assert decision.target_granularity == "broad_domain"
    assert decision.next_action == "ask_specific_concept"
    assert "具体对象" in decision.trace_reason


def test_initial_learning_intent_fallback_is_conservative(monkeypatch) -> None:
    monkeypatch.setattr(
        initial_learning_intent.openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: None,
    )

    practice = decide_initial_learning_intent(
        lesson_title="空白页",
        existing_summary="",
        existing_checklist=[],
        conversation=[],
        user_message="帮我做一组练习来巩固这部分内容",
    )
    undecided = decide_initial_learning_intent(
        lesson_title="空白页",
        existing_summary="",
        existing_checklist=[],
        conversation=[],
        user_message="先看看",
    )

    assert practice.learning_mode == "practice_activity"
    assert practice.next_action == "collect_practice_requirements"
    assert undecided.learning_mode == "undecided"
    assert undecided.next_action == "ask_learning_mode"


def test_initial_learning_intent_trace_contains_no_special_cases() -> None:
    decision = fallback_initial_learning_intent_decision("帮我做一组练习")
    metadata = initial_learning_intent_trace_metadata(
        decision,
        requirement_phase="collecting",
        minimal_frozen_requirement=False,
        board_editor_called=False,
    )
    serialized = json.dumps(metadata, ensure_ascii=False)

    for banned in ["法语", "数学", "CSAPP", "高考", "demo"]:
        assert banned not in serialized
