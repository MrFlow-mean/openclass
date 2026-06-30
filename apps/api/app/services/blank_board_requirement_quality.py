from __future__ import annotations

from dataclasses import dataclass

from app.services.openai_course_ai import BlankBoardRequirementRefinement


@dataclass(frozen=True)
class BlankBoardRequirementReplyQuality:
    issues: list[str]

    @property
    def needs_repair(self) -> bool:
        return bool(self.issues)

    @property
    def repair_reason(self) -> str:
        return " ".join(self.issues)


INTERNAL_REPLY_TERMS = [
    "learning_goal",
    "current_level",
    "target_scenario",
    "known_background",
    "output_preference",
    "board_scope",
    "ready_for_board",
    "LearningRequirementSheet",
    "LearningClarificationStatus",
    "work_mode",
    "granularity",
    "key_facts",
    "missing_items",
    "学习需求清单",
    "核心因素",
    "缺失项",
]

FORM_LIKE_TERMS = [
    "请填写",
    "填写以下",
    "补充以下",
    "按以下格式",
    "按下面格式",
    "表单",
    "字段",
    "学习内容：",
    "学习内容:",
    "当前水平：",
    "当前水平:",
    "面向场景：",
    "面向场景:",
]

STARTING_LEVEL_TERMS = [
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

NOVICE_INTRO_TERMS = [
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

DELEGATED_INTRO_TERMS = [
    "为我指导",
    "你安排",
    "你来安排",
    "你帮我安排",
    "帮我安排",
    "帮我规划",
    "帮我定",
    "帮我推荐",
    "按你推荐",
    "听你的",
    "直接安排",
    "不知道，你安排",
    "不知道你安排",
]

ENTRY_CONFIRMATION_TERMS = [
    "哪个最吸引你",
    "哪个入口",
    "哪个方向",
    "哪条路线",
    "哪一条",
    "哪部分",
    "选哪个",
    "选择哪个",
    "愿意从",
    "愿意先从",
    "你愿意",
    "要不要从",
    "是否从",
    "想从",
    "更想",
    "可以吗",
    "准备好了吗",
    "准备好",
]

EXTERNAL_GOAL_QUESTION_TERMS = [
    "考试",
    "面试",
    "工作",
    "赚钱",
    "项目",
    "应用场景",
    "使用场景",
    "学习目的",
    "现实产出",
    "为了什么",
    "用来做什么",
]

PRACTICE_NEED_TERMS = [
    "练习",
    "训练",
    "提高",
    "提升",
    "巩固",
    "复习",
    "做题",
    "刷题",
    "测验",
    "模拟",
    "实战",
    "角色扮演",
]

RECENT_EXPERIENCE_CONTEXT_TERMS = [
    "最近",
    "刚学",
    "刚做",
    "刚写",
    "刚看",
    "刚遇到",
]

STUCK_POINT_USER_TERMS = [
    "卡",
    "看不懂",
    "听不懂",
    "做不出来",
    "不会做",
    "不理解",
    "不懂",
    "困惑",
    "不知道从哪开始",
]

SCENARIO_USER_TERMS = [
    "为了",
    "用来",
    "面向",
    "场景",
    "应对",
    "解决",
]

GOAL_OUTPUT_USER_TERMS = [
    "学完",
    "做到",
    "能写",
    "会做",
    "能讲",
    "看懂",
    "听懂",
    "用于",
]


def assess_blank_board_requirement_reply(
    result: BlankBoardRequirementRefinement,
    *,
    user_message: str = "",
) -> BlankBoardRequirementReplyQuality:
    if result.route != "requirement_refining":
        return BlankBoardRequirementReplyQuality(issues=[])

    issues: list[str] = []
    message = result.chatbot_message.strip()
    if result.ready_for_board:
        if not _has_confirmed_novice_context(result, user_message=user_message):
            return BlankBoardRequirementReplyQuality(issues=[])
        if _asks_entry_confirmation("\n".join([result.next_question, message])):
            issues.append("纯新手基础入口已 ready 时不应把入口确认压力交回用户。")
        return BlankBoardRequirementReplyQuality(issues=_dedupe_text(issues))
    issues.extend(_strategy_alignment_issues(result, user_message=user_message))
    if _is_broad_knowledge_refinement(result):
        issues.extend(_broad_guidance_issues(result, message, user_message=user_message))
    if _is_practice_signal(user_message) or result.work_mode == "practice_artifact":
        issues.extend(_practice_guidance_issues(result))
    issues.extend(_natural_conversation_issues(message))
    return BlankBoardRequirementReplyQuality(issues=_dedupe_text(issues))


def merge_guidance_repair(
    original: BlankBoardRequirementRefinement,
    repaired: BlankBoardRequirementRefinement,
    *,
    allow_core_updates: bool = False,
) -> BlankBoardRequirementRefinement:
    data = original.model_dump(mode="json")
    field_names = [
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
    ]
    if allow_core_updates:
        field_names.extend(
            [
                "progress",
                "summary",
                "work_mode",
                "granularity",
                "learning_goal",
                "current_level",
                "target_scenario",
                "known_background",
                "target_depth",
                "success_criteria",
                "key_facts",
                "checklist",
                "missing_items",
                "recommended_teaching_plan_summary",
                "ready_for_board",
            ]
        )
    for field_name in field_names:
        value = getattr(repaired, field_name)
        if allow_core_updates and field_name in {"next_question", "missing_items"}:
            data[field_name] = value
            continue
        if isinstance(value, str):
            if field_name == "work_mode" and value == "unknown":
                continue
            if field_name == "granularity" and value == "unclear":
                continue
            if field_name == "guidance_strategy" and value == "none":
                continue
            if _has_text(value):
                data[field_name] = value
            continue
        if value:
            data[field_name] = value
    return BlankBoardRequirementRefinement.model_validate(data)


def build_guidance_metadata(
    result: BlankBoardRequirementRefinement,
    *,
    quality_repaired: bool = False,
    quality_issues: list[str] | None = None,
    quality_repair_skipped: bool = False,
) -> dict[str, object]:
    issues = quality_issues or []
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
        "quality_repair_skipped": quality_repair_skipped or (bool(issues) and not quality_repaired),
        "quality_issues": issues,
    }


def allows_core_quality_repair(issues: list[str]) -> bool:
    return any(
        any(
            keyword in issue
            for keyword in [
                "委托式入门",
                "新手基础入口",
                "纯新手入门应直接落定",
                "练习型水平选择卡片",
                "练习型",
                "已会/未会",
                "最近经历",
                "卡点",
                "目标产出",
                "场景定位",
            ]
        )
        for issue in issues
    )


def _strategy_alignment_issues(
    result: BlankBoardRequirementRefinement,
    *,
    user_message: str,
) -> list[str]:
    issues: list[str] = []
    if not user_message.strip():
        return issues

    if _is_practice_signal(user_message) and result.work_mode != "practice_artifact":
        issues.append("练习型需求不应被领域地图或新知识收敛替代。")
    if _is_known_unknown_signal(user_message):
        if result.guidance_strategy not in {"known_unknown", "light_self_report", "implicit_observation"}:
            issues.append("用户已会/未会自述应优先使用已会/未会法或轻量自述法。")
        if not _has_text(result.known_background) and not result.key_facts:
            issues.append("用户已会/未会自述没有记录到 known_background 或 key_facts。")
    if _is_stuck_point_signal(user_message):
        if result.guidance_strategy not in {"stuck_point", "recent_experience", "implicit_observation"}:
            issues.append("用户卡点表达应优先使用卡点定位法。")
        if not _has_text(result.known_background) and not _has_text(result.learner_profile_inference):
            issues.append("用户卡点没有记录到背景或起点推断。")
    elif _is_recent_experience_signal(user_message):
        if result.guidance_strategy not in {"recent_experience", "stuck_point", "implicit_observation"}:
            issues.append("用户最近经历应优先使用最近经历法。")
        if not _has_text(result.known_background) and not _has_text(result.learner_profile_inference):
            issues.append("用户最近经历没有记录到背景或起点推断。")
    if _is_scenario_signal(user_message) and result.guidance_strategy in {"domain_map", "recommended_entry"}:
        if not _has_text(result.target_scenario) and not _has_text(result.success_criteria):
            issues.append("用户场景定位信息没有记录到面向场景或成功标准。")
    if _is_goal_output_signal(user_message) and result.guidance_strategy in {"domain_map", "recommended_entry"}:
        if not _has_text(result.target_depth) and not _has_text(result.success_criteria):
            issues.append("用户目标产出信息没有记录到目标深度或成功标准。")
    return issues


def _practice_guidance_issues(result: BlankBoardRequirementRefinement) -> list[str]:
    issues: list[str] = []
    if result.work_mode != "practice_artifact" or result.granularity != "practice_artifact":
        issues.append("练习型需求必须归入 practice_artifact。")
        return issues
    if result.guidance_strategy in {"domain_map", "recommended_entry"}:
        issues.append("练习型需求应优先收敛内容、当前水平和面向场景，而不是只给领域地图。")
    if not _has_text(result.learning_goal):
        issues.append("练习型需求缺少想练的内容。")
    if not _has_text(result.current_level) and not asks_about_starting_level(_first_text(result.next_question, result.chatbot_message)):
        issues.append("练习型需求缺少当前水平，也没有自然追问当前水平。")
    if not _has_text(result.current_level):
        if not _has_practice_level_choice_cards(result, _first_text(result.next_question, result.chatbot_message)):
            issues.append("练习型水平选择卡片缺失：水平未知时应使用选择卡片探寻当前水平。")
        return issues
    if not _has_text(result.target_scenario) and not _asks_scenario_question(_first_text(result.next_question, result.chatbot_message)):
        issues.append("练习型需求缺少面向场景，也没有自然追问面向场景。")
    return issues


def _broad_guidance_issues(
    result: BlankBoardRequirementRefinement,
    message: str,
    *,
    user_message: str,
) -> list[str]:
    issues: list[str] = []
    options = [option for option in result.entry_point_options if _has_text(option.label)]
    confirmed_novice_intro = _has_confirmed_novice_context(result, user_message=user_message)
    delegated_intro = _is_delegated_intro_refinement(result, user_message=user_message)
    if len(message) < 160:
        issues.append("chatbot_message 太短，像追问而不是学习地图引导。")
    if not _has_text(result.learning_map_summary):
        issues.append("缺少 learning_map_summary。")
    if len(options) < 2:
        issues.append("entry_point_options 少于 2 个。")
    if not _has_text(result.recommended_entry_point):
        issues.append("缺少 recommended_entry_point。")
    if not _has_text(result.reason_for_recommendation):
        issues.append("缺少 reason_for_recommendation。")
    visible_option_count = sum(1 for option in options if option.label.strip() in message)
    if options and visible_option_count < min(2, len(options)):
        issues.append("chatbot_message 没有呈现足够入口选项。")
    if _has_text(result.recommended_entry_point) and result.recommended_entry_point.strip() not in message:
        issues.append("chatbot_message 没有呈现推荐入口。")
    if (
        _has_text(result.recommended_entry_point)
        and not _has_starting_level_context(result)
        and not asks_about_starting_level(_first_text(result.next_question, result.chatbot_message))
    ):
        issues.append("已经推荐入口，但没有追问用户当前水平、已会/未会或最近学到哪里。")
    if "？" not in message and "?" not in message:
        issues.append("chatbot_message 没有一个关键问题。")
    if confirmed_novice_intro and _asks_external_goal_question("\n".join([result.next_question, message])):
        issues.append("纯新手入门场景不应继续追问考试、工作、赚钱或应用场景。")
    if confirmed_novice_intro and (
        result.granularity != "single_knowledge_point" or not result.ready_for_board
    ):
        issues.append("纯新手入门应直接落定新手基础入口并进入 ready。")
    if confirmed_novice_intro and _asks_entry_confirmation("\n".join([result.next_question, message])):
        issues.append("纯新手入门不应让用户在入口路线里继续选择。")
    if delegated_intro and (
        result.granularity != "single_knowledge_point" or not result.ready_for_board
    ):
        issues.append("纯新手委托式入门应主动落定领域总览型第一课并进入 ready。")
    if delegated_intro and _asks_entry_confirmation("\n".join([result.next_question, message])):
        issues.append("纯新手委托式入门不应把入口确认压力交回用户。")
    return issues


def _natural_conversation_issues(message: str) -> list[str]:
    issues: list[str] = []
    if not message:
        return ["chatbot_message 为空。"]
    leaked_terms = [term for term in INTERNAL_REPLY_TERMS if term in message]
    if leaked_terms:
        issues.append("chatbot_message 泄露内部字段或清单术语：" + "、".join(leaked_terms[:3]) + "。")
    form_terms = [term for term in FORM_LIKE_TERMS if term in message]
    if form_terms:
        issues.append("chatbot_message 像填表或字段收集：" + "、".join(form_terms[:3]) + "。")
    if _question_count(message) > 1:
        issues.append("chatbot_message 一次问了多个独立问题，应只保留一个最关键问题。")
    return issues


def asks_about_starting_level(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in STARTING_LEVEL_TERMS)


def _has_practice_level_choice_cards(result: BlankBoardRequirementRefinement, text: str) -> bool:
    options = [option for option in result.entry_point_options if _has_text(option.label)]
    if result.guidance_strategy != "choice_cards" or len(options) < 4:
        return False
    if not asks_about_starting_level(text):
        return False
    visible_option_count = sum(1 for option in options if option.label.strip() in text)
    return visible_option_count >= min(3, len(options))


def _is_broad_knowledge_refinement(result: BlankBoardRequirementRefinement) -> bool:
    if result.work_mode == "practice_artifact" or result.granularity == "practice_artifact":
        return False
    return (
        result.granularity == "broad_topic"
        or result.work_mode in {"knowledge_board", "narrow_topic"}
        or _has_text(result.learning_goal)
    )


def _has_starting_level_context(result: BlankBoardRequirementRefinement) -> bool:
    return any(
        _has_text(value)
        for value in [
            result.current_level,
            result.known_background,
            result.learner_profile_inference,
        ]
    )


def _is_novice_intro_refinement(result: BlankBoardRequirementRefinement) -> bool:
    if result.work_mode == "practice_artifact" or result.granularity == "practice_artifact":
        return False
    text = " ".join(
        [
            result.current_level,
            result.known_background,
            result.learner_profile_inference,
            result.summary,
            result.learning_goal,
            result.chatbot_message,
            result.next_question,
        ]
    )
    return any(keyword in text for keyword in NOVICE_INTRO_TERMS)


def _is_delegated_intro_refinement(
    result: BlankBoardRequirementRefinement,
    *,
    user_message: str,
) -> bool:
    if not _is_novice_intro_refinement(result):
        return False
    text = " ".join(
        [
            user_message,
            result.summary,
            result.known_background,
            result.learner_profile_inference,
            result.chatbot_message,
            result.next_question,
        ]
    )
    return any(keyword in text for keyword in DELEGATED_INTRO_TERMS)


def _has_confirmed_novice_context(
    result: BlankBoardRequirementRefinement,
    *,
    user_message: str,
) -> bool:
    if result.work_mode == "practice_artifact" or result.granularity == "practice_artifact":
        return False
    text = " ".join(
        [
            user_message,
            result.current_level,
            result.known_background,
            result.learner_profile_inference,
            result.summary,
        ]
    )
    return any(keyword in text for keyword in NOVICE_INTRO_TERMS)


def _asks_external_goal_question(text: str) -> bool:
    compact = (text or "").strip()
    if not compact or ("？" not in compact and "?" not in compact):
        return False
    return any(
        any(keyword in fragment for keyword in EXTERNAL_GOAL_QUESTION_TERMS)
        for fragment in _question_fragments(compact)
    )


def _asks_entry_confirmation(text: str) -> bool:
    compact = (text or "").strip()
    if not compact or ("？" not in compact and "?" not in compact):
        return False
    return any(
        any(keyword in fragment for keyword in ENTRY_CONFIRMATION_TERMS)
        for fragment in _question_fragments(compact)
    )


def _asks_scenario_question(text: str) -> bool:
    compact = (text or "").strip()
    if not compact or ("？" not in compact and "?" not in compact):
        return False
    return any(
        any(keyword in fragment for keyword in ["场景", "为了", "用来", "应对", "解决", "不限定具体场景"])
        for fragment in _question_fragments(compact)
    )


def _is_practice_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in PRACTICE_NEED_TERMS)


def _is_known_unknown_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    has_known = any(keyword in compact for keyword in ["已经会", "已会", "学过", "会一些", "基础"])
    has_unknown = any(keyword in compact for keyword in ["还没学", "没学过", "不会", "未会", "忘得", "忘了"])
    return has_known and has_unknown


def _is_recent_experience_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in RECENT_EXPERIENCE_CONTEXT_TERMS)


def _is_stuck_point_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in STUCK_POINT_USER_TERMS)


def _is_scenario_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in SCENARIO_USER_TERMS)


def _is_goal_output_signal(text: str) -> bool:
    compact = (text or "").strip()
    if not compact:
        return False
    return any(keyword in compact for keyword in GOAL_OUTPUT_USER_TERMS)


def _question_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in "。.!！\n；;":
            start = index + 1
            continue
        if char not in "？?":
            continue
        fragment = text[start : index + 1].strip()
        if fragment:
            fragments.append(fragment)
        start = index + 1
    return fragments


def _question_count(text: str) -> int:
    return text.count("？") + text.count("?")


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
