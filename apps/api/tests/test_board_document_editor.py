from app.models import LearningClarificationStatus
from app.services.board_document_editor import generate_from_requirements
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardDocumentEditResult, openai_course_ai


def test_generate_from_requirements_saves_first_model_output_without_quality_retry(monkeypatch) -> None:
    lesson = create_empty_lesson("一次生成")
    requirements = lesson.learning_requirements
    assert requirements is not None
    requirements.learning_goal = "生成一份板书"
    requirements.level = "入门"
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="ready",
        ready_for_board=True,
    )
    calls: list[dict[str, object]] = []

    def _fake_board_edit(**kwargs):
        calls.append(kwargs)
        return BoardDocumentEditResult(
            operation="replace_document",
            title="一次生成",
            content_text="# 一次生成\n\n## 1.1 概念引入\n\n保留第一稿 displaystyle。",
            summary="已生成。",
            chatbot_message="已生成。",
            section_titles=["1.1 概念引入"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)

    outcome = generate_from_requirements(
        lesson=lesson,
        requirements=requirements,
        clarification=clarification,
    )

    assert outcome.changed is True
    assert len(calls) == 1
    assert "board_generation_quality_pipeline" not in calls[0]["learning_requirement_context"]
    assert "document_quality_repair" not in calls[0]["learning_requirement_context"]
    assert "displaystyle" in outcome.new_document.content_text
