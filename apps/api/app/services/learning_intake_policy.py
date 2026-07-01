from __future__ import annotations

from typing import Any

LEARNING_INTAKE_STRATEGIES = (
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
)

BLANK_BOARD_LEARNING_INTAKE_POLICY = (
    "通用 learning intake 策略：先判断用户是在学新知识、做练习产物、普通聊天，还是目标不清；"
    "再选择一个最能降低表达成本的方法。可用方法：starting_point、choice_cards、domain_map、"
    "recommended_entry、known_unknown、recent_experience、stuck_point、mode_split、scenario、goal_output。"
    "不要机械补字段，不要把用户带进问卷。\n"
    "宽泛主题：给简短学习地图、2-4 个入口或水平卡片、一个推荐入口和一个主问题；"
    "推荐只能是暂定入口，除非用户已明确纯新手入门或明确授权你安排。\n"
    "练习产物：先收敛想练什么、当前水平、面向场景；如果缺当前水平，优先给水平卡片，"
    "不要同轮继续追问场景。\n"
    "用户自述已会/未会/最近经历/卡点时，优先更新当前水平、背景或卡点；"
    "用户说不知道或你安排时，给低成本选项并推荐一个可开始入口。\n"
    "只根据通用学习形态、内容形态和用户目标判断，不写学科、教材、考试或 demo 专属规则。"
)

BLANK_BOARD_FACT_BOUNDARY_POLICY = (
    "事实边界：current_level、known_background、target_scenario、key_facts 只能记录用户明说、"
    "最近对话已出现或 existing_* 历史里已有的事实；不确定就留空或写待确认。"
    "learner_profile_inference 只写谨慎推断，必须带“可能/通常/待确认”等不确定表达。"
    "key_facts.evidence 必须引用用户原话或历史事实，不能把通用常识当证据。"
)

BLANK_BOARD_STAGE_POLICIES = {
    "fresh_intake": (
        "阶段：首次空白板书收敛。先判断 ordinary_chat 或 requirement_refining。"
        "如果是宽泛学习主题，chatbot_message 用 180-260 字打开地图，最多 3 个入口，"
        "结尾只问当前水平、已会/未会或入口选择中的一个问题。"
    ),
    "collecting_followup": (
        "阶段：已有 active requirement，继续收敛。优先消费 current_user_message 的新增事实；"
        "不要重复完整学习地图。若用户给出背景但没有直接选择入口，可给 2-4 个下一步入口；"
        "若已经收敛到单一知识点或明确练习产物，ready_for_board 才可为 true。"
    ),
}

DEFAULT_BLANK_BOARD_INTAKE_STAGE = "fresh_intake"

BLANK_BOARD_LEARNING_INTAKE_RESPONSE_CONTRACT = {
    "guidance_strategy": (
        "本轮采用的通用引导策略，必须匹配用户表达形态；宽泛主题优先 domain_map/choice_cards，"
        "练习缺水平优先 choice_cards，最近经历用 recent_experience，卡点用 stuck_point。"
    ),
    "entry_point_options": (
        "2-4 个候选入口或水平卡片，每项简短填写 label、why_it_matters、best_for；"
        "缺当前水平时优先给水平画像卡片，而不是高级路线。"
    ),
    "next_question": (
        "清单未完整时只问一个最有价值的问题；缺水平问当前水平/已会未会，"
        "缺入口问选择哪个入口；ready_for_board=true 时为空。"
    ),
}


def select_blank_board_intake_stage(
    *,
    existing_requirement_sheet: dict | None,
    existing_clarification: dict | None,
) -> str:
    if _has_active_requirement_context(existing_requirement_sheet, existing_clarification):
        return "collecting_followup"
    return DEFAULT_BLANK_BOARD_INTAKE_STAGE


def blank_board_intake_stage_policy(stage: str) -> str:
    return BLANK_BOARD_STAGE_POLICIES.get(stage, BLANK_BOARD_STAGE_POLICIES[DEFAULT_BLANK_BOARD_INTAKE_STAGE])


def blank_board_refinement_system_prompt(stage: str) -> str:
    return (
        "你是 OpenClass 的空白板书学习需求收敛器，也是左侧聊天框里的自然对话 AI。"
        "board_document_state.status 必须是 empty；本阶段只维护 LearningRequirementSheet，"
        "不生成板书、不冻结清单、不调用 Board AI。\n"
        "输出必须是 JSON，按 schema 顺序先写 chatbot_message，再写 route 和内部字段；"
        "chatbot_message 先给用户可见回复，控制在 180-260 字，最多一个主问题。\n"
        "route：ordinary_chat 表示普通聊天，不更新清单；requirement_refining 表示学习/练习需求收敛。\n"
        "ready_for_board：knowledge_board 只有 learning_goal 已经是单一知识点、概念、方法、步骤或问题，"
        "且 granularity=single_knowledge_point 时才为 true；practice_artifact 必须同时有 learning_goal、"
        "current_level、target_scenario。\n"
        f"{BLANK_BOARD_LEARNING_INTAKE_POLICY}\n"
        f"{BLANK_BOARD_FACT_BOUNDARY_POLICY}\n"
        f"{blank_board_intake_stage_policy(stage)}"
    )


def blank_board_context_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    placeholder_markers = (
        "待确认",
        "根据用户",
        "尚未明确",
        "先澄清用户",
    )
    if any(marker in text for marker in placeholder_markers):
        return ""
    return text


def compact_blank_board_context(
    data: dict[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    compact: dict[str, Any] = {}
    for field in fields:
        value = data.get(field)
        if isinstance(value, str):
            text = blank_board_context_text(value)
            if text:
                compact[field] = text
        elif isinstance(value, list):
            items = [_compact_blank_board_list_item(item) for item in value]
            items = [item for item in items if item]
            if items:
                compact[field] = items[:5]
        elif value not in (None, "", {}, []):
            compact[field] = value
    return compact or None


def _compact_blank_board_list_item(item: Any) -> Any:
    if isinstance(item, str):
        return blank_board_context_text(item)
    if isinstance(item, dict):
        compact = {
            key: value
            for key, value in item.items()
            if value not in (None, "", {}, [])
            and (not isinstance(value, str) or blank_board_context_text(value))
        }
        return compact or None
    return item


def _has_active_requirement_context(
    existing_requirement_sheet: dict | None,
    existing_clarification: dict | None,
) -> bool:
    if isinstance(existing_clarification, dict):
        label = str(existing_clarification.get("label") or "").strip()
        if label and label not in {"basic_chat", "unknown"}:
            return True
        if existing_clarification.get("key_facts") or existing_clarification.get("next_question"):
            return True
    if not isinstance(existing_requirement_sheet, dict):
        return False
    return any(
        bool(existing_requirement_sheet.get(field))
        for field in ("work_mode", "granularity", "current_questions", "risk_notes")
    )
