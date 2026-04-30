import pytest

from app.models import ChatRequest
from app.services.ai_workflow import course_workflow
from app.services.course_store import build_initial_course_package
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai


def test_course_workflow_reset_does_not_execute_legacy_role_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    package = build_initial_course_package()
    lesson = create_empty_lesson("流程重写")
    package.lessons.append(lesson)

    def fail_if_ai_role_is_called(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("reset workflow should not call legacy AI role parsing")

    monkeypatch.setattr(openai_course_ai, "_parse", fail_if_ai_role_is_called)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我要从零开始学虚拟内存，请生成板书并开始讲"),
        }
    )

    assert result["board_decision"].action == "no_change"
    assert "旧版角色流程节点已删除" in result["board_decision"].reason
    assert result["document_updated"] is False
    assert result["teacher_document"] == lesson.board_document
    assert result["board_teaching_guide"] is None
    assert result["resource_matches"] == []
