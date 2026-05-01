from app.services import ai_model_catalog


def _models_by_provider(catalog, capability: str, provider: str) -> list[str]:
    options = catalog.text if capability == "text" else catalog.realtime
    return [option.model for option in options if option.provider == provider]


def test_catalog_keeps_model_selection_available_after_classroom_ai_reset(monkeypatch) -> None:
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
    assert "gpt-5.3" in _models_by_provider(catalog, "text", "openai")
    assert "gpt-5-mini" in _models_by_provider(catalog, "text", "openai")
    assert _models_by_provider(catalog, "realtime", "openai")[0] == "legacy-openai-realtime"


def test_catalog_defaults_to_selected_google_provider(monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_DISCOVERY_ENABLED", "0")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "google")
    monkeypatch.delenv("GOOGLE_TEXT_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_MODEL", raising=False)
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "google"
    assert catalog.defaults["text"].model == "gemini-3.1-pro-preview"
    assert catalog.defaults["realtime"].provider == "google"
    assert catalog.defaults["realtime"].model == "gemini-3.1-flash-live-preview"
    assert "gemini-3.1-flash-live-preview" in _models_by_provider(catalog, "realtime", "google")


def test_catalog_includes_configured_custom_text_providers(monkeypatch) -> None:
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

    assert catalog.defaults["text"].provider == "kimi"
    assert catalog.defaults["text"].model == "kimi-k2.6"
    assert "deepseek-v4-pro" in _models_by_provider(catalog, "text", "deepseek")
    assert "kimi-k2.6" in _models_by_provider(catalog, "text", "kimi")
    assert "MiniMax-M2.7" in _models_by_provider(catalog, "text", "minimax")

    enabled = {
        (option.provider, option.model): option.enabled
        for option in catalog.text
        if option.provider in {"deepseek", "kimi", "minimax", "openai_compatible", "anthropic_compatible"}
    }
    assert enabled[("deepseek", "deepseek-v4-pro")]
    assert enabled[("kimi", "kimi-k2.6")]
    assert enabled[("minimax", "MiniMax-M2.7")]
    assert enabled[("openai_compatible", "router-model")]
    assert enabled[("anthropic_compatible", "claude-router")]
