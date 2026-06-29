from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


LearningGuidanceDirection = Literal["none", "knowledge_point", "skill_practice"]


class LearningPurposeDetection(BaseModel):
    has_learning_purpose: bool = False
    needs_guidance: bool = False
    guidance_direction: LearningGuidanceDirection = "none"
    known_purpose: str = ""
    missing_piece: str = ""
    reason: str = ""

    @field_validator("known_purpose", "missing_piece", "reason", mode="before")
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @model_validator(mode="after")
    def _normalize_direction(self) -> "LearningPurposeDetection":
        if not self.has_learning_purpose:
            self.needs_guidance = False
            self.guidance_direction = "none"
            return self
        if not self.needs_guidance:
            self.guidance_direction = "none"
        return self

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "has_learning_purpose": self.has_learning_purpose,
            "needs_guidance": self.needs_guidance,
            "guidance_direction": self.guidance_direction,
            "known_purpose": self.known_purpose,
            "missing_piece": self.missing_piece,
            "reason": self.reason,
        }


def no_learning_purpose_detection(reason: str = "") -> LearningPurposeDetection:
    return LearningPurposeDetection(
        has_learning_purpose=False,
        needs_guidance=False,
        guidance_direction="none",
        reason=reason,
    )
