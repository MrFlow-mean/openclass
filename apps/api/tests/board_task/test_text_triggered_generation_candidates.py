from __future__ import annotations

from app.models import (
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    ResourceReferenceContext,
    ResourceReferencePrompt,
)
from app.services.chat.paths.generation_text_trigger import (
    classify_text_triggered_generation_request,
    text_triggered_generation_terminal_candidates,
)
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document


def _draft_state(*, actionable: bool = True):
    requirements = build_requirements("通用学习主题")
    clarification = LearningClarificationStatus(
        progress=55,
        label="收集中",
        reason="用户已经表达了通用学习产物方向。",
        missing_items=[],
        can_start=False,
        summary="用户想产出一份学习材料。",
        ready_for_board=False,
        key_facts=[],
        checklist=[],
        next_question="",
    )
    if actionable:
        clarification.key_facts.append(
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用学习主题",
                evidence="来自用户输入。",
                category="learning",
            )
        )
        clarification.checklist.append(
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        )
    return requirements, clarification


def _blank_lesson():
    return create_empty_lesson("空白学习页")


def _resource_prompt_resolution() -> ResourceResolution:
    return ResourceResolution(
        matches=[],
        reference_prompt=ResourceReferencePrompt(
            resource_id="res_generic",
            chapter_id="chap_generic",
            resource_name="资料",
            chapter_title="章节",
            question="是否参考这份资料？",
            reason="资料匹配到当前请求。",
        ),
        status="prompt",
    )


def _selected_resource_resolution() -> ResourceResolution:
    return ResourceResolution(
        matches=[],
        selected_reference=ResourceReferenceContext(
            resource_id="res_generic",
            chapter_id="chap_generic",
            resource_name="资料",
            chapter_title="章节",
            summary="资料摘要。",
        ),
        status="selected",
    )


def test_document_artifact_text_produces_single_terminal_candidate_without_mutating_state() -> None:
    lesson = _blank_lesson()
    requirements, clarification = _draft_state()
    requirement_snapshot = requirements.model_dump(mode="json")
    clarification_snapshot = clarification.model_dump(mode="json")
    board_snapshot = lesson.board_document.model_dump(mode="json")

    candidates = text_triggered_generation_terminal_candidates(
        lesson=lesson,
        request=ChatRequest(message="请生成一份学习材料"),
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )

    assert len(candidates) == 1
    assert candidates[0].terminal == "text_triggered_initial_generation"
    assert candidates[0].priority == 60
    assert candidates[0].request.trigger == "document_artifact_request"
    assert candidates[0].reason == candidates[0].request.reason
    assert requirements.model_dump(mode="json") == requirement_snapshot
    assert clarification.model_dump(mode="json") == clarification_snapshot
    assert lesson.board_document.model_dump(mode="json") == board_snapshot


def test_generation_control_requires_actionable_existing_context() -> None:
    lesson = _blank_lesson()
    requirements, clarification = _draft_state(actionable=True)

    classified = classify_text_triggered_generation_request(
        lesson=lesson,
        request=ChatRequest(message="开始吧，直接生成"),
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )

    assert classified is not None
    assert classified.trigger == "generation_control_request"

    empty_requirements, empty_clarification = _draft_state(actionable=False)
    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="继续"),
            requirements=empty_requirements,
            learning_clarification=empty_clarification,
            resource_resolution=ResourceResolution(matches=[], status="none"),
        )
        is None
    )
    assert (
        text_triggered_generation_terminal_candidates(
            lesson=lesson,
            request=ChatRequest(message="开始吧"),
            requirements=empty_requirements,
            learning_clarification=empty_clarification,
            resource_resolution=ResourceResolution(matches=[], status="none"),
        )
        == ()
    )


def test_classifier_excludes_api_start_resource_paths_and_existing_board() -> None:
    lesson = _blank_lesson()
    requirements, clarification = _draft_state()
    base_resolution = ResourceResolution(matches=[], status="none")

    excluded_requests = [
        ChatRequest(message="开始生成", board_generation_action="start"),
        ChatRequest(message="按资料生成", resource_reference_action="confirm"),
    ]
    for request in excluded_requests:
        assert (
            classify_text_triggered_generation_request(
                lesson=lesson,
                request=request,
                requirements=requirements,
                learning_clarification=clarification,
                resource_resolution=base_resolution,
            )
            is None
        )

    for resource_resolution in [_resource_prompt_resolution(), _selected_resource_resolution()]:
        assert (
            classify_text_triggered_generation_request(
                lesson=lesson,
                request=ChatRequest(message="根据资料生成一份学习材料"),
                requirements=requirements,
                learning_clarification=clarification,
                resource_resolution=resource_resolution,
            )
            is None
        )

    lesson.board_document = build_document(title="已有板书", content_text="# 已有板书\n\n已有内容")
    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="请生成一份学习材料"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=base_resolution,
        )
        is None
    )
