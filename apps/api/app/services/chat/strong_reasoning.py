from __future__ import annotations

import re

from app.models import ChatRequest, Lesson
from app.services.chat.context import compact_text
from app.services.openai_course_ai import openai_course_ai


COMPLEX_REASONING_REQUEST_PATTERN = re.compile(
    r"(深入|深度|严谨|复杂|难题|多步骤|推理|推导|证明|系统分析|仔细分析|完整分析|高质量|complex|reasoning)",
    re.IGNORECASE,
)
PRO_REASONING_REQUEST_PATTERN = re.compile(r"(最高|最强|pro|专家级|特别难|高风险|高价值)", re.IGNORECASE)


def requests_complex_reasoning(text: str) -> bool:
    compact = compact_text(text, limit=280)
    return bool(compact and COMPLEX_REASONING_REQUEST_PATTERN.search(compact))


def chatbot_message_with_solver_context(
    *,
    lesson: Lesson,
    request: ChatRequest,
    user_message: str,
    target_excerpt: str | None,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
) -> tuple[str, dict[str, object]]:
    if not requests_complex_reasoning(request.message) or not getattr(openai_course_ai, "client", None):
        return user_message, {}
    solution = openai_course_ai.solve_complex_problem(
        lesson_title=lesson.title,
        question=request.message,
        target_excerpt=compact_text(target_excerpt, limit=1600),
        board_summary=compact_text(board_summary, limit=2400),
        resource_summary=compact_text(resource_summary, limit=1600),
        conversation_summary=conversation_summary,
        desired_output="给 Chatbot 的隐藏解题材料，由 Chatbot 面向学习者直接讲答案。",
        high_value=bool(PRO_REASONING_REQUEST_PATTERN.search(request.message)),
    )
    if solution is None:
        return user_message, {}
    solver_context = (
        "隐藏强推理工具已给出解题材料。请仍以 OpenClass Chatbot 的口吻直接回答学习者，"
        "不要提到另一个模型或内部工具。\n"
        f"结论摘要：{solution.summary}\n"
        f"可转述答案材料：{solution.answer}\n"
        f"不确定性或前提：{solution.limits or '无'}\n"
        f"置信度：{solution.confidence}"
    )
    metadata = {
        "strong_reasoning_tool": {
            "model": solution.model,
            "reasoning_effort": solution.reasoning_effort,
            "confidence": solution.confidence,
            "summary": solution.summary,
            "limits": solution.limits,
        }
    }
    return f"{user_message}\n\n{solver_context}", metadata
