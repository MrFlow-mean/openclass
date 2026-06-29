import inspect

from app.services import learner_profile
from app.services.learner_profile import LearnerProfile, build_learning_intake
from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.learning_requirement_refiner import build_learning_requirement_from_detection


def test_broad_learning_intake_prioritizes_current_level() -> None:
    detection = LearningPurposeDetection(
        has_learning_purpose=True,
        needs_guidance=True,
        need_kind="unknown",
        known_purpose="想学习一个宽泛方向",
    )
    requirement = build_learning_requirement_from_detection("我想学一个新方向", detection)

    intake = build_learning_intake(
        user_message="我想学一个新方向",
        learning_purpose_detection=detection,
        learning_requirement=requirement,
    )

    assert intake.next_question_focus == "current_level"
    assert intake.guided_discovery is False
    assert "学习者起点画像" in intake.question_policy_reason


def test_broad_learning_intake_uses_previous_profile_for_guided_discovery() -> None:
    detection = LearningPurposeDetection(
        has_learning_purpose=True,
        needs_guidance=True,
        need_kind="unknown",
        known_purpose="想学习一个宽泛方向",
    )
    requirement = build_learning_requirement_from_detection("我想学一个新方向", detection)
    previous = LearnerProfile(current_level="零基础")

    intake = build_learning_intake(
        user_message="我想学一个新方向",
        learning_purpose_detection=detection,
        learning_requirement=requirement,
        previous_profile=previous,
    )

    assert intake.learner_profile.current_level == "零基础"
    assert intake.next_question_focus == "guided_discovery"
    assert intake.guided_discovery is True


def test_user_message_can_correct_previous_profile() -> None:
    detection = LearningPurposeDetection(
        has_learning_purpose=True,
        needs_guidance=True,
        need_kind="new_knowledge",
        known_purpose="想学习一个宽泛方向",
    )
    requirement = build_learning_requirement_from_detection("其实我有基础", detection)
    previous = LearnerProfile(current_level="零基础")

    intake = build_learning_intake(
        user_message="其实我有基础",
        learning_purpose_detection=detection,
        learning_requirement=requirement,
        previous_profile=previous,
    )

    assert intake.learner_profile.current_level == "有一定基础"
    assert any(evidence.source == "user_message" for evidence in intake.learner_profile.evidence)


def test_learner_profile_service_has_no_domain_specific_intake_terms() -> None:
    source = inspect.getsource(learner_profile)

    forbidden_terms = ("数学", "外语", "编程", "经济学", "年级", "词汇量", "项目经验")

    assert all(term not in source for term in forbidden_terms)
