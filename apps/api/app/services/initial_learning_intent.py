from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import (
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
)
from app.services.openai_course_ai import openai_course_ai


LearningMode = Literal["learn_concept", "practice_activity", "undecided"]
TargetGranularity = Literal["specific_concept", "broad_domain", "ambiguous"]
InitialLearningNextAction = Literal[
    "freeze_minimal_and_generate_board",
    "ask_specific_concept",
    "collect_practice_requirements",
    "ask_learning_mode",
]


PRACTICE_ACTIVITY_PATTERN = re.compile(
    r"(练习|训练|测验|测试|做题|出题|题目|问答|对话|角色|互动|纠错|批改|评估|巩固|提升)"
)
LOW_SUBSTANCE_CONTINUATION_PATTERN = re.compile(
    r"^(你好|您好|hi|hello|可以|好的|好|行|嗯)$|(?:都行|随便|继续|看你发挥|你来|按你来)",
    re.IGNORECASE,
)
KNOWLEDGE_QUESTION_PATTERN = re.compile(
    r"(?:什么是|请解释|解释一下|解释|讲解一下|讲解|说明一下|说明|帮我理解)"
    r"(?P<target>[^，。！？!?；;\n]{1,80})"
)
LEARNING_REQUEST_PATTERN = re.compile(
    r"(?:想要|想|希望|打算|准备)?(?:学习|了解|理解|掌握|研究|学)"
    r"(?:一下|下)?(?:关于|有关|围绕)?(?P<target>[^，。！？!?；;\n]{1,80})"
)
REQUIREMENT_DETAIL_PATTERN = re.compile(
    r"(目标|水平|基础|场景|用途|目的|输出|要求|已经|完整|清楚|为了|用于|用来|面向|准备)"
)


class InitialLearningIntentDecision(BaseModel):
    learning_mode: LearningMode = "undecided"
    target_granularity: TargetGranularity = "ambiguous"
    next_action: InitialLearningNextAction = "ask_learning_mode"
    trace_reason: str = Field(default="")

    @field_validator("trace_reason", mode="before")
    @classmethod
    def _coerce_trace_reason(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _align_action(self) -> "InitialLearningIntentDecision":
        if self.next_action == "freeze_minimal_and_generate_board":
            self.learning_mode = "learn_concept"
            self.target_granularity = "specific_concept"
        elif self.next_action == "ask_specific_concept":
            self.learning_mode = "learn_concept"
            if self.target_granularity == "specific_concept":
                self.target_granularity = "broad_domain"
        elif self.next_action == "collect_practice_requirements":
            self.learning_mode = "practice_activity"
            if self.target_granularity == "specific_concept":
                self.target_granularity = "ambiguous"
        elif self.next_action == "ask_learning_mode":
            self.learning_mode = "undecided"
            self.target_granularity = "ambiguous"
        if not self.trace_reason:
            self.trace_reason = "Initial learning intent gate selected a conservative next step."
        return self


def decide_initial_learning_intent(
    *,
    lesson_title: str,
    existing_summary: str,
    existing_checklist: list[str],
    conversation: list[ConversationTurn],
    user_message: str,
) -> InitialLearningIntentDecision:
    ai_decision = openai_course_ai.generate_initial_learning_intent_decision(
        lesson_title=lesson_title,
        existing_summary=existing_summary,
        existing_checklist=existing_checklist,
        conversation_summary=_conversation_summary(conversation),
        user_message=user_message,
    )
    if isinstance(ai_decision, InitialLearningIntentDecision):
        return ai_decision
    return fallback_initial_learning_intent_decision(user_message)


def build_requirements_from_initial_learning_intent(
    *,
    base: LearningRequirementSheet,
    user_message: str,
    decision: InitialLearningIntentDecision,
    ready_for_board: bool,
) -> LearningRequirementSheet:
    updated = LearningRequirementSheet.model_validate(base.model_dump(mode="json"))
    compact_goal = _compact_text(user_message, limit=240)
    if compact_goal:
        updated.learning_goal = compact_goal
        updated.learning_need_checklist = [compact_goal]
    updated.current_questions = []
    updated.risk_notes = []
    updated.target_location = None
    updated.location_status = "resolved" if ready_for_board else "missing"
    updated.location_clarification_question = ""
    updated.action_type = "generate_board" if ready_for_board else None
    updated.action_instruction = (
        f"围绕用户给出的明确知识目标生成第一版板书：{compact_goal}"
        if ready_for_board and compact_goal
        else ""
    )
    return updated


def build_clarification_from_initial_learning_intent(
    *,
    user_message: str,
    decision: InitialLearningIntentDecision,
    ready_for_board: bool,
) -> LearningClarificationStatus:
    compact_goal = _compact_text(user_message, limit=240)
    if ready_for_board:
        return LearningClarificationStatus(
            progress=100,
            label="知识目标已明确",
            reason=decision.trace_reason,
            missing_items=[],
            can_start=True,
            forced_start=False,
            summary=compact_goal,
            key_facts=[
                LearningRequirementKeyFact(
                    label="学习目标",
                    value=compact_goal,
                    evidence="来自用户本轮输入。",
                    category="learning",
                )
            ]
            if compact_goal
            else [],
            checklist=[
                LearningRequirementChecklistItem(
                    title="明确知识目标",
                    is_clear=True,
                    evidence="用户给出了可以生成第一版板书的明确目标。",
                )
            ],
            next_question="",
            ready_for_board=True,
        )
    if decision.next_action == "ask_specific_concept":
        missing_items = ["具体知识点、问题或范围"]
        next_question = "你具体想弄懂哪一个知识点、问题或范围？"
        label = "继续确认知识目标"
    else:
        missing_items = ["学习形态"]
        next_question = "你是想先学习一个知识内容，还是做练习型教学？"
        label = "继续确认学习形态"
    return LearningClarificationStatus(
        progress=30,
        label=label,
        reason=decision.trace_reason,
        missing_items=missing_items,
        can_start=False,
        forced_start=False,
        summary=compact_goal or decision.trace_reason,
        key_facts=[
            LearningRequirementKeyFact(
                label="用户当前表达",
                value=compact_goal,
                evidence="来自用户本轮输入。",
                category="learning",
            )
        ]
        if compact_goal
        else [],
        checklist=[
            LearningRequirementChecklistItem(
                title=missing_items[0],
                is_clear=False,
                evidence=decision.trace_reason,
            )
        ],
        next_question=next_question,
        ready_for_board=False,
    )


def fallback_initial_learning_intent_decision(user_message: str) -> InitialLearningIntentDecision:
    compact = _compact_text(user_message, limit=240)
    if not compact:
        return InitialLearningIntentDecision(
            learning_mode="undecided",
            target_granularity="ambiguous",
            next_action="ask_learning_mode",
            trace_reason="No substantive learning request was available.",
        )

    if LOW_SUBSTANCE_CONTINUATION_PATTERN.search(compact):
        return InitialLearningIntentDecision(
            learning_mode="practice_activity",
            target_granularity="ambiguous",
            next_action="collect_practice_requirements",
            trace_reason="The fallback gate preserves the existing requirement path for low-information turns.",
        )

    if PRACTICE_ACTIVITY_PATTERN.search(compact):
        return InitialLearningIntentDecision(
            learning_mode="practice_activity",
            target_granularity="ambiguous",
            next_action="collect_practice_requirements",
            trace_reason="The request asks for a practice-like learning activity.",
        )

    if REQUIREMENT_DETAIL_PATTERN.search(compact):
        return InitialLearningIntentDecision(
            learning_mode="practice_activity",
            target_granularity="ambiguous",
            next_action="collect_practice_requirements",
            trace_reason="The request appears to provide or confirm requirement details.",
        )

    knowledge_target = _extract_target(compact, KNOWLEDGE_QUESTION_PATTERN)
    learning_target = _extract_target(compact, LEARNING_REQUEST_PATTERN)
    if knowledge_target or learning_target:
        return InitialLearningIntentDecision(
            learning_mode="practice_activity",
            target_granularity="ambiguous",
            next_action="collect_practice_requirements",
            trace_reason="The fallback gate keeps substantive learning requests on the existing requirement path.",
        )

    return InitialLearningIntentDecision(
        learning_mode="undecided",
        target_granularity="ambiguous",
        next_action="ask_learning_mode",
        trace_reason="The request does not reliably specify whether to learn knowledge content or do practice.",
    )


def _extract_target(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    target = re.sub(r"\s+", " ", match.group("target") or "").strip()
    return target.strip(" ：:，,。！？!?；;\"'“”‘’")


def _compact_text(value: str | None, *, limit: int) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    turns = conversation[-6:]
    return "\n".join(
        f"{turn.role}: {_compact_text(turn.content, limit=500)}"
        for turn in turns
        if turn.content.strip()
    )
