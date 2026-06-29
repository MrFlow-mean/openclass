from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.minimal_learning_requirement import build_minimal_learning_requirement


def test_minimal_requirement_prioritizes_binary_need_kind_first() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="unknown",
            known_purpose="想开始学习，但还没说清方向",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "unknown",
        "known_purpose": "想开始学习，但还没说清方向",
        "specific_learning_content": "",
        "current_level": "",
        "missing_items": ["need_kind", "specific_learning_content", "current_level"],
        "next_question_focus": "need_kind",
    }


def test_minimal_requirement_records_specific_content_and_current_level() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="new_knowledge",
            known_purpose="想学习一个新知识点",
            specific_learning_content="一个明确知识点",
            current_level="零基础",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "new_knowledge",
        "known_purpose": "想学习一个新知识点",
        "specific_learning_content": "一个明确知识点",
        "current_level": "零基础",
        "missing_items": [],
        "next_question_focus": "none",
    }


def test_minimal_requirement_records_skill_practice_level_before_content_gap() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="skill_practice",
            known_purpose="想基于已有知识练习一项技能",
            current_level="有一点基础",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "skill_practice",
        "known_purpose": "想基于已有知识练习一项技能",
        "specific_learning_content": "",
        "current_level": "有一点基础",
        "missing_items": ["specific_learning_content"],
        "next_question_focus": "specific_learning_content",
    }
