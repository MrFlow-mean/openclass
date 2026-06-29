from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.learning_purpose_detector import LearningPurposeDetection


LearningMode = Literal["new_learning", "practice_old_skill", "unknown"]
RefinementStatus = Literal[
    "collecting_mode",
    "collecting_new_learning_purpose",
    "resolving_target_knowledge_point",
    "recommending_entry_points",
    "collecting_practice_content",
    "diagnosing_current_level",
    "ready_to_teach",
]
EntryDifficulty = Literal["easy", "medium", "hard"]
DiagnosticResultStatus = Literal["correct", "partially_correct", "incorrect", "unclear"]
DiagnosticErrorType = Literal[
    "concept_error",
    "rule_error",
    "step_error",
    "calculation_error",
    "transfer_error",
    "expression_error",
    "unclear",
]


class CandidateEntryPoint(BaseModel):
    knowledge_point: str
    reason: str
    difficulty: EntryDifficulty = "easy"
    prerequisites: list[str] = Field(default_factory=list)


class DiagnosticQuestion(BaseModel):
    question: str
    mapped_skill: str


class DiagnosticResult(BaseModel):
    question: str
    user_answer: str
    result: DiagnosticResultStatus
    mapped_skill: str
    error_type: DiagnosticErrorType | None = None
    inferred_weak_point: str = ""


class TeachingPreferences(BaseModel):
    difficulty_level: str = ""
    teaching_style: str = ""
    session_time: str = ""


class NewLearningRequirement(BaseModel):
    learning_purpose: str = ""
    learning_context: str = ""
    motivation_trigger: str = ""
    desired_output: str = ""
    current_background: str = ""
    target_knowledge_point: str = ""
    candidate_entry_points: list[CandidateEntryPoint] = Field(default_factory=list)
    selected_entry_point: str = ""
    reason_for_recommendation: str = ""


class PracticeOldSkillRequirement(BaseModel):
    practice_content: str = ""
    current_level: str = ""
    weak_points: list[str] = Field(default_factory=list)
    practice_goal: str = ""
    diagnostic_results: list[DiagnosticResult] = Field(default_factory=list)
    diagnostic_questions: list[DiagnosticQuestion] = Field(default_factory=list)


class LearningRequirement(BaseModel):
    learning_mode: LearningMode = "unknown"
    raw_user_input: str = ""
    domain: str = ""
    new_learning: NewLearningRequirement = Field(default_factory=NewLearningRequirement)
    practice_old_skill: PracticeOldSkillRequirement = Field(default_factory=PracticeOldSkillRequirement)
    teaching_preferences: TeachingPreferences = Field(default_factory=TeachingPreferences)
    status: RefinementStatus = "collecting_mode"
    next_question: str = ""
    teaching_contract: str = ""

    @field_validator("raw_user_input", "domain", "next_question", "teaching_contract", mode="before")
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @model_validator(mode="after")
    def _refresh_status(self) -> "LearningRequirement":
        self.status = determine_status(self)
        return self

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "learning_mode": self.learning_mode,
            "raw_user_input": self.raw_user_input,
            "domain": self.domain,
            "new_learning": self.new_learning.model_dump(mode="json"),
            "practice_old_skill": self.practice_old_skill.model_dump(mode="json"),
            "teaching_preferences": self.teaching_preferences.model_dump(mode="json"),
            "status": self.status,
            "next_question": self.next_question,
            "ready_to_teach": should_start_teaching(self),
            "teaching_contract": self.teaching_contract,
        }


def create_empty_learning_requirement(raw_user_input: str) -> LearningRequirement:
    return LearningRequirement(raw_user_input=raw_user_input)


def update_learning_requirement(
    requirement: LearningRequirement,
    user_message: str,
    ai_analysis: dict[str, object] | LearningPurposeDetection | None = None,
) -> LearningRequirement:
    updated = LearningRequirement.model_validate(requirement.model_dump(mode="json"))
    if user_message.strip():
        updated.raw_user_input = user_message.strip()

    if isinstance(ai_analysis, LearningPurposeDetection):
        if not ai_analysis.has_learning_purpose:
            return LearningRequirement(raw_user_input=updated.raw_user_input)
        _merge_detection(updated, ai_analysis)
    elif isinstance(ai_analysis, dict):
        _merge_mapping(updated, ai_analysis)

    if updated.learning_mode == "unknown":
        updated.learning_mode = infer_learning_mode(user_message, updated)
    if not updated.domain:
        updated.domain = extract_domain(user_message)
    if updated.learning_mode == "practice_old_skill" and not updated.practice_old_skill.practice_content:
        updated.practice_old_skill.practice_content = extract_practice_content(user_message)

    updated.status = determine_status(updated)
    if updated.status == "recommending_entry_points" and not updated.new_learning.candidate_entry_points:
        updated.new_learning.candidate_entry_points = recommend_entry_points(updated)
    if updated.status == "diagnosing_current_level" and not updated.practice_old_skill.diagnostic_questions:
        updated.practice_old_skill.diagnostic_questions = generate_diagnostic_questions(updated)

    if _user_delegates_choice(user_message) and updated.new_learning.candidate_entry_points:
        selected = updated.new_learning.candidate_entry_points[0]
        updated.new_learning.selected_entry_point = selected.knowledge_point
        updated.new_learning.reason_for_recommendation = selected.reason

    updated.status = determine_status(updated)
    updated.next_question = "" if should_start_teaching(updated) else generate_next_refinement_question(updated)
    updated.teaching_contract = build_teaching_contract(updated) if should_start_teaching(updated) else ""
    return updated


def infer_learning_mode(user_message: str, requirement: LearningRequirement | None = None) -> LearningMode:
    if requirement and requirement.learning_mode != "unknown":
        return requirement.learning_mode
    text = user_message.strip().lower()
    if not text:
        return "unknown"
    practice_markers = ("练", "练习", "复习", "巩固", "刷", "做题", "提高熟练", "practice", "review")
    new_markers = ("学", "学习", "了解", "入门", "没学过", "从零", "看懂", "理解", "learn")
    if any(marker in text for marker in practice_markers):
        return "practice_old_skill"
    if any(marker in text for marker in new_markers):
        return "new_learning"
    return "unknown"


def extract_domain(user_message: str) -> str:
    text = _compact(user_message)
    text = re.split(r"[，,。；;]|为了|因为|但是|但|用于|用来|面对", text, maxsplit=1)[0]
    text = re.sub(r"^(我|俺|本人)?(现在|最近)?(想|想要|希望|打算|准备)?", "", text)
    text = re.sub(r"^(学|学习|了解|入门|看懂|理解|练习|练|复习|巩固|刷|做)", "", text)
    text = re.sub(r"(题|练习|内容)$", "", text).strip()
    return text[:40]


def extract_practice_content(user_message: str) -> str:
    text = _compact(user_message)
    text = re.split(r"[，,。；;]|为了|因为|但是|但", text, maxsplit=1)[0]
    text = re.sub(r"^(我|俺|本人)?(现在|最近)?(想|想要|希望|打算|准备)?", "", text)
    text = re.sub(r"^(练习|练|复习|巩固|刷|做)", "", text)
    return text[:50]


def get_missing_required_slots(requirement: LearningRequirement) -> list[str]:
    if requirement.learning_mode == "unknown":
        return ["learning_mode"]
    if requirement.learning_mode == "new_learning":
        missing: list[str] = []
        if not requirement.domain:
            missing.append("domain")
        if not (requirement.new_learning.target_knowledge_point or requirement.new_learning.selected_entry_point):
            missing.append("target_knowledge_point")
        return missing
    missing = []
    if not requirement.practice_old_skill.practice_content:
        missing.append("practice_content")
    if not (requirement.practice_old_skill.current_level or requirement.practice_old_skill.diagnostic_results):
        missing.append("current_level")
    return missing


def generate_next_refinement_question(requirement: LearningRequirement) -> str:
    if requirement.learning_mode == "unknown":
        return "你现在是想学习一个之前没学过的新知识，还是想练习、巩固已经学过的内容？"
    if requirement.learning_mode == "new_learning":
        if not requirement.domain:
            return "你想学习的大方向是什么？可以说一个领域、一本书、一个项目场景，或者一个你看不懂的概念。"
        if not (requirement.new_learning.learning_purpose or requirement.new_learning.learning_context):
            return "你学这个主要是为了什么场景？比如考试、预习、工作项目、兴趣理解、做题、写代码、看懂资料，还是解决现实问题？"
        if not (requirement.new_learning.target_knowledge_point or requirement.new_learning.selected_entry_point):
            return "你现在有明确想学的知识点吗？如果没有，我可以给你推荐 2-4 个适合开始的入口。"
    if requirement.learning_mode == "practice_old_skill":
        if not requirement.practice_old_skill.practice_content:
            return "你想具体练哪一块？比如基础概念、规则题、应用题、综合题、代码实现、表达讲解。"
        if not (requirement.practice_old_skill.current_level or requirement.practice_old_skill.diagnostic_results):
            return "你现在大概是什么水平？完全没学过、学过一点、能做基础题，还是想提高速度和熟练度？"
    return ""


def recommend_entry_points(requirement: LearningRequirement) -> list[CandidateEntryPoint]:
    domain = requirement.domain or requirement.new_learning.learning_purpose or "这个方向"
    purpose = requirement.new_learning.learning_purpose or requirement.new_learning.desired_output
    suffix = f"，并服务于“{purpose}”这个目标" if purpose else ""
    return [
        CandidateEntryPoint(
            knowledge_point=f"{domain}的一个基础概念",
            reason=f"先选一个最小概念，有助于建立后续学习的共同语言{suffix}。",
            difficulty="easy",
        ),
        CandidateEntryPoint(
            knowledge_point=f"{domain}的一个典型例子",
            reason="用一个具体例子进入，能快速暴露你已经懂什么、哪里需要补。",
            difficulty="easy",
        ),
        CandidateEntryPoint(
            knowledge_point=f"{domain}的一个核心关系",
            reason="核心关系通常连接概念、规则和应用，适合作为正式学习前的入口。",
            difficulty="medium",
        ),
    ]


def generate_diagnostic_questions(requirement: LearningRequirement) -> list[DiagnosticQuestion]:
    content = requirement.practice_old_skill.practice_content or requirement.domain or "这个内容"
    return [
        DiagnosticQuestion(question=f"用一句话说说你理解的“{content}”是什么。", mapped_skill="conceptual_understanding"),
        DiagnosticQuestion(question=f"做一个最基础的“{content}”任务时，你通常第一步会怎么做？", mapped_skill="basic_procedure"),
    ]


def evaluate_diagnostic_answer(question: DiagnosticQuestion, user_answer: str) -> DiagnosticResult:
    answer = _compact(user_answer)
    if not answer or any(token in answer for token in ("不知道", "不会", "不清楚", "没思路")):
        return DiagnosticResult(
            question=question.question,
            user_answer=user_answer,
            result="unclear",
            mapped_skill=question.mapped_skill,
            error_type="unclear",
            inferred_weak_point=question.mapped_skill,
        )
    if len(answer) < 8:
        return DiagnosticResult(
            question=question.question,
            user_answer=user_answer,
            result="partially_correct",
            mapped_skill=question.mapped_skill,
            error_type="expression_error",
            inferred_weak_point=question.mapped_skill,
        )
    return DiagnosticResult(
        question=question.question,
        user_answer=user_answer,
        result="correct",
        mapped_skill=question.mapped_skill,
    )


def should_start_teaching(requirement: LearningRequirement) -> bool:
    if requirement.learning_mode == "new_learning":
        return bool(requirement.domain and (requirement.new_learning.target_knowledge_point or requirement.new_learning.selected_entry_point))
    if requirement.learning_mode == "practice_old_skill":
        return bool(
            requirement.practice_old_skill.practice_content
            and (requirement.practice_old_skill.current_level or requirement.practice_old_skill.diagnostic_results)
        )
    return False


def build_teaching_contract(requirement: LearningRequirement) -> str:
    if requirement.learning_mode == "new_learning":
        target = requirement.new_learning.selected_entry_point or requirement.new_learning.target_knowledge_point
        reason = requirement.new_learning.reason_for_recommendation or "这个入口足够具体，适合在一次教学中开始。"
        desired = requirement.new_learning.desired_output or f"理解“{target}”的核心含义，并能用自己的话说明它。"
        return (
            f"我们这次先学：{target}\n"
            f"选择这个入口的原因：{reason}\n"
            f"你学完应该能做到：{desired}\n"
            "接下来我会用：解释 → 示例 → 你尝试 → 反馈 → 小测 的方式带你完成。"
        )
    target = requirement.practice_old_skill.practice_content
    reason = "你已经明确了练习内容和当前水平，适合进入针对性练习。"
    desired = requirement.practice_old_skill.practice_goal or f"更稳定地完成“{target}”相关任务，并知道自己的薄弱点。"
    return (
        f"我们这次先练：{target}\n"
        f"选择这个入口的原因：{reason}\n"
        f"你练完应该能做到：{desired}\n"
        "接下来我会用：解释 → 示例 → 你尝试 → 反馈 → 小测 的方式带你完成。"
    )


def determine_status(requirement: LearningRequirement) -> RefinementStatus:
    if requirement.learning_mode == "unknown":
        return "collecting_mode"
    if requirement.learning_mode == "new_learning":
        if should_start_teaching(requirement):
            return "ready_to_teach"
        if not (requirement.new_learning.learning_purpose or requirement.new_learning.learning_context):
            return "collecting_new_learning_purpose"
        if not requirement.new_learning.target_knowledge_point:
            return "recommending_entry_points" if requirement.domain else "resolving_target_knowledge_point"
        return "resolving_target_knowledge_point"
    if should_start_teaching(requirement):
        return "ready_to_teach"
    if not requirement.practice_old_skill.practice_content:
        return "collecting_practice_content"
    return "diagnosing_current_level"


class LearningRequirementRefinementStateMachine:
    def advance(
        self,
        requirement: LearningRequirement,
        latest_user_message: str,
        ai_analysis: dict[str, object] | LearningPurposeDetection | None = None,
    ) -> LearningRequirement:
        return update_learning_requirement(requirement, latest_user_message, ai_analysis)


def build_learning_requirement_from_detection(
    raw_user_input: str,
    detection: LearningPurposeDetection,
    previous_requirement: LearningRequirement | None = None,
) -> LearningRequirement:
    requirement = previous_requirement or create_empty_learning_requirement(raw_user_input)
    return update_learning_requirement(requirement, raw_user_input, detection)


def _merge_detection(requirement: LearningRequirement, detection: LearningPurposeDetection) -> None:
    if not detection.has_learning_purpose:
        requirement.learning_mode = "unknown"
        return
    requirement.learning_mode = _mode_from_detection(detection)
    detected_domain = extract_domain(requirement.raw_user_input)
    if detected_domain and not requirement.domain:
        requirement.domain = detected_domain
    if detection.known_purpose:
        requirement.new_learning.learning_purpose = detection.known_purpose
    if requirement.learning_mode == "new_learning":
        requirement.new_learning.target_knowledge_point = detection.specific_knowledge_point
    if requirement.learning_mode == "practice_old_skill":
        requirement.practice_old_skill.practice_content = detection.specific_practice_content or extract_practice_content(
            requirement.raw_user_input
        )
        requirement.practice_old_skill.current_level = detection.current_level


def _merge_mapping(requirement: LearningRequirement, raw: dict[str, object]) -> None:
    mode = raw.get("learningMode") or raw.get("learning_mode")
    if mode in {"new_learning", "practice_old_skill", "unknown"}:
        requirement.learning_mode = mode  # type: ignore[assignment]
    domain = raw.get("domain")
    if isinstance(domain, str):
        requirement.domain = domain
    target = raw.get("targetKnowledgePoint") or raw.get("target_knowledge_point")
    if isinstance(target, str):
        requirement.new_learning.target_knowledge_point = target
    practice = raw.get("practiceContent") or raw.get("practice_content")
    if isinstance(practice, str):
        requirement.practice_old_skill.practice_content = practice
    current_level = raw.get("currentLevel") or raw.get("current_level")
    if isinstance(current_level, str):
        requirement.practice_old_skill.current_level = current_level


def _mode_from_detection(detection: LearningPurposeDetection) -> LearningMode:
    if detection.need_kind == "new_knowledge":
        return "new_learning"
    if detection.need_kind == "skill_practice":
        return "practice_old_skill"
    return "unknown"


def _user_delegates_choice(message: str) -> bool:
    return any(token in message for token in ("你帮我定", "你帮我决定", "你推荐", "你来定", "帮我选"))


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
