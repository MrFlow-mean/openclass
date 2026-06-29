from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.learning_requirement_refiner import LearningRequirement


LearnerProfileField = Literal[
    "current_level",
    "prior_knowledge",
    "difficulty_pattern",
    "goal_scenario",
    "preferred_learning_path",
    "confidence_state",
    "constraints",
]
LearnerProfileEvidenceSource = Literal[
    "user_message",
    "learning_purpose_detection",
    "learning_requirement",
    "previous_profile",
    "system_inference",
]
LearningIntakeNextFocus = Literal[
    "none",
    "current_level",
    "need_kind",
    "specific_knowledge_point",
    "specific_practice_content",
    "goal_scenario",
    "guided_discovery",
]


class LearnerProfileEvidence(BaseModel):
    profile_field: LearnerProfileField
    source: LearnerProfileEvidenceSource
    text: str = ""

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)


class LearnerProfile(BaseModel):
    current_level: str = ""
    prior_knowledge: str = ""
    difficulty_pattern: str = ""
    goal_scenario: str = ""
    preferred_learning_path: str = ""
    confidence_state: str = ""
    constraints: list[str] = Field(default_factory=list)
    evidence: list[LearnerProfileEvidence] = Field(default_factory=list)

    @field_validator(
        "current_level",
        "prior_knowledge",
        "difficulty_pattern",
        "goal_scenario",
        "preferred_learning_path",
        "confidence_state",
        mode="before",
    )
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    def to_prompt_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class LearningIntakeDecision(BaseModel):
    learner_profile: LearnerProfile = Field(default_factory=LearnerProfile)
    next_question_focus: LearningIntakeNextFocus = "none"
    question_policy_reason: str = ""
    guided_discovery: bool = False

    @field_validator("question_policy_reason", mode="before")
    @classmethod
    def _coerce_reason(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "learner_profile": self.learner_profile.to_prompt_payload(),
            "next_question_focus": self.next_question_focus,
            "question_policy_reason": self.question_policy_reason,
            "guided_discovery": self.guided_discovery,
        }


def build_learning_intake(
    *,
    user_message: str,
    learning_purpose_detection: LearningPurposeDetection,
    learning_requirement: LearningRequirement,
    previous_profile: LearnerProfile | None = None,
) -> LearningIntakeDecision:
    profile = _copy_profile(previous_profile)
    _merge_detection(profile, learning_purpose_detection)
    _merge_requirement(profile, learning_requirement)
    _merge_message(profile, user_message)

    next_focus, reason, guided_discovery = _choose_next_focus(
        profile=profile,
        detection=learning_purpose_detection,
        requirement=learning_requirement,
        user_message=user_message,
    )
    return LearningIntakeDecision(
        learner_profile=profile,
        next_question_focus=next_focus,
        question_policy_reason=reason,
        guided_discovery=guided_discovery,
    )


def _copy_profile(previous_profile: LearnerProfile | None) -> LearnerProfile:
    if previous_profile is None:
        return LearnerProfile()
    profile = LearnerProfile.model_validate(previous_profile.model_dump(mode="json"))
    for evidence in profile.evidence:
        evidence.source = "previous_profile"
    return profile


def _merge_detection(profile: LearnerProfile, detection: LearningPurposeDetection) -> None:
    _set_text(
        profile,
        "current_level",
        detection.current_level,
        source="learning_purpose_detection",
    )
    if detection.need_kind == "skill_practice":
        _set_text(
            profile,
            "preferred_learning_path",
            "练习或巩固已接触过的内容",
            source="learning_purpose_detection",
        )
    if detection.known_purpose and detection.need_kind == "skill_practice":
        _set_text(
            profile,
            "goal_scenario",
            detection.known_purpose,
            source="learning_purpose_detection",
        )


def _merge_requirement(profile: LearnerProfile, requirement: LearningRequirement) -> None:
    if requirement.learning_mode == "new_learning":
        _set_text(profile, "current_level", requirement.new_learning.current_level, source="learning_requirement")
        _set_text(
            profile,
            "goal_scenario",
            requirement.new_learning.application_scenario or requirement.new_learning.learning_purpose,
            source="learning_requirement",
        )
        _set_text(
            profile,
            "preferred_learning_path",
            requirement.new_learning.selected_entry_point,
            source="learning_requirement",
        )
        return

    if requirement.learning_mode == "practice_old_skill":
        _set_text(profile, "current_level", requirement.practice_old_skill.current_level, source="learning_requirement")
        _set_text(profile, "goal_scenario", requirement.practice_old_skill.practice_scenario, source="learning_requirement")
        if requirement.practice_old_skill.weak_points:
            _set_text(
                profile,
                "difficulty_pattern",
                "、".join(requirement.practice_old_skill.weak_points[:3]),
                source="learning_requirement",
            )
        _set_text(profile, "preferred_learning_path", "针对性练习", source="learning_requirement")


def _merge_message(profile: LearnerProfile, user_message: str) -> None:
    text = _compact(user_message)
    if not text:
        return

    level = _extract_level(text)
    _set_text(profile, "current_level", level, source="user_message")

    prior = _extract_prior_knowledge(text)
    _set_text(profile, "prior_knowledge", prior, source="user_message")

    difficulty = _extract_difficulty(text)
    _set_text(profile, "difficulty_pattern", difficulty, source="user_message")

    confidence = _extract_confidence(text)
    _set_text(profile, "confidence_state", confidence, source="user_message")

    constraints = _extract_constraints(text)
    for constraint in constraints:
        if constraint not in profile.constraints:
            profile.constraints.append(constraint)
            _append_evidence(profile, "constraints", "user_message", constraint)


def _choose_next_focus(
    *,
    profile: LearnerProfile,
    detection: LearningPurposeDetection,
    requirement: LearningRequirement,
    user_message: str,
) -> tuple[LearningIntakeNextFocus, str, bool]:
    if not detection.has_learning_purpose:
        return "none", "用户没有表达学习工作目的，本轮保持普通聊天。", False

    if _should_guided_discovery(user_message):
        return (
            "guided_discovery",
            "用户把入口选择交给系统或表达不知道从哪里开始，应给出少量通用入口建议。",
            True,
        )

    if detection.need_kind == "skill_practice" or requirement.learning_mode == "practice_old_skill":
        if not profile.current_level:
            return "current_level", "练习类任务已出现，但还缺少用户当前水平，先补最低必要画像。", False
        if not detection.specific_practice_content and not requirement.practice_old_skill.practice_content:
            return "specific_practice_content", "用户想练习但还没有明确练习内容。", False
        return "goal_scenario", "练习内容和水平已基本明确，可继续确认练习面向场景。", False

    if _is_broad_learning_intent(detection, requirement) and not profile.current_level:
        return "current_level", "用户只给出宽泛学习方向，先建立学习者起点画像再收敛内容入口。", False

    if _is_broad_learning_intent(detection, requirement):
        return "guided_discovery", "已有起点信息但学习入口仍宽泛，应给出 2-3 个通用入口供用户选择。", True

    if detection.need_kind == "unknown":
        return "need_kind", "已有学习目的但任务方式仍不明确，需要区分学习、练习或生成学习产物。", False

    if detection.need_kind == "new_knowledge" and not detection.specific_knowledge_point:
        return "specific_knowledge_point", "用户想学新知识但还没有明确到一个可执行入口。", False

    return "none", "本轮学习目的已足够清楚，保持自然承接。", False


def _is_broad_learning_intent(
    detection: LearningPurposeDetection,
    requirement: LearningRequirement,
) -> bool:
    has_domain = bool(requirement.domain.strip())
    has_specific_target = bool(
        detection.specific_knowledge_point.strip()
        or requirement.new_learning.target_knowledge_point.strip()
        or requirement.new_learning.selected_entry_point.strip()
    )
    return has_domain and detection.need_kind in {"unknown", "new_knowledge"} and not has_specific_target


def _should_guided_discovery(user_message: str) -> bool:
    text = _compact(user_message)
    return any(token in text for token in ("不知道", "没想法", "不确定", "你帮我", "你推荐", "你来定", "帮我选"))


def _set_text(
    profile: LearnerProfile,
    field_name: LearnerProfileField,
    value: str,
    *,
    source: LearnerProfileEvidenceSource,
) -> None:
    text = _compact(value)
    if not text:
        return
    current = getattr(profile, field_name)
    if current and (source != "user_message" or current == text):
        return
    setattr(profile, field_name, text[:120])
    _append_evidence(profile, field_name, source, text[:120])


def _append_evidence(
    profile: LearnerProfile,
    field_name: LearnerProfileField,
    source: LearnerProfileEvidenceSource,
    text: str,
) -> None:
    evidence = LearnerProfileEvidence(profile_field=field_name, source=source, text=text)
    if evidence not in profile.evidence:
        profile.evidence.append(evidence)


def _extract_level(text: str) -> str:
    level_markers = (
        ("零基础", "零基础"),
        ("从零", "零基础"),
        ("完全没接触", "零基础"),
        ("完全没学过", "零基础"),
        ("没学过", "零基础"),
        ("刚开始", "刚开始接触"),
        ("学过一点", "学过一点"),
        ("有一点基础", "有一点基础"),
        ("有基础", "有一定基础"),
        ("能做基础", "能完成基础任务"),
        ("查漏补缺", "想查漏补缺"),
        ("提高熟练", "想提高熟练度"),
    )
    for marker, label in level_markers:
        if marker in text:
            return label
    match = re.search(r"(?:我是|我在|目前|现在)?([^，,。；;]{1,24}(?:阶段|水平|基础))", text)
    return match.group(1).strip() if match else ""


def _extract_prior_knowledge(text: str) -> str:
    match = re.search(r"(?:学过|会|掌握|了解过)([^，,。；;]{1,40})", text)
    return match.group(0).strip() if match else ""


def _extract_difficulty(text: str) -> str:
    if any(token in text for token in ("看不懂", "听不懂", "不理解", "没理解")):
        return "理解卡点"
    if any(token in text for token in ("不会做", "做不出来", "没思路", "不知道怎么做")):
        return "执行或解题卡点"
    if any(token in text for token in ("不熟", "慢", "容易错", "不稳定")):
        return "熟练度或稳定性卡点"
    return ""


def _extract_confidence(text: str) -> str:
    if any(token in text for token in ("不知道", "不确定", "没想法", "不清楚", "没思路")):
        return "不确定，需要引导选择入口"
    if any(token in text for token in ("害怕", "焦虑", "担心", "怕难")):
        return "对学习难度存在顾虑"
    return ""


def _extract_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    if any(token in text for token in ("尽快", "快速", "马上", "短时间")):
        constraints.append("希望快速推进")
    if re.search(r"\d+\s*(?:分钟|小时|天|周)", text):
        constraints.append("有明确时间约束")
    if any(token in text for token in ("考试前", "面试前", "截止", "deadline")):
        constraints.append("有外部截止场景")
    return constraints


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
