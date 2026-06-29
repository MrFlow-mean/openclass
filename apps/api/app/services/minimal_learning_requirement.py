from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.learning_purpose_detector import LearningNeedKind, LearningPurposeDetection


MinimalRequirementMissingItem = Literal["need_kind", "specific_learning_content", "current_level"]
MinimalRequirementNextFocus = Literal["none", "need_kind", "specific_learning_content", "current_level"]


class MinimalLearningRequirement(BaseModel):
    has_learning_purpose: bool = False
    need_kind: LearningNeedKind = "none"
    known_purpose: str = ""
    specific_learning_content: str = ""
    current_level: str = ""
    missing_items: list[MinimalRequirementMissingItem] = Field(default_factory=list)
    next_question_focus: MinimalRequirementNextFocus = "none"

    @field_validator("known_purpose", "specific_learning_content", "current_level", mode="before")
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)

    @model_validator(mode="after")
    def _normalize_empty_state(self) -> "MinimalLearningRequirement":
        if not self.has_learning_purpose:
            self.need_kind = "none"
            self.known_purpose = ""
            self.specific_learning_content = ""
            self.current_level = ""
            self.missing_items = []
            self.next_question_focus = "none"
        return self

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "has_learning_purpose": self.has_learning_purpose,
            "need_kind": self.need_kind,
            "known_purpose": self.known_purpose,
            "specific_learning_content": self.specific_learning_content,
            "current_level": self.current_level,
            "missing_items": list(self.missing_items),
            "next_question_focus": self.next_question_focus,
        }


def build_minimal_learning_requirement(detection: LearningPurposeDetection) -> MinimalLearningRequirement:
    if not detection.has_learning_purpose:
        return MinimalLearningRequirement()

    missing_items: list[MinimalRequirementMissingItem] = []
    if detection.need_kind in {"none", "unknown"}:
        missing_items.append("need_kind")
    if not detection.specific_learning_content.strip():
        missing_items.append("specific_learning_content")
    if not detection.current_level.strip():
        missing_items.append("current_level")

    next_focus: MinimalRequirementNextFocus = missing_items[0] if missing_items else "none"
    return MinimalLearningRequirement(
        has_learning_purpose=True,
        need_kind=detection.need_kind,
        known_purpose=detection.known_purpose,
        specific_learning_content=detection.specific_learning_content,
        current_level=detection.current_level,
        missing_items=missing_items,
        next_question_focus=next_focus,
    )
