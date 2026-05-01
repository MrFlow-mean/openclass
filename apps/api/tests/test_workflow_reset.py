import pytest

from app.models import (
    AIModelSelection,
    ChatRequest,
    GoogleRealtimeSessionRequest,
    RealtimeConnectRequest,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers import realtime as realtime_router
from app.services.ai_workflow import course_workflow
from app.services.course_runtime import build_lesson_for_topic
from app.services.course_store import build_initial_course_package
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai
from app.services.openai_realtime import google_realtime_teacher, openai_realtime_teacher


def _user() -> UserView:
    return UserView(
        id="user_test",
        email="test@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


def _disable_course_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "assess_learning_requirements", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_document_edit", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_board_teaching_guide", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_teaching_guide", lambda **kwargs: None)


def test_course_workflow_collects_requirement_sheet_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="我想学数学"),
        }
    )

    requirements = result["learning_requirement_sheet"]
    assert result["board_decision"].action == "clarify_request"
    assert result["document_updated"] is False
    assert requirements.theme == "数学"
    assert "学习主题：数学" in requirements.learning_need_checklist
    assert result["learning_clarification"].can_start is False
    assert "学习者当前水平/已学背景" in result["learning_clarification"].missing_items


def test_course_workflow_generates_board_when_requirement_is_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("新课堂")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(
                message="我是小学生，我们老师刚刚给我们讲了平方和开方，你能为我讲解一下相关的知识吗？"
            ),
        }
    )

    assert result["board_decision"].action == "edit_board"
    assert result["document_updated"] is True
    assert result["learning_clarification"].can_start is True
    assert "平方和开方" in result["teacher_document"].content_text
    assert result["board_teaching_guide"] is not None


def test_course_workflow_replaces_topic_when_user_corrects_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_course_ai(monkeypatch)
    package = build_initial_course_package()
    lesson = create_empty_lesson("数学")
    package.lessons.append(lesson)

    result = course_workflow.invoke(
        {
            "lesson": lesson,
            "course_package": package,
            "request": ChatRequest(message="其实我不想学数学，我想学化学"),
        }
    )

    requirements = result["learning_requirement_sheet"]
    assert requirements.theme == "化学"
    assert "学习主题：化学" in requirements.learning_need_checklist
    assert all("学习主题：数学" not in item for item in requirements.learning_need_checklist)


def test_lesson_generation_still_creates_blank_lesson_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_ai_generation_is_called(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("blank lesson creation should not call AI document generation")

    monkeypatch.setattr(openai_course_ai, "generate_lesson_document", fail_if_ai_generation_is_called)

    lesson = build_lesson_for_topic("新课堂")

    assert lesson.title == "新课堂"
    assert lesson.board_document.content_text == ""
    assert lesson.teaching_guide.mappings == []


def test_realtime_routes_create_sessions_and_log_events(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user()
    package = build_initial_course_package()
    lesson = package.lessons[0]
    monkeypatch.setattr(realtime_router, "_lesson_for_user", lambda lesson_id, user_id: (package, lesson))
    monkeypatch.setattr(openai_realtime_teacher, "create_call", lambda **kwargs: "answer-sdp")
    monkeypatch.setattr(
        google_realtime_teacher,
        "create_live_session",
        lambda **kwargs: {
            "provider": "google",
            "model": kwargs["model_selection"].model,
            "voice": "Aoede",
            "setup": {"setup": {"model": "models/test"}},
        },
    )

    openai_response = realtime_router.connect_realtime_session(
        lesson.id,
        RealtimeConnectRequest(
            offer_sdp="v=0",
            realtime_model=AIModelSelection(provider="openai", model="gpt-realtime-mini"),
        ),
        user=user,
    )
    google_response = realtime_router.create_google_realtime_session(
        lesson.id,
        GoogleRealtimeSessionRequest(
            realtime_model=AIModelSelection(
                provider="google",
                model="gemini-2.5-flash-native-audio-preview-12-2025",
            )
        ),
        user=user,
    )
    log_response = realtime_router.log_realtime_event(
        lesson.id,
        RealtimeTranscriptLogRequest(
            role="user",
            transport_event_type="transcript",
            transcript="开始讲",
        ),
        user=user,
    )

    assert openai_response.answer_sdp == "answer-sdp"
    assert openai_response.model == "gpt-realtime-mini"
    assert google_response.websocket_url == f"/api/lessons/{lesson.id}/realtime/google/ws"
    assert google_response.model == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert log_response == {"status": "ok"}
