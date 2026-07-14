from app.models import CodexProviderStatus
from app.services import ai_model_catalog


def _status(*, configured: bool) -> CodexProviderStatus:
    return CodexProviderStatus(
        enabled=True,
        available=True,
        configured=configured,
    )


def test_catalog_exposes_only_codex_text_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("AI_TEXT_MODELS_JSON", '[{"provider":"deepseek","model":"legacy"}]')
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda **_kwargs: _status(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda: [
            {"model": "gpt-5.5", "displayName": "GPT-5.5"},
            {"model": "gpt-5.4-mini", "displayName": "GPT-5.4 Mini"},
        ],
    )

    catalog = ai_model_catalog.build_model_catalog()

    assert [(option.provider, option.model) for option in catalog.text] == [
        ("openai_codex", "gpt-5.5"),
        ("openai_codex", "gpt-5.4-mini"),
    ]
    assert catalog.defaults["text"].provider == "openai_codex"
    assert catalog.defaults["text"].model == "gpt-5.4-mini"
    assert catalog.defaults["realtime"] == catalog.defaults["text"]
    assert catalog.realtime == []
    assert [option.model for option in catalog.text if option.default] == ["gpt-5.4-mini"]
    assert all(option.enabled and option.configured for option in catalog.text)


def test_catalog_adds_configured_default_when_codex_does_not_list_it(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "custom-codex-model")
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda **_kwargs: _status(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda: [{"model": "gpt-5.5", "displayName": "GPT-5.5"}],
    )

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.text[0].provider == "openai_codex"
    assert catalog.text[0].model == "custom-codex-model"
    assert catalog.text[0].default is True


def test_catalog_disables_codex_options_until_account_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_CODEX_MODEL", raising=False)
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda **_kwargs: _status(configured=False),
    )
    monkeypatch.setattr(ai_model_catalog, "list_codex_models", lambda: [])

    catalog = ai_model_catalog.build_model_catalog()

    assert catalog.text
    assert {option.provider for option in catalog.text} == {"openai_codex"}
    assert all(not option.enabled and not option.configured for option in catalog.text)
    assert catalog.realtime == []
