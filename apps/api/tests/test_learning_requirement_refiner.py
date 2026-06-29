from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.learning_requirement_refiner import (
    CandidateEntryPoint,
    DiagnosticQuestion,
    LearningRequirement,
    LearningRequirementRefinementStateMachine,
    build_learning_requirement_from_detection,
    build_teaching_contract,
    evaluate_diagnostic_answer,
    generate_diagnostic_questions,
    recommend_entry_points,
    should_start_teaching,
    update_learning_requirement,
)


def test_vague_domain_collects_requirement_without_starting_teaching() -> None:
    requirement = build_learning_requirement_from_detection(
        "我想学数学",
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="new_knowledge",
            guidance_direction="knowledge_point",
            known_purpose="想学习一个笼统领域",
        ),
    )

    assert requirement.learning_mode == "new_learning"
    assert requirement.domain == "数学"
    assert requirement.status in {"collecting_new_learning_purpose", "recommending_entry_points"}
    assert should_start_teaching(requirement) is False
    assert requirement.next_question


def test_new_learning_recommends_entry_points_without_using_domain_as_target() -> None:
    requirement = LearningRequirement(
        learning_mode="new_learning",
        raw_user_input="我想学高等数学，为了以后学机器学习，但不知道从哪开始",
        domain="高等数学",
    )
    requirement.new_learning.learning_purpose = "以后学机器学习"

    candidates = recommend_entry_points(requirement)

    assert 2 <= len(candidates) <= 4
    assert all(candidate.knowledge_point != "高等数学" for candidate in candidates)
    assert all(candidate.reason for candidate in candidates)


def test_refiner_advances_previous_requirement_without_losing_domain() -> None:
    previous = build_learning_requirement_from_detection(
        "我想学高等数学",
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="new_knowledge",
            known_purpose="想学习一个笼统领域",
        ),
    )

    refined = build_learning_requirement_from_detection(
        "为了以后学机器学习",
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="new_knowledge",
            known_purpose="为了以后学机器学习",
        ),
        previous_requirement=previous,
    )

    assert refined.domain == "高等数学"
    assert refined.new_learning.learning_purpose == "为了以后学机器学习"
    assert refined.status == "recommending_entry_points"


def test_specific_new_knowledge_is_ready_to_teach() -> None:
    requirement = build_learning_requirement_from_detection(
        "我想学欧姆定律",
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=False,
            need_kind="new_knowledge",
            known_purpose="想学习一个明确知识点",
            specific_knowledge_point="欧姆定律",
        ),
    )

    assert requirement.learning_mode == "new_learning"
    assert requirement.new_learning.target_knowledge_point == "欧姆定律"
    assert should_start_teaching(requirement) is True
    assert "我们这次先学：欧姆定律" in requirement.teaching_contract


def test_practice_old_skill_generates_short_diagnostic_questions() -> None:
    requirement = build_learning_requirement_from_detection(
        "我想练习导数题",
        LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="skill_practice",
            guidance_direction="skill_practice",
            known_purpose="想练习旧知识技能",
            specific_practice_content="导数题",
        ),
    )

    assert requirement.learning_mode == "practice_old_skill"
    assert requirement.practice_old_skill.practice_content == "导数题"
    assert requirement.status == "diagnosing_current_level"
    assert 1 <= len(requirement.practice_old_skill.diagnostic_questions) <= 3


def test_diagnostic_answer_updates_level_and_weak_points() -> None:
    question = DiagnosticQuestion(question="用一句话说说你理解的“导数题”是什么。", mapped_skill="conceptual_understanding")
    result = evaluate_diagnostic_answer(question, "不清楚")
    requirement = LearningRequirement(learning_mode="practice_old_skill")
    requirement.practice_old_skill.practice_content = "导数题"
    requirement.practice_old_skill.diagnostic_results.append(result)
    requirement.practice_old_skill.weak_points.append(result.inferred_weak_point)
    requirement.practice_old_skill.current_level = "诊断显示概念理解还不稳定"

    assert result.result == "unclear"
    assert "conceptual_understanding" in requirement.practice_old_skill.weak_points
    assert should_start_teaching(requirement) is True


def test_user_delegates_entry_point_choice_and_reaches_contract() -> None:
    requirement = LearningRequirement(
        learning_mode="new_learning",
        raw_user_input="我不知道从哪开始",
        domain="一个学习方向",
    )
    requirement.new_learning.learning_purpose = "建立入门理解"
    requirement.new_learning.candidate_entry_points = [
        CandidateEntryPoint(
            knowledge_point="一个学习方向的基础概念",
            reason="它最适合作为第一个入口。",
            difficulty="easy",
        )
    ]

    refined = LearningRequirementRefinementStateMachine().advance(requirement, "你帮我决定从哪开始")

    assert refined.new_learning.selected_entry_point == "一个学习方向的基础概念"
    assert refined.status == "ready_to_teach"
    assert should_start_teaching(refined) is True
    assert "选择这个入口的原因：它最适合作为第一个入口。" in build_teaching_contract(refined)


def test_generate_diagnostic_questions_stays_short_and_skill_mapped() -> None:
    requirement = LearningRequirement(learning_mode="practice_old_skill")
    requirement.practice_old_skill.practice_content = "某项练习内容"

    questions = generate_diagnostic_questions(requirement)

    assert 1 <= len(questions) <= 3
    assert all(question.mapped_skill for question in questions)


def test_update_learning_requirement_keeps_ordinary_chat_empty() -> None:
    requirement = update_learning_requirement(
        LearningRequirement(),
        "你好，今天随便聊聊",
        LearningPurposeDetection(has_learning_purpose=False),
    )

    assert requirement.learning_mode == "unknown"
    assert requirement.domain == ""
    assert requirement.next_question == ""
