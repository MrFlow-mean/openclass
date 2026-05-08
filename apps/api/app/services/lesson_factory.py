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
    return re.sub(r"\s+", " ", topic or "").strip() or "新的课程"


def build_requirements(topic: str) -> LearningRequirementSheet:
    clean_topic = _clean_topic(topic)
    return LearningRequirementSheet(
        theme=clean_topic,
        learning_goal=f"围绕“{clean_topic}”建立可讲解、可练习、可迁移的理解。",
        level="待确认，可从入门讲起并按反馈调整",
        known_background="学习者背景尚未完全确认，需要在讲解中保留必要的前置说明。",
        current_questions=[
            f"“{clean_topic}”要解决的核心问题是什么？",
            "需要先理解哪些关键概念或步骤？",
            "如何通过例子或练习检查是否真的掌握？",
        ],
        learning_need_checklist=[
            "明确学习目标和使用场景",
            "建立核心概念之间的关系",
            "用例子、练习或材料证据检验理解",
        ],
        target_depth="先形成可复述的主线，再根据学习者目标继续加深。",
        output_preference="连续讲义：主线、概念、解释、例子、练习、答案与总结。",
        boundary="不预设特定学科模板；讲解深度由用户目标、资料上下文和学习反馈决定。",
        board_scope=["问题主线", "核心概念", "解释过程", "例子或证据", "练习与总结"],
        success_criteria="学习者能复述主线，解释关键关系，并完成一个迁移性检查任务。",
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
