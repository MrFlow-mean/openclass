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


def assess_blank_board_requirement_reply(
    result: BlankBoardRequirementRefinement,
) -> BlankBoardRequirementReplyQuality:
    if result.route != "requirement_refining" or result.ready_for_board:
        return BlankBoardRequirementReplyQuality(issues=[])

    issues: list[str] = []
    message = result.chatbot_message.strip()
    if _is_broad_knowledge_refinement(result):
        issues.extend(_broad_guidance_issues(result, message))
    issues.extend(_natural_conversation_issues(message))
    return BlankBoardRequirementReplyQuality(issues=_dedupe_text(issues))


def merge_guidance_repair(
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


def build_guidance_metadata(
    result: BlankBoardRequirementRefinement,
    *,
    quality_repaired: bool = False,
    quality_issues: list[str] | None = None,
) -> dict[str, object]:
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
        "quality_issues": quality_issues or [],
    }


def _broad_guidance_issues(result: BlankBoardRequirementRefinement, message: str) -> list[str]:
    issues: list[str] = []
    options = [option for option in result.entry_point_options if _has_text(option.label)]
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
