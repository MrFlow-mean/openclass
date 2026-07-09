from __future__ import annotations

from app.models import (
    AgentTurnDecision,
    BoardDecision,
    ChatRequest,
    ChatResponse,
    ConversationTurn,
    EvidenceBundle,
    LearningClarificationStatus,
)
from app.services import workspace_state
from app.services.agent_turn_decision import decide_agent_turn
from app.services.agent_workflow_orchestrator import AgentWorkflowOrchestrator
from app.services.agent_workflow_verifier import verify_agent_response
from app.services.blank_board_generation import run_blank_board_generation
from app.services.board_document_sensor import read_board_document_sensor
from app.services.board_task_executor import execute_ready_board_task
from app.services.board_task_history import BoardTaskHistoryStamp
from app.services.board_task_refiner import refine_existing_board_task_requirement
from app.services.board_teaching_orchestrator import (
    run_board_teaching_turn,
    should_continue_board_teaching,
    should_start_board_teaching,
)
from app.services.course_runtime import effective_requirements
from app.services.evidence_workflow import (
    evidence_confirmation_message,
    resolve_board_task_evidence_gate,
    source_absent_message,
)
from app.services.history import bind_commit_metadata, commit_operations, current_head_commit
from app.services.interaction_session_orchestrator import run_interaction_session_turn
from app.services.formula_ink_resolver import resolve_formula_ink_request
from app.services.learning_requirement_history import RequirementHistoryStamp
from app.services.learning_requirement_refiner import refine_blank_board_requirement
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import (
    bind_board_model_selection,
    bind_text_model_selection,
    openai_course_ai,
)
from app.services.resource_resolver import evidence_metadata, resource_resolver
from app.services.route_context import bind_ai_request_context
from app.services.source_evidence_store import source_evidence_store


BASIC_CHAT_METADATA_KIND = "basic_chat"
LEARNING_REQUIREMENT_REFINEMENT_METADATA_KIND = "learning_requirement_refinement"
BOARD_TASK_REFINEMENT_METADATA_KIND = "board_task_requirement_refinement"


def _conversation_summary(conversation: list[ConversationTurn], *, limit: int = 1600) -> str:
    lines = [f"{turn.role}: {turn.content.strip()}" for turn in conversation if turn.content.strip()]
    summary = "\n".join(lines[-8:])
    return summary[-limit:] if len(summary) > limit else summary


def _reset_clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=0,
        label="basic_chat",
        reason="当前聊天框只执行基础你问我答，不进入文档工作流。",
        missing_items=[],
        can_start=False,
        forced_start=False,
        summary="",
        next_question="",
        ready_for_board=False,
        work_mode="unknown",
        granularity="unclear",
    )


def _clear_legacy_runtime_state(lesson) -> None:
    lesson.learning_requirements = None
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None


def _clear_board_task_runtime_state(lesson) -> None:
    lesson.board_task_requirements = None
    lesson.active_interaction_session = None


def _is_default_learning_requirement_sheet(lesson) -> bool:
    if lesson.learning_requirements is None:
        return False
    default_requirements = build_requirements(lesson.title)
    return (
        lesson.learning_requirements.model_dump(mode="json")
        == default_requirements.model_dump(mode="json")
    )


def _build_response(
    *,
    workspace,
    package,
    lesson,
    chatbot_message: str,
    board_decision: BoardDecision,
    active_requirement_sheet=None,
    learning_clarification: LearningClarificationStatus | None = None,
    requirement_stamp: RequirementHistoryStamp | None = None,
    requirement_cleared: bool = True,
    active_board_task_sheet=None,
    board_task_stamp: BoardTaskHistoryStamp | None = None,
    board_task_questions: list[str] | None = None,
    resolved_focus=None,
    focus_candidates=None,
    board_search_evidence=None,
    board_document_operation_status="none",
    board_document_operation_failure_reason=None,
    board_patch_diff=None,
    teaching_progress=None,
    active_interaction_session=None,
    interaction_decision=None,
    evidence_bundle: EvidenceBundle | None = None,
    candidate_evidence_bundle: EvidenceBundle | None = None,
) -> ChatResponse:
    requirements = effective_requirements(lesson)
    return ChatResponse(
        chatbot_message=chatbot_message,
        learning_requirement_sheet=requirements,
        active_requirement_sheet=active_requirement_sheet,
        active_interaction_session=active_interaction_session,
        interaction_decision=interaction_decision,
        learning_clarification=learning_clarification or _reset_clarification(),
        requirement_run_id=requirement_stamp.run_id if requirement_stamp else None,
        requirement_version_id=requirement_stamp.version_id if requirement_stamp else None,
        requirement_phase=requirement_stamp.phase if requirement_stamp else None,
        board_task_sheet=active_board_task_sheet,
        active_board_task_sheet=active_board_task_sheet,
        board_task_run_id=board_task_stamp.run_id if board_task_stamp else None,
        board_task_version_id=board_task_stamp.version_id if board_task_stamp else None,
        board_task_phase=board_task_stamp.phase if board_task_stamp else None,
        board_task_questions=board_task_questions or [],
        board_decision=board_decision,
        needs_clarification=False,
        clarification_questions=[],
        resolved_focus=resolved_focus,
        focus_candidates=focus_candidates or [],
        board_search_evidence=board_search_evidence,
        evidence_bundle=evidence_bundle,
        candidate_evidence_bundle=candidate_evidence_bundle,
        requirement_cleared=requirement_cleared,
        board_document_operation_status=board_document_operation_status,
        board_document_operation_failure_reason=board_document_operation_failure_reason,
        board_patch_diff=board_patch_diff or [],
        teaching_progress=teaching_progress,
        course_package=workspace_state.package_view_for_lesson(workspace, package, lesson.id),
    )


def _board_task_ready_for_execution(active_board_task_sheet) -> bool:
    if active_board_task_sheet is None:
        return False
    if active_board_task_sheet.requested_action not in {"explain", "write", "edit", "chat"}:
        return False
    if active_board_task_sheet.progress < 100:
        return False
    if active_board_task_sheet.missing_items:
        return False
    if active_board_task_sheet.clarification_question.strip():
        return False
    return bool(
        active_board_task_sheet.question_or_topic.strip()
        or active_board_task_sheet.target_hint.strip()
        or active_board_task_sheet.interaction_rule_draft is not None
    )


def _run_board_task_refinement_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
) -> ChatResponse:
    history_state = workspace_state.load_board_task_history_state_for_user(user_id, lesson.id)
    outcome = refine_existing_board_task_requirement(
        owner_user_id=user_id,
        lesson=lesson,
        board_document_state=board_document_state,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        selection=request.selection,
        history_state=history_state,
    )
    if outcome is None:
        return _run_basic_chat_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
            clear_learning_requirements=True,
        )

    lesson.learning_requirements = None
    lesson.board_task_requirements = outcome.active_board_task_sheet
    lesson.active_interaction_session = None
    board_decision = BoardDecision(
        action="no_change",
        reason="已有板书任务需求收敛只维护清单，本阶段不执行讲解或写入。",
    )
    metadata_kind = (
        BOARD_TASK_REFINEMENT_METADATA_KIND
        if outcome.route == "board_task_refining"
        else BASIC_CHAT_METADATA_KIND
    )
    active_board_task = outcome.active_board_task_sheet
    candidate_evidence = None
    chatbot_message = outcome.chatbot_message
    if outcome.route == "board_task_refining" and _board_task_ready_for_execution(active_board_task):
        evidence_gate = resolve_board_task_evidence_gate(
            owner_user_id=user_id,
            package_id=package.id,
            lesson_id=lesson.id,
            user_message=request.message,
            board_task=active_board_task,
            board_task_run_id=outcome.history_stamp.run_id,
            base_chatbot_message=outcome.chatbot_message,
        )
        candidate_evidence = evidence_gate.evidence_bundle
        chatbot_message = evidence_gate.chatbot_message
        if evidence_gate.should_execute:
            execution = execute_ready_board_task(
                owner_user_id=user_id,
                lesson=lesson,
                board_task=active_board_task,
                user_message=request.message,
                selection=request.selection,
                conversation_summary=_conversation_summary(request.conversation),
                history_stamp=outcome.history_stamp,
                history_operations=outcome.history_operations,
                evidence_bundle=candidate_evidence,
            )
            workspace_state.normalize_package_state(package)
            if candidate_evidence is not None and candidate_evidence.status == "confirmed":
                source_evidence_store.consume_bundle(owner_user_id=user_id, bundle_id=candidate_evidence.id)
            workspace_state.save_workspace_and_board_task_history_for_user(
                user_id,
                workspace,
                board_task_history_operations=execution.history_operations,
            )
            return _build_response(
                workspace=workspace,
                package=package,
                lesson=lesson,
                chatbot_message=execution.chatbot_message,
                board_decision=execution.board_decision,
                active_requirement_sheet=None,
                learning_clarification=_reset_clarification(),
                requirement_cleared=True,
                active_board_task_sheet=execution.active_board_task_sheet,
                board_task_stamp=execution.board_task_stamp,
                board_task_questions=execution.board_task_questions,
                resolved_focus=execution.resolved_focus,
                focus_candidates=execution.focus_candidates,
                board_search_evidence=execution.board_search_evidence,
                board_document_operation_status=execution.board_document_operation_status,
                board_document_operation_failure_reason=execution.board_document_operation_failure_reason,
                board_patch_diff=execution.board_patch_diff,
                active_interaction_session=execution.active_interaction_session,
                evidence_bundle=candidate_evidence,
                candidate_evidence_bundle=(
                    candidate_evidence if candidate_evidence and candidate_evidence.status == "candidate" else None
                ),
            )
    commit_operations(
        lesson,
        [],
        label="Board task requirement refinement" if outcome.route == "board_task_refining" else "Basic chat",
        message=(
            "Recorded an existing-board task requirement refinement turn"
            if outcome.route == "board_task_refining"
            else "Recorded a basic chatbot conversation turn"
        ),
        new_document=lesson.board_document,
        metadata={
            "kind": metadata_kind,
            "board_task_refinement_route": outcome.route,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot" if chatbot_message else "chatbot_empty",
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": outcome.route == "ordinary_chat",
            "document_changed": False,
            **evidence_metadata(candidate_evidence),
            "active_requirement_sheet_after": None,
            "board_task_sheet": active_board_task.model_dump(mode="json") if active_board_task else None,
            "active_board_task_sheet_after": active_board_task.model_dump(mode="json") if active_board_task else None,
            "board_task_cleared": active_board_task is None,
            "board_task_questions": outcome.board_task_questions,
            "board_task_run_id": outcome.history_stamp.run_id,
            "board_task_version_id": outcome.history_stamp.version_id,
            "board_task_phase": outcome.history_stamp.phase,
            "board_task_history_changed": outcome.changed,
            "board_task_guidance": outcome.guidance_metadata,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_and_board_task_history_for_user(
        user_id,
        workspace,
        board_task_history_operations=outcome.history_operations,
    )
    return _build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        board_decision=board_decision,
        active_requirement_sheet=None,
        learning_clarification=_reset_clarification(),
        requirement_cleared=True,
        active_board_task_sheet=active_board_task,
        board_task_stamp=outcome.history_stamp,
        board_task_questions=outcome.board_task_questions,
        evidence_bundle=candidate_evidence,
        candidate_evidence_bundle=candidate_evidence if candidate_evidence and candidate_evidence.status == "candidate" else None,
    )


def _run_basic_chat_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
    clear_learning_requirements: bool,
) -> ChatResponse:
    candidate_evidence = None
    resource_summary = ""
    if resource_resolver.should_use_sources(request.message):
        candidate_evidence = resource_resolver.resolve_for_learning_requirement(
            owner_user_id=user_id,
            package_id=package.id,
            lesson_id=lesson.id,
            user_message=request.message,
            requirements=lesson.learning_requirements,
            purpose="chat",
        )
        if candidate_evidence is None:
            chatbot_message = source_absent_message()
            ai_reply = None
        else:
            resource_summary = candidate_evidence.context_text
            ai_reply = openai_course_ai.generate_basic_chat_reply(
                board_document_state=board_document_state.model_context(),
                conversation_summary=_conversation_summary(request.conversation),
                user_message=request.message,
                resource_summary=resource_summary,
            )
            chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    else:
        ai_reply = openai_course_ai.generate_basic_chat_reply(
            board_document_state=board_document_state.model_context(),
            conversation_summary=_conversation_summary(request.conversation),
            user_message=request.message,
        )
        chatbot_message = (ai_reply.chatbot_message if ai_reply else "").strip()
    if clear_learning_requirements:
        _clear_legacy_runtime_state(lesson)
        active_requirement_sheet = None
    else:
        if _is_default_learning_requirement_sheet(lesson):
            lesson.learning_requirements = None
        _clear_board_task_runtime_state(lesson)
        active_requirement_sheet = lesson.learning_requirements
    board_decision = BoardDecision(
        action="no_change",
        reason="基础聊天回合不修改右侧文档。",
    )
    commit_operations(
        lesson,
        [],
        label="Basic chat",
        message="Recorded a basic chatbot conversation turn",
        new_document=lesson.board_document,
        metadata={
            "kind": BASIC_CHAT_METADATA_KIND,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot" if chatbot_message else "chatbot_empty",
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": True,
            "document_changed": False,
            **evidence_metadata(candidate_evidence),
            "active_requirement_sheet_after": (
                active_requirement_sheet.model_dump(mode="json")
                if active_requirement_sheet is not None
                else None
            ),
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_for_user(user_id, workspace)
    return _build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        board_decision=board_decision,
        active_requirement_sheet=active_requirement_sheet,
        requirement_cleared=active_requirement_sheet is None,
        evidence_bundle=candidate_evidence,
        candidate_evidence_bundle=candidate_evidence,
    )


def _run_requirement_refinement_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
) -> ChatResponse:
    history_state = workspace_state.load_learning_requirement_history_state_for_user(user_id, lesson.id)
    outcome = refine_blank_board_requirement(
        owner_user_id=user_id,
        lesson=lesson,
        board_document_state=board_document_state,
        conversation_summary=_conversation_summary(request.conversation),
        user_message=request.message,
        history_state=history_state,
    )
    if outcome is None:
        return _run_basic_chat_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
            clear_learning_requirements=history_state is None,
        )

    if outcome.active_requirement_sheet is None:
        lesson.learning_requirements = None
    else:
        lesson.learning_requirements = outcome.active_requirement_sheet
    _clear_board_task_runtime_state(lesson)
    candidate_evidence = None
    chatbot_message = outcome.chatbot_message
    if (
        outcome.route == "requirement_refining"
        and outcome.active_requirement_sheet is not None
        and outcome.learning_clarification.ready_for_board
        and resource_resolver.should_use_sources(request.message)
    ):
        candidate_evidence = resource_resolver.resolve_for_learning_requirement(
            owner_user_id=user_id,
            package_id=package.id,
            lesson_id=lesson.id,
            user_message=request.message,
            requirements=outcome.active_requirement_sheet,
            requirement_run_id=outcome.history_stamp.run_id,
            purpose="board_generation",
        )
        chatbot_message = (
            evidence_confirmation_message(outcome.chatbot_message, candidate_evidence, action_label="生成板书")
            if candidate_evidence is not None
            else source_absent_message()
        )
    board_decision = BoardDecision(
        action="no_change",
        reason="空白板书学习需求收敛只维护清单，不修改右侧文档。",
    )
    metadata_kind = (
        LEARNING_REQUIREMENT_REFINEMENT_METADATA_KIND
        if outcome.route == "requirement_refining"
        else BASIC_CHAT_METADATA_KIND
    )
    commit_operations(
        lesson,
        [],
        label="Learning requirement refinement" if outcome.route == "requirement_refining" else "Basic chat",
        message=(
            "Recorded a blank-board learning requirement refinement turn"
            if outcome.route == "requirement_refining"
            else "Recorded a basic chatbot conversation turn"
        ),
        new_document=lesson.board_document,
        metadata={
            "kind": metadata_kind,
            "refinement_route": outcome.route,
            "user_message": request.message,
            "assistant_message": chatbot_message,
            "assistant_message_source": "chatbot" if chatbot_message else "chatbot_empty",
            "board_document_state": board_document_state.model_context(),
            "interaction_mode": request.interaction_mode,
            "selection": request.selection.model_dump(mode="json") if request.selection else None,
            "basic_chat_only": outcome.route == "ordinary_chat",
            "document_changed": False,
            **evidence_metadata(candidate_evidence),
            "active_requirement_sheet_after": (
                outcome.active_requirement_sheet.model_dump(mode="json")
                if outcome.active_requirement_sheet is not None
                else None
            ),
            "learning_clarification_after": outcome.learning_clarification.model_dump(mode="json"),
            "guided_requirement_discovery": outcome.guidance_metadata,
            "requirement_run_id": outcome.history_stamp.run_id,
            "requirement_version_id": outcome.history_stamp.version_id,
            "requirement_phase": outcome.history_stamp.phase,
            "requirement_history_changed": outcome.changed,
        },
    )
    workspace_state.normalize_package_state(package)
    workspace_state.save_workspace_and_learning_requirement_history_for_user(
        user_id,
        workspace,
        learning_requirement_history_operations=outcome.history_operations,
    )
    return _build_response(
        workspace=workspace,
        package=package,
        lesson=lesson,
        chatbot_message=chatbot_message,
        board_decision=board_decision,
        active_requirement_sheet=outcome.active_requirement_sheet,
        learning_clarification=outcome.learning_clarification,
        requirement_stamp=outcome.history_stamp,
        requirement_cleared=outcome.active_requirement_sheet is None,
        evidence_bundle=candidate_evidence,
        candidate_evidence_bundle=candidate_evidence,
    )


def _run_decided_chat_turn(
    *,
    workspace,
    package,
    lesson,
    request: ChatRequest,
    user_id: str,
    board_document_state,
    workflow: AgentWorkflowOrchestrator,
) -> ChatResponse:
    if request.board_generation_action == "start":
        workflow.record_context_ready(
            label="读取冻结学习需求",
            role="RequirementHistory",
            metadata={"board_document_state": board_document_state.model_context()},
        )
        response = run_blank_board_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=user_id,
        )
        workflow.record_execution(label="生成右侧板书", role="BoardEditor", response=response)
        return response
    if board_document_state.status == "empty":
        workflow.record_context_ready(
            label="构造空白板书需求上下文",
            role="RequirementManager",
            metadata={"board_document_state": board_document_state.model_context()},
        )
        response = _run_requirement_refinement_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
        )
        workflow.record_execution(label="整理学习需求或普通聊天", role="RequirementManager", response=response)
        return response
    if lesson.active_interaction_session is not None:
        workflow.record_context_ready(
            label="读取规则互动会话",
            role="InteractionSession",
            metadata={
                "session_id": lesson.active_interaction_session.id,
                "turn_count": lesson.active_interaction_session.turn_count,
            },
        )
        interaction = run_interaction_session_turn(
            lesson=lesson,
            request=request,
            conversation_summary=_conversation_summary(request.conversation),
        )
        if interaction.reroute_user_message:
            workflow.record_execution(
                label="结束规则互动并回流任务",
                role="InteractionSession",
                response=_build_response(
                    workspace=workspace,
                    package=package,
                    lesson=lesson,
                    chatbot_message=interaction.chatbot_message,
                    board_decision=interaction.board_decision,
                    active_requirement_sheet=None,
                    learning_clarification=_reset_clarification(),
                    requirement_cleared=True,
                    active_board_task_sheet=None,
                    active_interaction_session=None,
                    interaction_decision=interaction.interaction_decision,
                ),
            )
            response = _run_board_task_refinement_turn(
                workspace=workspace,
                package=package,
                lesson=lesson,
                request=request,
                user_id=user_id,
                board_document_state=board_document_state,
            )
            workflow.record_target_resolution(response)
            workflow.record_execution(label="回流已有板书任务", role="BoardTaskManager", response=response)
            return response
        response = _build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=interaction.chatbot_message,
            board_decision=interaction.board_decision,
            active_requirement_sheet=None,
            learning_clarification=_reset_clarification(),
            requirement_cleared=True,
            active_board_task_sheet=None,
            active_interaction_session=interaction.active_interaction_session,
            interaction_decision=interaction.interaction_decision,
        )
        workflow.record_execution(label="执行规则互动", role="InteractionSession", response=response)
        return response
    if should_continue_board_teaching(lesson, request):
        workflow.record_context_ready(
            label="读取当前讲解进度",
            role="BoardTeaching",
            metadata={
                "teaching_action": "continue",
                "has_progress": lesson.board_teaching_progress is not None,
            },
        )
        teaching = run_board_teaching_turn(
            lesson=lesson,
            request=request,
            teaching_action="continue",
            conversation_summary=_conversation_summary(request.conversation),
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        response = _build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=teaching.chatbot_message,
            board_decision=teaching.board_decision,
            active_requirement_sheet=None,
            learning_clarification=_reset_clarification(),
            requirement_cleared=True,
            active_board_task_sheet=None,
            teaching_progress=teaching.teaching_progress,
        )
        workflow.record_execution(label="继续讲解板书", role="BoardTeaching", response=response)
        return response
    if should_start_board_teaching(lesson, request):
        workflow.record_context_ready(
            label="构造从头讲解上下文",
            role="BoardTeaching",
            metadata={"teaching_action": "restart"},
        )
        teaching = run_board_teaching_turn(
            lesson=lesson,
            request=request,
            teaching_action="restart",
            conversation_summary=_conversation_summary(request.conversation),
        )
        workspace_state.normalize_package_state(package)
        workspace_state.save_workspace_for_user(user_id, workspace)
        response = _build_response(
            workspace=workspace,
            package=package,
            lesson=lesson,
            chatbot_message=teaching.chatbot_message,
            board_decision=teaching.board_decision,
            active_requirement_sheet=None,
            learning_clarification=_reset_clarification(),
            requirement_cleared=True,
            active_board_task_sheet=None,
            teaching_progress=teaching.teaching_progress,
        )
        workflow.record_execution(label="从头讲解板书", role="BoardTeaching", response=response)
        return response
    workflow.record_context_ready(
        label="构造已有板书任务上下文",
        role="BoardTaskManager",
        metadata={"board_document_state": board_document_state.model_context()},
    )
    response = _run_board_task_refinement_turn(
        workspace=workspace,
        package=package,
        lesson=lesson,
        request=request,
        user_id=user_id,
        board_document_state=board_document_state,
    )
    workflow.record_target_resolution(response)
    workflow.record_execution(label="整理或执行已有板书任务", role="BoardTaskManager", response=response)
    return response


def _refine_agent_decision_from_commit(
    *,
    decision: AgentTurnDecision,
    lesson,
) -> AgentTurnDecision:
    try:
        metadata = current_head_commit(lesson).metadata
    except Exception:
        return decision
    if not isinstance(metadata, dict) or metadata.get("basic_chat_only") is not True:
        return decision
    return decision.model_copy(
        update={
            "route": "ordinary_chat",
            "reason": "本轮最终被执行为普通聊天，没有创建或执行文档任务。",
            "required_role": "Chatbot",
            "blockers": [],
            "next_step": "直接返回自然聊天回复，不修改右侧板书。",
            "needs_user_confirmation": False,
        }
    )


def _run_chat_turn(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    request = resolve_formula_ink_request(request)
    workspace = workspace_state.load_workspace_for_user(user_id)
    package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    board_document_state = read_board_document_sensor(lesson.board_document)
    decision = decide_agent_turn(
        lesson=lesson,
        request=request,
        board_document_state=board_document_state,
    )
    workflow = AgentWorkflowOrchestrator(decision=decision)
    with bind_commit_metadata(workflow.commit_metadata):
        response = _run_decided_chat_turn(
            workspace=workspace,
            package=package,
            lesson=lesson,
            request=request,
            user_id=user_id,
            board_document_state=board_document_state,
            workflow=workflow,
        )
    decision = _refine_agent_decision_from_commit(decision=decision, lesson=lesson)
    workflow.update_decision(decision)
    verification = verify_agent_response(lesson=lesson, response=response, decision=decision)
    workflow.record_verification(verification)
    workflow.record_persisted(response)
    workflow.finalize_response(response)
    workspace_state.save_workspace_for_user(user_id, workspace)
    response.course_package = workspace_state.package_view_for_lesson(workspace, package, lesson.id)
    return response


def process_chat_on_lesson(lesson_id: str, request: ChatRequest, *, user_id: str) -> ChatResponse:
    with bind_ai_request_context(
        "/api/lessons/{lesson_id}/chat",
        trace_prefix="chat",
        lesson_id=lesson_id,
        user_id=user_id,
    ):
        with bind_text_model_selection(request.text_model):
            with bind_board_model_selection(request.board_model):
                return _run_chat_turn(lesson_id, request, user_id=user_id)


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
    return process_chat_on_lesson(lesson_id, request, user_id=user_id)
