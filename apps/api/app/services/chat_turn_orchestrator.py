from __future__ import annotations

import json
import re

from app.models import (
    BoardDecision,
    BoardFocusRef,
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
)
from app.services import workspace_state
from app.services.board_document_editor import edit_existing_document
from app.services.board_explanation_gate import (
    requirement_probe_instead_of_explanation_message,
)
from app.services.board_task_history import BoardTaskHistoryRecorder, BoardTaskHistoryStamp
from app.services.chat.board_focus_history import (
    implicit_board_search_evidence as _implicit_board_search_evidence,
    maybe_inherit_recent_board_edit_focus as _maybe_inherit_recent_board_edit_focus,
    recent_board_edit_focus_for_commit as _recent_board_edit_focus_for_commit,
)
from app.services.chat.intent import (
    DOCUMENT_WRITE_ACTIONS,
    EDIT_ACTIONS,
    _infer_board_task_action,
    _prefer_requirement_action,
    _requests_append_section,
    _requests_document_artifact_generation,
    _requests_explanation,
    _requests_learning_start,
    _requests_resource_backed_answer,
    _should_force_explain_task,
    _should_prompt_resource_reference,
)
from app.services.chat.context import (
    board_summary as _board_summary,
    chatbot_visible_selection_excerpt as _chatbot_visible_selection_excerpt,
    compact_text as _compact_text,
    conversation_summary as _conversation_summary,
    merge_selection_and_reference as _merge_selection_and_reference,
    resource_summary as _resource_summary,
    resource_summary_with_reference as _resource_summary_with_reference,
    selection_excerpt as _selection_excerpt,
)
from app.services.chatbot import (
    generate_board_directed_role_reply,
    generate_board_task_clarification_reply,
    generate_chatbot_role_reply,
    generate_focus_clarification_reply,
    generate_post_initial_board_generation_reply,
)
from app.services.chat.metadata import (
    _focus_metadata,
    _reference_metadata,
    _learning_requirement_metadata,
)
from app.services.chat.strong_reasoning import chatbot_message_with_solver_context as _chatbot_message_with_solver_context
from app.services.chat.handlers.board_task import BoardTaskRouteRuntime
from app.services.chat.handlers.edit_blackboard import EditBlackboardRuntime
from app.services.chat.handlers.explain import ExplainHandlerRuntime
from app.services.chat.handlers.existing_board_task import (
    ExistingBoardTaskRuntime,
    handle_existing_board_task_flow,
)
from app.services.chat.handlers.general_chat import GeneralChatRuntime, commit_general_chat_turn
from app.services.chat.handlers.initial_board import InitialBoardRuntime, run_initial_board_generation
from app.services.chat.handlers.interaction import (
    InteractionRuntime,
    handle_existing_interaction_session,
    maybe_start_interaction_session,
)
from app.services.chat.response import _board_task_questions, _response
from app.services.chat.resource_reference_flow import (
    matching_pending_resource_board_proposal,
    prompt_for_resource_reference,
    remember_resource_board_proposal,
    request_with_pending_resource_board_action,
    resource_board_proposal_unavailable_response,
    run_confirmed_resource_initial_board_generation,
    should_generate_board_after_reference_confirmation,
    should_store_resource_board_proposal,
    skip_pending_resource_board_proposal,
)
from app.services.chat.recommendations import requirement_recommendation_context
from app.services.chat.sequence import (
    SequenceRuntime,
    _handle_section_explanation_sequence_turn,
    _requests_sequential_explanation,
)
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


INTERACTION_RULE_REQUEST_PATTERN = re.compile(
    r"(规则|互动|轮流|你问我答|按.{0,12}来|角色|扮演|模拟|对话|练习|测验|检查我)"
)
EXISTING_BOARD_GENERATION_CONTROL_PATTERN = re.compile(r"(生成|创建|制作|准备).{0,8}(板书|版书|文档)")


def _should_preserve_requirement_update_for_action(request: ChatRequest) -> bool:
    return bool(INTERACTION_RULE_REQUEST_PATTERN.search(_compact_text(request.message, limit=280)))


def _requests_interaction_rule(text: str) -> bool:
    return bool(INTERACTION_RULE_REQUEST_PATTERN.search(_compact_text(text, limit=280)))


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


def _clear_task_requirements(lesson: Lesson) -> None:
    lesson.learning_requirements = None


def _activate_board_task_requirements(lesson: Lesson, board_task: BoardTaskRequirementSheet) -> None:
    _clear_task_requirements(lesson)
    lesson.board_task_requirements = board_task


def _requests_existing_board_generation_control(text: str) -> bool:
    compact = _compact_text(text, limit=220)
    return bool(compact and EXISTING_BOARD_GENERATION_CONTROL_PATTERN.search(compact))


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
    reply = generate_focus_clarification_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=request.message,
        focus_candidate_context=_focus_candidate_context(resolution),
        interaction_mode=request.interaction_mode,
    )
    return reply.chatbot_message, reply.assistant_message_source


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


def _sequence_runtime() -> SequenceRuntime:
    return SequenceRuntime(
        board_summary=_board_summary,
        resource_summary=_resource_summary,
        conversation_summary=_conversation_summary,
        generate_board_directed_explanation_message=_generate_board_directed_explanation_message,
        requirements_from_board_task=_requirements_from_board_task,
        board_search_evidence_metadata=_board_search_evidence_metadata,
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
    )


def _edit_blackboard_runtime() -> EditBlackboardRuntime:
    return EditBlackboardRuntime(
        resource_summary=_resource_summary,
        conversation_summary=_conversation_summary,
        requirements_from_board_task=_requirements_from_board_task,
        generate_board_directed_explanation_message=_generate_board_directed_explanation_message,
        recent_board_edit_focus_for_commit=_recent_board_edit_focus_for_commit,
        implicit_board_search_evidence=_implicit_board_search_evidence,
        board_search_evidence_metadata=_board_search_evidence_metadata,
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
    )


def _explain_handler_runtime() -> ExplainHandlerRuntime:
    return ExplainHandlerRuntime(
        requirements_from_board_task=_requirements_from_board_task,
        generate_board_directed_explanation_message=_generate_board_directed_explanation_message,
        board_search_evidence_metadata=_board_search_evidence_metadata,
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
    )


def _board_task_route_runtime() -> BoardTaskRouteRuntime:
    return BoardTaskRouteRuntime(
        edit_runtime=_edit_blackboard_runtime(),
        explain_runtime=_explain_handler_runtime(),
        decision_focus=_decision_focus,
        requirements_from_board_task=_requirements_from_board_task,
        board_search_evidence_metadata=_board_search_evidence_metadata,
        maybe_start_interaction_session=_maybe_start_interaction_session,
    )


def _existing_board_task_runtime() -> ExistingBoardTaskRuntime:
    return ExistingBoardTaskRuntime(
        edit_runtime=_edit_blackboard_runtime(),
        sequence_runtime=_sequence_runtime(),
        board_task_route_runtime=_board_task_route_runtime(),
        resource_summary=_resource_summary,
        resolve_board_focus=resolve_board_focus,
        latest_learning_clarification=_latest_learning_clarification,
        requests_existing_board_generation_control=_requests_existing_board_generation_control,
        requests_interaction_rule=_requests_interaction_rule,
        maybe_inherit_recent_board_edit_focus=_maybe_inherit_recent_board_edit_focus,
        activate_board_task_requirements=_activate_board_task_requirements,
        emit_board_task_update=_emit_board_task_update,
        generate_board_task_clarification_message=_generate_board_task_clarification_message,
        generate_focus_candidate_message=_generate_focus_candidate_message,
        requirements_from_board_task=_requirements_from_board_task,
        board_search_evidence_metadata=_board_search_evidence_metadata,
        save_workspace_for_user=_save_workspace_for_user,
    )


def _interaction_runtime() -> InteractionRuntime:
    return InteractionRuntime(
        board_summary=_board_summary,
        resource_summary=_resource_summary,
        conversation_summary=_conversation_summary,
        generate_board_directed_explanation_message=_generate_board_directed_explanation_message,
        latest_learning_clarification=_latest_learning_clarification,
        generate_focus_candidate_message=_generate_focus_candidate_message,
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
        sequence_runtime=_sequence_runtime,
        handle_existing_board_task_flow=_handle_existing_board_task_flow,
    )


def _initial_board_runtime() -> InitialBoardRuntime:
    return InitialBoardRuntime(
        with_task_details=_with_task_details,
        prepare_initial_requirement_for_board_generation=_prepare_initial_requirement_for_board_generation,
        checkpoint_initial_requirement_before_generation=_checkpoint_initial_requirement_before_generation,
        post_initial_board_generation_message=_post_initial_board_generation_message,
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
    )


def _general_chat_runtime() -> GeneralChatRuntime:
    return GeneralChatRuntime(
        clear_task_requirements=_clear_task_requirements,
        save_workspace_for_user=_save_workspace_for_user,
    )


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
    reply = generate_post_initial_board_generation_reply(
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
    if reply.chatbot_message:
        return reply.chatbot_message, reply.assistant_message_source
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
    reply = generate_board_directed_role_reply(
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
    return reply.chatbot_message, reply.assistant_message_source, reply.directive_payload


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


def _board_search_evidence_metadata(resolution: FocusResolution | None) -> dict[str, object]:
    return {
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
    }


def _decision_focus(decision: BoardTaskRouteDecision, resolution: FocusResolution | None) -> BoardFocusRef | None:
    return decision.target_focus or (resolution.focus if resolution else None)


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
    reply = generate_board_task_clarification_reply(
        lesson_title=lesson.title,
        learning_goal=board_task.question_or_topic or lesson.summary,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        visible_board_task=visible_task,
        clarification_context=context,
        interaction_mode=request.interaction_mode,
    )
    return reply.chatbot_message, reply.assistant_message_source


def _chatbot_visible_board_task(board_task: BoardTaskRequirementSheet) -> dict[str, object]:
    payload = board_task.model_dump(mode="json")
    if payload.get("target_hint"):
        payload["target_hint"] = "已由板书侧记录；Chatbot 无直接读取目标板书内容权限。"
    if payload.get("target_location"):
        payload["target_location"] = "已由板书侧定位；Chatbot 无直接读取目标板书内容权限。"
    return payload


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
    return handle_existing_board_task_flow(
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
        source_interaction_metadata=source_interaction_metadata,
        force_task_attempt=force_task_attempt,
        runtime=_existing_board_task_runtime(),
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
    return handle_existing_interaction_session(
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
        runtime=_interaction_runtime(),
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
    return maybe_start_interaction_session(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resources=resources,
        selection_text=selection_text,
        action_type=action_type,
        requirement_history=requirement_history,
        board_task=board_task,
        board_task_history=board_task_history,
        board_task_stamp=board_task_stamp,
        board_task_decision=board_task_decision,
        resolved_focus=resolved_focus,
        source_interaction_metadata=source_interaction_metadata,
        runtime=_interaction_runtime(),
    )


def _chatbot_recommendation_context(
    *,
    lesson: Lesson,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    request: ChatRequest,
    action_type: BoardTaskAction | None,
    selected_reference,
    reference_prompt,
) -> str:
    if not is_document_empty(lesson.board_document):
        return ""
    if learning_clarification.ready_for_board or learning_clarification.forced_start:
        return ""
    if request.interaction_mode == "direct_edit":
        return ""
    if action_type is not None:
        return ""
    if selected_reference is not None or reference_prompt is not None:
        return ""
    if request.board_generation_action == "start" or request.teaching_action is not None:
        return ""
    if request.resource_reference_action is not None or request.resource_board_action is not None:
        return ""
    return requirement_recommendation_context(
        lesson=lesson,
        requirements=requirements,
        resources=resources,
        conversation=conversation,
        user_message=request.message,
    )


def _chat_response(
    *,
    lesson_id: str,
    request: ChatRequest,
    user_id: str,
    selection_text: str | None = None,
) -> ChatResponse:
    # 单次教学回合总编排：加载 workspace、识别选区/资料/动作，再按当前状态选择唯一主路线。
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    requirements = effective_requirements(lesson)
    request = request_with_pending_resource_board_action(lesson, request)
    requirement_history = _new_requirement_history_recorder(user_id=user_id, lesson_id=lesson.id)
    board_task_history = _new_board_task_history_recorder(user_id=user_id, lesson_id=lesson.id)
    track_initial_requirement_run = _should_track_initial_requirement_run(lesson)
    visible_package = workspace_state.package_context_for_lesson(workspace, package, lesson.id)
    selection_excerpt = _selection_excerpt(request.selection, selection_text)
    initial_board_task_action = _infer_board_task_action(
        request,
        has_selection=bool(selection_excerpt),
        document_empty=is_document_empty(lesson.board_document),
    )
    action_type = initial_board_task_action
    action_type = _prefer_requirement_action(
        action_type,
        requirements.action_type,
        request_message=request.message,
        requirements=requirements,
    )
    resource_backed_answer_without_generation = (
        _requests_resource_backed_answer(request.message)
        and not _requests_document_artifact_generation(request.message)
        and not _requests_learning_start(request.message)
    )
    explicit_resource_board_generation = (
        is_document_empty(lesson.board_document)
        and (
            request.resource_board_action == "generate"
            or
            is_generation_control_request(request.message)
            or _requests_document_artifact_generation(request.message)
        )
    )
    allow_direct_resource_reference = (
        request.interaction_mode != "direct_edit"
        and action_type not in DOCUMENT_WRITE_ACTIONS
        and request.board_generation_action != "start"
        and (resource_backed_answer_without_generation or explicit_resource_board_generation)
    )
    pending_resource_board_proposal = matching_pending_resource_board_proposal(lesson, request)
    resource_reference_action = request.resource_reference_action
    resource_reference_resource_id = request.resource_reference_resource_id
    resource_reference_chapter_id = request.resource_reference_chapter_id
    if request.resource_board_action == "generate" and pending_resource_board_proposal is not None:
        resource_reference_action = "confirm"
        resource_reference_resource_id = pending_resource_board_proposal.resource_id
        resource_reference_chapter_id = pending_resource_board_proposal.chapter_id
    resource_resolution = resolve_resource_reference(
        # 资料选择必须先由 ResourceResolver 明确处理，不能把所有资料默认污染进 Chatbot 上下文。
        resources=visible_package.resources,
        user_message=request.message,
        reference_action=resource_reference_action,
        reference_resource_id=resource_reference_resource_id,
        reference_chapter_id=resource_reference_chapter_id,
        allow_direct_reference=allow_direct_resource_reference,
    )
    selected_reference = resource_resolution.selected_reference
    selection_or_reference_excerpt = _merge_selection_and_reference(selection_excerpt, selected_reference)
    resource_summary_for_turn = _resource_summary_with_reference(visible_package.resources, selected_reference)
    resource_board_proposal_for_turn = (
        remember_resource_board_proposal(
            lesson,
            resource_resolution,
            require_empty_document=True,
        )
        if should_store_resource_board_proposal(
            lesson=lesson,
            request=request,
            resource_resolution=resource_resolution,
        )
        else None
    )

    resource_board_skip_response = skip_pending_resource_board_proposal(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=_latest_learning_clarification(lesson, requirements=requirements),
        requirement_history=requirement_history,
        save_workspace_for_user=_save_workspace_for_user,
    )
    if resource_board_skip_response is not None:
        return resource_board_skip_response

    interaction_response = _handle_existing_interaction_session(
        # 如果已有互动 session，先判断本轮是否继续规则、退出规则或转成新任务。
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

    should_try_existing_board_task = resource_resolution.selected_reference is None and resource_resolution.reference_prompt is None
    if (
        initial_board_task_action == "explain_target"
        and request.interaction_mode != "direct_edit"
        and not is_document_empty(lesson.board_document)
    ):
        should_try_existing_board_task = True
    if should_try_existing_board_task:
        board_task_response = _handle_existing_board_task_flow(
            # 已有板书时优先走第二层任务单链路，防止 Chatbot 绕过定位和授权直接回答。
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
        return run_initial_board_generation(
            trigger="explicit_start",
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_summary=_resource_summary(visible_package.resources),
            requirement_history=requirement_history,
            track_initial_requirement_run=track_initial_requirement_run,
            runtime=_initial_board_runtime(),
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
                    **_focus_metadata(focus=None, focus_candidates=resolution.candidates),
                    "requirement_cleared": True,
                    "active_requirement_sheet_after": None,
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
                **_focus_metadata(focus=resolution.focus, focus_candidates=resolution.candidates),
                "requirement_cleared": True,
                "active_requirement_sheet_after": None,
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
                    "requirement_cleared": True,
                    "active_requirement_sheet_after": None,
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
                    **_focus_metadata(focus=None, focus_candidates=resolution.candidates),
                    "requirement_cleared": True,
                    "active_requirement_sheet_after": None,
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
                    **_focus_metadata(focus=resolution.focus, focus_candidates=resolution.candidates),
                    "requirement_cleared": True,
                    "active_requirement_sheet_after": None,
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
                **_focus_metadata(focus=resolution.focus, focus_candidates=resolution.candidates),
                "requirement_cleared": True,
                "active_requirement_sheet_after": None,
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

    if request.resource_board_action == "generate" or is_generation_control_request(request.message) or _requests_document_artifact_generation(
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
        if request.resource_board_action == "generate" and pending_resource_board_proposal is not None:
            lesson.learning_requirements = requirements
            if resource_resolution.selected_reference is not None and resource_resolution.evidence_bundle is not None:
                lesson.pending_resource_board_proposal = None
                return run_initial_board_generation(
                    trigger="resource_board_proposal_generate",
                    workspace=workspace,
                    package=package,
                    lesson=lesson,
                    user_id=user_id,
                    request=request,
                    requirements=requirements,
                    learning_clarification=learning_clarification,
                    resource_summary=resource_summary_for_turn,
                    resource_resolution=resource_resolution,
                    requirement_history=requirement_history,
                    track_initial_requirement_run=track_initial_requirement_run,
                    runtime=_initial_board_runtime(),
                    action_instruction=pending_resource_board_proposal.target_title,
                    solver_metadata={
                        "resource_board_action": "generate",
                        "resource_board_proposal_id": pending_resource_board_proposal.id,
                        "resource_board_proposal": pending_resource_board_proposal.model_dump(mode="json"),
                    },
                )
        if request.resource_board_action == "generate":
            lesson.learning_requirements = requirements
            return resource_board_proposal_unavailable_response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                requirement_history=requirement_history,
                save_workspace_for_user=_save_workspace_for_user,
            )
        if resource_resolution.reference_prompt is not None and request.resource_reference_action is None:
            lesson.learning_requirements = requirements
            return prompt_for_resource_reference(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_resolution=resource_resolution,
                requirement_history=requirement_history,
                track_initial_requirement_run=track_initial_requirement_run,
                commit_message="Asked the learner to confirm a relevant resource chapter before continuing",
                save_workspace_for_user=_save_workspace_for_user,
            )
        resource_generation_response = run_confirmed_resource_initial_board_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
            resource_summary=resource_summary_for_turn,
            requirement_history=requirement_history,
            track_initial_requirement_run=track_initial_requirement_run,
            runtime=_initial_board_runtime(),
        )
        if resource_generation_response is not None:
            return resource_generation_response
        if _should_generate_board_from_explicit_request(
            lesson=lesson,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
        ):
            return run_initial_board_generation(
                trigger="explicit_board_request",
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=user_id,
                request=request,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resource_summary=resource_summary_for_turn,
                resource_resolution=resource_resolution,
                requirement_history=requirement_history,
                track_initial_requirement_run=track_initial_requirement_run,
                runtime=_initial_board_runtime(),
            )
        lesson.learning_requirements = requirements
        chatbot_user_message = (
            requirement_probe_instead_of_explanation_message(request.message)
            if _requests_explanation(request.message)
            else request.message
        )
        role_reply = generate_chatbot_role_reply(
            lesson_title=lesson.title,
            learning_goal=learning_clarification.summary or requirements.learning_goal,
            board_summary=_board_summary(lesson),
            resource_summary=resource_summary_for_turn,
            conversation_summary=_conversation_summary(request.conversation),
            user_message=chatbot_user_message,
            selection_excerpt=_chatbot_visible_selection_excerpt(request, selection_or_reference_excerpt),
            interaction_mode=request.interaction_mode,
            recommendation_context=_chatbot_recommendation_context(
                lesson=lesson,
                requirements=requirements,
                learning_clarification=learning_clarification,
                resources=visible_package.resources,
                conversation=request.conversation,
                request=request,
                action_type=action_type,
                selected_reference=selected_reference,
                reference_prompt=resource_resolution.reference_prompt,
            ),
        )
        chatbot_message = role_reply.chatbot_message
        chatbot_message_source = role_reply.assistant_message_source

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
                **_learning_requirement_metadata(
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
            resource_evidence_bundle=resource_resolution.evidence_bundle,
            selected_reference=selected_reference,
            requirement_history=requirement_history if track_initial_requirement_run else None,
        )

    if (
        resource_resolution.reference_prompt is not None
        and request.resource_reference_action is None
        and _should_prompt_resource_reference(request.message)
    ):
        learning_clarification = _latest_learning_clarification(lesson, requirements=requirements)
        return prompt_for_resource_reference(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
            requirement_history=requirement_history,
            track_initial_requirement_run=track_initial_requirement_run,
            commit_message="Asked the learner to confirm a relevant resource chapter before answering",
            save_workspace_for_user=_save_workspace_for_user,
        )

    if (
        request.resource_reference_action == "confirm"
        and selected_reference is not None
        and should_generate_board_after_reference_confirmation(request.message)
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
        resource_generation_response = run_confirmed_resource_initial_board_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
            request=request,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resource_resolution=resource_resolution,
            resource_summary=resource_summary_for_turn,
            requirement_history=requirement_history,
            track_initial_requirement_run=track_initial_requirement_run,
            runtime=_initial_board_runtime(),
        )
        if resource_generation_response is not None:
            return resource_generation_response

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
                "requirement_cleared": True,
                "active_requirement_sheet_after": None,
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
            resource_evidence_bundle=resource_resolution.evidence_bundle,
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
    role_reply = generate_chatbot_role_reply(
        lesson_title=lesson.title,
        learning_goal=requirements.learning_goal,
        board_summary=_board_summary(lesson),
        resource_summary=resource_summary_for_turn,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=solver_user_message,
        selection_excerpt=_chatbot_visible_selection_excerpt(request, selection_or_reference_excerpt),
        interaction_mode=request.interaction_mode,
        recommendation_context=_chatbot_recommendation_context(
            lesson=lesson,
            requirements=requirements,
            learning_clarification=learning_clarification,
            resources=visible_package.resources,
            conversation=request.conversation,
            request=request,
            action_type=action_type,
            selected_reference=selected_reference,
            reference_prompt=resource_resolution.reference_prompt,
        ),
    )
    chatbot_message = role_reply.chatbot_message
    chatbot_message_source = role_reply.assistant_message_source
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

    return commit_general_chat_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=user_id,
        request=request,
        requirements=requirements,
        learning_clarification=learning_clarification,
        resource_resolution=resource_resolution,
        selected_reference=selected_reference,
        resource_board_proposal=resource_board_proposal_for_turn,
        requirement_history=requirement_history,
        track_initial_requirement_run=track_initial_requirement_run,
        chatbot_message=chatbot_message,
        chatbot_message_source=chatbot_message_source,
        solver_metadata=solver_metadata,
        runtime=_general_chat_runtime(),
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
