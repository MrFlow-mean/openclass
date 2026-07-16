from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import UserView
from app.routers import auth as auth_router
from app.routers import speech as speech_router
from app.services.speech_service import SpeechAudio, SpeechNotConfiguredError


TEST_USER = UserView(
    id="user_speech",
    email="speech@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


@pytest.fixture
def api_client():
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: TEST_USER
    try:
        yield TestClient(main_module.app)
    finally:
        main_module.app.dependency_overrides.clear()


def test_speech_endpoint_returns_generated_audio(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        speech_router,
        "synthesize_speech",
        lambda text: SpeechAudio(content=f"audio:{text}".encode(), model="tts-1", voice="marin"),
    )

    response = api_client.post("/api/speech", json={"input": "新的聊天回复"})

    assert response.status_code == 200
    assert response.content == "audio:新的聊天回复".encode()
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["x-speech-model"] == "tts-1"
    assert response.headers["x-speech-voice"] == "marin"
    assert response.headers["cache-control"] == "no-store"


def test_speech_endpoint_requires_nonempty_bounded_input(api_client: TestClient) -> None:
    assert api_client.post("/api/speech", json={"input": ""}).status_code == 422
    assert api_client.post("/api/speech", json={"input": "x" * 4097}).status_code == 422


def test_speech_endpoint_reports_missing_provider_configuration(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_: str) -> SpeechAudio:
        raise SpeechNotConfiguredError("missing key")

    monkeypatch.setattr(speech_router, "synthesize_speech", unavailable)

    response = api_client.post("/api/speech", json={"input": "需要播报的内容"})

    assert response.status_code == 503
    assert response.json()["detail"] == "语音播报尚未配置 OPENAI_API_KEY"
