from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import (
    BoardWorkflow,
    InitialLearningGranularity,
    InitialLearningWorkMode,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)


GuidedRequirementStrategy = Literal[
    "none",
    "starting_point",
    "light_self_report",
    "recent_experience",
    "known_unknown",
    "mode_split",
    "scenario",
    "goal_output",
    "stuck_point",
    "choice_cards",
    "domain_map",
    "recommended_entry",
    "implicit_observation",
]

GuidedRequirementOptionKind = Literal["level_profile", "learning_entry"]
LearningLevelEvidenceSource = Literal[
    "none",
    "user_statement",
    "level_profile_choice",
    "existing_requirement",
]


class GuidedRequirementEntryPoint(BaseModel):
    selection_key: str = Field(default="", max_length=32)
    option_kind: GuidedRequirementOptionKind = "learning_entry"
    level_profile: str = Field(default="", max_length=500)
    label: str = Field(max_length=160)
    why_it_matters: str = Field(default="", max_length=360)
    best_for: str = Field(default="", max_length=240)


class BlankBoardRequirementTurn(BaseModel):
    route: Literal["ordinary_chat", "requirement_refining"] = "ordinary_chat"
    chatbot_message: str = Field(default="", max_length=2400)
    work_mode: InitialLearningWorkMode = "unknown"
    granularity: InitialLearningGranularity = "unclear"
    learning_goal: str = Field(default="", max_length=500)
    current_level: str = Field(default="", max_length=500)
    current_level_source: LearningLevelEvidenceSource = "none"
    current_level_evidence: str = Field(default="", max_length=500)
    known_background: str = Field(default="", max_length=700)
    target_scenario: str = Field(default="", max_length=500)
    target_depth: str = Field(default="", max_length=400)
    output_preference: str = Field(default="", max_length=400)
    boundary: str = Field(default="", max_length=500)
    ready_for_board: bool = False
    missing_items: list[str] = Field(default_factory=list, max_length=5)
    next_question: str = Field(default="", max_length=600)
    guidance_strategy: GuidedRequirementStrategy = "none"
    learning_map_summary: str = Field(default="", max_length=900)
    entry_point_options: list[GuidedRequirementEntryPoint] = Field(default_factory=list, max_length=6)
    recommended_entry_point: str = Field(default="", max_length=300)
    reason_for_recommendation: str = Field(default="", max_length=600)
    learner_profile_inference: str = Field(default="", max_length=700)


class BlankBoardRequirementRefinement(BaseModel):
    route: Literal["ordinary_chat", "requirement_refining"] = "ordinary_chat"
    chatbot_message: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    summary: str = ""
    board_workflow: BoardWorkflow = "generate_from_scratch"
    work_mode: InitialLearningWorkMode = "unknown"
    granularity: InitialLearningGranularity = "unclear"
    learning_goal: str = ""
    current_level: str = ""
    current_level_source: LearningLevelEvidenceSource = "none"
    current_level_evidence: str = ""
    target_scenario: str = ""
    known_background: str = ""
    target_depth: str = ""
    output_preference: str = ""
    boundary: str = ""
    board_scope: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    learning_need_checklist: list[str] = Field(default_factory=list)
    key_facts: list[LearningRequirementKeyFact] = Field(default_factory=list)
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    guidance_strategy: GuidedRequirementStrategy = "none"
    learning_map_summary: str = ""
    entry_point_options: list[GuidedRequirementEntryPoint] = Field(default_factory=list)
    recommended_entry_point: str = ""
    reason_for_recommendation: str = ""
    learner_profile_inference: str = ""
    missing_items: list[str] = Field(default_factory=list)
    next_question: str = ""
    recommended_teaching_plan_summary: str = ""
    ready_for_board: bool = False


def refinement_from_turn(turn: BlankBoardRequirementTurn) -> BlankBoardRequirementRefinement:
    summary = _turn_summary(turn)
    return BlankBoardRequirementRefinement(
        route=turn.route,
        chatbot_message=turn.chatbot_message,
        progress=_turn_progress(turn),
        summary=summary,
        work_mode=turn.work_mode,
        granularity=turn.granularity,
        learning_goal=turn.learning_goal,
        current_level=turn.current_level,
        current_level_source=turn.current_level_source,
        current_level_evidence=turn.current_level_evidence,
        known_background=turn.known_background,
        target_scenario=turn.target_scenario,
        target_depth=turn.target_depth,
        output_preference=turn.output_preference,
        boundary=turn.boundary,
        key_facts=_turn_key_facts(turn),
        guidance_strategy=turn.guidance_strategy,
        learning_map_summary=turn.learning_map_summary,
        entry_point_options=turn.entry_point_options,
        recommended_entry_point=turn.recommended_entry_point,
        reason_for_recommendation=turn.reason_for_recommendation,
        learner_profile_inference=turn.learner_profile_inference,
        missing_items=turn.missing_items,
        next_question=turn.next_question,
        recommended_teaching_plan_summary=summary,
        ready_for_board=turn.ready_for_board,
    )


def compact_requirement_state(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    compact: dict[str, Any] = {}
    for source, target in [
        ("learning_goal", "learning_goal"),
        ("level", "current_level"),
        ("known_background", "known_background"),
        ("target_depth", "target_depth"),
        ("output_preference", "output_preference"),
        ("boundary", "boundary"),
        ("success_criteria", "target_scenario"),
        ("work_mode", "work_mode"),
        ("granularity", "granularity"),
    ]:
        value = raw.get(source)
        if isinstance(value, str) and value.strip() and not _is_default_placeholder(value):
            compact[target] = value.strip()
    questions = raw.get("current_questions")
    if isinstance(questions, list):
        next_question = next((str(item).strip() for item in questions if str(item).strip()), "")
        if next_question:
            compact["next_question"] = next_question
    return compact or None


def merge_turn_with_existing_requirement(
    turn: BlankBoardRequirementTurn,
    existing: dict[str, Any] | None,
) -> BlankBoardRequirementTurn:
    """Keep established requirement facts when a compact model turn leaves them blank."""

    if not existing:
        return turn
    payload = turn.model_dump(mode="python")
    for key in (
        "learning_goal",
        "current_level",
        "known_background",
        "target_scenario",
        "target_depth",
        "output_preference",
        "boundary",
    ):
        previous = existing.get(key)
        current = payload.get(key)
        if isinstance(previous, str) and previous.strip() and not str(current or "").strip():
            payload[key] = previous.strip()
    previous_work_mode = existing.get("work_mode")
    if payload["work_mode"] == "unknown" and previous_work_mode in {
        "knowledge_board",
        "narrow_topic",
        "practice_artifact",
    }:
        payload["work_mode"] = previous_work_mode
    previous_granularity = existing.get("granularity")
    if payload["granularity"] == "unclear" and previous_granularity in {
        "single_knowledge_point",
        "source_chapter",
        "practice_artifact",
        "broad_topic",
    }:
        payload["granularity"] = previous_granularity
    return BlankBoardRequirementTurn.model_validate(payload)


def compact_clarification_state(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    compact: dict[str, Any] = {}
    for key in [
        "progress",
        "summary",
        "next_question",
        "ready_for_board",
        "work_mode",
        "granularity",
        "current_level_source",
        "current_level_evidence",
        "pending_level_profiles",
    ]:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            compact[key] = value.strip()
        elif isinstance(value, (int, bool)):
            compact[key] = value
    missing_items = raw.get("missing_items")
    if isinstance(missing_items, list):
        compact["missing_items"] = [str(item).strip() for item in missing_items if str(item).strip()][:5]
    return compact or None


def _turn_progress(turn: BlankBoardRequirementTurn) -> int:
    if turn.route == "ordinary_chat":
        return 0
    if turn.ready_for_board:
        return 100
    if turn.work_mode == "practice_artifact":
        clear_count = sum(
            1
            for value in (turn.learning_goal, turn.current_level, turn.target_scenario)
            if value.strip()
        )
        return min(90, 25 + clear_count * 20)
    if turn.work_mode == "knowledge_board":
        return 45 if turn.learning_goal.strip() else 25
    return 10


def _turn_summary(turn: BlankBoardRequirementTurn) -> str:
    if turn.route == "ordinary_chat":
        return ""
    return next(
        (
            value.strip()
            for value in (
                turn.learning_map_summary,
                turn.learning_goal,
                turn.recommended_entry_point,
                turn.next_question,
            )
            if value.strip()
        ),
        "",
    )


def _turn_key_facts(turn: BlankBoardRequirementTurn) -> list[LearningRequirementKeyFact]:
    candidates = [
        ("用户想学的内容", turn.learning_goal, "learning"),
        ("当前水平", turn.current_level, "level"),
        ("已知背景", turn.known_background, "level"),
        ("面向场景", turn.target_scenario, "scenario"),
    ]
    return [
        LearningRequirementKeyFact(
            label=label,
            value=value.strip(),
            evidence=value.strip(),
            category=category,
        )
        for label, value, category in candidates
        if value.strip()
    ][:5]


def _is_default_placeholder(value: str) -> bool:
    return any(
        marker in value.strip()
        for marker in (
            "先澄清用户具体想学什么",
            "待确认用户",
            "用户背景尚未明确",
            "根据用户水平和目标场景",
            "根据用户目标、资料结构和交互意图",
            "优先围绕当前主题展开",
        )
    )
