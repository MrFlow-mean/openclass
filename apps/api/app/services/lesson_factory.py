from __future__ import annotations

import re

from app.models import (
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    ResourceReferenceContext,
    TeachingGuide,
    TeachingGuideMapping,
    new_id,
)
from app.services.renderer import build_document_for_topic_render
from app.services.rich_document import build_document


def slugify(value: str) -> str:
    lowered = re.sub(r"\s+", "-", value.strip().lower())
    lowered = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", lowered)
    return lowered.strip("-") or new_id("lesson")


def _clean_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", topic or "").strip() or "新学习主题"


def build_requirements(topic: str) -> LearningRequirementSheet:
    normalized_topic = _clean_topic(topic)

    return LearningRequirementSheet(
        theme=normalized_topic,
        learning_goal="先澄清用户具体想学什么、当前水平、学习目的和使用场景，再决定讲解与板书方式。",
        level="待确认用户在这个领域的已有基础、熟悉程度和卡点。",
        known_background="用户背景尚未明确，需要通过对话了解已有经验、相关资料和学习约束。",
        current_questions=[
            "你具体想学什么内容，或想解决哪个问题？",
            "你在这个领域目前是什么水平，已经掌握了哪些基础？",
            "你为什么学，之后要面对什么任务、场景或输出要求？",
        ],
        learning_need_checklist=[],
        target_depth="根据用户水平和目标场景动态决定讲到入门、理解、练习还是应用。",
        output_preference="根据用户目标、资料结构和交互意图动态决定输出形态",
        boundary="优先围绕当前主题展开，不自动跳到无关领域",
        board_scope=[],
        success_criteria="用户能说明具体内容、已有基础、学习目的或应用场景后，再生成匹配的讲义与练习。",
    )


def _document_headings(document: BoardDocument) -> list[str]:
    headings = re.findall(r"^(.{1,60})$", document.content_text or "", flags=re.MULTILINE)
    return [heading.strip() for heading in headings if heading.strip()][:6]


def build_teaching_guide(
    lesson_id: str,
    lesson_title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet | None = None,
) -> TeachingGuide:
    normalized_requirements = requirements or build_requirements(lesson_title)
    headings = _document_headings(document) or normalized_requirements.board_scope or [lesson_title]
    mappings = [
        TeachingGuideMapping(
            block_id=f"section_{index}",
            supports_goal=heading,
            teaching_mode="definition" if index == 1 else "example",
            focus_points=[heading],
            check_questions=[f"你能用自己的话说明“{heading}”和本课目标的关系吗？"],
        )
        for index, heading in enumerate(headings[:5], start=1)
    ]
    return TeachingGuide(
        lesson_id=lesson_id,
        summary=normalized_requirements.learning_goal,
        structure_note="按学习目标、资料证据和当前板书顺序组织讲解。",
        pacing="先讲主线，再讲关键关系，最后用例子或练习检查理解。",
        mappings=mappings,
        strategy="根据学习者反馈动态调整深度，不绑定特定学科或预设课程结构。",
    )


def _initial_history(document: BoardDocument) -> LessonHistoryGraph:
    commit = CommitRecord(
        label="Initial document",
        message=f"Generated starter rich document for {document.title}",
        branch_name="main",
        snapshot=document,
        metadata={"kind": "initial_document"},
    )
    return LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main",
                head_commit_id=commit.id,
                base_commit_id=commit.id,
            )
        },
        commits=[commit],
        current_branch="main",
    )


def create_lesson(
    title: str,
    *,
    requirements: LearningRequirementSheet | None = None,
    reference_context: ResourceReferenceContext | None = None,
) -> Lesson:
    clean_title = _clean_topic(title)
    document = build_document_for_topic_render(clean_title, reference_context)
    normalized_requirements = requirements or build_requirements(clean_title)
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, clean_title, document, normalized_requirements)
    return Lesson(
        id=lesson_id,
        title=clean_title,
        slug=slugify(clean_title),
        summary=normalized_requirements.learning_goal,
        tags=[],
        board_document=document,
        learning_requirements=normalized_requirements,
        teaching_guide=guide,
        history_graph=_initial_history(document),
    )


def create_empty_lesson(title: str) -> Lesson:
    clean_title = _clean_topic(title)
    document = build_document(title=clean_title)
    normalized_requirements = build_requirements(clean_title)
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, clean_title, document, normalized_requirements)
    return Lesson(
        id=lesson_id,
        title=clean_title,
        slug=slugify(clean_title),
        summary="",
        tags=[],
        board_document=document,
        learning_requirements=normalized_requirements,
        teaching_guide=guide,
        history_graph=_initial_history(document),
    )
