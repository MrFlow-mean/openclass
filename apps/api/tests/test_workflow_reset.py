import pytest
from fastapi import HTTPException

from app.models import (
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


def _user() -> UserView:
    return UserView(
        id="user_test",
        email="test@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )


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


def test_lesson_generation_reset_creates_blank_lesson_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_ai_generation_is_called(*args, **kwargs):  # noqa: ANN002, ANN003
        pytest.fail("reset lesson generation should not call legacy AI document generation")

    monkeypatch.setattr(openai_course_ai, "generate_lesson_document", fail_if_ai_generation_is_called)

    lesson = build_lesson_for_topic("新课堂")

    assert lesson.title == "新课堂"
    assert lesson.board_document.content_text == ""
    assert lesson.teaching_guide.mappings == []


def test_realtime_routes_are_removed() -> None:
    user = _user()

    with pytest.raises(HTTPException) as openai_exc:
        realtime_router.connect_realtime_session(
            "lesson_test",
            RealtimeConnectRequest(offer_sdp="v=0"),
            user=user,
        )

    with pytest.raises(HTTPException) as google_exc:
        realtime_router.create_google_realtime_session(
            "lesson_test",
            GoogleRealtimeSessionRequest(),
            user=user,
        )

    with pytest.raises(HTTPException) as log_exc:
        realtime_router.log_realtime_event(
            "lesson_test",
            RealtimeTranscriptLogRequest(
                role="user",
                transport_event_type="transcript",
                transcript="开始讲",
            ),
            user=user,
        )

    assert openai_exc.value.status_code == 410
    assert google_exc.value.status_code == 410
    assert log_exc.value.status_code == 410
