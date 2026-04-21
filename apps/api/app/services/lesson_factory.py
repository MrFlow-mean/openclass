from __future__ import annotations

import re

from app.models import (
    BlockStyle,
    BoardBlock,
    BoardDocument,
    BranchRef,
    CommitRecord,
    LearningRequirementSheet,
    Lesson,
    LessonHistoryGraph,
    TeachingGuide,
    TeachingGuideMapping,
    new_id,
    now_iso,
)


def slugify(value: str) -> str:
    lowered = re.sub(r"\s+", "-", value.strip().lower())
    lowered = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", lowered)
    return lowered.strip("-") or new_id("lesson")


def build_requirements(topic: str) -> LearningRequirementSheet:
    is_language = any(keyword in topic.lower() for keyword in ["french", "法语", "dialogue"])
    if is_language:
        return LearningRequirementSheet(
            theme=topic,
            learning_goal="用场景对话掌握真实交流所需的表达",
            level="初学者",
            known_background="需要从高频表达和语感切入",
            current_questions=["如何开口", "如何在真实场景中使用"],
            target_depth="能在场景里完成一轮对话",
            output_preference="对话示例 + 讲解 + 可模仿表达",
            boundary="先聚焦一个场景，不展开完整语法体系",
            board_scope=["场景设定", "核心表达", "示例对话", "替换练习"],
            success_criteria="用户能读懂并模仿一段场景对话",
        )
    return LearningRequirementSheet(
        theme=topic,
        learning_goal="理解概念、能跟着板书讲清楚并完成基础练习",
        level="初学到进阶之间",
        known_background="已有零散印象，但需要结构化讲解",
        current_questions=[f"{topic}的定义是什么", "它为什么重要", "应该怎么用"],
        target_depth="先做到能讲清基本定义并会做入门题",
        output_preference="定义 + 直觉 + 例题 + 总结",
        boundary="先不无限展开相邻学科的更大知识域",
        board_scope=["定义", "直觉", "核心公式或规律", "例题", "练习"],
        success_criteria="用户能复述核心概念并完成一题相关练习",
    )


def build_blocks(topic: str) -> list[BoardBlock]:
    normalized = topic.lower()
    if any(keyword in normalized for keyword in ["法语", "french", "dialogue"]):
        return [
            BoardBlock(
                type="heading",
                title=topic,
                content="目标：围绕当前场景进行听说训练，并保留可替换表达。",
                style=BlockStyle(font_size="xl", emphasis="accent"),
            ),
            BoardBlock(
                type="dialogue",
                title="场景对话",
                content="A: Bonjour, je cherche une station de metro.\nB: Elle est a deux rues d'ici.\nA: Merci beaucoup !",
                style=BlockStyle(font_size="lg"),
            ),
            BoardBlock(
                type="note",
                title="表达拆解",
                content="je cherche = 我在寻找；a deux rues d'ici = 离这里两条街。",
                style=BlockStyle(emphasis="callout"),
            ),
            BoardBlock(
                type="exercise",
                title="替换练习",
                content="把 metro 替换成 hotel、restaurant，再自己读一遍。",
            ),
        ]

    if any(keyword in normalized for keyword in ["勾股", "pythagorean", "triangle", "几何"]):
        return [
            BoardBlock(
                type="heading",
                title=topic,
                content="目标：理解直角三角形三边关系，并能完成一到两道基础题。",
                style=BlockStyle(font_size="xl", emphasis="accent"),
            ),
            BoardBlock(
                type="paragraph",
                title="直观定义",
                content="在直角三角形中，两条直角边的平方和，等于斜边的平方。",
            ),
            BoardBlock(
                type="formula",
                title="核心公式",
                content="a^2 + b^2 = c^2",
                style=BlockStyle(font_size="lg", alignment="center", emphasis="callout"),
            ),
            BoardBlock(
                type="note",
                title="使用条件",
                content="只有在直角三角形里，这个公式才能直接使用。",
                style=BlockStyle(emphasis="callout"),
            ),
            BoardBlock(
                type="exercise",
                title="例题",
                content="若两直角边为 3 和 4，斜边是多少？答案：5。",
            ),
        ]

    return [
        BoardBlock(
            type="heading",
            title=topic,
            content=f"目标：围绕“{topic}”先建立一份可持续演化的板书骨架。",
            style=BlockStyle(font_size="xl", emphasis="accent"),
        ),
        BoardBlock(
            type="paragraph",
            title="概念入口",
            content=f"先从“{topic}”最核心的定义、用途和直觉出发建立理解。",
        ),
        BoardBlock(
            type="note",
            title="为什么重要",
            content=f"把“{topic}”放回更大的知识地图里，说明它解决什么问题。",
            style=BlockStyle(emphasis="callout"),
        ),
        BoardBlock(
            type="exercise",
            title="练习建议",
            content="补一题最小可验证练习，检查是否真的理解。",
        ),
    ]


def build_teaching_guide(
    lesson_id: str,
    title: str,
    document: BoardDocument,
    requirements: LearningRequirementSheet,
) -> TeachingGuide:
    mappings: list[TeachingGuideMapping] = []
    for block in document.blocks:
        mode: str = "definition"
        if block.type in {"exercise", "dialogue"}:
            mode = "example"
        elif block.type == "note":
            mode = "intuition"
        elif block.type == "formula":
            mode = "analogy"
        mappings.append(
            TeachingGuideMapping(
                block_id=block.id,
                supports_goal=requirements.learning_goal,
                teaching_mode=mode,  # type: ignore[arg-type]
                focus_points=[block.title, block.content[:80]],
                optional_points=["视时间补充一个反例或替换题"],
                difficult_points=["若用户追问外层知识域，先判断是否要范围升级"],
                check_questions=[f"你能用自己的话复述“{block.title}”吗？"],
            )
        )

    return TeachingGuide(
        lesson_id=lesson_id,
        summary=f"讲解围绕《{title}》当前板书进行，始终服务于：{requirements.learning_goal}",
        structure_note="先让用户建立核心概念，再进入例题或对话练习，最后用总结块收束。",
        pacing="定义 -> 直觉 -> 例题/对话 -> 检查理解 -> 决定是否继续扩展",
        mappings=mappings,
        strategy="优先围绕板书解释，不脱离当前 lesson 自由发散。",
    )


def create_lesson(topic: str, requirements: LearningRequirementSheet | None = None) -> Lesson:
    requirements = requirements or build_requirements(topic)
    document = BoardDocument(title=topic, blocks=build_blocks(topic))
    lesson_id = new_id("lesson")
    guide = build_teaching_guide(lesson_id, topic, document, requirements)
    commit = CommitRecord(
        label="Initial board draft",
        message=f"Generated starter board for {topic}",
        branch_name="main",
        snapshot=document,
    )
    history = LessonHistoryGraph(
        branches={
            "main": BranchRef(
                name="main", head_commit_id=commit.id, base_commit_id=commit.id
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
        tags=[topic, *requirements.board_scope[:2]],
        board_document=document,
        learning_requirements=requirements,
        teaching_guide=guide,
        history_graph=history,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
