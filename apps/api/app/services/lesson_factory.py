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
        learning_goal=f"围绕“{normalized_topic}”建立可讲授、可复习、可练习的结构化讲义",
        level="根据用户背景和资料难度动态调整",
        known_background="用户背景尚未完全明确，先采用循序渐进的讲解方式",
        current_questions=[
            f"“{normalized_topic}”的核心问题是什么",
            "它包含哪些关键概念、步骤或例子",
            "学习后如何检查是否真正理解",
        ],
        learning_need_checklist=[
            "建立主题主线",
            "拆解关键概念",
            "补充必要例子",
            "给出练习或检查问题",
        ],
        target_depth="能复述核心内容，并能用例子解释或完成基础练习",
        output_preference="连续讲义：主线、概念、例子、练习、总结",
        boundary="优先围绕当前主题展开，不自动跳到无关领域",
        board_scope=[
            "主题定位",
            "核心概念",
            "重点展开",
            "例子或应用",
            "练习与总结",
        ],
        success_criteria="用户能说清主线、解释关键概念，并完成至少一个检查问题",
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
        strategy="根据学习者反馈动态调整深度，不绑定特定学科或固定课程模板。",
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
