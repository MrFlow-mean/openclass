from __future__ import annotations

import re

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
    Lesson,
    ResourceLibraryItem,
    SelectionRef,
)
from app.services import workspace_state
from app.services.course_runtime import effective_requirements
from app.services.history import commit_operations
from app.services.openai_course_ai import openai_course_ai


MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _board_summary(lesson: Lesson) -> str:
    document = lesson.board_document
    content = _compact_text(document.content_text, limit=MAX_CONTEXT_CHARS)
    if content:
        return content
    return document.title or lesson.title


def _resource_summary(resources: list[ResourceLibraryItem]) -> str:
    lines: list[str] = []
    for resource in resources[:6]:
        chapter_titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        if chapter_titles:
            lines.append(f"{resource.name}: {' / '.join(chapter_titles)}")
        else:
            lines.append(resource.name)
    return "\n".join(lines) or "暂无已上传资料摘要"


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-MAX_CONVERSATION_TURNS:]
    return "\n".join(f"{turn.role}: {_compact_text(turn.content, limit=500)}" for turn in turns if turn.content.strip())


def _selection_excerpt(selection: SelectionRef | None, fallback: str | None = None) -> str | None:
    excerpt = selection.excerpt if selection else fallback
    compact = _compact_text(excerpt, limit=1200)
    return compact or None


def _clarification_status(lesson: Lesson) -> LearningClarificationStatus:
    requirements = effective_requirements(lesson)
    progress = 70 if lesson.board_document.content_text.strip() else 40
    missing_items: list[str] = []
    if not requirements.known_background.strip():
        missing_items.append("学习背景")
    if not requirements.current_questions:
        missing_items.append("当前问题")
    if missing_items:
        progress = min(progress, 55)
    return LearningClarificationStatus(
        progress=progress,
        label="可继续对话" if progress >= 60 else "可先问答",
        reason="聊天机器人会基于当前课程、讲义、资料和最近对话持续回答。",
        missing_items=missing_items,
        can_start=True,
        forced_start=False,
    )


def _fallback_teacher_message(
    *,
    lesson: Lesson,
    request: ChatRequest,
    selection_excerpt: str | None,
) -> str:
    message = _compact_text(request.message, limit=600)
    board_summary = _board_summary(lesson)
    parts = [
        f"我先根据当前课程上下文回答：你问的是“{message}”。",
        f"当前讲义线索是：{board_summary}",
    ]
    if selection_excerpt:
        parts.append(f"你引用的内容是：{selection_excerpt}")
    parts.append("如果你希望我继续，可以直接追问“再具体一点”“举个例子”或“按步骤讲”。")
    return "\n\n".join(parts)


def _chat_response(
    *,
    lesson_id: str,
    request: ChatRequest,
    user_id: str,
    selection_text: str | None = None,
) -> ChatResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    requirements = effective_requirements(lesson)
    lesson.learning_requirements = requirements
    visible_package = workspace_state.package_context_for_lesson(workspace, package, lesson.id)
    selection_excerpt = _selection_excerpt(request.selection, selection_text)
    ai_reply = openai_course_ai.generate_teacher_chat(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(visible_package.resources),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=selection_excerpt,
        interaction_mode=request.interaction_mode,
    )
    teacher_message = (ai_reply.teacher_message if ai_reply else "").strip()
    teacher_message_source = "ai" if teacher_message else "fallback"
    if not teacher_message:
        teacher_message = _fallback_teacher_message(
            lesson=lesson,
            request=request,
            selection_excerpt=selection_excerpt,
        )

    commit_operations(
        lesson,
        [],
        label="Chat turn",
        message="Recorded a learner and AI teacher chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": teacher_message,
            "assistant_message_source": teacher_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_for_user(user_id, workspace)
    return ChatResponse(
        teacher_message=teacher_message,
        learning_requirement_sheet=requirements,
        learning_clarification=_clarification_status(lesson),
        board_decision=BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。"),
        needs_clarification=False,
        clarification_questions=[],
        patch_proposal=None,
        scope_options=[],
        resource_matches=[],
        reference_prompt=None,
        board_edit_prompt=None,
        selected_reference=None,
        created_lesson=None,
        teaching_progress=None,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    return _chat_response(lesson_id=lesson_id, request=request, user_id=user_id)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    request = ChatRequest(
        message=instruction,
        interaction_mode="direct_edit",
        conversation=conversation,
    )
    return _chat_response(
        lesson_id=lesson_id,
        request=request,
        user_id=user_id,
        selection_text=selection_text,
    )
