from app.models import ChatRequest
from app.services.chat.strong_reasoning import chatbot_message_with_solver_context, requests_complex_reasoning
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ComplexProblemSolution, openai_course_ai


def test_requests_complex_reasoning_detects_generic_reasoning_terms() -> None:
    assert requests_complex_reasoning("请深入推导这个问题") is True
    assert requests_complex_reasoning("普通聊一下学习计划") is False


def test_solver_context_returns_original_message_without_client(monkeypatch) -> None:
    lesson = create_empty_lesson("测试")
    monkeypatch.setattr(openai_course_ai, "client", None)

    message, metadata = chatbot_message_with_solver_context(
        lesson=lesson,
        request=ChatRequest(message="请深入分析"),
        user_message="请深入分析",
        target_excerpt=None,
        board_summary="",
        resource_summary="",
        conversation_summary="",
    )

    assert message == "请深入分析"
    assert metadata == {}


def test_solver_context_appends_hidden_material(monkeypatch) -> None:
    lesson = create_empty_lesson("测试")
    monkeypatch.setattr(openai_course_ai, "client", object())
    monkeypatch.setattr(
        openai_course_ai,
        "solve_complex_problem",
        lambda **kwargs: ComplexProblemSolution(
            summary="结论",
            answer="答案材料",
            confidence="high",
            limits="前提",
            model="reasoning-model",
            reasoning_effort="high",
        ),
    )

    message, metadata = chatbot_message_with_solver_context(
        lesson=lesson,
        request=ChatRequest(message="请深入分析"),
        user_message="请深入分析",
        target_excerpt="目标",
        board_summary="板书",
        resource_summary="资料",
        conversation_summary="对话",
    )

    assert "隐藏强推理工具已给出解题材料" in message
    assert "答案材料" in message
    assert metadata["strong_reasoning_tool"]["model"] == "reasoning-model"
    assert metadata["strong_reasoning_tool"]["confidence"] == "high"
