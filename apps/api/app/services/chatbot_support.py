from __future__ import annotations

import os
import re

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskAction,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    DocumentEvidence,
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
    StrongReasoningPrompt,
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
    is_explicit_board_generation_request,
    is_generation_control_request,
    update_learning_requirements_from_chat,
)
from app.services.openai_course_ai import bind_text_model_selection, openai_course_ai
from app.services.document_locator import (
    document_evidence_from_id,
    locate_document_evidence,
    looks_like_document_request,
    queued_resource_message,
)
from app.services.resource_document_import import (
    apply_resource_document_import,
    requests_pending_resource_document_import,
    requests_resource_document_import,
    resource_import_operation,
    select_resource_import_payload,
)
from app.services.rich_document import build_document, is_document_empty
from app.services.route_context import bind_ai_request_context
from app.services.resource_resolver import ResourceResolution, resolve_resource_reference
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus
from app.services.chatbot_patterns import (
    APPEND_REQUEST_PATTERN,
    COMPLEX_REASONING_REQUEST_PATTERN,
    CONTEXTUAL_CONTINUATION_EXPLANATION_PATTERN,
    DOCUMENT_ARTIFACT_REQUEST_PATTERN,
    DOCUMENT_TRANSFORM_REQUEST_PATTERN,
    DOCUMENT_WRITE_ACTIONS,
    EDIT_ACTIONS,
    EXPLAIN_REQUEST_PATTERN,
    EXPAND_REQUEST_PATTERN,
    EXPLICIT_RESOURCE_REFERENCE_PATTERN,
    FOLLOWUP_EXECUTION_PATTERN,
    INTERACTION_RULE_REQUEST_PATTERN,
    LEARNING_START_REQUEST_PATTERN,
    PRO_REASONING_REQUEST_PATTERN,
    RESOURCE_OUTPUT_EXPLANATION_PATTERN,
    RESOURCE_REFERENCE_HINT_PATTERN,
    REWRITE_REQUEST_PATTERN,
    SIMPLIFY_REQUEST_PATTERN,
    TARGET_LOCATION_HINT_PATTERN,
    WHOLE_DOCUMENT_TARGET_PATTERN,
    MAX_CONTEXT_CHARS,
    MAX_CONVERSATION_TURNS,
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
        status = f"索引状态={resource.index_status}"
        if resource.index_status == "ready":
            status = f"已索引 {resource.page_count or 0} 页/{resource.indexed_block_count or 0} 块"
        chapter_titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        if chapter_titles:
            lines.append(f"{resource.name}: {status}; {' / '.join(chapter_titles)}")
        else:
            lines.append(f"{resource.name}: {status}")
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


def _resource_resolution_query(request: ChatRequest, requirements: LearningRequirementSheet) -> str:
    parts = [request.message]
    should_carry_requirement_target = (
        request.board_generation_action == "start"
        or is_generation_control_request(request.message)
        or _requests_document_artifact_generation(request.message)
        or _requests_resource_output_explanation(request.message)
    )
    if should_carry_requirement_target:
        parts.extend(
            [
                requirements.theme,
                requirements.learning_goal,
                requirements.action_instruction,
                str(requirements.target_location or ""),
            ]
        )
    return "\n".join(part for part in parts if part and part.strip())


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-MAX_CONVERSATION_TURNS:]
    return "\n".join(f"{turn.role}: {_compact_text(turn.content, limit=500)}" for turn in turns if turn.content.strip())


def _requests_complex_reasoning(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and COMPLEX_REASONING_REQUEST_PATTERN.search(compact))


def _strong_reasoning_allowed_pro() -> bool:
    return (os.getenv("OPENCLASS_STRONG_REASONING_ALLOW_PRO") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _strong_reasoning_model_label(*, high_value: bool) -> str:
    if high_value and _strong_reasoning_allowed_pro():
        return os.getenv("OPENAI_PRO_REASONING_MODEL", "gpt-5.5-pro")
    return os.getenv("OPENAI_STRONG_REASONING_MODEL", "gpt-5.5")


def _should_offer_strong_reasoning(*, request: ChatRequest, target_excerpt: str | None) -> bool:
    if request.strong_reasoning_action is not None:
        return False
    if not getattr(openai_course_ai, "client", None):
        return False
    if _requests_complex_reasoning(request.message):
        return True
    compact_message = _compact_text(request.message, limit=280)
    compact_target = _compact_text(target_excerpt, limit=2400)
    if len(compact_target) >= 700 and re.search(r"(讲解|解释|分析|推导|证明|求解|解答|思路)", compact_message):
        return True
    return False


def _build_strong_reasoning_prompt(request: ChatRequest) -> StrongReasoningPrompt:
    high_value = bool(PRO_REASONING_REQUEST_PATTERN.search(request.message))
    model_label = _strong_reasoning_model_label(high_value=high_value)
    return StrongReasoningPrompt(
        question="这道题可能需要更严谨的多步推理。要启用深度推理模型先完整求解，再由 Chatbot 为你讲解吗？",
        reason="检测到本轮问题包含复杂推导、证明或高难度分析信号。确认后会先调用隐藏强推理模型，不会直接修改板书。",
        confirm_label="确认推理",
        skip_label="先不用",
        model_label=model_label,
    )


def _strong_reasoning_prompt_metadata(
    *,
    prompt: StrongReasoningPrompt | None,
    action: str | None,
) -> dict[str, object]:
    return {
        "strong_reasoning_prompt": prompt.model_dump(mode="json") if prompt else None,
        "strong_reasoning_action": action,
    }


def _generate_strong_reasoning_recommendation(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    target_excerpt: str | None,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=(
            f"用户原始请求：{request.message}\n"
            "系统判断这可能需要深度推理模型辅助。请不要解题、不要给最终结论、不要展开证明。"
            "只用自然语言说明建议先启用深度推理，并提示用户可以点击确认继续。"
        ),
        selection_excerpt=target_excerpt,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot" if chatbot_message else "chatbot_empty"


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
    if request.strong_reasoning_action != "confirm":
        return user_message, {}
    if not getattr(openai_course_ai, "client", None):
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
        solver_context = (
            "用户已确认深度推理，但隐藏强推理工具暂不可用或没有返回可用结果。"
            "请不要暴露内部错误；自然说明当前无法启用深度推理，并继续给出普通讲解或下一步建议。"
        )
        return f"{user_message}\n\n{solver_context}", {
            "strong_reasoning_tool": {
                "status": "unavailable",
                "model": _strong_reasoning_model_label(
                    high_value=bool(PRO_REASONING_REQUEST_PATTERN.search(request.message))
                ),
            }
        }
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


def _has_explicit_resource_reference(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and EXPLICIT_RESOURCE_REFERENCE_PATTERN.search(compact))


def _requests_append_section(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and APPEND_REQUEST_PATTERN.search(compact))


def _requests_document_transform(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and DOCUMENT_TRANSFORM_REQUEST_PATTERN.search(compact))


def _requests_whole_document_transform(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(
        compact
        and DOCUMENT_TRANSFORM_REQUEST_PATTERN.search(compact)
        and WHOLE_DOCUMENT_TARGET_PATTERN.search(compact)
    )


def _is_followup_execution_request(text: str) -> bool:
    compact = _compact_text(text, limit=80)
    return bool(compact and FOLLOWUP_EXECUTION_PATTERN.search(compact))


def _requests_contextual_continuation_explanation(
    request: ChatRequest,
    *,
    has_selection: bool,
    document_empty: bool,
) -> bool:
    if request.interaction_mode != "ask" or has_selection or document_empty:
        return False
    message = _compact_text(request.message, limit=280)
    if not message or not CONTEXTUAL_CONTINUATION_EXPLANATION_PATTERN.search(message):
        return False
    if (
        _requests_append_section(message)
        or is_generation_control_request(message)
        or _requests_document_artifact_generation(message)
        or REWRITE_REQUEST_PATTERN.search(message)
    ):
        return False
    return True


def _requirements_imply_append(requirements: LearningRequirementSheet) -> bool:
    if requirements.action_type == "append_section":
        return True
    action_text = " ".join(
        part
        for part in [
            requirements.action_instruction,
            requirements.learning_goal,
            *requirements.learning_need_checklist,
        ]
        if part
    )
    return _requests_append_section(action_text)


def _should_preserve_requirement_update_for_action(request: ChatRequest) -> bool:
    return bool(INTERACTION_RULE_REQUEST_PATTERN.search(_compact_text(request.message, limit=280)))


def _infer_board_task_action(request: ChatRequest, *, has_selection: bool, document_empty: bool) -> BoardTaskAction | None:
    if request.board_generation_action == "start":
        return "generate_board"
    message = _compact_text(request.message, limit=280)
    if request.interaction_mode == "direct_edit":
        if _requests_append_section(message):
            return "append_section"
        if SIMPLIFY_REQUEST_PATTERN.search(message):
            return "simplify_target"
        if EXPAND_REQUEST_PATTERN.search(message):
            return "expand_target"
        return "rewrite_target"
    if _requests_document_transform(message) and not document_empty:
        if has_selection or _requests_whole_document_transform(message):
            return "rewrite_target"
    if not has_selection and _has_explicit_resource_reference(message):
        return None
    if _requests_append_section(message) and not document_empty:
        return "append_section"
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
    if not has_selection and RESOURCE_REFERENCE_HINT_PATTERN.search(message):
        return None
    if has_selection and not document_empty:
        return "explain_target"
    return None


def _prefer_requirement_action(
    inferred: BoardTaskAction | None,
    requirement_action: BoardTaskAction | None,
    *,
    request_message: str,
    requirements: LearningRequirementSheet,
) -> BoardTaskAction | None:
    if inferred is None and _is_followup_execution_request(request_message) and _requirements_imply_append(requirements):
        return "append_section"
    if requirement_action == "append_section":
        return requirement_action
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


def _has_actionable_generation_context(
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    if requirements.action_type == "generate_board" and requirements.action_instruction.strip():
        return True
    return any(
        fact.value.strip() and fact.category in {"learning", "level", "vocabulary", "scenario", "output"}
        for fact in learning_clarification.key_facts
    )


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
        or _requests_resource_output_explanation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def _should_generate_board_after_reference_confirmation(text: str) -> bool:
    return (
        _requests_document_artifact_generation(text)
        or _requests_resource_output_explanation(text)
        or is_generation_control_request(text)
        or _requests_learning_start(text)
    )


def _requests_resource_output_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and RESOURCE_OUTPUT_EXPLANATION_PATTERN.search(compact))


def _resource_context_excerpt(reference: ResourceReferenceContext | None) -> str | None:
    if reference is None:
        return None
    lines = [
        f"参考资料：{reference.resource_name} / {reference.chapter_title}",
        f"资料摘要：{reference.summary}",
    ]
    if not reference.text_evidence_available:
        lines.append("资料正文状态：只命中目录或结构线索，未抽到可引用正文；不要声称已依据原文细节。")
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
                "segment_id": resolution.selected_reference.segment_id,
                "resource_name": resolution.selected_reference.resource_name,
                "chapter_title": resolution.selected_reference.chapter_title,
                "summary": resolution.selected_reference.summary,
                "chunks": [chunk.model_dump(mode="json") for chunk in resolution.selected_reference.chunks],
                "text_evidence_available": resolution.selected_reference.text_evidence_available,
                "text_evidence_status": resolution.selected_reference.text_evidence_status,
            }
            if resolution.selected_reference
            else None
        ),
        "resource_resolution_status": resolution.status,
    }


def _resource_generation_metadata(reference: ResourceReferenceContext | None) -> dict[str, object]:
    has_text = bool(reference and reference.text_evidence_available)
    degraded = bool(reference and not reference.text_evidence_available)
    return {
        "resource_backed_generation": has_text,
        "resource_text_evidence_available": has_text,
        "resource_reference_degraded": degraded,
        "resource_text_evidence_status": reference.text_evidence_status if reference else None,
    }


def _should_generate_board_from_explicit_request(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    if not is_document_empty(lesson.board_document):
        return False
    if (
        is_explicit_board_generation_request(request.message)
        or _requests_document_artifact_generation(request.message)
        or _requests_resource_output_explanation(request.message)
    ):
        return True
    return is_generation_control_request(request.message) and _has_actionable_generation_context(
        requirements,
        learning_clarification,
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
    document_evidence: list[DocumentEvidence] | None = None,
    reference_prompt: ResourceReferencePrompt | None = None,
    strong_reasoning_prompt: StrongReasoningPrompt | None = None,
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
        document_evidence=document_evidence or [],
        reference_prompt=reference_prompt,
        board_edit_prompt=None,
        strong_reasoning_prompt=strong_reasoning_prompt,
        selected_reference=selected_reference,
        resolved_focus=resolved_focus,
        focus_candidates=focus_candidates or [],
        requirement_cleared=requirement_cleared,
        created_lesson=None,
        teaching_progress=teaching_progress,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )
