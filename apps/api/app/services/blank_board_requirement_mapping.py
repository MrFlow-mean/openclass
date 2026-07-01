from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    InitialLearningGranularity,
    InitialLearningWorkMode,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
    Lesson,
)
from app.services.openai_course_ai import BlankBoardRequirementRefinement


@dataclass(frozen=True)
class BlankBoardRequirementState:
    requirement: LearningRequirementSheet
    clarification: LearningClarificationStatus
    work_mode: InitialLearningWorkMode
    granularity: InitialLearningGranularity
    ready_for_board: bool
    missing_items: list[str]


def build_blank_board_requirement_state(
    *,
    lesson: Lesson,
    base_requirement: LearningRequirementSheet,
    result: BlankBoardRequirementRefinement,
) -> BlankBoardRequirementState:
    work_mode = normalize_work_mode(result)
    granularity = normalize_granularity(result, work_mode)
    ready_for_board = is_core_ready(result, work_mode, granularity)
    missing_items = [] if ready_for_board else merged_missing_items(result, work_mode, granularity)
    requirement = build_requirement_sheet(
        lesson=lesson,
        base_requirement=base_requirement,
        result=result,
        work_mode=work_mode,
        granularity=granularity,
        ready_for_board=ready_for_board,
        missing_items=missing_items,
    )
    clarification = build_clarification(
        result=result,
        requirement=requirement,
        work_mode=work_mode,
        granularity=granularity,
        ready_for_board=ready_for_board,
        missing_items=missing_items,
    )
    return BlankBoardRequirementState(
        requirement=requirement,
        clarification=clarification,
        work_mode=work_mode,
        granularity=granularity,
        ready_for_board=ready_for_board,
        missing_items=missing_items,
    )


def normalize_work_mode(result: BlankBoardRequirementRefinement) -> InitialLearningWorkMode:
    if result.work_mode in {"knowledge_board", "narrow_topic"}:
        return "knowledge_board"
    if result.work_mode == "practice_artifact":
        return "practice_artifact"
    if _has_text(result.learning_goal) or result.granularity == "broad_topic":
        return "knowledge_board"
    return "unknown"


def normalize_granularity(
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


def is_core_ready(
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


def merged_missing_items(
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


def build_requirement_sheet(
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
    novice_intro = is_novice_intro_knowledge_request(result, work_mode)
    learning_goal = _first_text(result.learning_goal, requirement.learning_goal, lesson.title)
    current_questions = [] if ready_for_board else _dedupe_text([_first_text(result.next_question, *missing_items)])
    requirement.theme = learning_goal
    requirement.learning_goal = learning_goal
    requirement.level = _first_text(
        result.current_level,
        "零基础纯新手" if novice_intro else "",
        requirement.level,
    )
    requirement.known_background = _first_text(result.known_background, requirement.known_background)
    requirement.current_questions = current_questions
    requirement.learning_need_checklist = []
    requirement.target_depth = _first_text(
        result.target_depth,
        "入门了解 / 建立领域地图" if novice_intro else "",
        requirement.target_depth,
    )
    requirement.output_preference = _first_text(result.output_preference, requirement.output_preference)
    requirement.boundary = _first_text(result.boundary, requirement.boundary)
    requirement.board_scope = []
    requirement.success_criteria = _first_text(result.target_scenario if work_mode == "practice_artifact" else "")
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


def build_clarification(
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
        key_facts=merge_key_facts(result, work_mode),
        checklist=merge_checklist(result, work_mode, granularity),
        next_question="" if ready_for_board else _first_text(result.next_question),
        ready_for_board=ready_for_board,
        work_mode=work_mode,
        granularity=granularity,
    )


def merge_key_facts(
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


def merge_checklist(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> list[LearningRequirementChecklistItem]:
    novice_intro = is_novice_intro_knowledge_request(result, work_mode)
    checklist = [
        item
        for item in result.checklist
        if _has_text(item.title) and not (novice_intro and _is_scenario_or_external_goal_text(item.title))
    ]
    existing_titles = {item.title for item in checklist}
    for item in core_checklist(result, work_mode, granularity):
        if item.title not in existing_titles:
            checklist.append(item)
            existing_titles.add(item.title)
    return checklist[:5]


def core_checklist(
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


def is_novice_intro_knowledge_request(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
) -> bool:
    if work_mode != "knowledge_board":
        return False
    text = " ".join(
        [
            result.current_level,
            result.known_background,
            result.learner_profile_inference,
            result.summary,
            result.learning_goal,
        ]
    )
    return _has_novice_intro_signal(text)


def is_novice_intro_broad_topic(
    result: BlankBoardRequirementRefinement,
    work_mode: InitialLearningWorkMode,
    granularity: InitialLearningGranularity,
) -> bool:
    return granularity == "broad_topic" and is_novice_intro_knowledge_request(result, work_mode)


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


def _has_novice_intro_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(
        keyword in compact
        for keyword in [
            "纯新手",
            "零基础",
            "完全新手",
            "完全零基础",
            "纯外行",
            "完全外行",
            "入门了解",
            "新手入门",
            "从零开始",
            "刚开始入门",
            "先了解",
            "感兴趣想学",
        ]
    )


def _is_scenario_or_external_goal_text(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    if "无明确应用场景" in compact or "入门了解" in compact:
        return False
    return any(
        keyword in compact
        for keyword in [
            "应用场景",
            "使用场景",
            "学习目的",
            "目的或场景",
            "目标场景",
            "考试",
            "面试",
            "工作",
            "赚钱",
            "项目",
            "现实产出",
        ]
    )


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
