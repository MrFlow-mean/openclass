from app.models import AIModelSelection, LearningRequirementSheet
from app.services.openai_course_ai import OpenAICourseAI, bind_text_model_selection


def _learning_requirement_sheet() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="线性代数",
        learning_goal="理解矩阵特征值",
        level="本科",
        known_background="学过矩阵乘法",
        current_questions=["特征值有什么用"],
        learning_need_checklist=["特征值定义"],
        target_depth="概念理解",
        output_preference="中文讲义",
        boundary="不展开证明",
        board_scope=["当前课程"],
        success_criteria="能判断资料章节是否相关",
    )


def test_catalog_role_uses_openai_mini_by_default(monkeypatch) -> None:
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.delenv("OPENAI_CATALOG_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BOARD_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_PM_MODEL", raising=False)

    ai = OpenAICourseAI()

    assert ai._model_for("catalog") == ("openai", "gpt-5.4-mini")
    assert ai._model_for("pm") == ("openai", "gpt-5.4-nano")
    assert ai._model_for("board") == ("openai", "gpt-5.5")


def test_board_role_ignores_frontend_text_selection(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BOARD_MODEL", raising=False)
    ai = OpenAICourseAI()

    with bind_text_model_selection(AIModelSelection(provider="google", model="gemini-test")):
        assert ai._model_for("board") == ("openai", "gpt-5.5")
        assert ai._model_for("guide") == ("google", "gemini-test")


def test_resource_catalog_methods_use_catalog_role(monkeypatch) -> None:
    ai = OpenAICourseAI.__new__(OpenAICourseAI)
    roles: list[str] = []

    def fake_parse(role, **kwargs):
        roles.append(role)
        return None

    monkeypatch.setattr(ai, "_parse", fake_parse)

    ai.generate_resource_outline(
        resource_name="教材.pdf",
        mime_type="application/pdf",
        extracted_text="第一章 特征值与特征向量",
    )
    ai.compare_requirements_to_resource_catalog(
        learning_requirement_sheet=_learning_requirement_sheet(),
        resource_candidates=[
            {
                "resource_id": "res_1",
                "chapter_id": "ch_1",
                "resource_name": "教材.pdf",
                "chapter_title": "特征值",
            }
        ],
    )

    assert roles == ["catalog", "catalog"]
