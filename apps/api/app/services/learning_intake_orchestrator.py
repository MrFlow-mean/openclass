from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import ChatRequest, EvidenceBundle, LearningRequirementSheet, Lesson
from app.services.board_document_sensor import BoardDocumentSensorReading
from app.services.learning_requirement_refiner import (
    LearningRequirementRefinementOutcome,
    refine_blank_board_requirement,
)
from app.services.learning_source_discovery import (
    LearningSourceDiscoveryOutcome,
    bind_learning_source_discovery,
    discover_learning_sources,
    rollback_learning_source_discovery,
)
from app.services.openai_course_ai import (
    InitialLearningWorkModeDecision,
    OpenAICourseAI,
    emit_ai_stream_event,
    openai_course_ai,
)
from app.services.resource_resolver import ResourceResolver, resource_resolver
from app.services.source_reference_context import (
    source_aware_user_message,
    source_reference_selection,
)


LearningIntakeTurnRoute = Literal["ordinary_chat", "requirement_refining"]


@dataclass(frozen=True)
class LearningIntakeTurnOutcome:
    route: LearningIntakeTurnRoute
    initial_decision: InitialLearningWorkModeDecision | None
    refinement: LearningRequirementRefinementOutcome | None
    source_discovery: LearningSourceDiscoveryOutcome | None
    chatbot_message: str
    assistant_message_source: str
    evidence_bundle: EvidenceBundle | None
    candidate_evidence_bundle: EvidenceBundle | None


def run_learning_intake_turn(
    *,
    owner_user_id: str,
    package_id: str,
    lesson: Lesson,
    request: ChatRequest,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    history_state: dict[str, object] | None,
    resolver: ResourceResolver = resource_resolver,
    course_ai: OpenAICourseAI = openai_course_ai,
) -> LearningIntakeTurnOutcome:
    refinement_user_message = source_aware_user_message(request)
    retrieval_user_message = source_aware_user_message(request, include_locator=True)
    active_requirement = _active_requirement(lesson, history_state)
    active_requirement_run_id = _active_requirement_run_id(history_state)
    active_evidence = (
        resolver.latest_requirement_bundle(
            owner_user_id=owner_user_id,
            lesson_id=lesson.id,
            requirement_run_id=active_requirement_run_id,
        )
        if active_requirement_run_id
        else None
    )
    source_requested = bool(
        source_reference_selection(request)
        or resolver.should_use_sources(retrieval_user_message)
        or (active_requirement and active_requirement.source_grounding.requested_by_user)
    )
    initial_decision = course_ai.generate_initial_learning_work_mode(
        lesson_title=lesson.title,
        resource_summary=_initial_learning_context(active_requirement),
        conversation_summary=conversation_summary,
        user_message=refinement_user_message,
    )
    initial_decision = _preserve_active_learning_intent(
        initial_decision,
        active_requirement=active_requirement,
        source_requested=source_requested,
        user_message=request.message,
    )

    probe: LearningRequirementRefinementOutcome | None = None
    if initial_decision is None:
        probe = refine_blank_board_requirement(
            owner_user_id=owner_user_id,
            lesson=lesson,
            board_document_state=board_document_state,
            conversation_summary=conversation_summary,
            user_message=refinement_user_message,
            history_state=history_state,
            include_stream_result=False,
            source_requested_by_user=source_requested,
        )
        if probe is None or probe.route == "ordinary_chat":
            return LearningIntakeTurnOutcome(
                route="ordinary_chat",
                initial_decision=None,
                refinement=probe,
                source_discovery=None,
                chatbot_message="",
                assistant_message_source="chatbot",
                evidence_bundle=None,
                candidate_evidence_bundle=None,
            )
        initial_decision = _decision_from_refinement(probe)

    if initial_decision.route == "ordinary_chat":
        return LearningIntakeTurnOutcome(
            route="ordinary_chat",
            initial_decision=initial_decision,
            refinement=None,
            source_discovery=None,
            chatbot_message="",
            assistant_message_source="chatbot",
            evidence_bundle=None,
            candidate_evidence_bundle=None,
        )

    emit_ai_stream_event({"type": "role_start", "role": "resource_resolver"})
    discovery_requirements = probe.active_requirement_sheet if probe is not None else active_requirement
    source_discovery = discover_learning_sources(
        owner_user_id=owner_user_id,
        package_id=package_id,
        lesson_id=lesson.id,
        retrieval_user_message=retrieval_user_message,
        requirements=discovery_requirements,
        active_requirement_run_id=active_requirement_run_id,
        topic_hint=initial_decision.topic,
        source_requested_by_user=source_requested,
        pre_resolved_evidence=active_evidence,
        resolver=resolver,
    )
    if probe is not None and not source_discovery.context_text:
        refinement = probe
    else:
        refinement = refine_blank_board_requirement(
            owner_user_id=owner_user_id,
            lesson=lesson,
            board_document_state=board_document_state,
            conversation_summary=conversation_summary,
            user_message=refinement_user_message,
            history_state=history_state,
            resource_summary=source_discovery.context_text,
            include_stream_result=False,
            initial_work_mode_decision=initial_decision,
            source_requested_by_user=source_discovery.source_requested_by_user,
        )
    if refinement is None or refinement.route == "ordinary_chat" or refinement.active_requirement_sheet is None:
        return LearningIntakeTurnOutcome(
            route="ordinary_chat",
            initial_decision=initial_decision,
            refinement=refinement,
            source_discovery=source_discovery,
            chatbot_message="",
            assistant_message_source="chatbot",
            evidence_bundle=None,
            candidate_evidence_bundle=None,
        )

    bound_discovery = bind_learning_source_discovery(
        source_discovery,
        requirement_run_id=refinement.history_stamp.run_id,
        resolver=resolver,
    )
    evidence_bundle = bound_discovery.evidence_bundle
    candidate_evidence_bundle = (
        evidence_bundle if evidence_bundle is not None and evidence_bundle.status == "candidate" else None
    )
    try:
        reply = course_ai.generate_learning_intake_reply(
            requirement_reply_draft=refinement.chatbot_message,
            user_message=request.message,
            requirement_context=refinement.active_requirement_sheet.model_dump(mode="json"),
            clarification_context=refinement.learning_clarification.model_dump(mode="json"),
            guidance_context=refinement.guidance_metadata,
            initial_work_mode_decision=initial_decision.model_dump(mode="json"),
            discovery_status=bound_discovery.status,
            evidence_references=bound_discovery.evidence_references,
            source_requested_by_user=bound_discovery.source_requested_by_user,
            requires_confirmation=bool(
                evidence_bundle is not None and evidence_bundle.status == "candidate"
            ),
        )
    except Exception:
        rollback_learning_source_discovery(bound_discovery, resolver=resolver)
        raise
    chatbot_message = _first_text(
        reply.chatbot_message if reply is not None else "",
        refinement.chatbot_message,
        initial_decision.guided_discovery_reply,
        initial_decision.next_question,
    )
    return LearningIntakeTurnOutcome(
        route="requirement_refining",
        initial_decision=initial_decision,
        refinement=refinement,
        source_discovery=bound_discovery,
        chatbot_message=chatbot_message,
        assistant_message_source=(
            "chatbot_learning_intake" if reply is not None and reply.chatbot_message.strip() else "chatbot_learning_intake_fallback"
        ),
        evidence_bundle=evidence_bundle,
        candidate_evidence_bundle=candidate_evidence_bundle,
    )


def rollback_learning_intake_turn(
    outcome: LearningIntakeTurnOutcome,
    *,
    resolver: ResourceResolver = resource_resolver,
) -> None:
    rollback_learning_source_discovery(outcome.source_discovery, resolver=resolver)


def _active_requirement(
    lesson: Lesson,
    history_state: dict[str, object] | None,
) -> LearningRequirementSheet | None:
    if history_state:
        raw = history_state.get("latest_sheet_json")
        if isinstance(raw, str) and raw.strip():
            try:
                return LearningRequirementSheet.model_validate_json(raw)
            except Exception:
                pass
        if history_state.get("status") in {"collecting", "ready"}:
            return lesson.learning_requirements
    return None


def _active_requirement_run_id(history_state: dict[str, object] | None) -> str | None:
    run_id = history_state.get("run_id") if history_state else None
    return run_id if isinstance(run_id, str) and run_id else None


def _preserve_active_learning_intent(
    decision: InitialLearningWorkModeDecision | None,
    *,
    active_requirement: LearningRequirementSheet | None,
    source_requested: bool,
    user_message: str,
) -> InitialLearningWorkModeDecision | None:
    if decision is None or (decision.route == "learning_intake" and active_requirement is None):
        return decision
    if decision.route == "learning_intake" and active_requirement is not None:
        return decision.model_copy(
            update={
                "work_mode": active_requirement.work_mode or decision.work_mode,
                "granularity": active_requirement.granularity or decision.granularity,
                "topic": active_requirement.learning_goal or decision.topic,
            }
        )
    if not source_requested:
        return decision
    return decision.model_copy(
        update={
            "route": "learning_intake",
            "work_mode": active_requirement.work_mode if active_requirement and active_requirement.work_mode else "unknown",
            "granularity": (
                active_requirement.granularity if active_requirement and active_requirement.granularity else "unclear"
            ),
            "topic": active_requirement.learning_goal if active_requirement else user_message.strip(),
            "reason": decision.reason or "当前存在持续学习需求或明确资料意图。",
        }
    )


def _decision_from_refinement(
    refinement: LearningRequirementRefinementOutcome,
) -> InitialLearningWorkModeDecision:
    requirements = refinement.active_requirement_sheet
    return InitialLearningWorkModeDecision(
        route="learning_intake",
        work_mode=requirements.work_mode if requirements and requirements.work_mode else "unknown",
        granularity=requirements.granularity if requirements and requirements.granularity else "unclear",
        topic=requirements.learning_goal if requirements else "",
        reason="初始分类不可用，使用隐藏需求判断结果继续资料优先链路。",
        next_question=refinement.learning_clarification.next_question,
        guided_discovery_reply=refinement.chatbot_message,
    )


def _initial_learning_context(requirement: LearningRequirementSheet | None) -> str:
    if requirement is None:
        return ""
    return "\n".join(
        part
        for part in [
            f"当前学习目标：{requirement.learning_goal}" if requirement.learning_goal else "",
            f"当前待确认：{'；'.join(requirement.current_questions)}" if requirement.current_questions else "",
            f"当前工作模式：{requirement.work_mode}" if requirement.work_mode else "",
        ]
        if part
    )


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""
