from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.board_explanation_gate import (
    generate_board_directed_explanation_message as _gate_board_directed_explanation_message,
)
from app.services.openai_course_ai import openai_course_ai


@dataclass(frozen=True)
class ChatbotRoleReply:
    chatbot_message: str
    assistant_message_source: str
    directive_payload: dict[str, object] | None = None


def generate_chatbot_role_reply(
    *,
    lesson_title: str,
    learning_goal: str,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
    user_message: str,
    selection_excerpt: str | None = None,
    interaction_mode: str = "ask",
    interaction_context: dict[str, Any] | None = None,
    recommendation_context: str | None = None,
    assistant_message_source: str = "chatbot",
    empty_message_source: str = "chatbot_empty",
) -> ChatbotRoleReply:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=user_message,
        selection_excerpt=selection_excerpt,
        interaction_mode=interaction_mode,
        interaction_context=interaction_context,
        recommendation_context=recommendation_context,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return ChatbotRoleReply(
        chatbot_message=chatbot_message,
        assistant_message_source=assistant_message_source if chatbot_message else empty_message_source,
    )


def generate_board_directed_role_reply(
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
) -> ChatbotRoleReply:
    directed = _gate_board_directed_explanation_message(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=user_message,
        action_type=action_type,
        target_excerpt=target_excerpt,
        interaction_mode=interaction_mode,
        interaction_context=interaction_context,
    )
    return ChatbotRoleReply(
        chatbot_message=directed.chatbot_message,
        assistant_message_source=directed.assistant_message_source,
        directive_payload=directed.directive_payload,
    )


def generate_focus_clarification_reply(
    *,
    lesson_title: str,
    learning_goal: str,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
    user_message: str,
    focus_candidate_context: str,
    interaction_mode: str = "ask",
) -> ChatbotRoleReply:
    return generate_chatbot_role_reply(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=(
            f"用户原始请求：{user_message}\n"
            "系统还不能唯一确定用户要操作的板书位置。"
            "请根据候选位置，用自然语言让用户确认目标，不要执行讲解或编辑。\n"
            f"候选位置：\n{focus_candidate_context}"
        ),
        selection_excerpt=None,
        interaction_mode=interaction_mode,
    )


def generate_board_task_clarification_reply(
    *,
    lesson_title: str,
    learning_goal: str,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
    visible_board_task: dict[str, object],
    clarification_context: str,
    interaction_mode: str = "ask",
) -> ChatbotRoleReply:
    return generate_chatbot_role_reply(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=(
            "当前已有板书任务清单还不完整，不能执行写、改、讲或聊。"
            "请只自然追问一个最关键缺项，不要讲解，也不要承诺已经改写文档。\n"
            f"任务清单：{visible_board_task}\n"
            f"追问方向：{clarification_context}"
        ),
        selection_excerpt=None,
        interaction_mode=interaction_mode,
        assistant_message_source="chatbot_board_task_clarification",
    )


def generate_post_initial_board_generation_reply(
    *,
    lesson_title: str,
    learning_goal: str,
    board_summary: str,
    resource_summary: str,
    requirement_context: dict[str, object],
    editor_summary: str,
    section_titles: list[str],
) -> ChatbotRoleReply:
    ai_reply = openai_course_ai.generate_post_board_generation_reply(
        lesson_title=lesson_title,
        learning_goal=learning_goal,
        board_summary=board_summary,
        resource_summary=resource_summary,
        requirement_context=requirement_context,
        editor_summary=editor_summary,
        section_titles=section_titles,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return ChatbotRoleReply(
        chatbot_message=chatbot_message,
        assistant_message_source="chatbot_post_board_generation" if chatbot_message else "chatbot_empty",
    )
