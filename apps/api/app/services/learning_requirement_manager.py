from __future__ import annotations

import re

from app.models import (
    BoardTaskAction,
    ConversationTurn,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    LearningRequirementSheet,
    Lesson,
    ResourceLibraryItem,
)
from app.services.course_runtime import effective_requirements
from app.services.openai_course_ai import LearningRequirementUpdate, openai_course_ai


MAX_CONTEXT_CHARS = 1800
MAX_TURNS = 10
INTERNAL_KEY_FACT_LABELS = {
    "preferredoutput",
    "outputpreference",
    "输出偏好",
    "输出形式",
    "输出形态",
}
BOARD_GENERATION_ARTIFACTS = r"(板书|版书)"
BOARD_GENERATION_ACTIONS = r"(生成|写|出|创建|整理|做一份|做个|做一个|做出)"
BOARD_GENERATION_ARTIFACT_PATTERN = re.compile(BOARD_GENERATION_ARTIFACTS)
BOARD_GENERATION_ACTION_PATTERN = re.compile(BOARD_GENERATION_ACTIONS)
BOARD_GENERATION_COMMAND_PATTERN = re.compile(
    rf"{BOARD_GENERATION_ACTIONS}.{{0,24}}{BOARD_GENERATION_ARTIFACTS}"
    r"|"
    rf"{BOARD_GENERATION_ARTIFACTS}.{{0,24}}{BOARD_GENERATION_ACTIONS}"
)
NEGATED_BOARD_GENERATION_PATTERN = re.compile(
    r"(不要|别|先不|暂不|不必)\s*(?:现在|立刻|立即|马上|直接)?\s*"
    rf"{BOARD_GENERATION_ACTIONS}.{{0,24}}{BOARD_GENERATION_ARTIFACTS}"
)
IMMEDIATE_GENERATION_REQUEST_PATTERN = re.compile(
    r"(直接生成|开始生成|马上生成|现在生成|生成吧|直接来|开始吧|"
    r"不用再?问|别再?问|不需要再?问|无需再?问)"
)
TEACHING_START_REQUEST_PATTERN = re.compile(
    r"(直接.{0,12}(开始)?讲|开始.{0,8}讲|先讲|从零开始|"
    r"当我是.{0,12}基础|你自己.{0,8}安排|不用再?问|别再?问|不需要再?问|无需再?问)"
)
LEARNING_CONTENT_LABELS = {
    "学习内容",
    "学习主题",
    "学习目标",
    "学习意愿",
    "目标语言",
    "学习语言",
    "具体领域",
    "学习方向",
    "具体学习需求",
}
ACTIONABLE_REQUIREMENT_LABELS = {
    *LEARNING_CONTENT_LABELS,
    "学习内容需求",
    "学习需求",
    "输出需求",
    "生成需求",
    "产出需求",
    "面向场景",
    "使用场景",
    "应用场景",
    "任务场景",
    "当前水平",
    "语言水平",
    "词汇量",
}
ACTIONABLE_REQUIREMENT_LABEL_PARTS = {
    "学习内容",
    "学习主题",
    "学习目标",
    "学习意愿",
    "目标语言",
    "学习语言",
    "具体领域",
    "学习方向",
    "内容需求",
    "学习需求",
    "输出需求",
    "生成需求",
    "产出需求",
    "场景",
    "水平",
    "基础",
    "词汇",
}
KEY_FACT_CATEGORY_ORDER = ["learning", "level", "vocabulary", "scenario", "output"]
KEY_FACT_CATEGORIES = {*KEY_FACT_CATEGORY_ORDER, "other"}
LEARNING_VERB = r"(?:学习(?!者|员|生)|复习|练习|了解|理解|掌握|研究|学(?!习|生))"
EXPLICIT_LEARNING_CONTENT_PATTERNS = [
    re.compile(
        rf"(?:想要|想|希望|打算|准备){LEARNING_VERB}"
        r"(?:一下|下)?(?:关于|有关|围绕)?(?P<target>[^，。！？!?；;\n]{1,80})"
    ),
    re.compile(
        rf"(?<!大)(?<!中)(?<!小){LEARNING_VERB}"
        r"(?:一下|下)?(?:关于|有关|围绕)?(?P<target>[^，。！？!?；;\n]{1,80})"
    ),
]
QUESTION_LEARNING_CONTENT_PATTERNS = [
    re.compile(r"(?:什么是|请解释|解释一下|讲解一下|讲解)(?P<target>[^，。！？!?；;\n]{1,80})"),
]
EXPLAIN_ACTION_PATTERN = re.compile(r"(讲解|解释|说明|讲一下|解释一下|帮我理解)")
APPEND_ACTION_PATTERN = re.compile(
    r"(续写|继续写|接着写|往后写|后续|新增|追加|新加|新章节|新小节|下一节|下一章|下一部分|末尾)"
)
EXPAND_ACTION_PATTERN = re.compile(r"(扩写|扩展|补充|增加|添加)")
SIMPLIFY_ACTION_PATTERN = re.compile(r"(简化|简单一点|通俗|更容易懂|更好懂)")
REWRITE_ACTION_PATTERN = re.compile(r"(改写|重写|修改|编辑|润色|优化)")


def _compact_text(value: str | None, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _board_summary(lesson: Lesson) -> str:
    return _compact_text(lesson.board_document.content_text, limit=MAX_CONTEXT_CHARS) or lesson.board_document.title


def _resource_summary(resources: list[ResourceLibraryItem]) -> str:
    lines: list[str] = []
    for resource in resources[:6]:
        chapter_titles = [chapter.title for chapter in resource.outline[:4] if chapter.title.strip()]
        lines.append(f"{resource.name}: {' / '.join(chapter_titles)}" if chapter_titles else resource.name)
    return "\n".join(lines)


def _conversation_summary(conversation: list[ConversationTurn]) -> str:
    return "\n".join(
        f"{turn.role}: {_compact_text(turn.content, limit=500)}"
        for turn in conversation[-MAX_TURNS:]
        if turn.content.strip()
    )


def _has_substantive_learning_signal(text: str) -> bool:
    compact = _compact_text(text, limit=240)
    if len(compact) >= 18:
        return True
    if re.search(r"(想学|学习|复习|练习|理解|掌握|讲义|板书|版书|解释|准备)", compact):
        return True
    return any(char in compact for char in "？?，,：:")


def _requests_immediate_board_generation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact:
        return False
    if NEGATED_BOARD_GENERATION_PATTERN.search(compact):
        return False
    if not (
        BOARD_GENERATION_ARTIFACT_PATTERN.search(compact)
        and BOARD_GENERATION_ACTION_PATTERN.search(compact)
        and BOARD_GENERATION_COMMAND_PATTERN.search(compact)
    ):
        return False
    return True


def is_explicit_board_generation_request(text: str) -> bool:
    # 学生明确说“生成板书”时，才允许从需求澄清推进到首次板书生成。
    return _requests_immediate_board_generation(text)


def _requests_immediate_generation(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact:
        return False
    if NEGATED_BOARD_GENERATION_PATTERN.search(compact):
        return False
    return bool(IMMEDIATE_GENERATION_REQUEST_PATTERN.search(compact))


def is_generation_control_request(text: str) -> bool:
    """Whether the user is trying to move from PM clarification into generation."""
    # 这类话不是普通聊天，而是在控制“是否现在开始生成板书”。
    return _requests_immediate_board_generation(text) or _requests_immediate_generation(text)


def _requests_teaching_start(text: str) -> bool:
    compact = _compact_text(text, limit=280)
    if not compact:
        return False
    if NEGATED_BOARD_GENERATION_PATTERN.search(compact):
        return False
    return bool(TEACHING_START_REQUEST_PATTERN.search(compact))


def _infer_action_type(
    text: str,
    *,
    forced_board_generation: bool = False,
    fallback: BoardTaskAction | None = None,
) -> BoardTaskAction | None:
    # 只识别通用动作形态：生成、追加、简化、扩写、改写、讲解；不按具体学科分支。
    compact = _compact_text(text, limit=280)
    if forced_board_generation:
        return "generate_board"
    if APPEND_ACTION_PATTERN.search(compact):
        return "append_section"
    if SIMPLIFY_ACTION_PATTERN.search(compact):
        return "simplify_target"
    if EXPAND_ACTION_PATTERN.search(compact):
        return "expand_target"
    if REWRITE_ACTION_PATTERN.search(compact):
        return "rewrite_target"
    if EXPLAIN_ACTION_PATTERN.search(compact):
        return "explain_target"
    return fallback


def _normalize_learning_content_value(value: str) -> str:
    compact = _compact_text(value, limit=120)
    compact = re.sub(r"^(?:我|俺|本人|用户)?\s*(?:想要|想|希望|打算|准备)?\s*", "", compact)
    compact = re.sub(r"^(?:学习|复习|练习|了解|理解|掌握|研究|学)(?:一下|下)?", "", compact)
    compact = re.sub(r"^(?:关于|有关|围绕)", "", compact)
    compact = re.sub(r"^[^，。！？!?；;]{1,24}中的", "", compact)
    compact = re.sub(r"(?:是什么|是啥)$", "", compact)
    return compact.strip(" ：:，,。！？!?；;\"'“”‘’")


def _extract_learning_content(text: str, *, allow_question_topic: bool = True) -> str | None:
    compact = _compact_text(text, limit=240)
    patterns = EXPLICIT_LEARNING_CONTENT_PATTERNS
    if allow_question_topic:
        patterns = [*patterns, *QUESTION_LEARNING_CONTENT_PATTERNS]
    for pattern in patterns:
        match = pattern.search(compact)
        if not match:
            continue
        target = _normalize_learning_content_value(match.group("target"))
        if target:
            return target
    return None


def _fallback_update(*, lesson: Lesson, conversation: list[ConversationTurn]) -> LearningRequirementUpdate:
    latest_user = next((turn.content for turn in reversed(conversation) if turn.role == "user"), "")
    if not _has_substantive_learning_signal(latest_user):
        return LearningRequirementUpdate(
            progress=15,
            summary="用户还没有透露足够具体的学习需求。",
            key_facts=[],
            checklist=[
                LearningRequirementChecklistItem(
                    title="用户具体想学什么内容或解决什么问题",
                    is_clear=False,
                    evidence="最近对话还没有说明要围绕哪个主题、资料或问题学习。",
                ),
                LearningRequirementChecklistItem(
                    title="用户在这个领域目前是什么水平",
                    is_clear=False,
                    evidence="最近对话还没有说明已有基础、经验或卡点。",
                ),
                LearningRequirementChecklistItem(
                    title="用户为什么学以及要面对什么场景",
                    is_clear=False,
                    evidence="最近对话还没有说明学习目的、任务场景或输出要求。",
                ),
            ],
            missing_items=["具体学习内容", "当前水平", "学习目的或使用场景"],
            next_question="你想围绕哪个主题、资料或具体问题开始学习？",
            ready_for_board=False,
        )

    compact_goal = _compact_text(latest_user, limit=160)
    return LearningRequirementUpdate(
        progress=55,
        summary=f"用户提出了一个待整理的学习请求：{compact_goal}",
            key_facts=[
                LearningRequirementKeyFact(
                    label="用户当前表达",
                    value=compact_goal,
                    evidence="来自用户最近一轮输入。",
                    category="other",
                )
            ],
        checklist=[
            LearningRequirementChecklistItem(
                title="已捕捉到用户具体想学的入口",
                is_clear=True,
                evidence=compact_goal,
            ),
            LearningRequirementChecklistItem(
                title="用户在这个领域目前是什么水平",
                is_clear=False,
                evidence="对话中还没有足够信息说明已有基础、经验或卡点。",
            ),
            LearningRequirementChecklistItem(
                title="用户为什么学以及要面对什么场景",
                is_clear=False,
                evidence="对话中还没有足够信息说明学习目的、任务场景或输出要求。",
            ),
        ],
        missing_items=["当前水平", "学习目的或使用场景"],
        next_question="你现在对这个内容大概是什么水平：完全入门、了解一点，还是已经在解决具体问题？",
        ready_for_board=False,
    )


def _is_internal_key_fact(item: LearningRequirementKeyFact) -> bool:
    compact_label = re.sub(r"[\s_-]+", "", item.label).strip().lower()
    if compact_label in INTERNAL_KEY_FACT_LABELS:
        return True
    return "preferredoutput" in compact_label or "outputpreference" in compact_label


def _is_learning_content_label(label: str) -> bool:
    compact_label = re.sub(r"[\s_-]+", "", label).strip().lower()
    return compact_label in {re.sub(r"[\s_-]+", "", item).lower() for item in LEARNING_CONTENT_LABELS}


def _is_actionable_requirement_label(label: str) -> bool:
    compact_label = re.sub(r"[\s_-]+", "", label).strip().lower()
    exact_labels = {re.sub(r"[\s_-]+", "", item).lower() for item in ACTIONABLE_REQUIREMENT_LABELS}
    if compact_label in exact_labels:
        return True
    return any(re.sub(r"[\s_-]+", "", item).lower() in compact_label for item in ACTIONABLE_REQUIREMENT_LABEL_PARTS)


def _has_actionable_generation_context(update: LearningRequirementUpdate) -> bool:
    return any(_key_fact_category(item) in KEY_FACT_CATEGORY_ORDER and item.value.strip() for item in update.key_facts)


def _normalize_key_fact(item: LearningRequirementKeyFact) -> LearningRequirementKeyFact:
    label = _compact_text(item.label, limit=40)
    value = _compact_text(item.value, limit=140)
    category = _key_fact_category(item)
    if category == "learning":
        normalized_value = _normalize_learning_content_value(value)
        if normalized_value:
            label = "学习内容"
            value = normalized_value
    else:
        if category == "level":
            label = "当前水平"
        elif category == "vocabulary":
            label = "词汇量"
        elif category == "scenario":
            label = "面向场景"
        elif category == "output":
            label = "输出需求"
    return LearningRequirementKeyFact(
        label=label,
        value=value,
        evidence=_compact_text(item.evidence, limit=120),
        category=category,
    )


def _legacy_key_fact_category(label: str) -> str:
    compact_label = re.sub(r"[\s_-]+", "", label).strip().lower()
    if _is_learning_content_label(label):
        return "learning"
    if "词汇" in compact_label:
        return "vocabulary"
    if "水平" in compact_label or "基础" in compact_label:
        return "level"
    if "场景" in compact_label:
        return "scenario"
    if any(part in compact_label for part in ("输出", "产出", "生成需求", "生成", "需求类型", "学习需求")):
        return "output"
    return "other"


def _key_fact_category(item: LearningRequirementKeyFact) -> str:
    if item.category in KEY_FACT_CATEGORIES:
        return item.category
    return _legacy_key_fact_category(item.label)


def _latest_learning_clarification(lesson: Lesson) -> LearningClarificationStatus | None:
    for commit in reversed(lesson.history_graph.commits):
        raw = commit.metadata.get("learning_clarification") if isinstance(commit.metadata, dict) else None
        if not raw:
            continue
        try:
            return LearningClarificationStatus.model_validate(raw)
        except Exception:
            continue
    return None


def _merge_key_facts(
    existing_facts: list[LearningRequirementKeyFact],
    current_facts: list[LearningRequirementKeyFact],
) -> list[LearningRequirementKeyFact]:
    merged: dict[str, LearningRequirementKeyFact] = {}
    for fact in [*existing_facts, *current_facts]:
        normalized = _normalize_key_fact(fact)
        if not normalized.label.strip() or not normalized.value.strip() or _is_internal_key_fact(normalized):
            continue
        category = _key_fact_category(normalized)
        merge_key = category if category != "other" else f"other:{normalized.label}"
        merged[merge_key] = normalized

    ordered_keys = [
        *[key for key in KEY_FACT_CATEGORY_ORDER if key in merged],
        *[key for key in merged if key not in KEY_FACT_CATEGORY_ORDER],
    ]
    return [merged[key] for key in ordered_keys][:5]


def _merge_update_with_existing_facts(update: LearningRequirementUpdate, *, lesson: Lesson) -> LearningRequirementUpdate:
    previous = _latest_learning_clarification(lesson)
    if not previous or not previous.key_facts:
        return update
    previous_topic = _topic_key(next(
        (fact.value for fact in previous.key_facts if _key_fact_category(fact) == "learning" and fact.value.strip()),
        "",
    ))
    current_topic = _topic_key(next(
        (fact.value for fact in update.key_facts if _key_fact_category(fact) == "learning" and fact.value.strip()),
        "",
    ))
    same_topic = not previous_topic or not current_topic or previous_topic == current_topic
    progress = max(update.progress, previous.progress) if same_topic else update.progress
    return LearningRequirementUpdate(
        progress=progress,
        summary=update.summary,
        key_facts=_merge_key_facts(previous.key_facts, update.key_facts),
        checklist=update.checklist,
        missing_items=update.missing_items,
        next_question=update.next_question,
        ready_for_board=update.ready_for_board,
        action_type=update.action_type,
        action_instruction=update.action_instruction,
        target_hint=update.target_hint,
        interaction_rule_draft=update.interaction_rule_draft,
    )


def _topic_key(value: str | None) -> str:
    return re.sub(r"[\s：:，,。！？!?；;\"'“”‘’（）()/_-]+", "", value or "").lower()


def _key_fact_value(update: LearningRequirementUpdate, category: str) -> str | None:
    for fact in update.key_facts:
        if _key_fact_category(fact) == category and fact.value.strip():
            return fact.value.strip()
    return None


def _ensure_learning_content_fact(
    key_facts: list[LearningRequirementKeyFact],
    *,
    user_message: str,
) -> list[LearningRequirementKeyFact]:
    content = _extract_learning_content(user_message, allow_question_topic=not key_facts)
    if not content:
        return key_facts

    next_facts = list(key_facts)
    for fact in next_facts:
        if _key_fact_category(fact) == "learning":
            return next_facts

    return [
        LearningRequirementKeyFact(
            label="学习内容",
            value=content,
            evidence="来自用户最近一轮输入。",
            category="learning",
        ),
        *next_facts,
    ]


def _normalize_update(update: LearningRequirementUpdate, *, user_message: str = "") -> LearningRequirementUpdate:
    key_facts = [
        _normalize_key_fact(item)
        for item in update.key_facts
        if item.label.strip() and item.value.strip() and not _is_internal_key_fact(item)
    ][:5]
    key_facts = _ensure_learning_content_fact(key_facts, user_message=user_message)[:5]
    checklist = [
        LearningRequirementChecklistItem(
            title=_compact_text(item.title, limit=80),
            is_clear=item.is_clear,
            evidence=_compact_text(item.evidence, limit=160),
        )
        for item in update.checklist
        if item.title.strip()
    ][:5]
    if not checklist:
        checklist = [
            LearningRequirementChecklistItem(
                title="明确一个具体学习目标",
                is_clear=False,
                evidence="需求管理 AI 没有提取到可展示的清单项。",
            )
        ]

    ready = update.ready_for_board
    progress = max(0, min(100, update.progress))
    if ready:
        progress = 100
    elif progress >= 100:
        progress = 99

    return LearningRequirementUpdate(
        progress=progress,
        summary=_compact_text(update.summary, limit=220),
        key_facts=key_facts,
        checklist=checklist,
        missing_items=[_compact_text(item, limit=80) for item in update.missing_items if item.strip()][:5],
        next_question=_compact_text(update.next_question, limit=160),
        ready_for_board=ready,
        action_type=update.action_type,
        action_instruction=_compact_text(update.action_instruction, limit=240),
        target_hint=_compact_text(update.target_hint, limit=240),
        interaction_rule_draft=update.interaction_rule_draft,
    )


def _apply_update_to_requirements(
    requirements: LearningRequirementSheet,
    update: LearningRequirementUpdate,
    *,
    forced_start: bool = False,
    user_message: str = "",
    forced_board_generation: bool = False,
) -> LearningRequirementSheet:
    updated = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    if learning_content := _key_fact_value(update, "learning"):
        updated.theme = learning_content
    if update.summary:
        updated.learning_goal = update.summary
    if level := _key_fact_value(update, "level"):
        updated.level = level
    if output := _key_fact_value(update, "output"):
        updated.output_preference = output
    background_values = [
        fact.value.strip()
        for fact in update.key_facts
        if fact.category in {"level", "vocabulary", "scenario", "other"} and fact.value.strip()
    ]
    if background_values:
        updated.known_background = "；".join(dict.fromkeys(background_values))
    updated.learning_need_checklist = [item.title for item in update.checklist]
    if forced_start or update.ready_for_board:
        updated.current_questions = []
        updated.risk_notes = []
    else:
        updated.current_questions = [update.next_question] if update.next_question else updated.current_questions
        updated.risk_notes = update.missing_items
    action_type = update.action_type or _infer_action_type(
        user_message,
        forced_board_generation=forced_board_generation,
    )
    updated.action_type = action_type
    updated.action_instruction = update.action_instruction or _compact_text(user_message, limit=240)
    updated.interaction_rule_draft = update.interaction_rule_draft
    if update.target_hint and updated.target_location is None:
        updated.location_clarification_question = ""
    if forced_board_generation:
        updated.location_status = "resolved"
    return updated


def _clarification_from_update(
    update: LearningRequirementUpdate,
    *,
    forced_board_generation: bool = False,
    forced_teaching_start: bool = False,
) -> LearningClarificationStatus:
    if forced_board_generation:
        reason = "用户明确要求现在生成板书，系统将基于当前已知需求进入生成。"
        return LearningClarificationStatus(
            progress=100,
            label="准备生成板书",
            reason=reason,
            missing_items=[],
            can_start=True,
            forced_start=True,
            summary=update.summary or reason,
            key_facts=update.key_facts,
            checklist=update.checklist,
            next_question="",
            ready_for_board=True,
        )

    if forced_teaching_start:
        reason = "用户明确要求开始讲解，系统将基于当前已知需求进入教学。"
        return LearningClarificationStatus(
            progress=max(90, min(update.progress, 99)),
            label="可以开始讲解",
            reason=reason,
            missing_items=[],
            can_start=True,
            forced_start=True,
            summary=update.summary or reason,
            key_facts=update.key_facts,
            checklist=update.checklist,
            next_question="",
            ready_for_board=False,
        )

    ready = update.ready_for_board
    return LearningClarificationStatus(
        progress=100 if ready else update.progress,
        label="需求已清晰" if ready else "继续澄清",
        reason=update.summary,
        missing_items=[] if ready else update.missing_items,
        can_start=ready,
        forced_start=False,
        summary=update.summary,
        key_facts=update.key_facts,
        checklist=update.checklist,
        next_question="" if ready else update.next_question,
        ready_for_board=ready,
    )


def update_learning_requirements_from_chat(
    *,
    lesson: Lesson,
    resources: list[ResourceLibraryItem],
    conversation: list[ConversationTurn],
    user_message: str,
    chatbot_message: str,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus]:
    requirements = effective_requirements(lesson)
    existing_checklist = list(requirements.learning_need_checklist)
    ai_update = openai_course_ai.generate_learning_requirement_update(
        lesson_title=lesson.title,
        existing_summary=requirements.learning_goal,
        existing_checklist=existing_checklist,
        board_summary=_board_summary(lesson),
        resource_summary=_resource_summary(resources),
        conversation_summary=_conversation_summary(conversation),
        user_message=user_message,
        chatbot_message=chatbot_message,
    )
    update = _normalize_update(
        ai_update or _fallback_update(lesson=lesson, conversation=conversation),
        user_message=user_message,
    )
    update = _merge_update_with_existing_facts(update, lesson=lesson)
    forced_board_generation = _requests_immediate_board_generation(user_message) or (
        _requests_immediate_generation(user_message) and _has_actionable_generation_context(update)
    )
    forced_teaching_start = (
        not forced_board_generation
        and _requests_teaching_start(user_message)
        and _has_actionable_generation_context(update)
    )
    updated_requirements = _apply_update_to_requirements(
        requirements,
        update,
        forced_start=forced_board_generation or forced_teaching_start,
        user_message=user_message,
        forced_board_generation=forced_board_generation,
    )
    return updated_requirements, _clarification_from_update(
        update,
        forced_board_generation=forced_board_generation,
        forced_teaching_start=forced_teaching_start,
    )
