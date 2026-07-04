from __future__ import annotations

from app.models import AgentTurnDecision, ChatRequest, Lesson
from app.services.board_teaching_orchestrator import (
    should_continue_board_teaching,
    should_start_board_teaching,
)


def decide_agent_turn(
    *,
    lesson: Lesson,
    request: ChatRequest,
    board_document_state,
) -> AgentTurnDecision:
    if request.board_generation_action == "start":
        blockers = [] if board_document_state.status == "empty" else ["board_document_not_empty"]
        return AgentTurnDecision(
            route="blank_board_generate",
            reason="用户显式要求从已冻结学习需求生成右侧板书。",
            required_role="BoardEditor",
            blockers=blockers,
            next_step="冻结需求校验通过后生成右侧板书。",
            needs_user_confirmation=False,
        )

    if board_document_state.status == "empty":
        return AgentTurnDecision(
            route="blank_requirement_refine",
            reason="当前右侧板书为空，本轮先判断是否需要维护第一层学习需求。",
            required_role="RequirementManager",
            next_step="普通聊天则直接回复；学习需求不清晰则继续收敛；清晰后等待用户确认生成板书。",
            needs_user_confirmation=False,
        )

    if lesson.active_interaction_session is not None:
        return AgentTurnDecision(
            route="interaction_session_turn",
            reason="当前已有进行中的规则互动会话。",
            required_role="InteractionSession",
            next_step="先判断用户输入是否继续当前规则、纠错、退出或发起新任务。",
            needs_user_confirmation=False,
        )

    if should_continue_board_teaching(lesson, request):
        return AgentTurnDecision(
            route="board_teaching_continue",
            reason="当前板书讲解进度正在等待继续，本轮用户表达继续。",
            required_role="BoardTeaching",
            next_step="根据当前讲解进度继续讲下一段板书内容。",
            needs_user_confirmation=False,
        )

    if should_start_board_teaching(lesson, request):
        return AgentTurnDecision(
            route="post_generation_teaching_start",
            reason="用户确认生成后的承接问题，或明确要求从头讲解当前板书。",
            required_role="BoardTeaching",
            next_step="从第一段可讲解板书内容开始讲解。",
            needs_user_confirmation=False,
        )

    return AgentTurnDecision(
        route="board_task_refine_or_execute",
        reason="当前右侧板书已有内容，本轮进入第二层已有板书任务清单链路。",
        required_role="BoardTaskManager",
        next_step="先维护 BoardTaskRequirementSheet；清单完整后定位并执行讲解、补写、改写或规则互动。",
        needs_user_confirmation=False,
    )
