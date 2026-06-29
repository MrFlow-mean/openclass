from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.minimal_learning_requirement import build_minimal_learning_requirement


def test_minimal_requirement_prioritizes_current_level_for_unknown_learning_intent() -> None:
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
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": ["current_level", "need_kind"],
        "next_question_focus": "current_level",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }


def test_minimal_requirement_records_new_knowledge_point_as_core_factor() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=False,
            need_kind="new_knowledge",
            known_purpose="想学习一个新知识点",
            specific_knowledge_point="一个明确知识点",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "new_knowledge",
        "known_purpose": "想学习一个新知识点",
        "specific_knowledge_point": "一个明确知识点",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": [],
        "next_question_focus": "none",
        "core_factors_recorded": True,
        "board_work_allowed": True,
    }


def test_minimal_requirement_guides_new_knowledge_toward_specific_point() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="new_knowledge",
            known_purpose="想学一个笼统领域",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "new_knowledge",
        "known_purpose": "想学一个笼统领域",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": ["current_level", "specific_knowledge_point"],
        "next_question_focus": "current_level",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }


def test_minimal_requirement_records_skill_practice_content_and_level() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=False,
            need_kind="skill_practice",
            known_purpose="想基于已有知识练习一项技能",
            specific_practice_content="一个明确练习内容",
            current_level="有一点基础",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "skill_practice",
        "known_purpose": "想基于已有知识练习一项技能",
        "specific_knowledge_point": "",
        "specific_practice_content": "一个明确练习内容",
        "current_level": "有一点基础",
        "missing_items": [],
        "next_question_focus": "none",
        "core_factors_recorded": True,
        "board_work_allowed": True,
    }


def test_minimal_requirement_asks_current_level_before_practice_content() -> None:
    requirement = build_minimal_learning_requirement(
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="skill_practice",
            known_purpose="想基于已有知识练习一项技能",
        )
    )

    assert requirement.to_prompt_payload() == {
        "has_learning_purpose": True,
        "need_kind": "skill_practice",
        "known_purpose": "想基于已有知识练习一项技能",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": ["current_level", "specific_practice_content"],
        "next_question_focus": "current_level",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }
