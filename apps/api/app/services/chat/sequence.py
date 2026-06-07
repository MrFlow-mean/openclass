from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardSegment,
    BoardTaskRequirementSheet,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    InteractionSession,
    InteractionTurnDecision,
    LearningClarificationStatus,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services import workspace_state
from app.services.board_segment_index import build_board_segment_index
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.intent import _compact_text, _requests_explanation
from app.services.chat.metadata import _board_task_metadata, _task_metadata
from app.services.chat.response import _response
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE, build_atomic_explanation_sequence
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_context_payload, interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.segment_resolver import FocusResolution, focus_context


SEQUENTIAL_EXPLANATION_REQUEST_PATTERN = re.compile(
    r"(都讲|全都讲|全部讲|都解释|全部解释|逐个|一个个|挨个|依次|按顺序|从头到尾|"
    r"(?:讲解|解释|讲|说明).{0,12}(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个)|"
    r"(?:所有|全部|每个|每一(?:个|道|题|节|小节|部分|段)?|每道|每题|各个).{0,12}(?:都)?(?:讲|讲解|解释|说明))"
)
COLLECTION_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目|"
    r"exercise|exercises|question|questions|problem|problems|quiz|quizzes|task|tasks)",
    re.IGNORECASE,
)
SINGLE_EXPLANATION_TARGET_PATTERN = re.compile(
    r"(第\s*[0-9０-９一二三四五六七八九十两]+.{0,8}(?:章|节|小节|部分|段|句|行|题|项|条|步)|"
    r"(?:练习|习题|题目|小题|题项|问题|问答|测验|例题|示例题|步骤|条目|项目)"
    r"\s*[0-9０-９一二三四五六七八九十两]+|"
    r"倒数|选中|这里|这(?:一|个)?(?:段|句|行|题|项|条|步|部分)|某(?:段|句|行|题|项|条|步))",
    re.IGNORECASE,
)
OVERVIEW_EXPLANATION_REQUEST_PATTERN = re.compile(r"(概括|总结|总览|整体把握|大意|框架|梳理(?:框架|结构)?)")
SEQUENCE_CONTINUE_PATTERN = re.compile(
    r"^(可以|可以的|没问题|没有问题|没啥问题|没有啥问题|好|好的|继续|继续讲|下一节|下一个|明白了|懂了|可以接受)$"
)
SEQUENCE_EXIT_PATTERN = re.compile(r"(不用继续|先停|停止|结束|退出|不讲了|够了)")


@dataclass(frozen=True)
class SequenceRuntime:
    board_summary: Callable[[Lesson], str]
    resource_summary: Callable[[list[ResourceLibraryItem]], str]
    conversation_summary: Callable[[list[ConversationTurn]], str]
    generate_board_directed_explanation_message: Callable[..., tuple[str, str, dict[str, object] | None]]
    requirements_from_board_task: Callable[..., LearningRequirementSheet]
    board_search_evidence_metadata: Callable[[FocusResolution | None], dict[str, object]]
    clear_task_requirements: Callable[[Lesson], None]
    save_workspace_for_user: Callable[..., None]


def _decision_focus(decision: BoardTaskRouteDecision, resolution: FocusResolution | None) -> BoardFocusRef | None:
    return decision.target_focus or (resolution.focus if resolution else None)


def _requests_sequential_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and SEQUENTIAL_EXPLANATION_REQUEST_PATTERN.search(compact))


def _requests_collection_explanation_sequence(
    *,
    board_task: BoardTaskRequirementSheet,
    request_message: str,
) -> bool:
    if board_task.requested_action != "explain":
        return False
    if _requests_sequential_explanation(request_message):
        return True
    request_compact = _compact_text(request_message, limit=160)
    sheet_compact = _compact_text(
        " ".join(part for part in [board_task.target_hint, board_task.question_or_topic] if part),
        limit=240,
    )
    combined = _compact_text(" ".join(part for part in [request_compact, sheet_compact] if part), limit=360)
    if not combined or not COLLECTION_EXPLANATION_TARGET_PATTERN.search(combined):
        return False
    if SINGLE_EXPLANATION_TARGET_PATTERN.search(combined):
        return False
    return True


def _requests_overview_explanation(text: str) -> bool:
    compact = _compact_text(text, limit=120)
    return bool(compact and OVERVIEW_EXPLANATION_REQUEST_PATTERN.search(compact))


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
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    request_message: str,
) -> BoardTaskRouteDecision:
    if board_task.requested_action != "explain":
        return decision
    if decision.route != "clarify_location" or decision.location_status != "ambiguous":
        return decision
    if not _requests_collection_explanation_sequence(board_task=board_task, request_message=request_message):
        return decision
    candidates = _ordered_explanation_candidates(decision=decision, resolution=resolution)
    if not candidates:
        return decision
    segments = build_board_segment_index(lesson.board_document).segments
    scope_heading = _scope_heading_for_explanation_sequence(
        segments=segments,
        focus=None,
        candidates=candidates,
        explicit_sequence=True,
    )
    if scope_heading is None:
        return decision
    return BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=candidates[0],
        candidate_focuses=candidates,
        reason=(
            "用户请求讲解同一父级下的集合型内容；"
            "本轮按最小可讲单元从第一个目标开始讲解，不再反复要求用户选择位置。"
        ),
        write_proposal=decision.write_proposal,
    )


def _path_starts_with(path: list[str], prefix: list[str]) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


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
    candidates = _dedupe_focuses(candidates)
    for candidate in candidates:
        if candidate.kind != "heading" or not candidate.heading_path:
            continue
        if all(_path_starts_with(other.heading_path, candidate.heading_path) for other in candidates if other.heading_path):
            heading = _find_heading_segment_by_path(segments, candidate.heading_path)
            if heading and _direct_child_section_headings(segments, heading):
                return heading

    if len(candidates) == 1:
        candidate_path = candidates[0].heading_path
        while candidate_path:
            heading = _find_heading_segment_by_path(segments, candidate_path)
            if heading and _direct_child_section_headings(segments, heading):
                return heading
            candidate_path = candidate_path[:-1]
        return None

    direct_parent_paths: list[list[str]] = []
    for candidate in candidates:
        if not candidate.heading_path:
            return None
        direct_parent_path = candidate.heading_path[:-1]
        if not direct_parent_path:
            return None
        direct_parent_paths.append(direct_parent_path)
    if direct_parent_paths and all(path == direct_parent_paths[0] for path in direct_parent_paths):
        heading = _find_heading_segment_by_path(segments, direct_parent_paths[0])
        if heading and _direct_child_section_headings(segments, heading):
            return heading
    return None


def _shared_heading_for_atomic_sequence(
    *,
    segments: list[BoardSegment],
    candidates: list[BoardFocusRef],
) -> BoardSegment | None:
    candidates = _dedupe_focuses(candidates)
    heading_paths = [candidate.heading_path for candidate in candidates if candidate.heading_path]
    if not heading_paths:
        return None
    first_path = heading_paths[0]
    if not all(path == first_path for path in heading_paths):
        return None
    return _find_heading_segment_by_path(segments, first_path)


def _scope_heading_for_explanation_sequence(
    *,
    segments: list[BoardSegment],
    focus: BoardFocusRef | None,
    candidates: list[BoardFocusRef],
    explicit_sequence: bool,
) -> BoardSegment | None:
    if explicit_sequence:
        shared_heading = _shared_heading_for_atomic_sequence(segments=segments, candidates=candidates)
        if shared_heading is not None:
            return shared_heading
        return _parent_heading_for_section_sequence(segments=segments, candidates=candidates)
    if focus is None or focus.kind != "heading" or not focus.heading_path:
        return None
    return _find_heading_segment_by_path(segments, focus.heading_path)


def _is_current_sequence_followup(text: str) -> bool:
    compact = _compact_text(text, limit=160)
    if not compact:
        return False
    if SEQUENCE_CONTINUE_PATTERN.search(compact) or SEQUENCE_EXIT_PATTERN.search(compact):
        return False
    if re.search(r"(第\s*[0-9０-９一二三四五六七八九十两]+.{0,8}[章节部分段]|下一节|下一个)", compact):
        return False
    if _requests_explanation(compact):
        return True
    return bool(re.search(r"(这个|这里|这段|刚才|上面|为什么|怎么|如何|哪里|哪儿|吗|呢|？|\?)", compact))


def _section_explanation_sequence(
    *,
    lesson: Lesson,
    board_task: BoardTaskRequirementSheet,
    decision: BoardTaskRouteDecision,
    resolution: FocusResolution | None,
    request_message: str,
) -> list[BoardFocusRef]:
    if board_task.requested_action != "explain":
        return []
    if _requests_overview_explanation(request_message):
        return []
    focus = _decision_focus(decision, resolution)
    segments = build_board_segment_index(lesson.board_document).segments
    explicit_sequence = _requests_collection_explanation_sequence(
        board_task=board_task,
        request_message=request_message,
    )
    if explicit_sequence:
        candidates = decision.candidate_focuses or (resolution.candidates if resolution else [])
        if focus is not None:
            candidates = [focus, *candidates]
        candidates = _dedupe_focuses(candidates)
        if not candidates:
            return []
    else:
        candidates = [focus] if focus is not None else []
    scope_heading = _scope_heading_for_explanation_sequence(
        segments=segments,
        focus=focus,
        candidates=candidates,
        explicit_sequence=explicit_sequence,
    )
    if scope_heading is None:
        return []
    atomic_items = build_atomic_explanation_sequence(
        lesson=lesson,
        segments=segments,
        scope_heading=scope_heading,
    )
    if len(atomic_items) < 2:
        return []
    return atomic_items


def _section_sequence_instruction(
    *,
    request_message: str,
    focus: BoardFocusRef,
    index: int,
    total: int,
    sequence_mode: str = ATOMIC_EXPLANATION_SEQUENCE_MODE,
) -> str:
    unit_label = _sequence_unit_label(sequence_mode)
    next_note = (
        f"讲完后请询问学习者是否可以继续下一个{unit_label}。"
        if index + 1 < total
        else "讲完后请确认学习者是否还有问题；如果没有问题，本组顺序讲解可以结束。"
    )
    atom_instruction = (
        "如果当前目标是题目、练习或带参考答案的内容，必须讲题目要求、关键线索、"
        "推理步骤、答案如何得到和易错点；不能只翻译、复述或直接报答案。"
        if sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
        else ""
    )
    return (
        f"{request_message}\n"
        f"系统顺序讲解要求：本轮只讲第 {index + 1}/{total} 个{unit_label}："
        f"{focus.display_label or ' / '.join(focus.heading_path)}。"
        f"{next_note}不要越界讲解其它{unit_label}。{atom_instruction}"
    )


def _sequence_unit_label(sequence_mode: str) -> str:
    if sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE:
        return "讲解单元"
    return "子节"


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
    runtime: SequenceRuntime,
) -> ChatResponse:
    first_focus = sequence_items[0]
    session_before = lesson.active_interaction_session
    sequence_mode = ATOMIC_EXPLANATION_SEQUENCE_MODE
    unit_label = _sequence_unit_label(sequence_mode)
    session_after = InteractionSession(
        status="active",
        rule_text="按板书内容的最小可讲单元顺序逐个讲解。",
        interaction_goal=(
            f"按最小内容单元讲解 {first_focus.heading_path[-1]}"
            if first_focus.heading_path
            else board_task.question_or_topic or board_task.target_hint
        ),
        target_focus=first_focus,
        reference_context=focus_context(first_focus),
        compliant_input_rule=f"用户确认理解、提出当前{unit_label}问题，或要求继续下一个{unit_label}。",
        expected_user_behavior=f"用户确认当前{unit_label}是否可以接受；没有问题时继续下一个{unit_label}。",
        assistant_behavior=f"每轮只讲当前{unit_label}，结尾询问是否继续下一个{unit_label}。",
        progress_note=f"准备讲解第 1/{len(sequence_items)} 个{unit_label}。",
        turn_count=0,
        source_board_task_run_id=board_task_stamp.run_id,
        source_board_task_version_id=board_task_stamp.version_id,
        source_board_task_route="explain",
        sequence_items=sequence_items,
        sequence_index=0,
        sequence_mode=sequence_mode,
    )
    lesson.active_interaction_session = session_after
    task_requirements = runtime.requirements_from_board_task(
        base=requirements,
        board_task=board_task,
        action_type="explain_target",
        focus=first_focus,
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = runtime.generate_board_directed_explanation_message(
        lesson=lesson,
        requirements=task_requirements,
        resources=resources,
        conversation=request.conversation,
        request=request.model_copy(
            update={
                "message": _section_sequence_instruction(
                    request_message=request.message,
                    focus=first_focus,
                    index=0,
                    total=len(sequence_items),
                    sequence_mode=sequence_mode,
                )
            }
        ),
        learning_clarification=learning_clarification,
        action_type="explain_target",
        target_excerpt=focus_context(first_focus),
        interaction_context=interaction_context_payload(session=session_after),
    )
    lesson.board_task_requirements = None
    runtime.clear_task_requirements(lesson)
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
            **runtime.board_search_evidence_metadata(resolution),
            "section_explanation_sequence": [item.model_dump(mode="json") for item in sequence_items],
            "explanation_sequence": [item.model_dump(mode="json") for item in sequence_items],
            "explanation_sequence_mode": sequence_mode,
            **_task_metadata(
                requirements=task_requirements,
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
    runtime.save_workspace_for_user(
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
        completed_board_task_sheet=board_task,
    )


def _is_section_explanation_session(session: InteractionSession) -> bool:
    return session.sequence_mode in {"section_explanation", ATOMIC_EXPLANATION_SEQUENCE_MODE} and bool(session.sequence_items)


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
    runtime: SequenceRuntime,
) -> tuple[str, str]:
    unit_label = _sequence_unit_label(session.sequence_mode)
    ai_reply = openai_course_ai.generate_chatbot_reply(
        lesson_title=lesson.title,
        learning_goal=session.interaction_goal or requirements.learning_goal,
        board_summary=runtime.board_summary(lesson),
        resource_summary=runtime.resource_summary(resources),
        conversation_summary=runtime.conversation_summary(conversation),
        user_message=(
            f"用户已经确认顺序讲解的最后一个{unit_label}没有问题。"
            "请自然结束本组顺序讲解，并询问是否还要回顾、练习或进入新的任务。"
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
    runtime: SequenceRuntime,
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
            runtime=runtime,
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
        runtime.save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
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
        if not _is_current_sequence_followup(request.message):
            return None
        focus = session_before.target_focus or session_before.sequence_items[session_before.sequence_index]
        session_after = session_before.model_copy(
            update={
                "target_focus": focus,
                "reference_context": focus_context(focus),
                "turn_count": session_before.turn_count + 1,
                "status": "active",
                "pause_reason": "",
            }
        )
        lesson.active_interaction_session = session_after
        unit_label = _sequence_unit_label(session_after.sequence_mode)
        sequence_request = request.model_copy(
            update={
                "message": (
                    f"{request.message}\n"
                    f"系统顺序讲解要求：用户正在追问当前第 "
                    f"{session_after.sequence_index + 1}/{len(session_after.sequence_items)} 个{unit_label}："
                    f"{focus.display_label or ' / '.join(focus.heading_path)}。"
                    f"请只围绕当前{unit_label}补充解释，不要推进到下一个{unit_label}。"
                    f"结尾询问当前{unit_label}是否还有问题，或是否继续下一个{unit_label}。"
                )
            }
        )
        chatbot_message, chatbot_message_source, board_explanation_directive = runtime.generate_board_directed_explanation_message(
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
            reason=f"用户追问当前{unit_label}，继续围绕当前{unit_label}讲解。",
            progress_note=session_after.progress_note,
            user_intent=f"追问当前{unit_label}",
        )
        commit_operations(
            lesson,
            [],
            label="Section explanation follow-up",
            message="Answered a follow-up within the current sequential section",
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
        runtime.save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
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

    next_index = session_before.sequence_index + 1
    if next_index >= len(session_before.sequence_items):
        unit_label = _sequence_unit_label(session_before.sequence_mode)
        lesson.active_interaction_session = None
        chatbot_message, chatbot_message_source = _generate_sequence_end_message(
            lesson=lesson,
            requirements=requirements,
            resources=resources,
            conversation=request.conversation,
            request=request,
            session=session_before,
            runtime=runtime,
        )
        decision = InteractionTurnDecision(
            route="exit_rule",
            reason="顺序讲解已经完成。",
            progress_note="顺序讲解已经完成。",
            user_intent=f"确认最后一个{unit_label}无问题",
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
        runtime.save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
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
    unit_label = _sequence_unit_label(session_before.sequence_mode)
    session_after = session_before.model_copy(
        update={
            "target_focus": focus,
            "reference_context": focus_context(focus),
            "sequence_index": next_index,
            "progress_note": f"准备讲解第 {next_index + 1}/{len(session_before.sequence_items)} 个{unit_label}。",
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
                sequence_mode=session_after.sequence_mode,
            )
        }
    )
    chatbot_message, chatbot_message_source, board_explanation_directive = runtime.generate_board_directed_explanation_message(
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
        reason=f"用户确认当前{unit_label}后继续下一个{unit_label}。",
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
    runtime.save_workspace_for_user(user_id=user_id, workspace=workspace, requirement_history=requirement_history)
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
