from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


LearningGuidanceDirection = Literal["none", "knowledge_point", "skill_practice"]
LearningNeedKind = Literal["none", "unknown", "new_knowledge", "skill_practice"]


class LearningPurposeDetection(BaseModel):
    has_learning_purpose: bool = False
    needs_guidance: bool = False
    need_kind: LearningNeedKind = "none"
    guidance_direction: LearningGuidanceDirection = "none"
    known_purpose: str = ""
    specific_knowledge_point: str = ""
    specific_practice_content: str = ""
    current_level: str = ""
    missing_piece: str = ""
    reason: str = ""

    @field_validator(
        "known_purpose",
        "specific_knowledge_point",
        "specific_practice_content",
        "current_level",
        "missing_piece",
        "reason",
        mode="before",
    )
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @model_validator(mode="after")
    def _normalize_direction(self) -> "LearningPurposeDetection":
        if not self.has_learning_purpose:
            self.needs_guidance = False
            self.need_kind = "none"
            self.guidance_direction = "none"
            self.known_purpose = ""
            self.specific_knowledge_point = ""
            self.specific_practice_content = ""
            self.current_level = ""
            return self
        if self.need_kind == "none":
            if self.guidance_direction == "knowledge_point":
                self.need_kind = "new_knowledge"
            elif self.guidance_direction == "skill_practice":
                self.need_kind = "skill_practice"
            else:
                self.need_kind = "unknown"
        if self.need_kind == "new_knowledge" and self.guidance_direction == "none":
            self.guidance_direction = "knowledge_point"
        if self.need_kind == "skill_practice" and self.guidance_direction == "none":
            self.guidance_direction = "skill_practice"
        if not self.needs_guidance:
            self.guidance_direction = "none"
        return self

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "has_learning_purpose": self.has_learning_purpose,
            "needs_guidance": self.needs_guidance,
            "need_kind": self.need_kind,
            "guidance_direction": self.guidance_direction,
            "known_purpose": self.known_purpose,
            "specific_knowledge_point": self.specific_knowledge_point,
            "specific_practice_content": self.specific_practice_content,
            "current_level": self.current_level,
            "missing_piece": self.missing_piece,
            "reason": self.reason,
        }


def no_learning_purpose_detection(reason: str = "") -> LearningPurposeDetection:
    return LearningPurposeDetection(
        has_learning_purpose=False,
        needs_guidance=False,
        need_kind="none",
        guidance_direction="none",
        reason=reason,
    )
