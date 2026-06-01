from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.openai_course_ai import BoardExplanationDirective, openai_course_ai


@dataclass(frozen=True)
class BoardDirectedExplanationResult:
    chatbot_message: str
    assistant_message_source: str
    directive_payload: dict[str, object] | None


def requirement_probe_instead_of_explanation_message(user_message: str) -> str:
    return (
        "当前没有板书侧讲解指令。请不要讲解，只继续探寻学习需求："
        "确认学习目标、当前水平、使用场景，或询问是否先生成/定位板书。\n"
        f"学习者请求：{user_message}"
    )


def generate_board_directed_explanation_message(
    *,
    lesson_title: str,
    learning_goal: str,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
    user_message: str,
    action_type: str,
    target_excerpt: str,
    interaction_mode: str = "ask",
    interaction_context: dict[str, Any] | None = None,
) -> BoardDirectedExplanationResult:
    directive = openai_course_ai.generate_board_explanation_directive(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        target_excerpt=target_excerpt,
        user_message=user_message,
        action_type=action_type,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        interaction_context=interaction_context,
    )
    directive_payload = directive.model_dump(mode="json") if directive else None
    if directive is None:
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson_title,
            learning_goal=learning_goal,
            board_summary=board_summary,
            resource_summary=resource_summary,
            conversation_summary=conversation_summary,
            user_message=requirement_probe_instead_of_explanation_message(user_message),
            selection_excerpt=target_excerpt,
            interaction_mode=interaction_mode,
            interaction_context=interaction_context,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        return BoardDirectedExplanationResult(
            chatbot_message=chatbot_message,
            assistant_message_source="chatbot_requirement_probe" if chatbot_message else "chatbot_empty",
            directive_payload=None,
        )

    if directive.status == "approved":
        gated_user_message = _board_directed_instruction_message(user_message=user_message, directive=directive)
        source = "chatbot_board_directed"
    else:
        gated_user_message = _board_directed_clarification_message(user_message=user_message, directive=directive)
        source = "chatbot_board_directed_clarification"

    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=gated_user_message,
        selection_excerpt=directive.target_excerpt or target_excerpt,
        interaction_mode=interaction_mode,
        interaction_context={
            **(interaction_context or {}),
            "board_explanation_directive": directive_payload,
        },
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return BoardDirectedExplanationResult(
        chatbot_message=chatbot_message,
        assistant_message_source=source if chatbot_message else "chatbot_empty",
        directive_payload=directive_payload,
    )


def _board_directed_instruction_message(*, user_message: str, directive: BoardExplanationDirective) -> str:
    constraints = "；".join(directive.constraints)
    parts = [
        "板书侧已允许 Chatbot 进行讲解。请只依据下面的板书反馈和指令回答学习者。",
        f"学习者请求：{user_message}",
        f"板书对象：{directive.target_summary}",
        f"板书依据：{directive.target_excerpt}",
        f"板书反馈：{directive.board_feedback}",
        f"讲解指令：{directive.teaching_instruction}",
        f"限制：{constraints}" if constraints else "",
    ]
    return "\n".join(part for part in parts if part.strip())


def _board_directed_clarification_message(*, user_message: str, directive: BoardExplanationDirective) -> str:
    parts = [
        "板书侧没有允许 Chatbot 直接讲解。请不要讲解，只根据板书侧反馈向学习者追问或说明需要先定位/补充板书。",
        f"学习者请求：{user_message}",
        f"板书侧状态：{directive.status}",
        f"原因：{directive.reason}",
        f"可追问方向：{directive.clarification_question}",
    ]
    return "\n".join(part for part in parts if part.strip())
