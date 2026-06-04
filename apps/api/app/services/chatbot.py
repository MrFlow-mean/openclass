from __future__ import annotations

import json
import re

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardSegment,
    BoardTaskRequirementSheet,
    BoardTaskUpdateStreamPayload,
    BoardTaskAction,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    RequirementUpdateStreamPayload,
    ResourceLibraryItem,
    ResourceMatch,
    ResourceReferenceContext,
    ResourceReferencePrompt,
    SelectionRef,
)
from app.services import workspace_state
from app.services.ai_logging import current_ai_log_context
from app.services.board_document_editor import edit_existing_document, generate_from_requirements
from app.services.board_explanation_gate import (
    generate_board_directed_explanation_message as _gate_board_directed_explanation_message,
    requirement_probe_instead_of_explanation_message,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.board_task_manager import (
    is_write_confirmation,
    is_write_decline,
    make_write_task_from_topic,
    normalize_board_task_sheet,
    update_board_task_from_chat,
)
from app.services.board_segment_index import build_board_segment_index, segment_text_hash
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
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.openai_course_ai import (
    BoardTaskRouteDecision,
    bind_text_model_selection,
    emit_ai_stream_event,
    openai_course_ai,
)
from app.services.rich_document import is_document_empty
from app.services.route_context import bind_ai_request_context
from app.services.resource_resolver import ResourceResolution, resolve_resource_reference
from app.services.segment_resolver import FocusResolution, focus_context, resolve_board_focus


MAX_CONTEXT_CHARS = 1800
MAX_CONVERSATION_TURNS = 8
EXPLAIN_REQUEST_PATTERN = re.compile(
    r"(讲解|解释|说明|讲一下|解释一下|帮我理解|为什么|是什么|什么意思|是什么意思|什么含义|含义|"
    r"(?:怎么|如何|怎样).{0,12}(?:表达|体现|说明|运用|使用|写出|看出|表现))"
)
APPEND_REQUEST_PATTERN = re.compile(
    r"(续写|继续写|接着写|往后写|后续|新增|追加|新加|新章节|新小节|下一节|下一章|下一部分|末尾|"
    r"(?:帮我|为我|请|可以|能不能|你可以)?.{0,8}(?:写|编写|生成|设计|创建|做)"
    r"(?:一|几|[0-9０-９一二三四五六七八九十两]|个|段|篇|份|条|点|些|一下))"
)
EXPAND_REQUEST_PATTERN = re.compile(r"(扩写|扩展|补充|增加|添加)")
SIMPLIFY_REQUEST_PATTERN = re.compile(
    r"(简化|简单(?:一点|点|些)?|更简单|通俗|更容易懂|更好懂|好理解|容易理解|降低难度|浅显|"
    r"缩短|改短|短(?:一点|点|些)|精简|压缩|太长|篇幅|"
    r"控制.{0,8}(?:以内|以下)|[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下))"
)
REWRITE_REQUEST_PATTERN = re.compile(
    r"(改写|重写|修改|编辑|润色|优化|"
    r"改(?:得|的)?(?:简单|通俗|容易|好懂|清楚|更清楚|更难|难一点|有难度|更有区分度)|"
    r"(?:提高|增加|提升).{0,6}难度|换(?:个|一种)说法)"
)
TARGET_LOCATION_HINT_PATTERN = re.compile(
    r"(选中|这一段|这段|这部分|这里|前面|上面|下面|"
    r"第.{0,8}[章节部分段空题项条句行]|定义|概念|例子|示例|结论|总结|表格|为什么)"
)
RESOURCE_REFERENCE_HINT_PATTERN = re.compile(r"(资料|材料|文档|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word|章节|小节|第.{0,8}[章节部分])", re.IGNORECASE)
EXPLICIT_RESOURCE_REFERENCE_PATTERN = re.compile(r"(资料|材料|上传|教材|课本|原文|参考|根据|来自|文件|PDF|Word)", re.IGNORECASE)
LEARNING_START_REQUEST_PATTERN = re.compile(r"(我要学|我想学|想学习|学习一下|开始学|帮我学|学一学)")
FOLLOWUP_EXECUTION_PATTERN = re.compile(r"^(写啊|写|开始|执行|可以|好的|好|就这样|按这个来|照这个来|继续)$")
INTERACTION_RULE_REQUEST_PATTERN = re.compile(r"(规则|互动|轮流|你问我答|按.{0,12}来)")
SEQUENTIAL_EXPLANATION_REQUEST_PATTERN = re.compile(
    r"(都讲|全都讲|全部讲|都解释|全部解释|逐个|一个个|挨个|依次|按顺序|从头到尾)"
)
SEQUENCE_CONTINUE_PATTERN = re.compile(
    r"^(可以|可以的|没问题|没有问题|没啥问题|没有啥问题|好|好的|继续|继续讲|下一节|下一个|明白了|懂了|可以接受)$"
)
SEQUENCE_EXIT_PATTERN = re.compile(r"(不用继续|先停|停止|结束|退出|不讲了|够了)")
RECENT_EDIT_FOLLOWUP_PATTERN = re.compile(
    r"(太长|篇幅|缩短|改短|短(?:一点|点|些)|精简|压缩|控制.{0,8}(?:以内|以下)|"
    r"[0-9０-９一二三四五六七八九十两]+.{0,8}(?:以内|以下)|来回|回合)"
)
RECENT_WRITE_FOLLOWUP_PATTERN = re.compile(r"(继续|接着|直接|再|进一步|你自己看|自己看|自行|自己判断)")
WHOLE_DOCUMENT_SCOPE_PATTERN = re.compile(r"(全文|整篇|整份|整个(?:文档|板书)|全篇|全部内容|整体)")
EXISTING_BOARD_GENERATION_CONTROL_PATTERN = re.compile(r"(生成|创建|制作|准备).{0,8}(板书|版书|文档)")
EDIT_ACTIONS: set[BoardTaskAction] = {"rewrite_target", "expand_target", "simplify_target"}
DOCUMENT_WRITE_ACTIONS: set[BoardTaskAction] = {*EDIT_ACTIONS, "append_section"}
DOCUMENT_GENERATION_ACTIONS = r"(生成|写|撰写|创建|整理|制作|设计|输出|产出|编写)"
DOCUMENT_ARTIFACT_NOUNS = (
    r"(文档|讲义|板书|版书|课文|文章|作文|报告|对话|练习|题目|试题|测验|课程|"
    r"教案|教程|学习计划|提纲|大纲|案例|表格|清单|材料|页面|章节|小节)"
)
DOCUMENT_ARTIFACT_REQUEST_PATTERN = re.compile(
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,48}}{DOCUMENT_ARTIFACT_NOUNS}"
    r"|"
    rf"{DOCUMENT_ARTIFACT_NOUNS}.{{0,24}}{DOCUMENT_GENERATION_ACTIONS}"
    r"|"
    rf"{DOCUMENT_GENERATION_ACTIONS}.{{0,12}}(?:一|几|若干|多)?(?:篇|份|个|套|道|组|页|段|部分)[^吧吗呢啊。！？!?；;\n]{{2,80}}"
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


def _requests_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and EXPLAIN_REQUEST_PATTERN.search(compact))


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


def _chatbot_visible_selection_excerpt(request: ChatRequest, excerpt: str | None) -> str | None:
    if request.selection and request.selection.kind == "board":
        return None
    return excerpt


def _has_explicit_resource_reference(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and EXPLICIT_RESOURCE_REFERENCE_PATTERN.search(compact))


def _requests_append_section(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    return bool(compact and APPEND_REQUEST_PATTERN.search(compact))


def _is_followup_execution_request(text: str) -> bool:
    compact = _compact_text(text, limit=80)
    return bool(compact and FOLLOWUP_EXECUTION_PATTERN.search(compact))


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
    if not has_selection and _has_explicit_resource_reference(message):
        return None
    if _requests_append_section(message) and not document_empty:
        return "append_section"
    if not document_empty and SIMPLIFY_REQUEST_PATTERN.search(message):
        return "simplify_target"
    if not document_empty and EXPAND_REQUEST_PATTERN.search(message):
        return "expand_target"
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
    updated.action_instruction = _structured_action_instruction(
        updated,
        action_type=action_type,
        instruction=instruction,
    )
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


def _structured_action_instruction(
    requirements: LearningRequirementSheet,
    *,
    action_type: BoardTaskAction | None,
    instruction: str,
) -> str:
    if action_type != "generate_board":
        return _compact_text(instruction, limit=240)
    parts = ["生成第一版板书"]
    if requirements.learning_goal.strip():
        parts.append(f"学习目标：{requirements.learning_goal.strip()}")
    if requirements.level.strip():
        parts.append(f"学习水平：{requirements.level.strip()}")
    if requirements.output_preference.strip():
        parts.append(f"输出形式：{requirements.output_preference.strip()}")
    if requirements.target_depth.strip():
        parts.append(f"讲解深度：{requirements.target_depth.strip()}")
    return _compact_text("；".join(parts), limit=360)


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


def _requirement_history_metadata(
    stamp: RequirementHistoryStamp | None,
    *,
    run_status_after_commit: str | None = None,
) -> dict[str, object]:
    if stamp is None:
        return {
            "requirement_run_id": None,
            "frozen_requirement_version_id": None,
        }
    metadata = {
        "requirement_run_id": stamp.run_id,
        "frozen_requirement_version_id": stamp.version_id,
        "requirement_phase": stamp.phase,
        "frozen_requirement_phase": stamp.phase,
    }
    if run_status_after_commit is not None:
        metadata["requirement_run_status_after_commit"] = run_status_after_commit
    return metadata


def _clear_task_requirements(lesson: Lesson) -> None:
    lesson.learning_requirements = None


def _activate_board_task_requirements(lesson: Lesson, board_task: BoardTaskRequirementSheet) -> None:
    _clear_task_requirements(lesson)
    lesson.board_task_requirements = board_task


def _looks_like_recent_edit_followup(text: str) -> bool:
    compact = _compact_text(text, limit=180)
    return bool(compact and RECENT_EDIT_FOLLOWUP_PATTERN.search(compact))


def _looks_like_recent_write_followup(text: str) -> bool:
    compact = _compact_text(text, limit=180)
    return bool(compact and RECENT_WRITE_FOLLOWUP_PATTERN.search(compact))


def _text_overlap_tokens(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    tokens: set[str] = set(re.findall(r"[a-zÀ-ÿ][a-zÀ-ÿ'’_-]{2,}", compact))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", compact):
        tokens.update(chunk[index : index + 2] for index in range(0, max(0, len(chunk) - 1)))
    return {token for token in tokens if len(token) >= 2}


def _recent_focus_matches_board_task(focus: BoardFocusRef, board_task: BoardTaskRequirementSheet) -> bool:
    query = _compact_text(" ".join([board_task.target_hint, board_task.question_or_topic]), limit=500)
    if not query:
        return True
    focus_text = _compact_text(
        " ".join(
            [
                focus.display_label,
                " ".join(focus.heading_path),
                focus.excerpt,
                focus.before_text,
                focus.after_text,
            ]
        ),
        limit=1200,
    )
    query_tokens = _text_overlap_tokens(query)
    focus_tokens = _text_overlap_tokens(focus_text)
    if not query_tokens or not focus_tokens:
        return False
    return len(query_tokens & focus_tokens) >= 2


def _requests_whole_document_scope(*values: str) -> bool:
    compact = _compact_text(" ".join(value for value in values if value), limit=300)
    return bool(compact and WHOLE_DOCUMENT_SCOPE_PATTERN.search(compact))


def _requests_existing_board_generation_control(text: str) -> bool:
    compact = _compact_text(text, limit=220)
    return bool(compact and EXISTING_BOARD_GENERATION_CONTROL_PATTERN.search(compact))


def _whole_document_focus(lesson: Lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=None,
        kind=None,
        heading_path=[lesson.board_document.title or lesson.title],
        excerpt=_compact_text(lesson.board_document.content_text, limit=2400),
        confidence=1.0,
        reason="用户明确要求处理全文，板书侧将目标范围设为 whole_document。",
        display_label="全文",
        match_id=f"whole_document:{lesson.board_document.id}",
        score_breakdown={"whole_document_scope": 1.0},
    )


def _synthetic_focus_resolution(focus: BoardFocusRef) -> FocusResolution:
    return FocusResolution(focus=focus, candidates=[focus], status="resolved", question="")


def _latest_successful_board_edit_focus(lesson: Lesson) -> BoardFocusRef | None:
    for commit in reversed(lesson.history_graph.commits):
        metadata = commit.metadata if isinstance(commit.metadata, dict) else {}
        if metadata.get("kind") != "board_document_edit":
            continue
        if metadata.get("board_task_cleared") is False:
            continue
        raw_focus = metadata.get("recent_board_edit_focus") or metadata.get("resolved_focus")
        if isinstance(raw_focus, dict):
            try:
                return BoardFocusRef.model_validate(raw_focus)
            except Exception:
                pass
        section_titles = metadata.get("board_section_titles")
        if isinstance(section_titles, list):
            titles = [str(title).strip() for title in section_titles if str(title).strip()]
            for title in reversed(titles):
                focus = _focus_from_section_title(lesson=lesson, title=title)
                if focus is not None:
                    return focus
    return None


def _maybe_inherit_recent_board_edit_focus(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
) -> BoardTaskRequirementSheet:
    if board_task.requested_action not in {"edit", "write"}:
        return board_task
    if board_task.target_location is not None and board_task.location_status in {"selected", "resolved"}:
        return board_task
    if board_task.requested_action == "edit":
        if not _looks_like_recent_edit_followup(request_message):
            return board_task
        if board_task.target_hint.strip() and not _looks_like_recent_edit_followup(board_task.target_hint):
            return board_task
    elif board_task.requested_action == "write":
        if not _looks_like_recent_write_followup(request_message):
            return board_task
        if board_task.target_hint.strip() and not _looks_like_recent_write_followup(board_task.target_hint):
            return board_task
        if board_task.location_status not in {"missing", "ambiguous"}:
            return board_task
    else:
        return board_task
    focus = _latest_successful_board_edit_focus(lesson)
    if focus is None:
        return board_task
    if board_task.requested_action == "write" and not _recent_focus_matches_board_task(focus, board_task):
        return board_task
    inherited = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
    inherited.target_location = focus
    inherited.target_hint = focus.display_label or "最近一次板书编辑的目标区域"
    inherited.location_status = "resolved"
    inherited.clarification_question = ""
    return normalize_board_task_sheet(inherited)


def _recent_board_edit_focus_for_commit(
    *,
    lesson: Lesson,
    fallback_focus: BoardFocusRef | None,
    section_titles: list[str],
) -> BoardFocusRef | None:
    if fallback_focus is not None:
        return fallback_focus
    titles = [title.strip() for title in section_titles if title.strip()]
    for title in reversed(titles):
        focus = _focus_from_section_title(lesson=lesson, title=title)
        if focus is not None:
            return focus
    return None


def _focus_from_section_title(*, lesson: Lesson, title: str) -> BoardFocusRef | None:
    compact_title = _compact_text(title, limit=120)
    if not compact_title:
        return None
    index = build_board_segment_index(lesson.board_document)
    for idx, segment in enumerate(index.segments):
        if segment.kind != "heading" or compact_title not in _compact_text(segment.text, limit=240):
            continue
        target = segment
        for following in index.segments[idx + 1 :]:
            if following.kind == "heading":
                break
            if following.text.strip():
                target = following
                break
        before = index.segments[target.order_index - 1].text if target.order_index and target.order_index > 0 else ""
        after = index.segments[target.order_index + 1].text if target.order_index is not None and target.order_index + 1 < len(index.segments) else ""
        return BoardFocusRef(
            source="board",
            lesson_id=lesson.id,
            document_id=lesson.board_document.id,
            segment_id=target.segment_id,
            kind=target.kind,
            heading_path=target.heading_path,
            excerpt=target.text,
            before_text=before,
            after_text=after,
            text_hash=target.text_hash,
            confidence=0.95,
            reason="根据最近一次板书编辑返回的 section title 定位到新增/编辑区域。",
            display_label=" / ".join(target.heading_path) or compact_title,
            match_id=f"recent:{target.segment_id}",
            source_segment_ids=[target.segment_id],
            order_start=target.order_index,
            order_end=target.order_index,
            score_breakdown={"recent_board_edit_focus": 0.95},
        )
    return None


def _with_decision_target_scope(
    *,
    decision: BoardTaskRouteDecision,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    scope = decision.target_scope
    if not scope:
        if _requests_whole_document_scope(request_message, board_task.target_hint, board_task.question_or_topic):
            scope = "whole_document"
        elif decision.route == "write" and _decision_focus(decision, resolution) is None:
            scope = "append"
        elif _decision_focus(decision, resolution) is not None:
            scope = "focus"
    if scope == decision.target_scope:
        return decision
    return BoardTaskRouteDecision(
        route=decision.route,
        location_status=decision.location_status,
        target_focus=decision.target_focus,
        candidate_focuses=decision.candidate_focuses,
        reason=decision.reason,
        write_proposal=decision.write_proposal,
        target_scope=scope,
    )


def _implicit_board_search_evidence(
    *,
    route: str,
    target_scope: str | None,
    reason: str,
) -> dict[str, object]:
    return {
        "status": "found" if target_scope in {"append", "whole_document"} else "missing",
        "query_plan": {"source": "workflow", "target_scope": target_scope, "route": route},
        "candidates": [],
        "selected_match_id": None,
        "reason": reason,
    }


def _focus_candidate_context(resolution: FocusResolution) -> str:
    if not resolution.candidates:
        return resolution.question
    lines = [resolution.question]
    for index, candidate in enumerate(resolution.candidates[:3], start=1):
        path = " / ".join(candidate.heading_path) if candidate.heading_path else "当前板书"
        kind = candidate.kind or "片段"
        label = candidate.display_label or f"{path}（{kind}）"
        lines.append(f"{index}. {label}（内容摘录已由板书侧隔离）")
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


def _should_generate_board_from_explicit_request(
    *,
    lesson: Lesson,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> bool:
    if not is_document_empty(lesson.board_document):
        return False
    if is_explicit_board_generation_request(request.message) or _requests_document_artifact_generation(request.message):
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


def _new_requirement_history_recorder(
    *,
    user_id: str,
    lesson_id: str,
) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(user_id, lesson_id),
    )


def _new_board_task_history_recorder(
    *,
    user_id: str,
    lesson_id: str,
) -> BoardTaskHistoryRecorder:
    return BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        state=workspace_state.load_board_task_history_state_for_user(user_id, lesson_id),
    )


def _save_workspace_for_user(
    *,
    user_id: str,
    workspace,
    requirement_history: LearningRequirementHistoryRecorder | None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
) -> None:
    requirement_operations = requirement_history.operations if requirement_history is not None else []
    board_task_operations = board_task_history.operations if board_task_history is not None else []
    if requirement_operations or board_task_operations:
        workspace_state.save_workspace_for_user_with_histories(
            user_id,
            workspace,
            requirement_history_operations=requirement_operations,
            board_task_history_operations=board_task_operations,
        )
        return
    workspace_state.save_workspace_for_user(user_id, workspace)


def _persist_requirement_history_checkpoint(
    *,
    user_id: str,
    workspace,
    package,
    requirement_history: LearningRequirementHistoryRecorder,
) -> None:
    workspace_state.normalize_package_state(package)
    if requirement_history.operations:
        workspace_state.save_workspace_for_user_with_requirement_history(
            user_id,
            workspace,
            requirement_history.operations,
        )
        requirement_history.operations.clear()
    else:
        workspace_state.save_workspace_for_user(user_id, workspace)


def _clarification_questions(learning_clarification: LearningClarificationStatus) -> list[str]:
    question = learning_clarification.next_question.strip()
    return [question] if question else []


def _requirement_stream_payload(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    stamp: RequirementHistoryStamp | None,
) -> RequirementUpdateStreamPayload:
    return RequirementUpdateStreamPayload(
        learning_requirement_sheet=requirements,
        active_requirement_sheet=lesson.learning_requirements,
        learning_clarification=learning_clarification,
        requirement_run_id=stamp.run_id if stamp else None,
        requirement_version_id=stamp.version_id if stamp else None,
        requirement_phase=stamp.phase if stamp else None,
        clarification_questions=_clarification_questions(learning_clarification),
    )


def _board_task_questions(sheet: BoardTaskRequirementSheet | None) -> list[str]:
    if sheet is None:
        return []
    question = sheet.clarification_question.strip()
    return [question] if question else []


def _board_task_stream_payload(
    *,
    lesson: Lesson,
    sheet: BoardTaskRequirementSheet,
    stamp: BoardTaskHistoryStamp | None,
) -> BoardTaskUpdateStreamPayload:
    return BoardTaskUpdateStreamPayload(
        board_task_sheet=sheet,
        active_board_task_sheet=lesson.board_task_requirements,
        board_task_run_id=stamp.run_id if stamp else None,
        board_task_version_id=stamp.version_id if stamp else None,
        board_task_phase=stamp.phase if stamp else None,
        board_task_questions=_board_task_questions(sheet),
    )


def _emit_board_task_update(
    *,
    lesson: Lesson,
    sheet: BoardTaskRequirementSheet,
    stamp: BoardTaskHistoryStamp | None,
) -> None:
    if stamp is None:
        return
    payload = _board_task_stream_payload(lesson=lesson, sheet=sheet, stamp=stamp)
    emit_ai_stream_event(
        {
            "type": "board_task_update",
            "payload": payload.model_dump(mode="json"),
        }
    )


def _emit_requirement_update(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    stamp: RequirementHistoryStamp | None,
) -> None:
    if stamp is None:
        return
    payload = _requirement_stream_payload(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        stamp=stamp,
    )
    emit_ai_stream_event(
        {
            "type": "requirement_update",
            "payload": payload.model_dump(mode="json"),
        }
    )


def _record_requirement_update(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> RequirementHistoryStamp:
    return requirement_history.record_update(
        requirements=requirements,
        clarification=learning_clarification,
    )


def _freeze_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> RequirementHistoryStamp:
    return requirement_history.freeze(
        requirements=requirements,
        clarification=learning_clarification,
        forced=learning_clarification.forced_start or not learning_clarification.ready_for_board,
    )


def _should_track_initial_requirement_run(lesson: Lesson) -> bool:
    return is_document_empty(lesson.board_document)


def _frozen_requirement_snapshot(
    requirement_history: LearningRequirementHistoryRecorder,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus] | None:
    snapshot = requirement_history.snapshot
    if snapshot.status != "frozen" or not snapshot.latest_sheet_json or not snapshot.latest_clarification_json:
        return None
    try:
        requirements = LearningRequirementSheet.model_validate(json.loads(snapshot.latest_sheet_json))
        clarification = LearningClarificationStatus.model_validate(json.loads(snapshot.latest_clarification_json))
    except Exception:
        return None
    return requirements, clarification


def _normalize_requirement_for_board_generation(
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus]:
    frozen_requirements = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    frozen_clarification = LearningClarificationStatus.model_validate(
        learning_clarification.model_dump(mode="json")
    )
    frozen_requirements.current_questions = []
    frozen_requirements.risk_notes = []
    frozen_requirements.location_clarification_question = ""
    frozen_clarification.progress = 100
    frozen_clarification.missing_items = []
    frozen_clarification.can_start = True
    frozen_clarification.next_question = ""
    if not frozen_clarification.ready_for_board:
        frozen_clarification.forced_start = True
    frozen_clarification.ready_for_board = True
    return frozen_requirements, frozen_clarification


def _maybe_record_initial_requirement_update(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    enabled: bool,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> RequirementHistoryStamp | None:
    if not enabled:
        return None
    if requirement_history.snapshot.status == "frozen":
        return requirement_history.current_stamp()
    if learning_clarification.forced_start and learning_clarification.ready_for_board:
        return None
    return _record_requirement_update(
        requirement_history,
        requirements=requirements,
        learning_clarification=learning_clarification,
    )


def _prepare_initial_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    enabled: bool,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus, RequirementHistoryStamp | None]:
    if not enabled:
        return requirements, learning_clarification, None
    existing_frozen = _frozen_requirement_snapshot(requirement_history)
    if existing_frozen is not None:
        frozen_requirements, frozen_clarification = existing_frozen
        return frozen_requirements, frozen_clarification, requirement_history.current_stamp()
    frozen_requirements, frozen_clarification = _normalize_requirement_for_board_generation(
        requirements=requirements,
        learning_clarification=learning_clarification,
    )
    frozen_stamp = _freeze_requirement_for_board_generation(
        requirement_history,
        requirements=frozen_requirements,
        learning_clarification=frozen_clarification,
    )
    return frozen_requirements, frozen_clarification, frozen_stamp


def _checkpoint_initial_requirement_before_generation(
    *,
    user_id: str,
    workspace,
    package,
    lesson: Lesson,
    requirement_history: LearningRequirementHistoryRecorder,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    stamp: RequirementHistoryStamp | None,
) -> None:
    if stamp is None:
        return
    lesson.learning_requirements = requirements
    _persist_requirement_history_checkpoint(
        user_id=user_id,
        workspace=workspace,
        package=package,
        requirement_history=requirement_history,
    )
    _emit_requirement_update(
        lesson=lesson,
        requirements=requirements,
        learning_clarification=learning_clarification,
        stamp=stamp,
    )


def _post_initial_board_generation_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resource_summary: str,
    edit_outcome,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_post_board_generation_reply(
        lesson_title=lesson.title,
        learning_goal=learning_clarification.summary or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary,
        requirement_context={
            "sheet": requirements.model_dump(mode="json"),
            "clarification": learning_clarification.model_dump(mode="json"),
        },
        editor_summary=edit_outcome.summary,
        section_titles=edit_outcome.section_titles,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    if chatbot_message:
        return chatbot_message, "chatbot_post_board_generation"
    return edit_outcome.chatbot_message, edit_outcome.assistant_message_source


def _generate_board_directed_explanation_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    learning_clarification: LearningClarificationStatus,
    action_type: str,
    target_excerpt: str,
    interaction_context: dict[str, object] | None = None,
) -> tuple[str, str, dict[str, object] | None]:
    resource_summary = _resource_summary(resources)
    conversation_summary = _conversation_summary(conversation)
    directed = _gate_board_directed_explanation_message(
        lesson_title=lesson.title,
        learning_goal=learning_clarification.summary or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary,
        conversation_summary=conversation_summary,
        user_message=request.message,
        action_type=action_type,
        target_excerpt=target_excerpt,
        interaction_mode=request.interaction_mode,
        interaction_context=interaction_context,
    )
    return directed.chatbot_message, directed.assistant_message_source, directed.directive_payload


def _board_task_metadata(
    *,
    board_task: BoardTaskRequirementSheet | None,
    stamp: BoardTaskHistoryStamp | None,
    route: str | None = None,
    decision: dict[str, object] | None = None,
    cleared: bool = False,
) -> dict[str, object]:
    return {
        "board_task_sheet": board_task.model_dump(mode="json") if board_task else None,
        "board_task_run_id": stamp.run_id if stamp else None,
        "board_task_version_id": stamp.version_id if stamp else None,
        "board_task_phase": stamp.phase if stamp else None,
        "board_task_route": route,
        "board_task_decision": decision,
        "board_task_cleared": cleared,
        "requirement_cleared": True,
        "active_requirement_sheet_after": None,
    }


def _requirements_from_board_task(
    *,
    base: LearningRequirementSheet,
    board_task: BoardTaskRequirementSheet,
    action_type: BoardTaskAction | None,
    focus: BoardFocusRef | None = None,
) -> LearningRequirementSheet:
    updated = LearningRequirementSheet.model_validate(base.model_dump(mode="json"))
    updated.theme = board_task.question_or_topic or updated.theme
    updated.learning_goal = board_task.question_or_topic or updated.learning_goal
    updated.action_type = action_type
    updated.action_instruction = board_task.question_or_topic or board_task.target_hint
    updated.target_location = focus
    updated.location_status = "resolved" if focus else "missing"
    updated.location_clarification_question = board_task.clarification_question
    updated.interaction_rule_draft = board_task.interaction_rule_draft
    updated.current_questions = []
    updated.risk_notes = []
    return updated


def _task_location_evidence(resolution: FocusResolution | None) -> dict[str, object]:
    if resolution is None:
        return {"status": "missing", "focus": None, "candidates": [], "board_search_evidence": None}
    return {
        "status": resolution.status,
        "focus": resolution.focus.model_dump(mode="json") if resolution.focus else None,
        "candidates": [candidate.model_dump(mode="json") for candidate in resolution.candidates],
        "question": resolution.question,
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution.evidence else None,
    }


def _board_search_evidence_metadata(resolution: FocusResolution | None) -> dict[str, object]:
    return {
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
    }


def _fallback_board_task_decision(
    *,
    board_task: BoardTaskRequirementSheet,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    if board_task.requested_action == "write":
        if resolution is not None and resolution.resolved:
            return BoardTaskRouteDecision(
                route="write",
                location_status="found",
                target_focus=resolution.focus,
                candidate_focuses=resolution.candidates,
                reason="定位器已找到扩写目标位置。",
                write_proposal=board_task.question_or_topic,
            )
        if resolution is not None and resolution.status == "ambiguous":
            return BoardTaskRouteDecision(
                route="clarify_location",
                location_status="ambiguous",
                target_focus=None,
                candidate_focuses=resolution.candidates,
                reason=resolution.question,
            )
        if board_task.confirmation_status == "confirmed":
            return BoardTaskRouteDecision(
                route="write",
                location_status="content_absent" if resolution is None or not resolution.resolved else "found",
                target_focus=resolution.focus if resolution and resolution.focus else None,
                candidate_focuses=resolution.candidates if resolution else [],
                reason="用户已经确认扩写或明确要求写入新内容。",
                write_proposal=board_task.question_or_topic,
            )
        if board_task.confirmation_status == "none":
            return BoardTaskRouteDecision(
                route="write",
                location_status="missing",
                target_focus=None,
                candidate_focuses=[],
                reason="用户明确要求写入或续写板书内容。",
                write_proposal=board_task.question_or_topic,
            )
        return BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            target_focus=None,
            candidate_focuses=[],
            reason="当前板书没有可直接处理的目标内容，需要先确认是否扩写。",
            write_proposal=board_task.question_or_topic,
        )
    if resolution is None or not resolution.resolved:
        if resolution and resolution.status == "ambiguous":
            return BoardTaskRouteDecision(
                route="clarify_location",
                location_status="ambiguous",
                target_focus=None,
                candidate_focuses=resolution.candidates,
                reason=resolution.question,
            )
        if board_task.requested_action in {"explain", "chat"} and board_task.question_or_topic:
            return BoardTaskRouteDecision(
                route="await_write_confirmation",
                location_status="content_absent",
                target_focus=None,
                candidate_focuses=[],
                reason="当前板书没有定位到相关内容，需要先确认是否扩写。",
                write_proposal=board_task.question_or_topic,
            )
        return BoardTaskRouteDecision(
            route="clarify_location",
            location_status="missing",
            target_focus=None,
            candidate_focuses=resolution.candidates if resolution else [],
            reason=resolution.question if resolution else "还不能定位目标位置。",
        )
    if board_task.requested_action == "edit":
        route = "edit"
    elif board_task.requested_action == "chat":
        route = "chat"
    else:
        route = "explain"
    return BoardTaskRouteDecision(
        route=route,
        location_status="found",
        target_focus=resolution.focus,
        candidate_focuses=resolution.candidates,
        reason="定位器已找到可操作的板书位置。",
    )


def _decision_focus(decision: BoardTaskRouteDecision, resolution: FocusResolution | None) -> BoardFocusRef | None:
    return decision.target_focus or (resolution.focus if resolution else None)


def _decision_must_have_focus(
    *,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
) -> bool:
    if decision.route in {"edit", "explain", "chat"}:
        return True
    return decision.route == "write" and bool(board_task.target_hint.strip()) and decision.location_status != "content_absent"


def _clarify_decision_for_missing_focus(
    *,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="clarify_location",
        location_status="ambiguous" if resolution and resolution.status == "ambiguous" else "missing",
        target_focus=None,
        candidate_focuses=resolution.candidates if resolution else decision.candidate_focuses,
        reason=(resolution.question if resolution and resolution.question else decision.reason or "需要先确认目标位置。"),
        write_proposal=decision.write_proposal,
    )


def _requests_sequential_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and SEQUENTIAL_EXPLANATION_REQUEST_PATTERN.search(compact))


def _ordered_explanation_candidates(
    *,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> list[BoardFocusRef]:
    candidates = decision.candidate_focuses or (resolution.candidates if resolution else [])
    seen: set[tuple[str | None, str]] = set()
    ordered: list[BoardFocusRef] = []
    for candidate in candidates:
        key = (candidate.segment_id, candidate.excerpt)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered


def _apply_explicit_sequential_explanation_choice(
    *,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    request_message: str,
) -> BoardTaskRouteDecision:
    if board_task.requested_action != "explain":
        return decision
    if decision.route != "clarify_location" or decision.location_status != "ambiguous":
        return decision
    if not _requests_sequential_explanation(request_message):
        return decision
    candidates = _ordered_explanation_candidates(decision=decision, resolution=resolution)
    if not candidates:
        return decision
    return BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=candidates[0],
        candidate_focuses=candidates,
        reason=(
            "用户已经明确要求全部或按顺序讲解多个候选目标；"
            "本轮先从第一个候选目标开始讲解，不再反复要求用户选择位置。"
        ),
        write_proposal=decision.write_proposal,
    )


def _board_task_action_to_board_action(board_task: BoardTaskRequirementSheet) -> BoardTaskAction | None:
    if board_task.requested_action == "edit":
        return "rewrite_target"
    if board_task.requested_action == "explain":
        return "explain_target"
    if board_task.requested_action == "chat":
        return "explain_target"
    if board_task.requested_action == "write":
        return "append_section"
    return None


def _generate_board_task_clarification_message(
    *,
    lesson: Lesson,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    board_task: BoardTaskRequirementSheet,
    context: str,
) -> tuple[str, str]:
    visible_task = _chatbot_visible_board_task(board_task)
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=board_task.question_or_topic or lesson.summary,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=(
            "当前已有板书任务清单还不完整，不能执行写、改、讲或聊。"
            "请只自然追问一个最关键缺项，不要讲解，也不要承诺已经改写文档。\n"
            f"任务清单：{visible_task}\n"
            f"追问方向：{context}"
        ),
        selection_excerpt=None,
        interaction_mode=request.interaction_mode,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_board_task_clarification" if chatbot_message else "chatbot_empty"


def _chatbot_visible_board_task(board_task: BoardTaskRequirementSheet) -> dict[str, object]:
    payload = board_task.model_dump(mode="json")
    if payload.get("target_hint"):
        payload["target_hint"] = "已由板书侧记录；Chatbot 无直接读取目标板书内容权限。"
    if payload.get("target_location"):
        payload["target_location"] = "已由板书侧定位；Chatbot 无直接读取目标板书内容权限。"
    return payload


def _board_task_explanation_target_excerpt(
    *,
    board_task: BoardTaskRequirementSheet,
    focus: BoardFocusRef | None,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> str:
    parts = [
        "已有板书任务清单已进入 explain 路线。",
        f"用户目标线索：{board_task.target_hint or '未单独提供'}",
        f"用户问题/主题：{board_task.question_or_topic or '未单独提供'}",
        f"定位裁决：{decision.reason or '已定位目标内容'}",
    ]
    if focus is not None:
        parts.append(f"当前允许讲解的目标内容：\n{focus_context(focus)}")
    other_candidates = [
        candidate
        for candidate in (decision.candidate_focuses or (resolution.candidates if resolution else []))
        if focus is None or (candidate.segment_id, candidate.excerpt) != (focus.segment_id, focus.excerpt)
    ]
    if other_candidates:
        candidate_lines = [
            f"{index}. {candidate.display_label or ' / '.join(candidate.heading_path) or '板书片段'}（正文摘录仅供板书侧后续授权，不交给 Chatbot）"
            for index, candidate in enumerate(other_candidates[:4], start=1)
        ]
        parts.append("同一任务中还存在的后续候选目标，仅作为顺序讲解上下文，不得越界讲解：\n" + "\n".join(candidate_lines))
    return "\n\n".join(part for part in parts if part.strip())


def _path_starts_with(path: list[str], prefix: list[str]) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def _common_heading_path(candidates: list[BoardFocusRef]) -> list[str]:
    paths = [candidate.heading_path for candidate in candidates if candidate.heading_path]
    if not paths:
        return []
    common: list[str] = []
    for parts in zip(*paths):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return common


def _dedupe_focuses(candidates: list[BoardFocusRef]) -> list[BoardFocusRef]:
    seen: set[tuple[str | None, str]] = set()
    deduped: list[BoardFocusRef] = []
    for candidate in candidates:
        key = (candidate.segment_id, candidate.excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _find_heading_segment_by_path(segments: list[BoardSegment], heading_path: list[str]) -> BoardSegment | None:
    if not heading_path:
        return None
    return next(
        (
            segment
            for segment in segments
            if segment.kind == "heading"
            and segment.heading_path == heading_path
            and _compact_text(segment.text, limit=240) == _compact_text(heading_path[-1], limit=240)
        ),
        None,
    )


def _section_bounds(segments: list[BoardSegment], heading: BoardSegment) -> tuple[int, int]:
    start = heading.order_index
    end = start
    level = len(heading.heading_path)
    for segment in segments[start + 1 :]:
        if segment.kind == "heading" and len(segment.heading_path) <= level:
            break
        end = segment.order_index
    return start, end


def _section_focus_from_heading(
    *,
    lesson: Lesson,
    segments: list[BoardSegment],
    heading: BoardSegment,
    confidence: float,
    reason: str,
    match_source: str,
) -> BoardFocusRef:
    start, end = _section_bounds(segments, heading)
    section_segments = [segment for segment in segments[start : end + 1] if segment.text.strip()]
    excerpt = _compact_text("\n".join(segment.text for segment in section_segments), limit=2600)
    before = _compact_text(segments[start - 1].text, limit=500) if start > 0 else ""
    after = _compact_text(segments[end + 1].text, limit=500) if end + 1 < len(segments) else ""
    source_segment_ids = [segment.segment_id for segment in section_segments]
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=heading.segment_id,
        kind="heading",
        heading_path=heading.heading_path,
        excerpt=excerpt,
        before_text=before,
        after_text=after,
        text_hash=heading.text_hash,
        excerpt_hash=segment_text_hash(excerpt),
        confidence=confidence,
        reason=reason,
        display_label=" / ".join(heading.heading_path),
        match_id=f"{match_source}:{heading.segment_id}",
        source_segment_ids=source_segment_ids,
        order_start=start,
        order_end=end,
        score_breakdown={match_source: confidence},
    )


def _direct_child_section_headings(segments: list[BoardSegment], parent_heading: BoardSegment) -> list[BoardSegment]:
    parent_path = parent_heading.heading_path
    parent_start, parent_end = _section_bounds(segments, parent_heading)
    return [
        segment
        for segment in segments[parent_start + 1 : parent_end + 1]
        if segment.kind == "heading"
        and len(segment.heading_path) == len(parent_path) + 1
        and _path_starts_with(segment.heading_path, parent_path)
    ]


def _parent_heading_for_section_sequence(
    *,
    segments: list[BoardSegment],
    candidates: list[BoardFocusRef],
) -> BoardSegment | None:
    for candidate in candidates:
        if candidate.kind != "heading" or not candidate.heading_path:
            continue
        if all(_path_starts_with(other.heading_path, candidate.heading_path) for other in candidates if other.heading_path):
            heading = _find_heading_segment_by_path(segments, candidate.heading_path)
            if heading and _direct_child_section_headings(segments, heading):
                return heading

    common_path = _common_heading_path(candidates)
    while len(common_path) >= 2:
        heading = _find_heading_segment_by_path(segments, common_path)
        if heading and _direct_child_section_headings(segments, heading):
            return heading
        common_path = common_path[:-1]
    return None


def _section_explanation_sequence(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
) -> list[BoardFocusRef]:
    if board_task.requested_action != "explain":
        return []
    candidates = decision.candidate_focuses or (resolution.candidates if resolution else [])
    focus = _decision_focus(decision, resolution)
    if focus is not None:
        candidates = [focus, *candidates]
    candidates = _dedupe_focuses(candidates)
    if not candidates:
        return []

    segments = build_board_segment_index(lesson.board_document).segments
    parent_heading = _parent_heading_for_section_sequence(segments=segments, candidates=candidates)
    if parent_heading is None:
        return []
    child_headings = _direct_child_section_headings(segments, parent_heading)
    if len(child_headings) < 2:
        return []
    return [
        _section_focus_from_heading(
            lesson=lesson,
            segments=segments,
            heading=heading,
            confidence=0.92,
            reason="用户目标定位到父级章节；按该章节下的直接子节顺序讲解。",
            match_source="section_sequence",
        )
        for heading in child_headings
    ]


def _section_sequence_instruction(
    *,
    request_message: str,
    focus: BoardFocusRef,
    index: int,
    total: int,
) -> str:
    next_note = (
        "讲完后请询问学习者是否可以继续下一小节。"
        if index + 1 < total
        else "讲完后请确认学习者是否还有问题；如果没有问题，本组章节讲解可以结束。"
    )
    return (
        f"{request_message}\n"
        f"系统顺序讲解要求：本轮只讲第 {index + 1}/{total} 个子节："
        f"{focus.display_label or ' / '.join(focus.heading_path)}。"
        f"{next_note}不要越界讲解其它子节。"
    )


def _start_section_explanation_sequence(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    board_task_history: BoardTaskHistoryRecorder,
    board_task_stamp: BoardTaskHistoryStamp,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    sequence_items: list[BoardFocusRef],
    requirement_history: LearningRequirementHistoryRecorder,
    interaction_metadata: dict[str, object],
) -> ChatResponse:
    first_focus = sequence_items[0]
    session_before = lesson.active_interaction_session
    session_after = InteractionSession(
        status="active",
        rule_text="按板书父级章节的直接子节顺序逐小节讲解。",
        interaction_goal=(
            f"按顺序讲解 {first_focus.heading_path[-2]}"
            if len(first_focus.heading_path) >= 2
            else board_task.question_or_topic or board_task.target_hint
        ),
        target_focus=first_focus,
        reference_context=focus_context(first_focus),
        compliant_input_rule="用户确认理解、提出当前小节问题，或要求继续下一小节。",
        expected_user_behavior="用户确认当前小节是否可以接受；没有问题时继续下一小节。",
        assistant_behavior="每轮只讲当前子节，结尾询问是否继续下一子节。",
        progress_note=f"准备讲解第 1/{len(sequence_items)} 个子节。",
        turn_count=0,
        source_board_task_run_id=board_task_stamp.run_id,
        source_board_task_version_id=board_task_stamp.version_id,
        source_board_task_route="explain",
        sequence_items=sequence_items,
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=_requirements_from_board_task(
            base=requirements,
            board_task=board_task,
            action_type="explain_target",
            focus=first_focus,
        ),
        resources=resources,
        conversation=request.conversation,
        request=request.model_copy(
            update={
                "message": _section_sequence_instruction(
                    request_message=request.message,
                    focus=first_focus,
                    index=0,
                    total=len(sequence_items),
                )
            }
        ),
        learning_clarification=learning_clarification,
        action_type="explain_target",
        target_excerpt=focus_context(first_focus),
        interaction_context=interaction_context_payload(session=session_after),
    )
    lesson.board_task_requirements = None
    _clear_task_requirements(lesson)
    commit_operations(
        lesson,
        [],
        label="Section explanation session start",
        message="Started a sequential section explanation session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            **interaction_metadata,
            **_board_search_evidence_metadata(resolution),
            "section_explanation_sequence": [item.model_dump(mode="json") for item in sequence_items],
            **_task_metadata(
                requirements=_requirements_from_board_task(
                    base=requirements,
                    board_task=board_task,
                    action_type="explain_target",
                    focus=first_focus,
                ),
                learning_clarification=learning_clarification,
                focus=first_focus,
                focus_candidates=sequence_items,
                requirement_cleared=True,
            ),
            **_board_task_metadata(
                board_task=board_task,
                stamp=board_task_stamp,
                route="explain",
                decision=decision.model_dump(mode="json"),
                cleared=True,
            ),
            **interaction_session_metadata(before=session_before, after=session_after),
        },
    )
    consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=requirements,
        learning_clarification=learning_clarification,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        resolved_focus=first_focus,
        focus_candidates=sequence_items,
        requirement_cleared=True,
        board_task_stamp=consumed_stamp,
    )


def _handle_existing_board_task_flow(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    selection_excerpt: str | None,
    selection_text: str | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    source_interaction_metadata: dict[str, object] | None = None,
    force_task_attempt: bool = False,
) -> ChatResponse | None:
    if is_document_empty(lesson.board_document):
        return None
    if request.board_generation_action == "start" or request.teaching_action is not None:
        return None
    if request.resource_reference_action is not None:
        return None
    existing_task = lesson.board_task_requirements
    interaction_metadata = source_interaction_metadata or {}
    compact_request = _compact_text(request.message, limit=280)
    if not existing_task and (
        _requests_learning_start(request.message)
        or bool(re.search(r"(开始|直接|从头|零基础).{0,12}(讲解|讲|学)", compact_request))
        or _requests_existing_board_generation_control(request.message)
    ):
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    if (
        existing_task is not None
        and existing_task.confirmation_status == "awaiting"
        and existing_task.requested_action == "write"
    ):
        if is_write_decline(request.message):
            stamp = board_task_history.not_executed(reason="用户取消了扩写确认。")
            lesson.board_task_requirements = None
            commit_operations(
                lesson,
                [],
                label="Board task cancelled",
                message="Cancelled an awaiting board write task",
                new_document=lesson.board_document,
                metadata={
                    "kind": "chat_flow",
                    "user_message": request.message,
                    "assistant_message": "",
                    "assistant_message_source": "board_task_cancelled",
                    **interaction_metadata,
                    **_board_task_metadata(board_task=existing_task, stamp=stamp, route="await_write_confirmation", cleared=True),
                },
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message="",
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="no_change", reason="用户取消了扩写。"),
                board_task_stamp=stamp,
            )
        if is_write_confirmation(request.message):
            confirmed_task = BoardTaskRequirementSheet.model_validate(existing_task.model_dump(mode="json"))
            confirmed_task.confirmation_status = "confirmed"
            confirmed_task.progress = 100
            return _execute_board_task_write(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=resources,
                board_task=confirmed_task,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
            )

    action_type = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=False,
    )
    if (
        action_type is None
        and not existing_task
        and request.interaction_mode != "direct_edit"
        and not INTERACTION_RULE_REQUEST_PATTERN.search(_compact_text(request.message, limit=280))
        and not _requests_explanation(request.message)
        and not force_task_attempt
    ):
        return None

    board_task = update_board_task_from_chat(
        lesson=lesson,
        resources=resources,
        conversation=request.conversation,
        user_message=request.message,
        selection=request.selection,
        selection_excerpt=selection_excerpt,
        existing=existing_task,
    )
    board_task = _maybe_inherit_recent_board_edit_focus(
        lesson=lesson,
        board_task=board_task,
        request_message=request.message,
    )
    _activate_board_task_requirements(lesson, board_task)
    stamp = board_task_history.record_update(sheet=board_task)
    _emit_board_task_update(lesson=lesson, sheet=board_task, stamp=stamp)
    if board_task.progress < 100:
        chatbot_message, chatbot_message_source = _generate_board_task_clarification_message(
            lesson=lesson,
            resources=resources,
            conversation=request.conversation,
            request=request,
            board_task=board_task,
            context=board_task.clarification_question,
        )
        commit_operations(
            lesson,
            [],
            label="Board task clarification",
            message="Asked for a missing field in the existing-board task sheet",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **interaction_metadata,
                **_board_task_metadata(board_task=board_task, stamp=stamp, route="clarify_location", cleared=False),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason=board_task.clarification_question),
            board_task_history=board_task_history,
        )

    board_action = _board_task_action_to_board_action(board_task)
    resolution = None
    original_location_status = board_task.location_status
    if _requests_whole_document_scope(request.message, board_task.target_hint, board_task.question_or_topic):
        resolution = _synthetic_focus_resolution(_whole_document_focus(lesson))
    elif board_task.requested_action != "write" or board_task.target_hint or selection_excerpt:
        locator_query = _compact_text(" ".join(part for part in [board_task.target_hint, board_task.question_or_topic] if part), limit=500)
        resolution = resolve_board_focus(
            lesson=lesson,
            user_message=locator_query,
            selection=request.selection,
            selection_text=selection_text,
            action_type=board_action,
            board_task=board_task,
        )
    if resolution and resolution.resolved and resolution.focus:
        resolved_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
        resolved_task.target_location = resolution.focus
        resolved_task.location_status = "selected" if resolution.status == "selected" else "resolved"
        _activate_board_task_requirements(lesson, resolved_task)
        stamp = board_task_history.record_update(
            sheet=resolved_task,
            change_summary="Board-side locator confirmed the target location.",
        )
        _emit_board_task_update(lesson=lesson, sheet=resolved_task, stamp=stamp)
        board_task = resolved_task
    can_use_local_route_decision = (
        resolution is not None
        and resolution.resolved
        and board_task.requested_action in {"write", "edit", "explain", "chat"}
        and original_location_status != "ambiguous"
        and not _requests_sequential_explanation(request.message)
    )
    if can_use_local_route_decision:
        decision = _fallback_board_task_decision(board_task=board_task, resolution=resolution)
    else:
        decision = openai_course_ai.generate_board_task_route_decision(
            lesson_title=lesson.title,
            board_task=board_task,
            location_evidence=_task_location_evidence(resolution),
            resource_summary=_resource_summary(resources),
        ) or _fallback_board_task_decision(board_task=board_task, resolution=resolution)
    decision = _with_decision_target_scope(
        decision=decision,
        board_task=board_task,
        request_message=request.message,
        resolution=resolution,
    )
    if _decision_must_have_focus(board_task=board_task, decision=decision) and _decision_focus(decision, resolution) is None:
        decision = _clarify_decision_for_missing_focus(decision=decision, resolution=resolution)
    decision = _apply_explicit_sequential_explanation_choice(
        board_task=board_task,
        decision=decision,
        resolution=resolution,
        request_message=request.message,
    )
    decision = _with_decision_target_scope(
        decision=decision,
        board_task=board_task,
        request_message=request.message,
        resolution=resolution,
    )
    section_sequence = _section_explanation_sequence(
        lesson=lesson,
        board_task=board_task,
        decision=decision,
        resolution=resolution,
    )
    if section_sequence:
        return _start_section_explanation_sequence(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=stamp,
            decision=decision,
            resolution=resolution,
            sequence_items=section_sequence,
            requirement_history=requirement_history,
            interaction_metadata=interaction_metadata,
        )

    if decision.route == "clarify_location":
        next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
        next_task.location_status = "ambiguous" if decision.location_status == "ambiguous" else "missing"
        next_task.failure_count += 1 if board_task.requested_action == "edit" else 0
        if board_task.requested_action == "edit" and next_task.failure_count >= 2:
            old_stamp = board_task_history.record_update(
                sheet=next_task,
                change_summary="Edit target could not be located twice.",
            )
            board_task_history.not_executed(reason="编辑目标连续两次未定位，旧任务未执行。")
            new_task = make_write_task_from_topic(board_task.question_or_topic)
            _activate_board_task_requirements(lesson, new_task)
            new_stamp = board_task_history.record_update(
                sheet=new_task,
                status="awaiting_confirmation",
                change_summary="Created a write task from an unresolved edit topic.",
            )
            _emit_board_task_update(lesson=lesson, sheet=new_task, stamp=new_stamp)
            chatbot_message, chatbot_message_source = _generate_board_task_clarification_message(
                lesson=lesson,
                resources=resources,
                conversation=request.conversation,
                request=request,
                board_task=new_task,
                context="板书里没有定位到可编辑的原内容。请确认是否改为扩写相关内容。",
            )
            commit_operations(
                lesson,
                [],
                label="Board task converted to write confirmation",
                message="Archived an unresolved edit task and opened a write confirmation task",
                new_document=lesson.board_document,
                metadata={
                    "kind": "chat_flow",
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    **interaction_metadata,
                    **_board_search_evidence_metadata(resolution),
                    **_board_task_metadata(board_task=board_task, stamp=old_stamp, route="clarify_location", cleared=True),
                    "new_board_task": new_task.model_dump(mode="json"),
                    "new_board_task_run_id": new_stamp.run_id,
                    "new_board_task_version_id": new_stamp.version_id,
                },
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="no_change", reason="编辑目标未定位，已转为扩写确认。"),
                board_task_stamp=new_stamp,
            )
        _activate_board_task_requirements(lesson, next_task)
        stamp = board_task_history.record_update(sheet=next_task, change_summary=decision.reason)
        _emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
        chatbot_message, chatbot_message_source = _generate_focus_candidate_message(
            lesson=lesson,
            requirements=_requirements_from_board_task(
                base=requirements,
                board_task=next_task,
                action_type=board_action,
            ),
            resources=resources,
            conversation=request.conversation,
            request=request,
            resolution=resolution or FocusResolution(
                focus=None,
                candidates=decision.candidate_focuses,
                status="ambiguous" if decision.candidate_focuses else "missing",
                question=decision.reason,
            ),
        )
        commit_operations(
            lesson,
            [],
            label="Board task location clarification",
            message="Asked the learner to confirm the board task location",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                **interaction_metadata,
                **_board_search_evidence_metadata(resolution),
                **_task_metadata(
                    requirements=_requirements_from_board_task(
                        base=requirements,
                        board_task=next_task,
                        action_type=board_action,
                    ),
                    learning_clarification=learning_clarification,
                    focus=None,
                    focus_candidates=decision.candidate_focuses,
                    requirement_cleared=False,
                ),
                **_board_task_metadata(
                    board_task=next_task,
                    stamp=stamp,
                    route=decision.route,
                    decision=decision.model_dump(mode="json"),
                    cleared=False,
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="await_focus_choice", reason=decision.reason),
            focus_candidates=decision.candidate_focuses,
            board_task_history=board_task_history,
        )

    if decision.route == "await_write_confirmation":
        next_task = BoardTaskRequirementSheet.model_validate(board_task.model_dump(mode="json"))
        next_task.requested_action = "write"
        next_task.location_status = "content_absent"
        next_task.confirmation_status = "awaiting"
        next_task.progress = 100
        next_task.missing_items = []
        next_task.clarification_question = ""
        _activate_board_task_requirements(lesson, next_task)
        stamp = board_task_history.record_update(
            sheet=next_task,
            status="awaiting_confirmation",
            change_summary=decision.reason or "Awaiting learner confirmation before writing new board content.",
        )
        _emit_board_task_update(lesson=lesson, sheet=next_task, stamp=stamp)
        chatbot_message, chatbot_message_source = _generate_board_task_clarification_message(
            lesson=lesson,
            resources=resources,
            conversation=request.conversation,
            request=request,
            board_task=next_task,
            context="板书里没有对应内容。请询问用户是否要先扩写板书，再继续学习。",
        )
        commit_operations(
            lesson,
            [],
            label="Board write confirmation",
            message="Asked the learner to confirm writing absent board content",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                **interaction_metadata,
                **_board_search_evidence_metadata(resolution),
                **_board_task_metadata(
                    board_task=next_task,
                    stamp=stamp,
                    route=decision.route,
                    decision=decision.model_dump(mode="json"),
                    cleared=False,
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            board_task_history=board_task_history,
        )

    if decision.route == "write":
        return _execute_board_task_write(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            board_task=board_task,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
            route_decision=decision,
            search_evidence=resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
            source_interaction_metadata=interaction_metadata,
        )

    if decision.route == "edit":
        focus = decision.target_focus or (resolution.focus if resolution else None)
        edit_action = action_type if action_type in EDIT_ACTIONS else "rewrite_target"
        target_scope = decision.target_scope or (
            "whole_document" if focus and focus.match_id and focus.match_id.startswith("whole_document:") else "focus"
        )
        task_requirements = _requirements_from_board_task(
            base=requirements,
            board_task=board_task,
            action_type=edit_action,
            focus=focus,
        )
        edit_outcome = edit_existing_document(
            lesson=lesson,
            requirements=task_requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(resources),
            conversation_summary=_conversation_summary(request.conversation),
            user_instruction=request.message,
            selection_excerpt=selection_excerpt,
            focus=focus,
            target_scope=target_scope,
            allow_replace_document=target_scope == "whole_document",
        )
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
        stamp = board_task_history.record_update(sheet=board_task, status="ready")
        if not edit_outcome.changed:
            failed_stamp = board_task_history.execution_failed(
                reason=edit_outcome.summary or "Board task edit did not produce a safe document change.",
                metadata={
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_task_route": "edit",
                    "board_task_decision": decision.model_dump(mode="json"),
                    "board_task_cleared": False,
                    "target_scope": target_scope,
                    **_board_search_evidence_metadata(resolution),
                },
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                resolved_focus=focus,
                requirement_cleared=False,
                board_task_stamp=failed_stamp,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )
        recent_focus = _recent_board_edit_focus_for_commit(
            lesson=lesson,
            fallback_focus=None if target_scope == "whole_document" else focus,
            section_titles=edit_outcome.section_titles,
        )
        commit_operations(
            lesson,
            [],
            label="Board task edit",
            message="Executed an existing-board edit task",
            new_document=lesson.board_document,
            metadata={
                "kind": "board_document_edit",
                "user_message": request.message,
                "assistant_message": edit_outcome.chatbot_message,
                "assistant_message_source": edit_outcome.assistant_message_source,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_section_titles": edit_outcome.section_titles,
                "target_scope": target_scope,
                "recent_board_edit_focus": recent_focus.model_dump(mode="json") if recent_focus else None,
                **interaction_metadata,
                "board_search_evidence": (
                    resolution.evidence.model_dump(mode="json")
                    if resolution and resolution.evidence
                    else _implicit_board_search_evidence(
                        route="edit",
                        target_scope=target_scope,
                        reason="编辑链路使用全文或继承目标范围，没有独立检索证据。",
                    )
                ),
                **_task_metadata(
                    requirements=task_requirements,
                    learning_clarification=learning_clarification,
                    focus=focus,
                    requirement_cleared=True,
                ),
                **_board_task_metadata(
                    board_task=board_task,
                    stamp=stamp,
                    route="edit",
                    decision=decision.model_dump(mode="json"),
                    cleared=True,
                ),
            },
        )
        consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        lesson.board_task_requirements = None
        _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=edit_outcome.chatbot_message,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            resolved_focus=focus,
            requirement_cleared=True,
            board_task_stamp=consumed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )

    if decision.route == "explain":
        focus = decision.target_focus or (resolution.focus if resolution else None)
        focus_excerpt = _board_task_explanation_target_excerpt(
            board_task=board_task,
            focus=focus,
            decision=decision,
            resolution=resolution,
        )
        chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=_requirements_from_board_task(
                base=requirements,
                board_task=board_task,
                action_type="explain_target",
                focus=focus,
            ),
            resources=resources,
            conversation=request.conversation,
            request=request,
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=focus_excerpt,
        )
        stamp = board_task_history.record_update(sheet=board_task, status="ready")
        cleared = chatbot_message_source == "chatbot_board_directed" and bool(chatbot_message)
        if not chatbot_message:
            failed_stamp = board_task_history.execution_failed(
                reason="Board-directed explanation failed because Chatbot returned empty.",
                metadata={
                    "assistant_message_source": chatbot_message_source,
                    "board_explanation_failed": True,
                    "board_task_route": "explain",
                    "board_task_cleared": False,
                    "board_explanation_directive": board_explanation_directive,
                    "board_task_decision": decision.model_dump(mode="json"),
                    **_board_search_evidence_metadata(resolution),
                },
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message="",
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="no_change", reason="Board-directed explanation failed because Chatbot returned empty."),
                resolved_focus=focus,
                requirement_cleared=False,
                board_task_stamp=failed_stamp,
            )
        commit_operations(
            lesson,
            [],
            label="Board task explanation",
            message="Executed an existing-board explanation task",
            new_document=lesson.board_document,
            metadata={
                "kind": "chat_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "board_explanation_directive": board_explanation_directive,
                **interaction_metadata,
                **_board_search_evidence_metadata(resolution),
                **_task_metadata(
                    requirements=_requirements_from_board_task(
                        base=requirements,
                        board_task=board_task,
                        action_type="explain_target",
                        focus=focus,
                    ),
                    learning_clarification=learning_clarification,
                    focus=focus,
                    focus_candidates=resolution.candidates if resolution else [],
                    requirement_cleared=cleared,
                ),
                **_board_task_metadata(
                    board_task=board_task,
                    stamp=stamp,
                    route="explain",
                    decision=decision.model_dump(mode="json"),
                    cleared=cleared,
                ),
            },
        )
        consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id) if cleared else stamp
        if cleared:
            lesson.board_task_requirements = None
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            resolved_focus=focus,
            requirement_cleared=cleared,
            board_task_stamp=consumed_stamp,
        )

    if decision.route == "chat":
        focus = _decision_focus(decision, resolution)
        task_requirements = _requirements_from_board_task(
            base=requirements,
            board_task=board_task,
            action_type="explain_target",
            focus=focus,
        )
        lesson.learning_requirements = task_requirements
        return _maybe_start_interaction_session(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            resources=resources,
            selection_text=selection_text,
            action_type="explain_target",
            requirement_history=requirement_history,
            board_task=board_task,
            board_task_history=board_task_history,
            board_task_stamp=stamp,
            board_task_decision=decision,
            resolved_focus=focus,
            source_interaction_metadata={
                **interaction_metadata,
                **_board_search_evidence_metadata(resolution),
            },
        )

    return None


def _execute_board_task_write(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    board_task: BoardTaskRequirementSheet,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
    route_decision: BoardTaskRouteDecision | None = None,
    search_evidence: dict[str, object] | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse:
    interaction_metadata = source_interaction_metadata or {}
    target_focus = route_decision.target_focus if route_decision else None
    target_scope = (route_decision.target_scope if route_decision else None) or ("focus" if target_focus else "append")
    task_requirements = _requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="expand_target" if target_focus else "append_section",
        focus=target_focus,
    )
    task_requirements.action_instruction = route_decision.write_proposal if route_decision and route_decision.write_proposal else board_task.question_or_topic
    stamp = board_task_history.record_update(
        sheet=board_task,
        status="awaiting_confirmation" if board_task.confirmation_status == "confirmed" else "ready",
    )
    edit_outcome = edit_existing_document(
        lesson=lesson,
        requirements=task_requirements,
        clarification=learning_clarification,
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(request.conversation),
        user_instruction=task_requirements.action_instruction,
        selection_excerpt=None,
        focus=target_focus,
        target_scope=target_scope,
        allow_replace_document=False,
    )
    if edit_outcome.changed:
        old_text = lesson.board_document.content_text
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=task_requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        recent_focus = _recent_board_edit_focus_for_commit(
            lesson=lesson,
            fallback_focus=target_focus,
            section_titles=edit_outcome.section_titles,
        )
        new_text = lesson.board_document.content_text
        appended_excerpt = new_text[len(old_text):].strip() if new_text.startswith(old_text) else edit_outcome.new_document.content_text
        if edit_outcome.chatbot_message and board_task.confirmation_status != "confirmed":
            chatbot_message = edit_outcome.chatbot_message
            chatbot_message_source = edit_outcome.assistant_message_source
            board_explanation_directive = {
                "status": "approved",
                "source": "board_document_editor_ai",
                "target_excerpt": appended_excerpt or edit_outcome.new_document.content_text,
            }
        else:
            chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
                lesson=lesson,
                requirements=task_requirements,
                resources=resources,
                conversation=request.conversation,
                request=request,
                learning_clarification=learning_clarification,
                action_type="explain_target",
                target_excerpt=appended_excerpt or edit_outcome.new_document.content_text,
            )
    else:
        chatbot_message = edit_outcome.chatbot_message
        chatbot_message_source = edit_outcome.assistant_message_source
        board_explanation_directive = None
        recent_focus = None

    if not edit_outcome.changed:
        failed_stamp = board_task_history.execution_failed(
            reason=edit_outcome.summary or "Board task write did not produce a safe document change.",
            metadata={
                "assistant_message_source": chatbot_message_source,
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_task_route": "write",
                "board_task_decision": route_decision.model_dump(mode="json") if route_decision else None,
                "board_task_cleared": False,
                "target_scope": target_scope,
                "board_search_evidence": search_evidence
                or _implicit_board_search_evidence(
                    route="write",
                    target_scope=target_scope,
                    reason="写链路没有独立定位证据；由任务清单和 Board AI 裁决进入。",
                ),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=task_requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_cleared=False,
            board_task_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )

    commit_operations(
        lesson,
        [],
        label="Board task write",
        message="Wrote missing existing-board task content and prepared a board-grounded explanation",
        new_document=lesson.board_document,
        metadata={
            "kind": "board_document_edit",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            "target_scope": target_scope,
            "recent_board_edit_focus": recent_focus.model_dump(mode="json") if recent_focus else None,
            "board_explanation_directive": board_explanation_directive,
            **interaction_metadata,
            "board_search_evidence": search_evidence
            or _implicit_board_search_evidence(
                route="write",
                target_scope=target_scope,
                reason="写链路没有独立定位证据；由任务清单和 Board AI 裁决进入。",
            ),
            **_task_metadata(
                requirements=task_requirements,
                learning_clarification=learning_clarification,
                focus=target_focus,
                requirement_cleared=edit_outcome.changed,
            ),
            **_board_task_metadata(
                board_task=board_task,
                stamp=stamp,
                route="write",
                decision=route_decision.model_dump(mode="json") if route_decision else None,
                cleared=edit_outcome.changed,
            ),
        },
    )
    consumed_stamp = board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id) if edit_outcome.changed else stamp
    if edit_outcome.changed:
        lesson.board_task_requirements = None
        _clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        requirements=task_requirements,
        learning_clarification=learning_clarification,
        board_decision=edit_outcome.board_decision,
        requirement_cleared=edit_outcome.changed,
        board_task_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
    )


def _response_requirement_stamp(
    requirement_history: LearningRequirementHistoryRecorder | None,
    requirement_stamp: RequirementHistoryStamp | None,
) -> RequirementHistoryStamp | None:
    if requirement_stamp is not None:
        return requirement_stamp
    if requirement_history is None:
        return None
    return requirement_history.current_stamp()


def _response_board_task_stamp(
    board_task_history: BoardTaskHistoryRecorder | None,
    board_task_stamp: BoardTaskHistoryStamp | None,
) -> BoardTaskHistoryStamp | None:
    if board_task_stamp is not None:
        return board_task_stamp
    if board_task_history is None:
        return None
    return board_task_history.current_stamp()


def _board_document_failure_metadata(edit_outcome) -> dict[str, object]:
    context = current_ai_log_context()
    metadata: dict[str, object] = {
        "assistant_message_source": edit_outcome.assistant_message_source,
        "board_edit_operation": edit_outcome.operation,
        "board_edit_summary": edit_outcome.summary,
        "board_document_operation_status": edit_outcome.operation_status,
    }
    trace_id = context.get("trace_id")
    if trace_id:
        metadata["trace_id"] = trace_id
    return metadata


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
    requirement_history: LearningRequirementHistoryRecorder | None = None,
    requirement_stamp: RequirementHistoryStamp | None = None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    board_document_operation_status: str = "none",
    board_document_operation_failure_reason: str | None = None,
) -> ChatResponse:
    stamp = _response_requirement_stamp(requirement_history, requirement_stamp)
    board_task_stamp_value = _response_board_task_stamp(board_task_history, board_task_stamp)
    visible_requirement_cleared = requirement_cleared or lesson.board_task_requirements is not None
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=lesson.learning_requirements,
        active_interaction_session=lesson.active_interaction_session,
        interaction_decision=interaction_decision,
        learning_clarification=learning_clarification,
        requirement_run_id=stamp.run_id if stamp else None,
        requirement_version_id=stamp.version_id if stamp else None,
        requirement_phase=stamp.phase if stamp else None,
        board_task_sheet=lesson.board_task_requirements,
        active_board_task_sheet=lesson.board_task_requirements,
        board_task_run_id=board_task_stamp_value.run_id if board_task_stamp_value else None,
        board_task_version_id=board_task_stamp_value.version_id if board_task_stamp_value else None,
        board_task_phase=board_task_stamp_value.phase if board_task_stamp_value else None,
        board_task_questions=_board_task_questions(lesson.board_task_requirements),
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
        requirement_cleared=visible_requirement_cleared,
        board_document_operation_status=board_document_operation_status,
        board_document_operation_failure_reason=board_document_operation_failure_reason,
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
) -> tuple[str, str, dict[str, object] | None]:
    context = interaction_context_payload(session=session, decision=decision)
    if decision is not None and decision.route == "side_learning_request":
        return _generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=conversation,
            request=request,
            learning_clarification=_latest_learning_clarification(lesson, requirements=requirements),
            action_type="side_learning_request",
            target_excerpt=session.reference_context,
            interaction_context=context,
        )
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=request.message,
        selection_excerpt=session.reference_context,
        interaction_mode="interaction_rule",
        interaction_context=context,
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_interaction" if chatbot_message else "chatbot_empty", None


def _is_section_explanation_session(session: InteractionSession) -> bool:
    return session.sequence_mode == "section_explanation" and bool(session.sequence_items)


def _is_sequence_continue_message(text: str) -> bool:
    compact = _compact_text(text, limit=80)
    return bool(compact and SEQUENCE_CONTINUE_PATTERN.search(compact))


def _is_sequence_exit_message(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and SEQUENCE_EXIT_PATTERN.search(compact))


def _generate_sequence_end_message(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    session: InteractionSession,
) -> tuple[str, str]:
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=(
            "用户已经确认顺序讲解的最后一个子节没有问题。"
            "请自然结束本组章节讲解，并询问是否还要回顾、练习或进入新的任务。"
        ),
        selection_excerpt=None,
        interaction_mode=request.interaction_mode,
        interaction_context=interaction_context_payload(session=session),
    )
    chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    return chatbot_message, "chatbot_interaction" if chatbot_message else "chatbot_empty"


def _handle_section_explanation_sequence_turn(
    *,
    workspace,
    package,
    lesson: Lesson,
    user_id: str,
    request: ChatRequest,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    requirement_history: LearningRequirementHistoryRecorder,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None or not _is_section_explanation_session(session_before):
        return None
    if _is_sequence_exit_message(request.message):
        session_after = None
        lesson.active_interaction_session = None
        chatbot_message, chatbot_message_source = _generate_sequence_end_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
        )
        decision = InteractionTurnDecision(
            route="exit_rule",
            reason="用户结束当前顺序讲解。",
            progress_note=session_before.progress_note,
            user_intent="结束顺序讲解",
        )
        commit_operations(
            lesson,
            [],
            label="Section explanation session ended",
            message="Ended a sequential section explanation session",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            requirement_history=requirement_history,
        )
    if not _is_sequence_continue_message(request.message):
        return None

    next_index = session_before.sequence_index + 1
    if next_index >= len(session_before.sequence_items):
        lesson.active_interaction_session = None
        chatbot_message, chatbot_message_source = _generate_sequence_end_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
        )
        decision = InteractionTurnDecision(
            route="exit_rule",
            reason="顺序讲解已经完成。",
            progress_note="顺序讲解已经完成。",
            user_intent="确认最后一个子节无问题",
        )
        commit_operations(
            lesson,
            [],
            label="Section explanation session completed",
            message="Completed a sequential section explanation session",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_session_metadata(before=session_before, after=None, decision=decision),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            requirement_history=requirement_history,
        )

    focus = session_before.sequence_items[next_index]
    session_after = session_before.model_copy(
        update={
            "target_focus": focus,
            "reference_context": focus_context(focus),
            "sequence_index": next_index,
            "progress_note": f"准备讲解第 {next_index + 1}/{len(session_before.sequence_items)} 个子节。",
            "turn_count": session_before.turn_count + 1,
            "status": "active",
            "pause_reason": "",
        }
    )
    lesson.active_interaction_session = session_after
    sequence_request = request.model_copy(
        update={
            "message": _section_sequence_instruction(
                request_message=request.message,
                focus=focus,
                index=next_index,
                total=len(session_after.sequence_items),
            )
        }
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=requirements.model_copy(update={"target_location": focus, "location_status": "resolved"}),
        resources=resources,
        conversation=request.conversation,
        request=sequence_request,
        learning_clarification=learning_clarification,
        action_type="explain_target",
        target_excerpt=focus_context(focus),
        interaction_context=interaction_context_payload(session=session_after),
    )
    decision = InteractionTurnDecision(
        route="continue_rule",
        reason="用户确认当前子节后继续下一子节。",
        progress_note=session_after.progress_note,
        user_intent="继续顺序讲解",
    )
    commit_operations(
        lesson,
        [],
        label="Section explanation turn",
        message="Continued a sequential section explanation session",
        new_document=lesson.board_document,
        metadata={
            "kind": "interaction_flow",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_explanation_directive": board_explanation_directive,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=focus,
                requirement_cleared=False,
            ),
            **interaction_session_metadata(before=session_before, after=session_after, decision=decision),
        },
    )
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        resolved_focus=focus,
        requirement_history=requirement_history,
    )


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
    selection_text: str | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task_history: BoardTaskHistoryRecorder,
) -> ChatResponse | None:
    session_before = lesson.active_interaction_session
    if session_before is None:
        return None

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    section_sequence_response = _handle_section_explanation_sequence_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        requirement_history=requirement_history,
    )
    if section_sequence_response is not None:
        return section_sequence_response
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
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=""),
            requirement_history=requirement_history,
        )

    if decision.route in {"exit_rule", "new_task", "side_learning_request"}:
        lesson.active_interaction_session = None
        interaction_exit_metadata = interaction_session_metadata(before=session_before, after=None, decision=decision)
        should_attempt_board_task = decision.route in {"new_task", "side_learning_request"} or bool(
            _infer_board_task_action(
                request,
                has_selection=bool(selection_excerpt),
                document_empty=is_document_empty(lesson.board_document),
            )
            or _requests_explanation(request.message)
        )
        if should_attempt_board_task:
            board_task_response = _handle_existing_board_task_flow(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                resources=resources,
                selection_excerpt=selection_excerpt,
                selection_text=selection_text,
                requirement_history=requirement_history,
                board_task_history=board_task_history,
                source_interaction_metadata=interaction_exit_metadata,
                force_task_attempt=decision.route in {"new_task", "side_learning_request"},
            )
            if board_task_response is not None:
                board_task_response.interaction_decision = decision
                return board_task_response
        chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
            decision=decision,
        )
        commit_operations(
            lesson,
            [],
            label="Interaction session ended",
            message="Exited a rule-based interaction session and found no executable board task in the same turn",
            new_document=lesson.board_document,
            metadata={
                "kind": "interaction_flow",
                "user_message": request.message,
                "assistant_message": chatbot_message,
                "assistant_message_source": chatbot_message_source,
                "board_explanation_directive": board_explanation_directive,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=False,
                ),
                **interaction_exit_metadata,
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            interaction_decision=decision,
            requirement_history=requirement_history,
        )

    session_after = apply_interaction_decision(session_before, decision)
    reply_session = session_after or session_before
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
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
            "board_explanation_directive": board_explanation_directive,
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
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(action="no_change", reason=decision.reason),
        interaction_decision=decision,
        requirement_history=requirement_history,
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
    action_type: BoardTaskAction | None,
    requirement_history: LearningRequirementHistoryRecorder,
    board_task: BoardTaskRequirementSheet | None = None,
    board_task_history: BoardTaskHistoryRecorder | None = None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    board_task_decision: BoardTaskRouteDecision | None = None,
    resolved_focus: BoardFocusRef | None = None,
    source_interaction_metadata: dict[str, object] | None = None,
) -> ChatResponse | None:
    interaction_metadata = source_interaction_metadata or {}
    if request.interaction_mode == "direct_edit" and action_type != "append_section":
        return None
    if not should_start_interaction(requirements.interaction_rule_draft):
        return None

    start_resolution = build_interaction_start(
        lesson=lesson,
        draft=requirements.interaction_rule_draft,
        user_message=request.message,
        selection=request.selection,
        selection_text=selection_text,
        resolved_focus=resolved_focus,
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
                **interaction_metadata,
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    focus=None,
                    focus_candidates=start_resolution.focus_resolution.candidates,
                    requirement_cleared=False,
                ),
                **(
                    _board_task_metadata(
                        board_task=board_task,
                        stamp=board_task_stamp,
                        route="chat",
                        decision=board_task_decision.model_dump(mode="json") if board_task_decision else None,
                        cleared=False,
                    )
                    if board_task is not None
                    else {}
                ),
                **interaction_session_metadata(before=None, after=None),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
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
            requirement_history=requirement_history,
        )

    if start_resolution.session is None:
        return None

    session_before = lesson.active_interaction_session
    session_after = start_resolution.session
    if board_task is not None and board_task_stamp is not None:
        session_after = session_after.model_copy(
            update={
                "source_board_task_run_id": board_task_stamp.run_id,
                "source_board_task_version_id": board_task_stamp.version_id,
                "source_board_task_route": "chat",
            }
        )
    lesson.active_interaction_session = session_after
    chatbot_message, chatbot_message_source, board_explanation_directive = _generate_interaction_chatbot_message(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=request.conversation,
        request=request,
        session=session_after,
        decision=None,
    )
    _clear_task_requirements(lesson)
    if board_task is not None:
        lesson.board_task_requirements = None
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
            "board_explanation_directive": board_explanation_directive,
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            **interaction_metadata,
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                focus=session_after.target_focus,
                focus_candidates=(
                    start_resolution.focus_resolution.candidates
                    if start_resolution.focus_resolution
                    else []
                ),
                requirement_cleared=True,
            ),
            **(
                _board_task_metadata(
                    board_task=board_task,
                    stamp=board_task_stamp,
                    route="chat",
                    decision=board_task_decision.model_dump(mode="json") if board_task_decision else None,
                    cleared=board_task is not None,
                )
                if board_task is not None
                else {}
            ),
            **interaction_session_metadata(
                before=session_before,
                after=session_after,
            ),
        },
    )
    consumed_board_task_stamp = (
        board_task_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if board_task is not None and board_task_history is not None
        else board_task_stamp
    )
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    return _response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        learning_clarification=learning_clarification,
        requirements=requirements,
        board_decision=BoardDecision(
            action="no_change",
            reason=session_after.interaction_goal,
        ),
        resolved_focus=session_after.target_focus,
        focus_candidates=(
            start_resolution.focus_resolution.candidates
            if start_resolution.focus_resolution
            else []
        ),
        requirement_cleared=True,
        requirement_history=requirement_history,
        board_task_stamp=consumed_board_task_stamp,
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
    requirement_history: LearningRequirementHistoryRecorder,
    track_initial_requirement_run: bool,
) -> ChatResponse:
    requirements = _with_task_details(
        requirements,
        action_type="generate_board",
        instruction=request.message,
    )
    requirements, learning_clarification, frozen_requirement = _prepare_initial_requirement_for_board_generation(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
    )
    _checkpoint_initial_requirement_before_generation(
        user_id=user_id,
        workspace=workspace,
        package=package,
        lesson=lesson,
        requirement_history=requirement_history,
        requirements=requirements,
        learning_clarification=learning_clarification,
        stamp=frozen_requirement,
    )
    edit_outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=learning_clarification,
        resource_summary=resource_summary_for_turn,
        requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
        frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
    )
    chatbot_message = edit_outcome.chatbot_message
    if not edit_outcome.changed:
        failed_stamp = (
            requirement_history.generation_failed(
                reason=edit_outcome.summary or chatbot_message,
                metadata=_board_document_failure_metadata(edit_outcome),
            )
            if frozen_requirement is not None
            else None
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
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
            requirement_stamp=failed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )
    if edit_outcome.changed:
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        chatbot_message, chatbot_message_source = _post_initial_board_generation_message(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=resource_summary_for_turn,
            edit_outcome=edit_outcome,
        )
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
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "interaction_mode": request.interaction_mode,
            "resource_reference_action": request.resource_reference_action,
            "board_generation_action": "resource_reference_confirm",
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
            **_task_metadata(
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_cleared=requirement_cleared,
            ),
            **_reference_metadata(resolution=resource_resolution),
        },
    )
    consumed_stamp = (
        requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        if frozen_requirement is not None
        else None
    )
    if requirement_cleared:
        _clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
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
        requirement_stamp=consumed_stamp,
        board_document_operation_status=edit_outcome.operation_status,
        board_document_operation_failure_reason=edit_outcome.failure_reason,
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
    requirement_history = _new_requirement_history_recorder(user_id=user_id, lesson_id=lesson.id)
    board_task_history = _new_board_task_history_recorder(user_id=user_id, lesson_id=lesson.id)
    track_initial_requirement_run = _should_track_initial_requirement_run(lesson)
    visible_package = workspace_state.package_context_for_lesson(workspace, package, lesson.id)
    selection_excerpt = _selection_excerpt(request.selection, selection_text)
    action_type = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=is_document_empty(lesson.board_document),
    )
    action_type = _prefer_requirement_action(
        action_type,
        requirements.action_type,
        request_message=request.message,
        requirements=requirements,
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
            and action_type not in DOCUMENT_WRITE_ACTIONS
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
        selection_text=selection_text,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
    )
    if interaction_response is not None:
        return interaction_response

    if resource_resolution.selected_reference is None and resource_resolution.reference_prompt is None:
        board_task_response = _handle_existing_board_task_flow(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            resources=visible_package.resources,
            selection_excerpt=selection_or_reference_excerpt,
            selection_text=selection_text,
            requirement_history=requirement_history,
            board_task_history=board_task_history,
        )
        if board_task_response is not None:
            return board_task_response

    if request.board_generation_action == "start":
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        requirements = _with_task_details(
            requirements,
            action_type="generate_board",
            instruction=request.message,
        )
        requirements, learning_clarification, frozen_requirement = _prepare_initial_requirement_for_board_generation(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
        )
        _checkpoint_initial_requirement_before_generation(
            user_id=user_id,
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirement_history=requirement_history,
            requirements=requirements,
            learning_clarification=learning_clarification,
            stamp=frozen_requirement,
        )
        edit_outcome = generate_from_requirements(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
            frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
        )
        chatbot_message = edit_outcome.chatbot_message
        if not edit_outcome.changed:
            failed_stamp = (
                requirement_history.generation_failed(
                    reason=edit_outcome.summary or chatbot_message,
                    metadata=_board_document_failure_metadata(edit_outcome),
                )
                if frozen_requirement is not None
                else None
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                requirement_stamp=failed_stamp,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )
        if edit_outcome.changed:
            refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
            lesson.board_teaching_guide = build_board_teaching_guide(lesson)
            lesson.board_teaching_progress = None
            chatbot_message, chatbot_message_source = _post_initial_board_generation_message(
                lesson=lesson,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                edit_outcome=edit_outcome,
            )
        requirement_cleared = edit_outcome.changed
        metadata = {
            "kind": "board_document_generation",
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": chatbot_message_source,
            "board_editor_message": edit_outcome.chatbot_message,
            "board_generation_action": request.board_generation_action,
            "board_edit_operation": edit_outcome.operation,
            "board_edit_summary": edit_outcome.summary,
            "board_section_titles": edit_outcome.section_titles,
            **_requirement_history_metadata(
                frozen_requirement,
                run_status_after_commit="consumed" if frozen_requirement is not None else None,
            ),
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
        consumed_stamp = (
            requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
            if frozen_requirement is not None
            else None
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=edit_outcome.board_decision,
            requirement_cleared=requirement_cleared,
            requirement_stamp=consumed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
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
                "assistant_message_source": teaching_result.assistant_message_source,
                "interaction_mode": request.interaction_mode,
                "teaching_action": request.teaching_action,
                "teaching_progress": teaching_result.progress_view.model_dump(mode="json"),
                "board_explanation_directive": teaching_result.board_explanation_directive,
                "learning_clarification": learning_clarification.model_dump(mode="json"),
            },
        )
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=teaching_result.chatbot_message,
            requirements=requirements,
            learning_clarification=learning_clarification,
            board_decision=BoardDecision(action="no_change", reason="本轮是分节讲解，不修改板书。"),
            teaching_progress=teaching_result.progress_view,
            requirement_history=requirement_history if track_initial_requirement_run else None,
        )

    if request.interaction_mode == "direct_edit" and action_type != "append_section":
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
        _maybe_record_initial_requirement_update(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
        )
        action_type = _prefer_requirement_action(
            action_type,
            requirements.action_type,
            request_message=request.message,
            requirements=requirements,
        ) or "rewrite_target"
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
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
                requirement_history=requirement_history if track_initial_requirement_run else None,
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
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
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
            requirement_history=requirement_history if track_initial_requirement_run else None,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )

    if action_type in {*DOCUMENT_WRITE_ACTIONS, "explain_target"} and not is_document_empty(lesson.board_document):
        if _should_preserve_requirement_update_for_action(request):
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
            _maybe_record_initial_requirement_update(
                requirement_history,
                enabled=track_initial_requirement_run,
                requirements=requirements,
                learning_clarification=learning_clarification,
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
                action_type=action_type,
                requirement_history=requirement_history,
            )
            if interaction_start_response is not None:
                return interaction_start_response
            action_type = _prefer_requirement_action(
                action_type,
                requirements.action_type,
                request_message=request.message,
                requirements=requirements,
            )
        else:
            learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)

        if action_type == "append_section":
            requirements = _with_task_details(
                requirements,
                action_type=action_type,
                instruction=request.message,
            )
            edit_outcome = edit_existing_document(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=_resource_summary(visible_package.resources),
                conversation_summary=_conversation_summary(request.conversation),
                user_instruction=request.message,
                selection_excerpt=None,
                focus=None,
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
                message="Appended new board content at the end of the current document",
                new_document=lesson.board_document,
                metadata={
                    "kind": "board_document_edit",
                    "user_message": request.message,
                    "assistant_message": edit_outcome.chatbot_message,
                    "assistant_message_source": edit_outcome.assistant_message_source,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "selection_text": None,
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=requirement_cleared,
                    ),
                },
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=edit_outcome.board_decision,
                requirement_cleared=requirement_cleared,
                requirement_history=requirement_history if track_initial_requirement_run else None,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )

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
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="await_focus_choice", reason=resolution.question),
                focus_candidates=resolution.candidates,
                requirement_history=requirement_history if track_initial_requirement_run else None,
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
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
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
                requirement_history=requirement_history if track_initial_requirement_run else None,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )

        focus_excerpt = focus_context(resolution.focus) if resolution.focus else ""
        chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=requirements,
            resources=visible_package.resources,
            conversation=request.conversation,
            request=request,
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=focus_excerpt,
        )

        requirement_cleared = bool(chatbot_message)
        if not chatbot_message:
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message="",
                requirements=requirements,
                learning_clarification=learning_clarification,
                board_decision=BoardDecision(action="no_change", reason="Board-directed explanation failed because Chatbot returned empty."),
                resolved_focus=resolution.focus,
                focus_candidates=resolution.candidates,
                requirement_cleared=False,
                requirement_history=requirement_history if track_initial_requirement_run else None,
            )
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
                "board_explanation_directive": board_explanation_directive,
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
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
        _maybe_record_initial_requirement_update(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
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
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
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
                requirement_history=requirement_history if track_initial_requirement_run else None,
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
                requirement_history=requirement_history,
                track_initial_requirement_run=track_initial_requirement_run,
            )
        if _should_generate_board_from_explicit_request(
            lesson=lesson,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
        ):
            requirements = _with_task_details(
                requirements,
                action_type="generate_board",
                instruction=request.message,
            )
            requirements, learning_clarification, frozen_requirement = _prepare_initial_requirement_for_board_generation(
                requirement_history,
                enabled=track_initial_requirement_run,
                requirements=requirements,
                learning_clarification=learning_clarification,
            )
            _checkpoint_initial_requirement_before_generation(
                user_id=user_id,
                workspace=workspace,
                package=package,
                lesson=lesson,
                requirement_history=requirement_history,
                requirements=requirements,
                learning_clarification=learning_clarification,
                stamp=frozen_requirement,
            )
            edit_outcome = generate_from_requirements(
                lesson=lesson,
                requirements=requirements,
                clarification=learning_clarification,
                resource_summary=resource_summary_for_turn,
                requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
                frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
            )
            if not edit_outcome.changed:
                failed_stamp = (
                    requirement_history.generation_failed(
                        reason=edit_outcome.summary or edit_outcome.chatbot_message,
                        metadata=_board_document_failure_metadata(edit_outcome),
                    )
                    if frozen_requirement is not None
                    else None
                )
                workspace_state.normalize_package_state(package)
                _save_workspace_for_user(
                    user_id=user_id,
                    workspace=workspace,
                    requirement_history=requirement_history,
                )
                return _response(
                    workspace=workspace,
                    package=package,
                    lesson=lesson,
                    chatbot_message=edit_outcome.chatbot_message,
                    learning_clarification=learning_clarification,
                    requirements=requirements,
                    board_decision=edit_outcome.board_decision,
                    resource_matches=resource_resolution.matches,
                    selected_reference=selected_reference,
                    requirement_stamp=failed_stamp,
                    board_document_operation_status=edit_outcome.operation_status,
                    board_document_operation_failure_reason=edit_outcome.failure_reason,
                )
            if edit_outcome.changed:
                refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
                lesson.board_teaching_guide = build_board_teaching_guide(lesson)
                lesson.board_teaching_progress = None
                chatbot_message, chatbot_message_source = _post_initial_board_generation_message(
                    lesson=lesson,
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    resource_summary=resource_summary_for_turn,
                    edit_outcome=edit_outcome,
                )
            requirement_cleared = edit_outcome.changed
            commit_operations(
                lesson,
                [],
                label="Board document generation",
                message="Generated board document from an explicit learner request",
                new_document=lesson.board_document,
                metadata={
                    "kind": "board_document_generation",
                    "user_message": request.message,
                    "assistant_message": chatbot_message,
                    "assistant_message_source": chatbot_message_source,
                    "board_editor_message": edit_outcome.chatbot_message,
                    "interaction_mode": request.interaction_mode,
                    "selection": request.selection.model_dump(mode="json") if request.selection else None,
                    "board_generation_action": "explicit_board_request",
                    "board_edit_operation": edit_outcome.operation,
                    "board_edit_summary": edit_outcome.summary,
                    "board_section_titles": edit_outcome.section_titles,
                    **_requirement_history_metadata(
                        frozen_requirement,
                        run_status_after_commit="consumed" if frozen_requirement is not None else None,
                    ),
                    **_task_metadata(
                        requirements=requirements,
                        learning_clarification=learning_clarification,
                        requirement_cleared=requirement_cleared,
                    ),
                    **_reference_metadata(resolution=resource_resolution),
                },
            )
            consumed_stamp = (
                requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
                if frozen_requirement is not None
                else None
            )
            if requirement_cleared:
                _clear_task_requirements(lesson)
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=chatbot_message,
                learning_clarification=learning_clarification,
                requirements=requirements,
                board_decision=edit_outcome.board_decision,
                resource_matches=resource_resolution.matches,
                selected_reference=selected_reference,
                requirement_cleared=requirement_cleared,
                requirement_stamp=consumed_stamp,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )
        lesson.learning_requirements = requirements
        chatbot_user_message = (
            requirement_probe_instead_of_explanation_message(request.message)
            if _requests_explanation(request.message)
            else request.message
        )
        ai_reply = openai_course_ai.generate_chatbot_reply(
            lesson_title=lesson.title,
            learning_goal=learning_clarification.summary or requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
            user_message=chatbot_user_message,
            selection_excerpt=_chatbot_visible_selection_excerpt(request, selection_or_reference_excerpt),
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
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
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
            requirement_history=requirement_history if track_initial_requirement_run else None,
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
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
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
            requirement_history=requirement_history if track_initial_requirement_run else None,
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
        _maybe_record_initial_requirement_update(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
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
            requirement_history=requirement_history,
            track_initial_requirement_run=track_initial_requirement_run,
        )

    learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
    if _requests_explanation(request.message) and not is_document_empty(lesson.board_document):
        target_excerpt = selection_or_reference_excerpt or _board_summary(lesson)
        requirements = _with_task_details(
            requirements,
            action_type="explain_target",
            instruction=request.message,
        )
        chatbot_message, chatbot_message_source, board_explanation_directive = _generate_board_directed_explanation_message(
            lesson=lesson,
            requirements=requirements,
            resources=visible_package.resources,
            conversation=request.conversation,
            request=request,
            learning_clarification=learning_clarification,
            action_type="explain_target",
            target_excerpt=target_excerpt,
        )
        requirement_cleared = bool(chatbot_message)
        commit_operations(
            lesson,
            [],
            label="Board explanation",
            message="Answered only after receiving a board-side explanation directive",
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
                "board_explanation_directive": board_explanation_directive,
                **_reference_metadata(resolution=resource_resolution),
            },
        )
        if requirement_cleared:
            _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=chatbot_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=BoardDecision(action="no_change", reason="本轮是板书指令授权后的讲解，不修改板书。"),
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            requirement_cleared=requirement_cleared,
            requirement_history=requirement_history if track_initial_requirement_run else None,
        )

    free_chat_user_message = (
        requirement_probe_instead_of_explanation_message(request.message)
        if _requests_explanation(request.message)
        else request.message
    )
    if _requests_explanation(request.message):
        solver_user_message, solver_metadata = free_chat_user_message, {}
    else:
        solver_user_message, solver_metadata = _chatbot_message_with_solver_context(
            lesson=lesson,
            request=request,
            user_message=free_chat_user_message,
            target_excerpt=_chatbot_visible_selection_excerpt(request, selection_or_reference_excerpt),
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
        selection_excerpt=_chatbot_visible_selection_excerpt(request, selection_or_reference_excerpt),
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
    _maybe_record_initial_requirement_update(
        requirement_history,
        enabled=track_initial_requirement_run,
        requirements=requirements,
        learning_clarification=learning_clarification,
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
        action_type=action_type,
        requirement_history=requirement_history,
    )
    if interaction_start_response is not None:
        return interaction_start_response

    if (
        track_initial_requirement_run
        and learning_clarification.ready_for_board
        and requirements.action_type == "generate_board"
    ):
        requirements = _with_task_details(
            requirements,
            action_type="generate_board",
            instruction=requirements.action_instruction or request.message,
        )
        requirements, learning_clarification, frozen_requirement = _prepare_initial_requirement_for_board_generation(
            requirement_history,
            enabled=track_initial_requirement_run,
            requirements=requirements,
            learning_clarification=learning_clarification,
        )
        _checkpoint_initial_requirement_before_generation(
            user_id=user_id,
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirement_history=requirement_history,
            requirements=requirements,
            learning_clarification=learning_clarification,
            stamp=frozen_requirement,
        )
        edit_outcome = generate_from_requirements(
            lesson=lesson,
            requirements=requirements,
            clarification=learning_clarification,
            resource_summary=resource_summary_for_turn,
            requirement_run_id=frozen_requirement.run_id if frozen_requirement else None,
            frozen_requirement_version_id=frozen_requirement.version_id if frozen_requirement else None,
        )
        if not edit_outcome.changed:
            failed_stamp = requirement_history.generation_failed(
                reason=edit_outcome.summary or edit_outcome.chatbot_message,
                metadata=_board_document_failure_metadata(edit_outcome),
            )
            workspace_state.normalize_package_state(package)
            _save_workspace_for_user(
                user_id=user_id,
                workspace=workspace,
                requirement_history=requirement_history,
            )
            return _response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=edit_outcome.chatbot_message or chatbot_message,
                learning_clarification=learning_clarification,
                requirements=requirements,
                board_decision=edit_outcome.board_decision,
                resource_matches=resource_resolution.matches,
                selected_reference=selected_reference,
                requirement_stamp=failed_stamp,
                board_document_operation_status=edit_outcome.operation_status,
                board_document_operation_failure_reason=edit_outcome.failure_reason,
            )
        refresh_lesson_runtime(lesson, document=edit_outcome.new_document, requirements=requirements)
        lesson.board_teaching_guide = build_board_teaching_guide(lesson)
        lesson.board_teaching_progress = None
        post_generation_message, post_generation_source = _post_initial_board_generation_message(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=resource_summary_for_turn,
            edit_outcome=edit_outcome,
        )
        commit_operations(
            lesson,
            [],
            label="Board document generation",
            message="Generated board document from a frozen learning requirement sheet",
            new_document=lesson.board_document,
            metadata={
                "kind": "board_document_generation",
                "user_message": request.message,
                "assistant_message": post_generation_message,
                "assistant_message_source": post_generation_source,
                "chatbot_requirement_reply": chatbot_message,
                "board_editor_message": edit_outcome.chatbot_message,
                "interaction_mode": request.interaction_mode,
                "selection": request.selection.model_dump(mode="json") if request.selection else None,
                "board_generation_action": "ready_requirement_sheet",
                "board_edit_operation": edit_outcome.operation,
                "board_edit_summary": edit_outcome.summary,
                "board_section_titles": edit_outcome.section_titles,
                **_requirement_history_metadata(
                    frozen_requirement,
                    run_status_after_commit="consumed" if frozen_requirement is not None else None,
                ),
                **_task_metadata(
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    requirement_cleared=True,
                ),
                **_reference_metadata(resolution=resource_resolution),
                **solver_metadata,
            },
        )
        consumed_stamp = requirement_history.consume(commit_id=lesson.history_graph.commits[-1].id)
        _clear_task_requirements(lesson)
        workspace_state.normalize_package_state(package)
        _save_workspace_for_user(
            user_id=user_id,
            workspace=workspace,
            requirement_history=requirement_history,
        )
        return _response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=post_generation_message,
            learning_clarification=learning_clarification,
            requirements=requirements,
            board_decision=edit_outcome.board_decision,
            resource_matches=resource_resolution.matches,
            selected_reference=selected_reference,
            requirement_cleared=True,
            requirement_stamp=consumed_stamp,
            board_document_operation_status=edit_outcome.operation_status,
            board_document_operation_failure_reason=edit_outcome.failure_reason,
        )

    board_decision = BoardDecision(action="no_change", reason="本轮是通用问答聊天，不自动修改讲义。")
    requirement_cleared = False

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
            **solver_metadata,
        },
    )
    if requirement_cleared:
        _clear_task_requirements(lesson)
    workspace_state.normalize_package_state(package)
    _save_workspace_for_user(
        user_id=user_id,
        workspace=workspace,
        requirement_history=requirement_history,
    )
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
        requirement_history=requirement_history if track_initial_requirement_run else None,
    )


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        trace_prefix="chat",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        with bind_text_model_selection(request.text_model):
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
