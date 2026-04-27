from __future__ import annotations

from app.services.ai_workflow import (
    WorkflowState,
    _available_reference_resources,
    _clarification_questions_for_status,
    _draft_requirements,
    _learning_clarification_status,
    _learning_need_checklist,
    _should_ask_brief_clarification,
    _should_use_fast_pm_path,
    _should_use_resource_followup_context,
    _status_with_resource_context_default,
)
from app.services.course_runtime import normalize_requirements
from app.services.openai_course_ai import openai_course_ai


def run_pm(state: WorkflowState) -> WorkflowState:
    lesson = state["lesson"]
    request = state["request"]
    draft_requirements = _draft_requirements(lesson, request)
    draft_status = _learning_clarification_status(
        lesson=lesson,
        request=request,
        requirements=draft_requirements,
    )
    if _should_use_resource_followup_context(course_package=state["course_package"], lesson=lesson, request=request):
        draft_status = _status_with_resource_context_default(
            draft_status,
            resource_count=len(_available_reference_resources(state["course_package"], lesson)),
        )

    if request.interaction_mode == "direct_edit":
        return {
            "learning_requirement_sheet": draft_requirements,
            "learning_clarification": draft_status,
            "needs_clarification": False,
            "clarification_questions": [],
            "pm_reason": "用户通过选区编辑入口直接提交文档修改指令，跳过 PM 澄清。",
        }

    if _should_use_fast_pm_path(lesson=lesson, request=request, status=draft_status):
        needs_clarification = _should_ask_brief_clarification(request=request, status=draft_status)
        questions = _clarification_questions_for_status(draft_status) if needs_clarification else []
        return {
            "learning_requirement_sheet": draft_requirements,
            "learning_clarification": draft_status,
            "needs_clarification": needs_clarification,
            "clarification_questions": questions[:1],
            "pm_reason": "优先走极速澄清策略：能直接讲就不追问，只有明显会讲偏时才补一句。",
        }

    assessment = openai_course_ai.assess_learning_requirements(
        lesson_title=lesson.title,
        lesson_summary=lesson.summary,
        lesson_tags=lesson.tags,
        document_outline=draft_requirements.board_scope,
        user_message=request.message,
        selection_excerpt=request.selection.excerpt if request.selection else None,
        conversation=[turn.model_dump(mode="json") for turn in request.conversation],
    )
    if assessment is not None:
        requirements = normalize_requirements(
            assessment.learning_requirement_sheet,
            lesson_title=lesson.title,
            document=lesson.board_document,
        )
        requirements.learning_need_checklist = _learning_need_checklist(lesson, request, requirements)
        status = _learning_clarification_status(
            lesson=lesson,
            request=request,
            requirements=requirements,
        )
        needs_clarification = not assessment.ready
        if status.progress < 35 and not status.forced_start:
            needs_clarification = True
        if status.progress >= 80 or status.forced_start:
            needs_clarification = False
        clarification_questions = assessment.clarification_questions[:3]
        if needs_clarification and not clarification_questions:
            clarification_questions = _clarification_questions_for_status(status)
        return {
            "learning_requirement_sheet": requirements,
            "learning_clarification": status,
            "needs_clarification": needs_clarification,
            "clarification_questions": clarification_questions,
            "pm_reason": assessment.reason,
        }

    needs_clarification = _should_ask_brief_clarification(request=request, status=draft_status)
    questions = _clarification_questions_for_status(draft_status) if needs_clarification else []
    return {
        "learning_requirement_sheet": draft_requirements,
        "learning_clarification": draft_status,
        "needs_clarification": needs_clarification,
        "clarification_questions": questions[:1],
        "pm_reason": draft_status.reason,
    }
