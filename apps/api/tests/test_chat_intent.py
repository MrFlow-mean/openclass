from __future__ import annotations

import pytest

from app.models import ChatRequest, LearningRequirementSheet
from app.services.chat.intent import (
    _infer_board_task_action,
    _prefer_requirement_action,
    _requests_append_section,
    _requests_document_artifact_generation,
    _requests_explanation,
    _requests_learning_start,
    _requests_resource_backed_answer,
    _should_prompt_resource_reference,
)


def _requirements(**updates) -> LearningRequirementSheet:
    base = LearningRequirementSheet(
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
    return base.model_copy(update=updates)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("解释这里是什么意思", True),
        ("概括一下这一段", True),
        ("你好，我想聊一下学习安排", False),
    ],
)
def test_requests_explanation(message: str, expected: bool) -> None:
    assert _requests_explanation(message) is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("继续写下一节", True),
        ("请新增一段练习", True),
        ("解释这里", False),
    ],
)
def test_requests_append_section(message: str, expected: bool) -> None:
    assert _requests_append_section(message) is expected


@pytest.mark.parametrize(
    ("chat_request", "has_selection", "document_empty", "expected"),
    [
        (ChatRequest(message="解释这里是什么意思"), False, False, "explain_target"),
        (ChatRequest(message="继续写下一节"), False, False, "append_section"),
        (ChatRequest(message="扩写这一段"), False, False, "expand_target"),
        (ChatRequest(message="把这里改短一点"), False, False, "simplify_target"),
        (ChatRequest(message="润色这一段"), False, False, "rewrite_target"),
        (ChatRequest(message="根据上传资料回答这个问题"), False, False, None),
        (ChatRequest(message="开始生成", board_generation_action="start"), False, True, "generate_board"),
        (ChatRequest(message="普通聊一下学习计划"), False, False, None),
        (ChatRequest(message="改短一点", interaction_mode="direct_edit"), False, False, "simplify_target"),
    ],
)
def test_infer_board_task_action(
    chat_request: ChatRequest,
    has_selection: bool,
    document_empty: bool,
    expected: str | None,
) -> None:
    assert _infer_board_task_action(chat_request, has_selection=has_selection, document_empty=document_empty) == expected


def test_prefer_requirement_action_preserves_pending_append_followup() -> None:
    requirements = _requirements(action_instruction="继续写下一节", learning_goal="补充后续内容")

    assert (
        _prefer_requirement_action(
            None,
            None,
            request_message="继续",
            requirements=requirements,
        )
        == "append_section"
    )


def test_prefer_requirement_action_preserves_explicit_edit_requirement() -> None:
    assert (
        _prefer_requirement_action(
            "explain_target",
            "rewrite_target",
            request_message="解释这里",
            requirements=_requirements(),
        )
        == "rewrite_target"
    )


def test_resource_and_generation_intents_are_detected_without_domain_branching() -> None:
    assert _requests_resource_backed_answer("根据上传资料回答这个问题") is True
    assert _should_prompt_resource_reference("根据上传资料生成一份讲义") is True
    assert _requests_document_artifact_generation("生成一份学习提纲") is True
    assert _requests_learning_start("我想学习一下这个主题") is True
