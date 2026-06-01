from __future__ import annotations

from app.constants import AUTH_ERROR_UNAUTHENTICATED
from app.models import LearningRequirementChecklistItem, LearningRequirementKeyFact
from app.services.openai_course_ai import ChatbotReply, LearningRequirementUpdate, openai_course_ai
from conftest import verified_headers


def _auth_headers(client, sent, *, email: str = "chat@example.com", password: str = "correct-password") -> dict[str, str]:
    return verified_headers(client, sent, email=email, password=password)


def _fake_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="用户已经说明当前学习目标，可以进入后续板书阶段。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习请求",
                value="用户提出了当前要解决的学习问题。",
                evidence="来自用户输入。",
                category="other",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="用户已经说明当前学习目标",
                is_clear=True,
                evidence="用户提出了当前要解决的学习问题。",
            )
        ],
        missing_items=[],
        next_question="",
        ready_for_board=True,
    )


def test_chat_sync_and_stream_endpoints(isolated_app, monkeypatch) -> None:
    client, _auth, _store, sent = isolated_app
    headers = _auth_headers(client, sent)

    package = client.post(
        "/api/packages",
        json={"title": "Chat HTTP", "summary": "integration"},
        headers=headers,
    )
    assert package.status_code == 200
    package_id = package.json()["active_package_id"]

    lesson = client.post(
        "/api/lessons/generate",
        json={"topic": "HTTP chat lesson", "start_blank": True, "target_package_id": package_id},
        headers=headers,
    )
    assert lesson.status_code == 200
    lesson_id = lesson.json()["lessons"][0]["id"]

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="HTTP integration reply"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _fake_requirement_update)

    chat = client.post(
        f"/api/lessons/{lesson_id}/chat",
        headers=headers,
        json={"message": "你好", "interaction_mode": "ask", "conversation": []},
    )
    assert chat.status_code == 200
    assert chat.json()["chatbot_message"] == "HTTP integration reply"

    stream = client.post(
        f"/api/lessons/{lesson_id}/chat/stream",
        headers=headers,
        json={"message": "再试一次", "interaction_mode": "ask", "conversation": []},
    )
    assert stream.status_code == 200
    assert "event: final" in stream.text
    assert "HTTP integration reply" in stream.text


def test_chat_requires_auth(isolated_app) -> None:
    client, _auth, _store, _sent = isolated_app

    response = client.post(
        "/api/lessons/any-lesson-id/chat",
        json={"message": "你好", "interaction_mode": "ask", "conversation": []},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == AUTH_ERROR_UNAUTHENTICATED


def test_chat_rejects_other_users_lesson(isolated_app, monkeypatch) -> None:
    client, _auth, _store, sent = isolated_app
    owner_headers = _auth_headers(client, sent, email="chat-owner@example.com")
    other_headers = verified_headers(client, sent, email="chat-other@example.com")

    package = client.post(
        "/api/packages",
        json={"title": "Owner package", "summary": "isolation"},
        headers=owner_headers,
    )
    assert package.status_code == 200
    package_id = package.json()["active_package_id"]

    lesson = client.post(
        "/api/lessons/generate",
        json={"topic": "Owner lesson", "start_blank": True, "target_package_id": package_id},
        headers=owner_headers,
    )
    assert lesson.status_code == 200
    lesson_id = lesson.json()["lessons"][0]["id"]

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="Should not run"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _fake_requirement_update)

    response = client.post(
        f"/api/lessons/{lesson_id}/chat",
        headers=other_headers,
        json={"message": "你好", "interaction_mode": "ask", "conversation": []},
    )
    assert response.status_code == 404
