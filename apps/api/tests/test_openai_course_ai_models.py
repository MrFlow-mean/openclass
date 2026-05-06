from app.models import AIModelSelection, BoardDocument, LearningRequirementSheet
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
    monkeypatch.delenv("OPENAI_PM_MODEL", raising=False)

    ai = OpenAICourseAI()

    assert ai._model_for("catalog") == ("openai", "gpt-5.4-mini")
    assert ai._model_for("pm") == ("openai", "gpt-5.4-nano")


def test_runtime_roles_ignore_frontend_text_selection(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_TEACHER_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_LESSON_MODEL", raising=False)
    ai = OpenAICourseAI()

    with bind_text_model_selection(AIModelSelection(provider="google", model="gemini-test")):
        assert ai._model_for("teacher") == ("openai", "gpt-5-mini")
        assert ai._model_for("lesson") == ("openai", "gpt-5-mini")


def test_status_does_not_expose_removed_board_or_guide_roles() -> None:
    ai = OpenAICourseAI()

    models = ai.status()["models"]

    assert "board" not in models
    assert "guide" not in models


def test_document_and_guide_generation_use_pm_and_teacher_roles(monkeypatch) -> None:
    ai = OpenAICourseAI.__new__(OpenAICourseAI)
    roles: list[str] = []
    document = BoardDocument(title="讲义", content_text="矩阵特征值用于描述线性变换。")
    requirements = _learning_requirement_sheet()

    def fake_parse(role, **kwargs):
        roles.append(role)
        return None

    monkeypatch.setattr(ai, "_parse", fake_parse)

    ai.generate_board_decision(
        lesson_title="线性代数",
        request_message="帮我生成讲义",
        selection=None,
        interaction_mode="ask",
        scope_action=None,
        requirements=requirements,
        document=document,
        resource_matches=[],
    )
    ai.generate_document_edit(
        lesson_id="lesson_1",
        lesson_title="线性代数",
        current_branch="main",
        request_message="帮我生成讲义",
        selection=None,
        interaction_mode="ask",
        scope_action=None,
        requirements=requirements,
        document=document,
        selected_reference=None,
    )
    ai.generate_teaching_guide(
        lesson_id="lesson_1",
        lesson_title="线性代数",
        requirements=requirements,
        document=document,
    )
    ai.generate_board_teaching_guide(
        lesson_title="线性代数",
        request_message="讲一下",
        requirements=requirements,
        document=document,
    )

    assert roles == ["pm", "teacher", "teacher", "teacher"]


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
