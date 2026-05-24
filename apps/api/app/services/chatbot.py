from __future__ import annotations

import re

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    SelectionRef,
)
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide, teach_first_section, teach_next_section
from app.services.course_runtime import effective_requirements
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.learning_requirement_manager import (
    is_generation_control_request,
    update_learning_requirements_from_chat,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import is_document_empty
from app.services.route_context import bind_ai_request_context
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus


MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8
EXPLAIN_REQUEST_PATTERN = re.compile(r"(讲解|解释|说明|讲一下|解释一下|帮我理解)")
EXPAND_REQUEST_PATTERN = re.compile(r"(扩写|扩展|补充|增加|添加)")
SIMPLIFY_REQUEST_PATTERN = re.compile(r"(简化|简单(?:一点|点|些)?|更简单|通俗|更容易懂|更好懂|好理解|容易理解|降低难度|浅显)")
REWRITE_REQUEST_PATTERN = re.compile(r"(改写|重写|修改|编辑|润色|优化|改(?:得|的)?(?:简单|通俗|容易|好懂)|换(?:个|一种)说法)")
TARGET_LOCATION_HINT_PATTERN = re.compile(r"(选中|这一段|这段|这部分|这里|前面|上面|下面|第.{0,8}[章节部分段]|定义|概念|例子|示例|结论|总结|表格|为什么)")
EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_GENERATION_ACTIONS = r"(生成|写|撰写|创建|整理|制作|设计|输出|产出|编写)"
DOCUMENT_ARTIFACT_NOUNS = (
    r"(文档|讲义|板书|版书|课文|文章|作文|报告|对话|练习|题目|试题|测验|课程|"
    r"教案|教程|学习计划|提纲|大纲|案例|表格|清单|材料|页面|章节|小节)"
)
DOCUMENT_ARTIFACT_REQUEST_PATTERN = re.compile(
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,48}}{DOCUMENT_ARTIFACT_NOUNS}"
    r"|"
    rf"{DOCUMENT_ARTIFACT_NOUNS}.{{0,24}}{DOCUMENT_GENERATION_ACTIONS}"
)


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


def _infer_board_task_action(request: ChatRequest, *, has_selection: bool, document_empty: bool) -> BoardTaskAction | None:
    if request.board_generation_action == "start":
        return "generate_board"
    message = _compact_text(request.message, limit=280)
    if request.interaction_mode == "direct_edit":
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
        return "rewrite_target"
    if REWRITE_REQUEST_PATTERN.search(message):
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
        return "rewrite_target"
    if has_selection and not document_empty:
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
    if EXPLAIN_REQUEST_PATTERN.search(message) and (has_selection or TARGET_LOCATION_HINT_PATTERN.search(message)):
        return "explain_target"
    if has_selection and not document_empty:
        return "explain_target"
    return None


def _prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
) -> BoardTaskAction | None:
    if requirement_action in EDIT_ACTIONS:
        return requirement_action
    if requirement_action == "explain_target" and inferred is None:
        return requirement_action
    return inferred


def _requests_document_artifact_generation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact:
        return False
    return bool(DOCUMENT_ARTIFACT_REQUEST_PATTERN.search(compact))


def _with_task_details(
    requirements: LearningRequirementSheet,
    *,
    action_type: BoardTaskAction | None,
    instruction: str,
    focus: BoardFocusRef | None = None,
    resolution: FocusResolution | None = None,
) -> LearningRequirementSheet:
    updated = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    updated.action_type = action_type
    updated.action_instruction = _compact_text(instruction, limit=240)
    if focus is not None:
        updated.target_location = focus
        updated.location_status = "selected" if focus.confidence >= 0.9 else "resolved"
        updated.location_clarification_question = ""
    elif resolution is not None:
        updated.target_location = None
        updated.location_status = "ambiguous" if resolution.candidates else "missing"
        updated.location_clarification_question = resolution.question
    elif action_type == "generate_board":
        updated.location_status = "resolved"
    return updated


def _task_metadata(
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    focus: BoardFocusRef | None = None,
    focus_candidates: list[BoardFocusRef] | None = None,
    requirement_cleared: bool = False,
) -> dict[str, object]:
    return {
        "task_requirement_sheet": requirements.model_dump(mode="json"),
        "learning_clarification": learning_clarification.model_dump(mode="json"),
        "resolved_focus": focus.model_dump(mode="json") if focus else None,
        "focus_candidates": [candidate.model_dump(mode="json") for candidate in (focus_candidates or [])],
        "requirement_cleared": requirement_cleared,
        "active_requirement_sheet_after": None if requirement_cleared else requirements.model_dump(mode="json"),
    }


def _clear_task_requirements(lesson: Lesson) -> None:
    lesson.learning_requirements = None


def _focus_candidate_context(resolution: FocusResolution) -> str:
    if not resolution.candidates:
        return resolution.question
    lines = [resolution.question]
    for index, candidate in enumerate(resolution.candidates[:3], start=1):
        path = " / ".join(candidate.heading_path) if candidate.heading_path else "当前板书"
        excerpt = _compact_text(candidate.excerpt, limit=180)
        lines.append(f"{index}. {path}：{excerpt}")
    return "\n".join(lines)


def _generate_focus_candidate_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    resolution: FocusResolution,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=(
            f"用户原始请求：{request.message}\n"
            "系统还不能唯一确定用户要操作的板书位置。"
            "请根据候选位置，用自然语言让用户确认目标，不要执行讲解或编辑。\n"
            f"候选位置：\n{_focus_candidate_context(resolution)}"
        ),
        selection_excerpt=None,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot" if chatbot_message else "chatbot_empty"


def _has_identified_learning_topic(learning_clarification: LearningClarificationStatus) -> bool:
    return any(
        item.category == "learning" and item.value.strip()
        for item in learning_clarification.key_facts
    )


def _should_auto_generate_board_from_teaching_start(
    *,
    lesson: Lesson,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    return (
        is_document_empty(lesson.board_document)
        and learning_clarification.forced_start
        and learning_clarification.can_start
        and not learning_clarification.ready_for_board
        and _has_identified_learning_topic(learning_clarification)
    )


def _latest_learning_clarification(
    lesson: Lesson,
    *,
    requirements,
) -> LearningClarificationStatus:
    for commit in reversed(lesson.history_graph.commits):
        raw = commit.metadata.get("learning_clarification") if isinstance(commit.metadata, dict) else None
        if not raw:
            continue
        try:
            return LearningClarificationStatus.model_validate(raw)
        except Exception:
            continue
    summary = requirements.learning_goal or "学习需求已确认，可以生成板书。"
    return LearningClarificationStatus(
        progress=100,
        label="准备生成板书",
        reason=summary,
        missing_items=[],
        can_start=True,
        summary=summary,
        ready_for_board=True,
    )


def _response(
    *,
    workspace,
    package,
    lesson: Lesson,
    chatbot_message: str,
    requirements,
    learning_clarification: LearningClarificationStatus,
    board_decision: BoardDecision,
    teaching_progress=None,
    resolved_focus: BoardFocusRef | None = None,
    focus_candidates: list[BoardFocusRef] | None = None,
    requirement_cleared: bool = False,
) -> ChatResponse:
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        patch_proposal=None,
        scope_options=[],
        resource_matches=[],
        reference_prompt=None,
        board_edit_prompt=None,
        selected_reference=None,
        resolved_focus=resolved_focus,
        focus_candidates=focus_candidates or [],
        requirement_cleared=requirement_cleared,
        created_lesson=None,
        teaching_progress=teaching_progress,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


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
    visible_package = workspace_state.package_context_for_lesson(workspace, package, lesson.id)
    selection_excerpt = _selection_excerpt(request.selection, selection_text)
    action_type = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=is_document_empty(lesson.board_document),
    )

    if request.board_generation_action == "start":
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        requirements = _with_task_details(
            requirements,
            action_type="generate_board",
            instruction=request.message,
        )
        edit_outcome = generate_from_requirements(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_instruction=request.message,
        )
        chatbot_message = edit_outcome.chatbot_message
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            requirements = lesson.learning_requirements
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
        requirement_cleared = edit_outcome.changed
        metadata = {
            "kind": "board_document_generation",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "board_generation_action": request.board_generation_action,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
        }

        commit_operations(
            lesson,
            [],
            label="Board document generation",
            message="Generated board document from the learning requirement sheet",
            new_document=lesson.board_document,
            metadata=metadata,
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_cleared=requirement_cleared,
        )

    if request.teaching_action in {"continue", "restart"}:
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        if request.teaching_action == "restart":
            lesson.board_teaching_progress = None
            teaching_result = teach_first_section(
                lesson=lesson,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
            )
        else:
            teaching_result = teach_next_section(
                lesson=lesson,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
            )
        commit_operations(
            lesson,
            [],
            label="Board teaching turn",
            message="Recorded a section-by-section board teaching turn",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": teaching_result.chatbot_message,
                "assistant_message_source": "chatbot",
                "interaction_mode": request.interaction_mode,
                "teaching_action": request.teaching_action,
                "teaching_progress": teaching_result.progress_view.model_dump(mode="json"),
                "learning_clarification": learning_clarification.model_dump(mode="json"),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=teaching_result.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是分节讲解，不修改板书。"),
            teaching_progress=teaching_result.progress_view,
        )

    if request.interaction_mode == "direct_edit":
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        action_type = _prefer_requirement_action(action_type, requirements.action_type) or "rewrite_target"
        resolution = resolve_board_focus(
            lesson=lesson,
            user_message=request.message,
            selection=request.selection,
            selection_text=selection_text,
            action_type=action_type,
        )
        requirements = _with_task_details(
            requirements,
            action_type=action_type,
            instruction=request.message,
            focus=resolution.focus,
            resolution=resolution,
        )
        if not resolution.resolved:
            lesson.learning_requirements = requirements
            chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
                lesson=lesson,
                requirements=requirements,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                resolution=resolution,
            )
            commit_operations(
                lesson,
                [],
                label="Board focus clarification",
                message="Asked the learner to confirm the board focus before editing",
                new_document=lesson.board_document,
                metadata={
                    "kind": "chat_flow",
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=None,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=False,
                    ),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
            )

        edit_outcome = edit_existing_document(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_instruction=request.message,
            selection_excerpt=selection_excerpt,
            focus=resolution.focus,
        )
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            requirements = lesson.learning_requirements
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
        requirement_cleared = edit_outcome.changed
        commit_operations(
            lesson,
            [],
            label="Board document edit",
            message="Applied a Board Document Editor AI update",
            new_document=lesson.board_document,
            metadata={
                "kind": "board_document_edit",
                "user_message": request.message,
                "assistant_message": edit_outcome.chatbot_message,
                "assistant_message_source": edit_outcome.assistant_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                "selection_text": selection_excerpt,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_section_titles": edit_outcome.section_titles,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=resolution.focus,
                    focus_candidates=resolution.candidates,
                    requirement_cleared=requirement_cleared,
                ),
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            resolved_focus=resolution.focus,
            focus_candidates=resolution.candidates,
            requirement_cleared=requirement_cleared,
        )

    if action_type in {*EDIT_ACTIONS, "explain_target"} and not is_document_empty(lesson.board_document):
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        action_type = _prefer_requirement_action(action_type, requirements.action_type)
        resolution = resolve_board_focus(
            lesson=lesson,
            user_message=request.message,
            selection=request.selection,
            selection_text=selection_text,
            action_type=action_type,
        )
        requirements = _with_task_details(
            requirements,
            action_type=action_type,
            instruction=request.message,
            focus=resolution.focus,
            resolution=resolution,
        )
        if not resolution.resolved:
            lesson.learning_requirements = requirements
            chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
                lesson=lesson,
                requirements=requirements,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                resolution=resolution,
            )
            commit_operations(
                lesson,
                [],
                label="Board focus clarification",
                message="Asked the learner to confirm the board focus before acting",
                new_document=lesson.board_document,
                metadata={
                    "kind": "chat_flow",
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=None,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=False,
                    ),
                },
            )
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
            )

        if action_type in EDIT_ACTIONS:
            edit_outcome = edit_existing_document(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
                user_instruction=request.message,
                selection_excerpt=selection_excerpt,
                focus=resolution.focus,
            )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                requirements = lesson.learning_requirements
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document edit",
                message="Applied a Board Document Editor AI update",
                new_document=lesson.board_document,
                metadata={
                    "kind": "board_document_edit",
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "selection_text": selection_excerpt,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        focus=resolution.focus,
                        focus_candidates=resolution.candidates,
                        requirement_cleared=requirement_cleared,
                    ),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            workspace_state.save_workspace_for_user(user_id, workspace)
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                resolved_focus=resolution.focus,
                focus_candidates=resolution.candidates,
                requirement_cleared=requirement_cleared,
            )

        focus_excerpt = focus_context(resolution.focus) if resolution.focus else ""
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=request.message,
            selection_excerpt=focus_excerpt,
            interaction_mode=request.interaction_mode,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"

        requirement_cleared = bool(chatbot_message)
        commit_operations(
            lesson,
            [],
            label="Board target explanation",
            message="Answered a learner question about a resolved board segment",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=resolution.focus,
                    focus_candidates=resolution.candidates,
                    requirement_cleared=requirement_cleared,
                ),
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是目标文段讲解，不修改板书。"),
            resolved_focus=resolution.focus,
            focus_candidates=resolution.candidates,
            requirement_cleared=requirement_cleared,
        )

    if is_generation_control_request(request.message) or _requests_document_artifact_generation(
        request.message
    ):
        requirement_conversation = [
            *request.conversation,
            ConversationTurn(role="user", content=request.message),
        ]
        requirements, learning_clarification = update_learning_requirements_from_chat(
            lesson=lesson,
            resources=visible_package.resources,
            conversation=requirement_conversation,
            user_message=request.message,
            chatbot_message="",
        )
        lesson.learning_requirements = requirements
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=learning_clarification.summary or requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=request.message,
            selection_excerpt=selection_excerpt,
            interaction_mode=request.interaction_mode,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
        chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"

        commit_operations(
            lesson,
            [],
            label="Chat turn",
            message="Recorded a learner and PM handoff chat turn",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason="本轮是需求确认到板书生成的交接，不自动写入板书。"),
        )

    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(visible_package.resources),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=selection_excerpt,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    chatbot_message_source = "chatbot" if chatbot_message else "chatbot_empty"
    requirement_conversation = [
        *request.conversation,
        ConversationTurn(role="user", content=request.message),
    ]
    if chatbot_message:
        requirement_conversation.append(ConversationTurn(role="assistant", content=chatbot_message))
    requirements, learning_clarification = update_learning_requirements_from_chat(
        lesson=lesson,
        resources=visible_package.resources,
        conversation=requirement_conversation,
        user_message=request.message,
        chatbot_message=chatbot_message,
    )
    lesson.learning_requirements = requirements

    board_decision = BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。")
    board_edit_metadata: dict[str, object] = {}
    requirement_cleared = False
    if _should_auto_generate_board_from_teaching_start(
        lesson=lesson,
        learning_clarification=learning_clarification,
    ):
        requirements = _with_task_details(
            requirements,
            action_type="generate_board",
            instruction=request.message,
        )
        edit_outcome = generate_from_requirements(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(requirement_conversation),
            user_instruction=request.message,
        )
        board_decision = edit_outcome.board_decision
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            requirements = lesson.learning_requirements
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
            requirement_cleared = True
            board_edit_metadata = {
                "auto_board_generation": True,
                "board_generation_action": "auto_start_from_teaching",
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_section_titles": edit_outcome.section_titles,
            }

    commit_operations(
        lesson,
        [],
        label="Chat turn",
        message="Recorded a learner and chatbot chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "chat_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **board_edit_metadata,
        },
    )
    if requirement_cleared:
        _clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_for_user(user_id, workspace)
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=board_decision,
        requirement_cleared=requirement_cleared,
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        trace_prefix="chat",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        return _chat_response(lesson_id=lesson_id, request=request, user_id=user_id)


def document_ai_edit_request(
    lesson_id: str,
    instruction: str,
    selection_text: str | None,
    conversation: list[ConversationTurn],
    *,
    user_id: str,
) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/document/ai-edit",
        trace_prefix="document_ai_edit",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
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
