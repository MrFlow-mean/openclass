from app.services import ai_model_catalog


def _models_by_provider(catalog, capability: str, provider: str) -> list[str]:
    options = catalog.text if capability == "text" else catalog.realtime
    return [option.model for option in options if option.provider == provider]


def test_catalog_exposes_only_openai_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_DISCOVERY_ENABLED", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "legacy-openai-realtime")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "openai")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "openai")
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "openai"
    assert catalog.defaults["text"].model == "gpt-5"
    assert "gpt-5" in _models_by_provider(catalog, "text", "openai")
    assert _models_by_provider(catalog, "realtime", "openai")[0] == "legacy-openai-realtime"
    assert len(catalog.text) == 1
    assert len(catalog.realtime) == 1


def test_catalog_ignores_non_openai_provider_env(monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_DISCOVERY_ENABLED", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "google")
    monkeypatch.delenv("GOOGLE_TEXT_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_MODEL", raising=False)
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "openai"
    assert catalog.defaults["realtime"].provider == "openai"
    assert _models_by_provider(catalog, "text", "google") == []
    assert _models_by_provider(catalog, "realtime", "google") == []


def test_catalog_hides_configured_custom_text_providers(monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_DISCOVERY_ENABLED", "0")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "kimi")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "custom-openai-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "router-model")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_API_KEY", "custom-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_BASE_URL", "https://anthropic-gateway.example.com")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_MODEL", "claude-router")

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "openai"
    assert all(option.provider == "openai" for option in catalog.text)
    assert all(option.provider == "openai" for option in catalog.realtime)
