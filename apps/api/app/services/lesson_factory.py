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
    now_iso,
)
from app.services.rich_document import build_document


def slugify(value: str) -> str:
    lowered = re.sub(r"\s+", "-", value.strip().lower())
    lowered = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", lowered)
    return lowered.strip("-") or new_id("lesson")


def build_requirements(topic: str) -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme=topic,
        learning_goal="理解概念、能跟着连续讲义讲清楚并完成基础练习",
        level="初学到进阶之间",
        known_background="已有零散印象，但需要结构化讲解",
        current_questions=[f"{topic}的定义是什么", "它为什么重要", "应该怎么用"],
        target_depth="先做到能讲清基本定义并会做入门题",
        output_preference="Word 式连续讲义：定义、直觉、例题、练习、总结",
        boundary="先不无限展开相邻学科的更大知识域",
        board_scope=["定义", "直觉", "核心公式或规律", "例题", "练习"],
        success_criteria="用户能复述核心概念并完成一题相关练习",
    )


def build_document_for_topic(
    topic: str,
    reference_context: ResourceReferenceContext | None = None,
) -> BoardDocument:
    _ = reference_context
    return build_blank_document(topic)


def build_blank_document(topic: str) -> BoardDocument:
    return build_document(title=topic, content_html="<p></p>")


def _outline_from_document(document: BoardDocument) -> list[str]:
    lines = [line.strip() for line in document.content_text.splitlines() if line.strip()]
    headings = [line for line in lines if len(line) <= 42][:8]
    return headings or [document.title]


def build_teaching_guide(
    lesson_id: str,
    title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    mappings = [
        TeachingGuideMapping(
            block_id=f"section_{index}",
            supports_goal=requirements.learning_goal,
            teaching_mode="dialogue" if "对话" in heading else "definition",
            focus_points=[heading],
            optional_points=["根据用户追问扩写当前段落，而不是拆成卡片。"],
            difficult_points=["如果用户只问一个词或一句话，优先结合整篇讲义上下文解释。"],
            check_questions=[f"你能用自己的话复述“{heading}”的重点吗？"],
        )
        for index, heading in enumerate(_outline_from_document(document), start=1)
    ]
    return TeachingGuide(
        lesson_id=lesson_id,
        summary=f"围绕《{title}》的连续讲义进行讲解，服务于：{requirements.learning_goal}",
        structure_note="以整篇文档为课堂板书，优先维持标题、正文、对话、练习的连续阅读体验。",
        pacing="场景/定义 -> 主体讲解 -> 例句或例题 -> 练习 -> 检查理解",
        mappings=mappings,
        strategy="讲解和编辑都围绕整篇富文档快照推进，避免回到分块卡片式板书。",
    )


def build_lesson(
    topic: str,
    *,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
    commit_label: str,
    commit_message: str,
    tags: list[str],
) -> Lesson:
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, topic, document, requirements)
    commit = CommitRecord(
        label=commit_label,
        message=commit_message,
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
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
    return Lesson(
        id=lesson_id,
        title=topic,
        slug=slugify(topic),
        summary=requirements.learning_goal,
        tags=tags,
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def create_lesson(
    topic: str,
    requirements: LearningRequirementSheet | None = None,
    reference_context: ResourceReferenceContext | None = None,
) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_document_for_topic(topic, reference_context)
    return build_lesson(
        topic,
        document=document,
        requirements=requirements,
        commit_label="Initial blank document",
        commit_message=f"Created empty rich document for {topic}",
        tags=[topic, *requirements.board_scope[:2]],
    )


def create_empty_lesson(topic: str, requirements: LearningRequirementSheet | None = None) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = build_blank_document(topic)
    return build_lesson(
        topic,
        document=document,
        requirements=requirements,
        commit_label="Initial blank document",
        commit_message=f"Created empty rich document for {topic}",
        tags=[topic],
    )
