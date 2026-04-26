import json

from app.services import ai_model_catalog


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_catalog_discovers_openai_compatible_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "openai")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "openai")
    monkeypatch.delenv("AI_MODEL_DISCOVERY_ENABLED", raising=False)
    monkeypatch.delenv("AI_MODEL_DISCOVERY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def fake_urlopen(request, timeout: float):
        assert request.full_url == "https://api.example.com/v1/models"
        assert request.get_header("Authorization") == "Bearer test-key"
        assert timeout == 4
        return _FakeResponse(
            {
                "data": [
                    {"id": "gpt-5.4"},
                    {"id": "gpt-5.4-mini"},
                    {"id": "text-embedding-3-large"},
                    {"id": "gpt-4o-realtime-preview-2024-12-17"},
                ]
            }
        )

    monkeypatch.setattr(ai_model_catalog.urllib.request, "urlopen", fake_urlopen)

    catalog = ai_model_catalog.build_model_catalog()

    assert any(option.model == "gpt-5.4" for option in catalog.text)
    assert any(option.model == "gpt-5.4-mini" for option in catalog.text)
    assert not any(option.model == "text-embedding-3-large" for option in catalog.text)
    realtime_option = next(
        option for option in catalog.realtime if option.model == "gpt-4o-realtime-preview-2024-12-17"
    )
    assert realtime_option.transport == "openai_webrtc"
    assert realtime_option.enabled


def test_catalog_skips_discovery_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODEL_DISCOVERY_ENABLED", "false")

    def fake_urlopen(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr(ai_model_catalog.urllib.request, "urlopen", fake_urlopen)

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.text
