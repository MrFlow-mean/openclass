from __future__ import annotations

import re

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    SelectionRef,
)
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.board_teaching import build_board_teaching_guide, teach_first_section, teach_next_section
from app.services.course_runtime import effective_requirements
from app.services.course_runtime import refresh_lesson_runtime
from app.services.history import commit_operations
from app.services.interaction_rules import (
    apply_interaction_decision,
    build_interaction_start,
    decide_interaction_turn,
    interaction_context_payload,
    interaction_session_metadata,
    should_start_interaction,
)
from app.services.learning_requirement_manager import (
    is_generation_control_request,
    update_learning_requirements_from_chat,
)
from app.services.openai_course_ai import openai_course_ai
from app.services.rich_document import is_document_empty
from app.services.route_context import bind_ai_request_context
from app.services.resource_resolver import ResourceResolution, resolve_resource_reference
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus


MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8
EXPLAIN_REQUEST_PATTERN = re.compile(r"(讲解|解释|说明|讲一下|解释一下|帮我理解)")
EXPAND_REQUEST_PATTERN = re.compile(r"(扩写|扩展|补充|增加|添加)")
SIMPLIFY_REQUEST_PATTERN = re.compile(r"(简化|简单(?:一点|点|些)?|更简单|通俗|更容易懂|更好懂|好理解|容易理解|降低难度|浅显)")
REWRITE_REQUEST_PATTERN = re.compile(r"(改写|重写|修改|编辑|润色|优化|改(?:得|的)?(?:简单|通俗|容易|好懂)|换(?:个|一种)说法)")
TARGET_LOCATION_HINT_PATTERN = re.compile(r"(选中|这一段|这段|这部分|这里|前面|上面|下面|第.{0,8}[章节部分段]|定义|概念|例子|示例|结论|总结|表格|为什么)")
RESOURCE_REFERENCE_HINT_PATTERN = re.compile(r"(资料|材料|文档|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word|章节|小节|第.{0,8}[章节部分])", re.IGNORECASE)
LEARNING_START_REQUEST_PATTERN = re.compile(r"(我要学|我想学|想学习|学习一下|开始学|帮我学|学一学)")
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
COMPLEX_REASONING_REQUEST_PATTERN = re.compile(
    r"(深入|深度|严谨|复杂|难题|多步骤|推理|推导|证明|系统分析|仔细分析|完整分析|高质量|complex|reasoning)",
    re.IGNORECASE,
)
PRO_REASONING_REQUEST_PATTERN = re.compile(r"(最高|最强|pro|专家级|特别难|高风险|高价值)", re.IGNORECASE)


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


def _resource_summary_with_reference(
    resources: list[ResourceLibraryItem],
    reference: ResourceReferenceContext | None,
) -> str:
    parts = [_resource_summary(resources)]
    reference_excerpt = _resource_context_excerpt(reference)
    if reference_excerpt:
        parts.append(reference_excerpt)
    return "\n\n".join(parts)


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-MAX_CONVERSATION_TURNS:]
    return "\n".join(f"{turn.role}: {_compact_text(turn.content, limit=500)}" for turn in turns if turn.content.strip())


def _requests_complex_reasoning(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and COMPLEX_REASONING_REQUEST_PATTERN.search(compact))


def _chatbot_message_with_solver_context(
    *,
    lesson: Lesson,
    request: ChatRequest,
    user_message: str,
    target_excerpt: str | None,
    board_summary: str,
    resource_summary: str,
    conversation_summary: str,
) -> tuple[str, dict[str, object]]:
    if not _requests_complex_reasoning(request.message) or not getattr(openai_course_ai, "client", None):
        return user_message, {}
    solution = openai_course_ai.solve_complex_problem(
        lesson_title=lesson.title,
        question=request.message,
        target_excerpt=_compact_text(target_excerpt, limit=1600),
        board_summary=_compact_text(board_summary, limit=2400),
        resource_summary=_compact_text(resource_summary, limit=1600),
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
    if not has_selection and RESOURCE_REFERENCE_HINT_PATTERN.search(message):
        return None
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


def _requests_resource_backed_answer(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and RESOURCE_REFERENCE_HINT_PATTERN.search(compact))


def _requests_learning_start(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and LEARNING_START_REQUEST_PATTERN.search(compact))


def _should_prompt_resource_reference(text: str) -> bool:
    return (
        _requests_resource_backed_answer(text)
        or _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def _should_generate_board_after_reference_confirmation(text: str) -> bool:
    return (
        _requests_document_artifact_generation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def _resource_context_excerpt(reference: ResourceReferenceContext | None) -> str | None:
    if reference is None:
        return None
    lines = [
        f"参考资料：{reference.resource_name} / {reference.chapter_title}",
        f"资料摘要：{reference.summary}",
    ]
    if reference.teaching_points:
        lines.append("讲解要点：" + "；".join(reference.teaching_points[:4]))
    for chunk in reference.chunks[:4]:
        lines.append(f"{chunk.title}：{_compact_text(chunk.excerpt, limit=520)}")
    return "\n".join(line for line in lines if line.strip())


def _merge_selection_and_reference(
    selection_excerpt: str | None,
    reference: ResourceReferenceContext | None,
) -> str | None:
    reference_excerpt = _resource_context_excerpt(reference)
    return "\n\n".join(part for part in [selection_excerpt, reference_excerpt] if part)


def _reference_metadata(
    *,
    resolution: ResourceResolution,
) -> dict[str, object]:
    return {
        "resource_matches": [match.model_dump(mode="json") for match in resolution.matches],
        "reference_prompt": (
            resolution.reference_prompt.model_dump(mode="json") if resolution.reference_prompt else None
        ),
        "selected_reference": (
            {
                "resource_id": resolution.selected_reference.resource_id,
                "chapter_id": resolution.selected_reference.chapter_id,
                "resource_name": resolution.selected_reference.resource_name,
                "chapter_title": resolution.selected_reference.chapter_title,
                "summary": resolution.selected_reference.summary,
            }
            if resolution.selected_reference
            else None
        ),
        "resource_resolution_status": resolution.status,
    }


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
    resource_matches: list[ResourceMatch] | None = None,
    reference_prompt: ResourceReferencePrompt | None = None,
    selected_reference: ResourceReferenceContext | None = None,
    interaction_decision: InteractionTurnDecision | None = None,
    requirement_cleared: bool = False,
) -> ChatResponse:
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=lesson.learning_requirements,
        active_interaction_session=lesson.active_interaction_session,
        interaction_decision=interaction_decision,
        learning_clarification=learning_clarification,
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        patch_proposal=None,
        scope_options=[],
        resource_matches=resource_matches or [],
        reference_prompt=reference_prompt,
        board_edit_prompt=None,
        selected_reference=selected_reference,
        resolved_focus=resolved_focus,
        focus_candidates=focus_candidates or [],
        requirement_cleared=requirement_cleared,
        created_lesson=None,
        teaching_progress=teaching_progress,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _generate_interaction_chatbot_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    session: InteractionSession,
    decision: InteractionTurnDecision | None,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=request.message,
        selection_excerpt=session.reference_context,
        interaction_mode="interaction_rule",
        interaction_context=interaction_context_payload(session=session, decision=decision),
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_interaction" if chatbot_message else "chatbot_empty"


def _handle_existing_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None:
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    decision = decide_interaction_turn(
        lesson=lesson,
        session=session_before,
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection_excerpt=selection_excerpt,
    )
    if decision is None:
        chatbot_message = ""
        lesson.active_interaction_session = session_before
        commit_operations(
            lesson,
            [],
            label="Interaction turn",
            message="Recorded an interaction-rule turn without a route decision",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": "interaction_decision_empty",
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=session_before),
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
            board_decision=BoardDecision(action="no_change", reason=""),
        )

    session_after = apply_interaction_decision(session_before, decision)
    reply_session = session_after or session_before
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=reply_session,
        decision=decision,
    )
    commit_operations(
        lesson,
        [],
        label="Interaction turn",
        message="Recorded an interaction-rule chat turn",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
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
            **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
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
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
    )


def _maybe_start_interaction_session(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    selection_text: str | None,
) -> ChatResponse | None:
    if request.interaction_mode == "direct_edit":
        return None
    if not should_start_interaction(requirements.interaction_rule_draft):
        return None

    start_resolution = build_interaction_start(
        lesson=lesson,
        draft=requirements.interaction_rule_draft,
        user_message=request.message,
        selection=request.selection,
        selection_text=selection_text,
    )
    if start_resolution.session is None and start_resolution.focus_resolution is not None:
        chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            resolution=start_resolution.focus_resolution,
        )
        lesson.learning_requirements = requirements
        commit_operations(
            lesson,
            [],
            label="Interaction focus clarification",
            message="Asked the learner to confirm the source content for an interaction rule",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=None,
                    focus_candidates=start_resolution.focus_resolution.candidates,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=None, after=None),
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
            board_decision=BoardDecision(
                action="await_focus_choice",
                reason=start_resolution.focus_resolution.question,
            ),
            focus_candidates=start_resolution.focus_resolution.candidates,
        )

    if start_resolution.session is None:
        return None

    session_before = lesson.active_interaction_session
    lesson.active_interaction_session = start_resolution.session
    chatbot_message, chatbot_message_source = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=start_resolution.session,
        decision=None,
    )
    _clear_task_requirements(lesson)
    commit_operations(
        lesson,
        [],
        label="Interaction session start",
        message="Started a rule-based interaction session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=start_resolution.session.target_focus,
                focus_candidates=(
                    start_resolution.focus_resolution.candidates
                    if start_resolution.focus_resolution
                    else []
                ),
                requirement_cleared=True,
            ),
            **interaction_session_metadata(
                before=session_before,
                after=start_resolution.session,
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
        board_decision=BoardDecision(
            action="no_change",
            reason=start_resolution.session.interaction_goal,
        ),
        resolved_focus=start_resolution.session.target_focus,
        focus_candidates=(
            start_resolution.focus_resolution.candidates
            if start_resolution.focus_resolution
            else []
        ),
        requirement_cleared=True,
    )


def _generate_board_from_confirmed_resource(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_resolution: ResourceResolution,
    resource_summary_for_turn: str,
    conversation_summary: str,
) -> ChatResponse:
    requirements = _with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        conversation_summary=conversation_summary,
        user_instruction=request.message,
    )
    chatbot_message = edit_outcome.chatbot_message
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        requirements = lesson.learning_requirements
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
    requirement_cleared = edit_outcome.changed
    commit_operations(
        lesson,
        [],
        label="Resource-backed board generation",
        message="Generated board document from a confirmed uploaded resource chapter",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_generation",
            "resource_backed_generation": True,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": edit_outcome.assistant_message_source,
            "interaction_mode": request.interaction_mode,
            "resource_reference_action": request.resource_reference_action,
            "board_generation_action": "resource_reference_confirm",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_reference_metadata(resolution=resource_resolution),
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
        board_decision=edit_outcome.board_decision,
        resource_matches=resource_resolution.matches,
        selected_reference=resource_resolution.selected_reference,
        requirement_cleared=requirement_cleared,
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
    resource_resolution = resolve_resource_reference(
        resources=visible_package.resources,
        user_message=request.message,
        reference_action=request.resource_reference_action,
        reference_resource_id=request.resource_reference_resource_id,
        reference_chapter_id=request.resource_reference_chapter_id,
        allow_direct_reference=(
            _requests_resource_backed_answer(request.message)
            and request.interaction_mode != "direct_edit"
            and action_type not in EDIT_ACTIONS
            and request.board_generation_action != "start"
            and not _requests_document_artifact_generation(request.message)
            and not _requests_learning_start(request.message)
        ),
    )
    selected_reference = resource_resolution.selected_reference
    selection_or_reference_excerpt = _merge_selection_and_reference(selection_excerpt, selected_reference)
    resource_summary_for_turn = _resource_summary_with_reference(visible_package.resources, selected_reference)

    interaction_response = _handle_existing_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        resources=visible_package.resources,
        selection_excerpt=selection_or_reference_excerpt,
    )
    if interaction_response is not None:
        return interaction_response

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
        interaction_start_response = _maybe_start_interaction_session(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=visible_package.resources,
            selection_text=selection_text,
        )
        if interaction_start_response is not None:
            return interaction_start_response
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
        solver_user_message, solver_metadata = _chatbot_message_with_solver_context(
            lesson=lesson,
            request=request,
            user_message=request.message,
            target_excerpt=focus_excerpt,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
        )
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=_resource_summary(visible_package.resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=solver_user_message,
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
                **solver_metadata,
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
        if resource_resolution.reference_prompt is not None and request.resource_reference_action is None:
            lesson.learning_requirements = requirements
            chatbot_message = resource_resolution.reference_prompt.question
            commit_operations(
                lesson,
                [],
                label="Resource reference prompt",
                message="Asked the learner to confirm a relevant resource chapter before continuing",
                new_document=lesson.board_document,
                metadata={
                    "kind": "chat_flow",
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": "resource_resolver",
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=False,
                    ),
                    **_reference_metadata(resolution=resource_resolution),
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
                board_decision=BoardDecision(
                    action="await_reference_choice",
                    reason=resource_resolution.reference_prompt.reason,
                ),
                resource_matches=resource_resolution.matches,
                reference_prompt=resource_resolution.reference_prompt,
            )
        if request.resource_reference_action == "confirm" and selected_reference is not None:
            return _generate_board_from_confirmed_resource(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_resolution=resource_resolution,
                resource_summary_for_turn=resource_summary_for_turn,
                conversation_summary=_conversation_summary(request.conversation),
            )
        lesson.learning_requirements = requirements
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=learning_clarification.summary or requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
            user_message=request.message,
            selection_excerpt=selection_or_reference_excerpt,
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
                **_reference_metadata(resolution=resource_resolution),
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
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
        )

    if (
        resource_resolution.reference_prompt is not None
        and request.resource_reference_action is None
        and _should_prompt_resource_reference(request.message)
    ):
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        chatbot_message = resource_resolution.reference_prompt.question
        commit_operations(
            lesson,
            [],
            label="Resource reference prompt",
            message="Asked the learner to confirm a relevant resource chapter before answering",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": "resource_resolver",
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **_reference_metadata(resolution=resource_resolution),
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
            board_decision=BoardDecision(
                action="await_reference_choice",
                reason=resource_resolution.reference_prompt.reason,
            ),
            resource_matches=resource_resolution.matches,
            reference_prompt=resource_resolution.reference_prompt,
        )

    if (
        request.resource_reference_action == "confirm"
        and selected_reference is not None
        and _should_generate_board_after_reference_confirmation(request.message)
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
        return _generate_board_from_confirmed_resource(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
            resource_summary_for_turn=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
        )

    solver_user_message, solver_metadata = _chatbot_message_with_solver_context(
        lesson=lesson,
        request=request,
        user_message=request.message,
        target_excerpt=selection_or_reference_excerpt,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=_conversation_summary(request.conversation),
    )
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=solver_user_message,
        selection_excerpt=selection_or_reference_excerpt,
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

    interaction_start_response = _maybe_start_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=visible_package.resources,
        selection_text=selection_text,
    )
    if interaction_start_response is not None:
        return interaction_start_response

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
            resource_summary=resource_summary_for_turn,
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
            **_reference_metadata(resolution=resource_resolution),
            **board_edit_metadata,
            **solver_metadata,
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
        resource_matches=resource_resolution.matches,
        selected_reference=selected_reference,
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
