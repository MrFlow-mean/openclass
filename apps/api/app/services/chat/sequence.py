from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.models import (
    BoardDecision,
    BoardFocusRef,
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
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.intent import _compact_text, _requests_explanation
from app.services.chat.metadata import _board_task_metadata, _task_metadata
from app.services.chat.response import _response
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_context_payload, interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.segment_resolver import FocusResolution, focus_context


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
