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
GoalShape = Literal[
    "atomic_concept",
    "bounded_question",
    "bounded_task_slice",
    "underbounded_process",
    "broad_domain",
    "practice_activity",
    "ambiguous",
]
ReadinessForInitialBoard = Literal[
    "ready",
    "needs_narrowing",
    "needs_practice_requirements",
    "needs_learning_mode",
]


PRACTICE_ACTIVITY_PATTERN = re.compile(
    r"(练习|训练|测验|测试|做题|出题|题目|问答|对话|角色|互动|纠错|批改|评估|巩固|提升)"
)
EXPLICIT_PRACTICE_ACTIVITY_PATTERN = re.compile(
    r"(练习|测验|测试|做题|出题|题目|问答|对话|角色|互动|纠错|批改|巩固)"
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
HOW_TO_PATTERN = re.compile(r"(怎么|如何|怎样|方法|流程|思路|路径|策略)")
PROCESS_CAPABILITY_PATTERN = re.compile(
    r"(优化|调参|配置|训练|评估|部署|建模|设计|实现|应用|分析|"
    r"改进|提升|排查|解决|规划|管理)"
)
DOMAIN_CONTAINER_PATTERN = re.compile(r"(里|中|里面|当中|领域|场景|流程|项目|系统)")
CONCRETE_PROCESS_SCOPE_PATTERN = re.compile(
    r"(第[0-9０-９一二三四五六七八九十两]+[章节节]|选中|"
    r"这(?:份|个|一)?(?:段|句|节|章|部分|数据|项目|模型|任务|问题|实验)|"
    r"当前(?:数据|项目|模型|任务|问题|实验)|"
    r"`[^`]{1,50}`|《[^》]{1,50}》|“[^”]{1,50}”|\"[^\"]{1,50}\"|"
    r"[A-Za-z_][A-Za-z0-9_]{2,})"
)
PROCESS_BOUNDARY_PATTERN = re.compile(
    r"(约束|限制|要求|目标是|希望达到|用于|用来|面向|为了|"
    r"(?:错误|报错|失败|卡住|瓶颈)[^，。！？!?；;\n]{1,24}|"
    r"在[^，。！？!?；;\n]{1,24}(?:场景|情况下|时候|环境))"
)
PROCESS_OBJECT_PATTERN = re.compile(
    r"(?:优化|调参|配置|训练|评估|部署|建模|设计|实现|应用|分析|"
    r"改进|提升|排查|解决|规划|管理)"
    r"(?P<object>[^，。！？!?；;\n]{1,40})"
)
PROCESS_WORD_CLEANER = re.compile(
    r"(怎么|如何|怎样|方法|流程|思路|路径|策略|优化|调参|配置|训练|评估|部署|建模|设计|"
    r"实现|应用|分析|改进|提升|排查|解决|规划|管理|一下|下|的|呢|吗|啊|吧)"
)
GENERIC_PROCESS_BOUNDARY_PATTERN = re.compile(
    r"^(这个|那个|这些|那些|它|内容|东西|方向|领域|场景|流程|项目|"
    r"系统|数据|模型|任务|问题|方法|策略|方案|过程|能力|目标|效果|"
    r"性能|结果|情况|错误|异常|风险|计划|路径|思路)$"
)


class LearningRequestReadiness(BaseModel):
    goal_shape: GoalShape = "ambiguous"
    readiness_for_initial_board: ReadinessForInitialBoard = "needs_learning_mode"
    missing_boundaries: list[str] = Field(default_factory=list)
    trace_reason: str = Field(default="")

    @field_validator("missing_boundaries", mode="before")
    @classmethod
    def _coerce_missing_boundaries(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            candidates = re.split(r"[、,，;；\n]+", value)
        elif isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = []
        return [str(item).strip() for item in candidates if str(item).strip()]

    @field_validator("trace_reason", mode="before")
    @classmethod
    def _coerce_trace_reason(cls, value: object) -> str:
        return str(value or "").strip()


class InitialLearningIntentDecision(BaseModel):
    learning_mode: LearningMode = "undecided"
    target_granularity: TargetGranularity = "ambiguous"
    next_action: InitialLearningNextAction = "ask_learning_mode"
    trace_reason: str = Field(default="")
    readiness: LearningRequestReadiness = Field(default_factory=LearningRequestReadiness)

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
        return _apply_readiness_guard(ai_decision, user_message=user_message)
    return fallback_initial_learning_intent_decision(user_message)


def _apply_readiness_guard(
    decision: InitialLearningIntentDecision,
    *,
    user_message: str,
) -> InitialLearningIntentDecision:
    readiness = evaluate_learning_request_readiness(user_message=user_message, decision=decision)
    next_action = decision.next_action
    learning_mode = decision.learning_mode
    target_granularity = decision.target_granularity
    trace_reason = decision.trace_reason

    if readiness.readiness_for_initial_board == "needs_narrowing":
        learning_mode = "learn_concept"
        target_granularity = "broad_domain"
        next_action = "ask_specific_concept"
        trace_reason = readiness.trace_reason or trace_reason
    elif readiness.readiness_for_initial_board == "needs_practice_requirements":
        learning_mode = "practice_activity"
        target_granularity = "ambiguous"
        next_action = "collect_practice_requirements"
        trace_reason = readiness.trace_reason or trace_reason
    elif readiness.readiness_for_initial_board == "needs_learning_mode":
        learning_mode = "undecided"
        target_granularity = "ambiguous"
        next_action = "ask_learning_mode"
        trace_reason = readiness.trace_reason or trace_reason
    elif decision.next_action == "freeze_minimal_and_generate_board":
        learning_mode = "learn_concept"
        target_granularity = "specific_concept"

    return InitialLearningIntentDecision(
        learning_mode=learning_mode,
        target_granularity=target_granularity,
        next_action=next_action,
        trace_reason=trace_reason,
        readiness=readiness,
    )


def evaluate_learning_request_readiness(
    *,
    user_message: str,
    decision: InitialLearningIntentDecision,
) -> LearningRequestReadiness:
    compact = _compact_text(user_message, limit=240)
    if not compact:
        return LearningRequestReadiness(
            goal_shape="ambiguous",
            readiness_for_initial_board="needs_learning_mode",
            missing_boundaries=["学习形态"],
            trace_reason="用户当前没有给出可判断的学习请求。",
        )
    if EXPLICIT_PRACTICE_ACTIVITY_PATTERN.search(compact):
        return LearningRequestReadiness(
            goal_shape="practice_activity",
            readiness_for_initial_board="needs_practice_requirements",
            missing_boundaries=["练习内容", "当前水平", "练习形式"],
            trace_reason="用户表达的是练习、互动或测验类学习活动，需要先补齐练习需求。",
        )
    if _is_underbounded_process_goal(compact):
        return LearningRequestReadiness(
            goal_shape="underbounded_process",
            readiness_for_initial_board="needs_narrowing",
            missing_boundaries=["具体对象", "任务场景", "约束"],
            trace_reason=(
                "用户给出了流程型学习方向，但还没有说明具体对象、任务场景或约束，"
                "暂不能作为最小冻结知识切片。"
            ),
        )
    if decision.next_action == "ask_learning_mode":
        return LearningRequestReadiness(
            goal_shape="ambiguous",
            readiness_for_initial_board="needs_learning_mode",
            missing_boundaries=["学习形态"],
            trace_reason="用户尚未说明是学习知识内容还是做练习型教学。",
        )
    if (
        decision.next_action == "ask_specific_concept"
        or decision.target_granularity == "broad_domain"
    ):
        return LearningRequestReadiness(
            goal_shape="broad_domain",
            readiness_for_initial_board="needs_narrowing",
            missing_boundaries=["具体知识点、问题或范围"],
            trace_reason="用户给出了学习方向，但还没有缩小到可执行的知识切片。",
        )
    if decision.next_action == "collect_practice_requirements":
        return LearningRequestReadiness(
            goal_shape="practice_activity",
            readiness_for_initial_board="needs_practice_requirements",
            missing_boundaries=["练习内容", "当前水平", "练习形式"],
            trace_reason="门禁判断本轮应进入练习型需求清单。",
        )
    if decision.next_action == "freeze_minimal_and_generate_board":
        return LearningRequestReadiness(
            goal_shape=_ready_goal_shape(compact),
            readiness_for_initial_board="ready",
            missing_boundaries=[],
            trace_reason="用户目标已具备生成第一版板书所需的最小边界。",
        )
    return LearningRequestReadiness(
        goal_shape="ambiguous",
        readiness_for_initial_board="needs_learning_mode",
        missing_boundaries=["学习形态"],
        trace_reason="门禁没有得到可靠的初始学习目标形态。",
    )


def _is_underbounded_process_goal(user_message: str) -> bool:
    compact = _compact_text(user_message, limit=240)
    if not (HOW_TO_PATTERN.search(compact) and PROCESS_CAPABILITY_PATTERN.search(compact)):
        return False
    if _has_process_boundary(compact):
        return False
    return bool(
        DOMAIN_CONTAINER_PATTERN.search(compact) or LEARNING_REQUEST_PATTERN.search(compact)
    )


def _has_process_boundary(compact: str) -> bool:
    if CONCRETE_PROCESS_SCOPE_PATTERN.search(compact) or PROCESS_BOUNDARY_PATTERN.search(
        compact
    ):
        return True
    return _has_specific_process_object(compact)


def _has_specific_process_object(compact: str) -> bool:
    for match in PROCESS_OBJECT_PATTERN.finditer(compact):
        candidate = PROCESS_WORD_CLEANER.sub("", match.group("object") or "")
        candidate = candidate.strip(" ：:，,。！？!?；;\"'“”‘’")
        if len(candidate) >= 2 and not GENERIC_PROCESS_BOUNDARY_PATTERN.match(candidate):
            return True
    return False


def _ready_goal_shape(compact: str) -> GoalShape:
    if HOW_TO_PATTERN.search(compact) and PROCESS_CAPABILITY_PATTERN.search(compact):
        return "bounded_task_slice"
    if KNOWLEDGE_QUESTION_PATTERN.search(compact):
        return "bounded_question"
    return "atomic_concept"


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
        missing_items = decision.readiness.missing_boundaries or ["具体知识点、问题或范围"]
        if decision.readiness.goal_shape == "underbounded_process":
            next_question = "你想先聚焦哪个具体对象、任务场景或约束？"
        else:
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

    def finalize(decision: InitialLearningIntentDecision) -> InitialLearningIntentDecision:
        return _apply_readiness_guard(decision, user_message=compact)

    if not compact:
        return finalize(
            InitialLearningIntentDecision(
                learning_mode="undecided",
                target_granularity="ambiguous",
                next_action="ask_learning_mode",
                trace_reason="No substantive learning request was available.",
            )
        )

    if LOW_SUBSTANCE_CONTINUATION_PATTERN.search(compact):
        return finalize(
            InitialLearningIntentDecision(
                learning_mode="practice_activity",
                target_granularity="ambiguous",
                next_action="collect_practice_requirements",
                trace_reason=(
                    "The fallback gate preserves the existing requirement path "
                    "for low-information turns."
                ),
            )
        )

    if PRACTICE_ACTIVITY_PATTERN.search(compact):
        return finalize(
            InitialLearningIntentDecision(
                learning_mode="practice_activity",
                target_granularity="ambiguous",
                next_action="collect_practice_requirements",
                trace_reason="The request asks for a practice-like learning activity.",
            )
        )

    if REQUIREMENT_DETAIL_PATTERN.search(compact):
        return finalize(
            InitialLearningIntentDecision(
                learning_mode="practice_activity",
                target_granularity="ambiguous",
                next_action="collect_practice_requirements",
                trace_reason="The request appears to provide or confirm requirement details.",
            )
        )

    knowledge_target = _extract_target(compact, KNOWLEDGE_QUESTION_PATTERN)
    learning_target = _extract_target(compact, LEARNING_REQUEST_PATTERN)
    if knowledge_target or learning_target:
        return finalize(
            InitialLearningIntentDecision(
                learning_mode="practice_activity",
                target_granularity="ambiguous",
                next_action="collect_practice_requirements",
                trace_reason=(
                    "The fallback gate keeps substantive learning requests on "
                    "the existing requirement path."
                ),
            )
        )

    return finalize(
        InitialLearningIntentDecision(
            learning_mode="undecided",
            target_granularity="ambiguous",
            next_action="ask_learning_mode",
            trace_reason=(
                "The request does not reliably specify whether to learn knowledge "
                "content or do practice."
            ),
        )
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
