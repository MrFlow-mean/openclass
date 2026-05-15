from app.services import ai_model_catalog


def _models_by_provider(catalog, capability: str, provider: str) -> list[str]:
    options = catalog.text if capability == "text" else catalog.realtime
    return [option.model for option in options if option.provider == provider]


def test_catalog_keeps_curated_openai_models_only(monkeypatch) -> None:
    monkeypatch.delenv("AI_SINGLE_API_KEY_MODE", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "legacy-openai-realtime")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "openai")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "openai")
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_COMPATIBLE_BASE_URL", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert _models_by_provider(catalog, "text", "openai") == ["gpt-5.5"]
    assert catalog.defaults["text"].model == "gpt-5.5"
    assert _models_by_provider(catalog, "realtime", "openai") == ["gpt-realtime-2"]
    assert catalog.defaults["realtime"].provider == "openai"
    assert catalog.defaults["realtime"].model == "gpt-realtime-2"


def test_catalog_realtime_options_use_openai_realtime_2(monkeypatch) -> None:
    monkeypatch.delenv("AI_SINGLE_API_KEY_MODE", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["realtime"].provider == "openai"
    assert catalog.defaults["realtime"].model == "gpt-realtime-2"
    assert _models_by_provider(catalog, "realtime", "openai") == ["gpt-realtime-2"]


def test_catalog_defaults_to_configured_google_when_openai_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("AI_SINGLE_API_KEY_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.delenv("AI_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("AI_REALTIME_PROVIDER", raising=False)
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "google"
    assert catalog.defaults["text"].model == "gemini-3-flash-preview"
    assert catalog.defaults["realtime"].provider == "openai"
    assert catalog.defaults["realtime"].model == "gpt-realtime-2"


def test_single_key_mode_keeps_text_models_on_official_openai(monkeypatch) -> None:
    monkeypatch.setenv("AI_SINGLE_API_KEY_MODE", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TEXT_MODEL", "gemini-3-flash-preview")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("AI_TEXT_MODELS_JSON", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_MODEL", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "openai"
    assert catalog.defaults["text"].model == "gpt-5.5"
    assert _models_by_provider(catalog, "text", "openai") == ["gpt-5.5"]
    assert _models_by_provider(catalog, "text", "google") == []
    assert _models_by_provider(catalog, "realtime", "openai") == ["gpt-realtime-2"]


def test_single_key_mode_does_not_use_shared_key_for_google_realtime(monkeypatch) -> None:
    monkeypatch.setenv("AI_SINGLE_API_KEY_MODE", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.router.example/v1")
    monkeypatch.setenv("AI_REALTIME_PROVIDER", "google")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_REALTIME_API_KEY", raising=False)
    monkeypatch.delenv("AI_REALTIME_MODELS_JSON", raising=False)

    catalog = ai_model_catalog.build_model_catalog()

    assert _models_by_provider(catalog, "realtime", "openai") == ["gpt-realtime-2"]


def test_catalog_includes_official_and_configured_custom_text_providers(monkeypatch) -> None:
    monkeypatch.delenv("AI_SINGLE_API_KEY_MODE", raising=False)
    monkeypatch.setenv("AI_TEXT_PROVIDER", "kimi")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "custom-openai-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://router.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "router-model")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_API_KEY", "custom-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_BASE_URL", "https://anthropic-router.example.com")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_MODEL", "claude-router")

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.defaults["text"].provider == "kimi"
    assert catalog.defaults["text"].model == "kimi-k2.6"
    assert _models_by_provider(catalog, "text", "deepseek") == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert _models_by_provider(catalog, "text", "kimi") == ["kimi-k2.5", "kimi-k2.6"]
    assert _models_by_provider(catalog, "text", "minimax") == ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"]

    enabled = {
        (option.provider, option.model): option.enabled
        for option in catalog.text
        if option.provider in {"deepseek", "kimi", "minimax", "openai_compatible", "anthropic_compatible"}
    }
    assert enabled[("deepseek", "deepseek-v4-flash")]
    assert enabled[("deepseek", "deepseek-v4-pro")]
    assert enabled[("kimi", "kimi-k2.6")]
    assert enabled[("minimax", "MiniMax-M2.7-highspeed")]
    assert enabled[("openai_compatible", "router-model")]
    assert enabled[("anthropic_compatible", "claude-router")]
