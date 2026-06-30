from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.models import (
    InitialLearningGranularity,
    InitialLearningWorkMode,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
    Lesson,
)
from app.services.board_document_sensor import BoardDocumentSensorReading
from app.services.course_runtime import active_task_requirements
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.lesson_factory import build_requirements
from app.services.openai_course_ai import BlankBoardRequirementRefinement, openai_course_ai


LearningRequirementRefinementRoute = Literal["ordinary_chat", "requirement_refining"]


@dataclass(frozen=True)
class LearningRequirementRefinementOutcome:
    route: LearningRequirementRefinementRoute
    chatbot_message: str
    active_requirement_sheet: LearningRequirementSheet | None
    learning_clarification: LearningClarificationStatus
    history_stamp: RequirementHistoryStamp
    history_operations: list[dict[str, Any]]
    guidance_metadata: dict[str, Any]
    changed: bool


def refine_blank_board_requirement(
    *,
    owner_user_id: str,
    lesson: Lesson,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    user_message: str,
    history_state: dict[str, Any] | None,
) -> LearningRequirementRefinementOutcome | None:
    active_requirement = _active_requirement_from_state(lesson, history_state)
    active_clarification = _active_clarification_from_state(history_state)
    base_requirement = active_requirement or build_requirements(lesson.title)
    result = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
    )
    if not isinstance(result, BlankBoardRequirementRefinement):
        return None
    result, quality_repaired = _repair_guided_reply_if_needed(
        result=result,
        board_document_state=board_document_state,
        conversation_summary=conversation_summary,
        user_message=user_message,
        base_requirement=base_requirement,
        active_clarification=active_clarification,
    )

    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=owner_user_id,
        lesson_id=lesson.id,
        state=history_state,
    )
    if result.route == "ordinary_chat":
        return LearningRequirementRefinementOutcome(
            route="ordinary_chat",
            chatbot_message=_first_text(result.chatbot_message),
            active_requirement_sheet=active_requirement,
            learning_clarification=active_clarification or _basic_chat_clarification(),
            history_stamp=recorder.current_stamp(),
            history_operations=[],
            guidance_metadata={},
            changed=False,
        )

    normalized_work_mode = _normalize_work_mode(result)
    normalized_granularity = _normalize_granularity(result, normalized_work_mode)
    ready_for_board = _is_core_ready(result, normalized_work_mode, normalized_granularity)
    missing_items = _merged_missing_items(result, normalized_work_mode, normalized_granularity)
    if ready_for_board:
        missing_items = []
    requirement = _build_requirement_sheet(
        lesson=lesson,
        base_requirement=base_requirement,
        result=result,
        work_mode=normalized_work_mode,
        granularity=normalized_granularity,
        ready_for_board=ready_for_board,
        missing_items=missing_items,
    )
    clarification = _build_clarification(
        result=result,
        requirement=requirement,
        work_mode=normalized_work_mode,
        granularity=normalized_granularity,
        ready_for_board=ready_for_board,
        missing_items=missing_items,
    )
    stamp = recorder.record_update(
        requirements=requirement,
        clarification=clarification,
        change_summary=clarification.summary or "更新空白板书学习需求清单。",
        metadata=_guidance_metadata(result, quality_repaired=quality_repaired),
    )
    return LearningRequirementRefinementOutcome(
        route="requirement_refining",
        chatbot_message=_first_text(result.chatbot_message, result.next_question, result.summary),
        active_requirement_sheet=requirement,
        learning_clarification=clarification,
        history_stamp=stamp,
        history_operations=list(recorder.operations),
        guidance_metadata=_guidance_metadata(result, quality_repaired=quality_repaired),
        changed=bool(recorder.operations),
    )


def _active_requirement_from_state(
    lesson: Lesson,
    history_state: dict[str, Any] | None,
) -> LearningRequirementSheet | None:
    from_history = _model_from_history_json(history_state, "latest_sheet_json", LearningRequirementSheet)
    if from_history is not None:
        return from_history
    if history_state and history_state.get("status") in {"collecting", "ready"}:
        return active_task_requirements(lesson)
    return None


def _active_clarification_from_state(
    history_state: dict[str, Any] | None,
) -> LearningClarificationStatus | None:
    return _model_from_history_json(history_state, "latest_clarification_json", LearningClarificationStatus)


def _model_from_history_json(
    history_state: dict[str, Any] | None,
    key: str,
    schema: type[LearningRequirementSheet] | type[LearningClarificationStatus],
):
    if not history_state:
        return None
    raw = history_state.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return schema.model_validate_json(raw)
    except Exception:
        return None


def _basic_chat_clarification() -> LearningClarificationStatus:
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


def _normalize_work_mode(result: BlankBoardRequirementRefinement) -> InitialLearningWorkMode:
    if result.work_mode in {"knowledge_board", "narrow_topic"}:
        return "knowledge_board"
    if result.work_mode == "practice_artifact":
        return "practice_artifact"
    if _has_text(result.learning_goal) or result.granularity == "broad_topic":
        return "knowledge_board"
    return "unknown"


def _normalize_granularity(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
) -> InitialLearningGranularity:
    if work_mode == "practice_artifact":
        return "practice_artifact"
    if result.granularity in {"single_knowledge_point", "broad_topic"}:
        return result.granularity
    if work_mode == "knowledge_board" and _has_text(result.learning_goal):
        return "broad_topic"
    return "unclear"


def _is_core_ready(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> bool:
    if not result.ready_for_board:
        return False
    if work_mode == "knowledge_board":
        return _has_text(result.learning_goal) and granularity == "single_knowledge_point"
    if work_mode == "practice_artifact":
        return (
            _has_text(result.learning_goal)
            and _has_text(result.current_level)
            and _has_text(result.target_scenario)
        )
    return False


def _merged_missing_items(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> list[str]:
    if work_mode == "knowledge_board":
        missing: list[str] = []
        if not _has_text(result.learning_goal):
            missing.append("用户想学的内容")
        if granularity != "single_knowledge_point":
            missing.append("用户想学的内容需要收敛到具体知识点")
    elif work_mode == "practice_artifact":
        missing = []
        if not _has_text(result.learning_goal):
            missing.append("用户想练的内容")
        if not _has_text(result.current_level):
            missing.append("当前水平")
        if not _has_text(result.target_scenario):
            missing.append("面向场景")
    else:
        missing = ["学习类型"]
    return _dedupe_text(missing)


def _build_requirement_sheet(
    *,
    lesson: Lesson,
    base_requirement: LearningRequirementSheet,
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
    ready_for_board: bool,
    missing_items: list[str],
) -> LearningRequirementSheet:
    requirement = base_requirement.model_copy(deep=True)
    learning_goal = _first_text(result.learning_goal, requirement.learning_goal, lesson.title)
    current_questions = [] if ready_for_board else _dedupe_text([_first_text(result.next_question, *missing_items)])
    requirement.theme = learning_goal
    requirement.learning_goal = learning_goal
    requirement.level = _first_text(result.current_level, requirement.level)
    requirement.known_background = _first_text(result.known_background, requirement.known_background)
    requirement.current_questions = current_questions
    requirement.learning_need_checklist = _dedupe_text(
        [
            *result.learning_need_checklist,
            *[item.title for item in _core_checklist(result, work_mode, granularity)],
        ]
    )
    requirement.target_depth = _first_text(result.target_depth, requirement.target_depth)
    requirement.output_preference = _first_text(result.output_preference, requirement.output_preference)
    requirement.boundary = _first_text(result.boundary, requirement.boundary)
    requirement.board_scope = _dedupe_text(result.board_scope) or list(requirement.board_scope)
    requirement.success_criteria = _first_text(
        result.success_criteria,
        result.target_scenario if work_mode == "practice_artifact" else "",
        requirement.success_criteria,
    )
    requirement.risk_notes = missing_items
    requirement.target_location = None
    requirement.location_status = "missing"
    requirement.action_type = None
    requirement.action_instruction = ""
    requirement.location_clarification_question = ""
    requirement.interaction_rule_draft = None
    requirement.work_mode = work_mode
    requirement.granularity = granularity
    return requirement


def _build_clarification(
    *,
    result: BlankBoardRequirementRefinement,
    requirement: LearningRequirementSheet,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
    ready_for_board: bool,
    missing_items: list[str],
) -> LearningClarificationStatus:
    summary = _first_text(result.summary, result.recommended_teaching_plan_summary, requirement.learning_goal)
    return LearningClarificationStatus(
        progress=100 if ready_for_board else min(result.progress, 99),
        label="ready" if ready_for_board else "collecting",
        reason=summary,
        missing_items=missing_items,
        can_start=ready_for_board,
        forced_start=False,
        summary=summary,
        key_facts=_merge_key_facts(result, work_mode),
        checklist=_merge_checklist(result, work_mode, granularity),
        next_question="" if ready_for_board else _first_text(result.next_question),
        ready_for_board=ready_for_board,
        work_mode=work_mode,
        granularity=granularity,
    )


def _merge_key_facts(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
) -> list[LearningRequirementKeyFact]:
    facts = [fact for fact in result.key_facts if _has_text(fact.value)]
    facts = _append_fact(
        facts,
        label="用户想学的内容",
        value=result.learning_goal,
        category="learning",
    )
    if work_mode == "practice_artifact":
        facts = _append_fact(
            facts,
            label="当前水平",
            value=result.current_level,
            category="level",
        )
        facts = _append_fact(
            facts,
            label="面向场景",
            value=result.target_scenario,
            category="scenario",
        )
    return facts[:5]


def _append_fact(
    facts: list[LearningRequirementKeyFact],
    *,
    label: str,
    value: str,
    category: str,
) -> list[LearningRequirementKeyFact]:
    if not _has_text(value):
        return facts
    if any(fact.label == label and fact.value.strip() == value.strip() for fact in facts):
        return facts
    return [
        *facts,
        LearningRequirementKeyFact(
            label=label,
            value=value.strip(),
            evidence=value.strip(),
            category=category,
        ),
    ]


def _merge_checklist(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> list[LearningRequirementChecklistItem]:
    checklist = [
        item
        for item in result.checklist
        if _has_text(item.title)
    ]
    existing_titles = {item.title for item in checklist}
    for item in _core_checklist(result, work_mode, granularity):
        if item.title not in existing_titles:
            checklist.append(item)
            existing_titles.add(item.title)
    return checklist[:5]


def _core_checklist(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> list[LearningRequirementChecklistItem]:
    if work_mode == "knowledge_board":
        return [
            LearningRequirementChecklistItem(
                title="用户想学的内容",
                is_clear=_has_text(result.learning_goal) and granularity == "single_knowledge_point",
                evidence=result.learning_goal.strip(),
            )
        ]
    if work_mode == "practice_artifact":
        return [
            LearningRequirementChecklistItem(
                title="用户想练的内容",
                is_clear=_has_text(result.learning_goal),
                evidence=result.learning_goal.strip(),
            ),
            LearningRequirementChecklistItem(
                title="当前水平",
                is_clear=_has_text(result.current_level),
                evidence=result.current_level.strip(),
            ),
            LearningRequirementChecklistItem(
                title="面向场景",
                is_clear=_has_text(result.target_scenario),
                evidence=result.target_scenario.strip(),
            ),
        ]
    return [
        LearningRequirementChecklistItem(
            title="学习类型",
            is_clear=False,
            evidence="",
        )
    ]


def _repair_guided_reply_if_needed(
    *,
    result: BlankBoardRequirementRefinement,
    board_document_state: BoardDocumentSensorReading,
    conversation_summary: str,
    user_message: str,
    base_requirement: LearningRequirementSheet,
    active_clarification: LearningClarificationStatus | None,
) -> tuple[BlankBoardRequirementRefinement, bool]:
    repair_reason = _guided_reply_repair_reason(result)
    if not repair_reason:
        return result, False
    repaired = openai_course_ai.generate_blank_board_requirement_refinement(
        board_document_state=board_document_state.model_context(),
        conversation_summary=conversation_summary,
        user_message=user_message,
        existing_requirement_sheet=base_requirement.model_dump(mode="json"),
        existing_clarification=active_clarification.model_dump(mode="json") if active_clarification else None,
        quality_repair_context={
            "repair_reason": repair_reason,
            "previous_output": result.model_dump(mode="json"),
            "must_preserve": [
                "route",
                "work_mode",
                "granularity",
                "learning_goal",
                "current_level",
                "target_scenario",
                "known_background",
                "summary",
                "ready_for_board",
            ],
            "must_improve": [
                "chatbot_message",
                "guidance_strategy",
                "learning_map_summary",
                "entry_point_options",
                "recommended_entry_point",
                "reason_for_recommendation",
                "learner_profile_inference",
                "next_question",
                "current_level_or_known_background_question",
            ],
        },
    )
    if not isinstance(repaired, BlankBoardRequirementRefinement):
        return result, False
    return _merge_guidance_repair(result, repaired), True


def _guided_reply_repair_reason(result: BlankBoardRequirementRefinement) -> str:
    if not _is_broad_knowledge_refinement(result):
        return ""
    reasons: list[str] = []
    options = [option for option in result.entry_point_options if _has_text(option.label)]
    message = result.chatbot_message.strip()
    if len(message) < 160:
        reasons.append("chatbot_message 太短，像追问而不是学习地图引导。")
    if not _has_text(result.learning_map_summary):
        reasons.append("缺少 learning_map_summary。")
    if len(options) < 2:
        reasons.append("entry_point_options 少于 2 个。")
    if not _has_text(result.recommended_entry_point):
        reasons.append("缺少 recommended_entry_point。")
    if not _has_text(result.reason_for_recommendation):
        reasons.append("缺少 reason_for_recommendation。")
    visible_option_count = sum(1 for option in options if option.label.strip() in message)
    if options and visible_option_count < min(2, len(options)):
        reasons.append("chatbot_message 没有呈现足够入口选项。")
    if _has_text(result.recommended_entry_point) and result.recommended_entry_point.strip() not in message:
        reasons.append("chatbot_message 没有呈现推荐入口。")
    if (
        _has_text(result.recommended_entry_point)
        and not _has_starting_level_context(result)
        and not _asks_about_starting_level(_first_text(result.next_question, result.chatbot_message))
    ):
        reasons.append("已经推荐入口，但没有追问用户当前水平、已会/未会或最近学到哪里。")
    if "？" not in message and "?" not in message:
        reasons.append("chatbot_message 没有一个关键问题。")
    return " ".join(reasons)


def _is_broad_knowledge_refinement(result: BlankBoardRequirementRefinement) -> bool:
    if result.route != "requirement_refining" or result.ready_for_board:
        return False
    if result.work_mode == "practice_artifact" or result.granularity == "practice_artifact":
        return False
    return (
        result.granularity == "broad_topic"
        or result.work_mode in {"knowledge_board", "narrow_topic"}
        or _has_text(result.learning_goal)
    )


def _merge_guidance_repair(
    original: BlankBoardRequirementRefinement,
    repaired: BlankBoardRequirementRefinement,
) -> BlankBoardRequirementRefinement:
    data = original.model_dump(mode="json")
    for field_name in [
        "chatbot_message",
        "guidance_strategy",
        "learning_map_summary",
        "entry_point_options",
        "recommended_entry_point",
        "reason_for_recommendation",
        "learner_profile_inference",
        "next_question",
        "learning_need_checklist",
        "board_scope",
    ]:
        value = getattr(repaired, field_name)
        if isinstance(value, str):
            if _has_text(value):
                data[field_name] = value
            continue
        if value:
            data[field_name] = value
    return BlankBoardRequirementRefinement.model_validate(data)


def _has_starting_level_context(result: BlankBoardRequirementRefinement) -> bool:
    return any(
        _has_text(value)
        for value in [
            result.current_level,
            result.known_background,
            result.learner_profile_inference,
        ]
    )


def _asks_about_starting_level(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(
        keyword in compact
        for keyword in [
            "当前水平",
            "现在水平",
            "目前水平",
            "什么水平",
            "学到哪里",
            "最近学",
            "已经会",
            "已会",
            "还没学",
            "没学过",
            "基础",
            "掌握",
            "接触过",
            "了解过",
        ]
    )


def _guidance_metadata(
    result: BlankBoardRequirementRefinement,
    *,
    quality_repaired: bool = False,
) -> dict[str, Any]:
    return {
        "guidance_strategy": result.guidance_strategy,
        "learning_map_summary": result.learning_map_summary,
        "entry_point_options": [
            option.model_dump(mode="json")
            for option in result.entry_point_options
            if _has_text(option.label)
        ],
        "recommended_entry_point": result.recommended_entry_point,
        "reason_for_recommendation": result.reason_for_recommendation,
        "learner_profile_inference": result.learner_profile_inference,
        "quality_repaired": quality_repaired,
    }


def _first_text(*values: str) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _has_text(value: str) -> bool:
    return bool((value or "").strip())


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = (value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
