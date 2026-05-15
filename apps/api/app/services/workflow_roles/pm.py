from __future__ import annotations

from app.models import (
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
)
from app.services.course_runtime import effective_requirements
from app.services.workflow_roles.shared import (
    compact,
    dedupe,
    is_low_substance_message,
    message_topic,
)


def update_requirements(lesson: Lesson, request: ChatRequest) -> LearningRequirementSheet:
    base = effective_requirements(lesson)
    topic = message_topic(lesson, request)
    selected = compact(request.selection.excerpt, limit=120) if request.selection else ""

    current_questions = dedupe(
        [
            request.message,
            *base.current_questions,
        ],
        limit=8,
    )
    checklist_seed = [*base.learning_need_checklist]
    if not is_low_substance_message(topic):
        checklist_seed.append(topic)
    if selected:
        checklist_seed.append(f"结合用户选中的板书片段：{selected}")
    if request.resource_reference_action == "confirm":
        checklist_seed.append("结合已确认的参考资料章节更新板书与讲解")
    if request.interaction_mode == "direct_edit":
        checklist_seed.append("按用户指令直接编辑当前板书")

    return LearningRequirementSheet(
        theme=lesson.title,
        learning_goal=topic if not is_low_substance_message(topic) else "等待具体学习主题",
        level=base.level or "根据用户背景和当前资料动态调整",
        known_background=base.known_background or "用户背景由后续互动继续补全",
        current_questions=current_questions or [topic],
        learning_need_checklist=dedupe(checklist_seed, limit=10),
        target_depth=base.target_depth or "能说清主线、关键关系，并完成一次理解检查",
        output_preference=base.output_preference or "根据请求在讲解、讨论和板书写入之间切换",
        boundary=base.boundary or "围绕当前学习请求和已有资料推进",
        board_scope=base.board_scope,
        success_criteria=base.success_criteria or "用户能复述本轮主线，并指出下一步想深入的位置",
        risk_notes=dedupe(base.risk_notes, limit=6),
    )


def clarification_status(
    requirements: LearningRequirementSheet,
    *,
    can_start: bool,
    reason: str,
) -> LearningClarificationStatus:
    checklist_score = min(25, len(requirements.learning_need_checklist) * 4)
    question_score = min(20, len(requirements.current_questions) * 4)
    progress = 45 + checklist_score + question_score + (20 if can_start else 0)
    return LearningClarificationStatus(
        progress=min(100, progress),
        label="可以开始" if can_start else "需要补充",
        reason=reason,
        missing_items=[] if can_start else ["请补充你最想解决的问题或目标"],
        can_start=can_start,
        forced_start=can_start,
    )

