from app.models import CodexProviderStatus
from app.services import ai_model_catalog

TEST_USER_ID = "user_model_catalog"


def _status(*, configured: bool) -> CodexProviderStatus:
    return CodexProviderStatus(
        enabled=True,
        available=True,
        configured=configured,
    )


def test_catalog_exposes_codex_and_shared_deepseek_text_models(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("AI_TEXT_MODELS_JSON", '[{"provider":"deepseek","model":"legacy"}]')
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: _status(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda _user_id: [
            {
                "model": "gpt-5.5",
                "displayName": "GPT-5.5",
                "defaultReasoningEffort": "medium",
                "supportedReasoningEfforts": [
                    {
                        "reasoningEffort": "medium",
                        "description": "Balanced reasoning",
                    },
                    {
                        "reasoningEffort": "high",
                        "description": "Deeper reasoning",
                    },
                ],
                "defaultServiceTier": None,
                "serviceTiers": [
                    {
                        "id": "priority",
                        "name": "Fast",
                        "description": "1.5x speed, increased usage",
                    }
                ],
            },
            {
                "model": "gpt-5.4-mini",
                "displayName": "GPT-5.4 Mini",
                "defaultReasoningEffort": "high",
                "supportedReasoningEfforts": [
                    {
                        "reasoningEffort": "high",
                        "description": "Deeper reasoning",
                    }
                ],
                "serviceTiers": [],
            },
        ],
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert [(option.provider, option.model) for option in catalog.text] == [
        ("openai_codex", "gpt-5.5"),
        ("openai_codex", "gpt-5.4-mini"),
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-v4-pro"),
    ]
    assert catalog.defaults["text"].provider == "openai_codex"
    assert catalog.defaults["text"].model == "gpt-5.4-mini"
    assert catalog.defaults["realtime"].provider == "openai_codex"
    assert catalog.defaults["realtime"].model == "realtime-unavailable"
    assert len(catalog.realtime) == 1
    assert catalog.realtime[0].model == "realtime-unavailable"
    assert catalog.realtime[0].default is True
    assert catalog.realtime[0].enabled is False
    assert catalog.realtime[0].configured is False
    assert [option.model for option in catalog.text if option.default] == ["gpt-5.4-mini"]
    assert all(
        option.enabled and option.configured
        for option in catalog.text
        if option.provider == "openai_codex"
    )
    assert all(
        not option.enabled and not option.configured
        for option in catalog.text
        if option.provider == "deepseek"
    )
    assert catalog.defaults["text"].reasoning_effort == "high"
    assert catalog.defaults["text"].service_tier is None
    assert [
        option.reasoning_effort
        for option in catalog.text[0].supported_reasoning_efforts
    ] == ["medium", "high"]
    assert catalog.text[0].service_tiers[0].id == "priority"


def test_catalog_uses_codex_live_default_without_an_environment_override(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_CODEX_MODEL", raising=False)
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: _status(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda _user_id: [
            {
                "model": "gpt-5.6-sol",
                "displayName": "GPT-5.6-Sol",
                "isDefault": True,
                "defaultReasoningEffort": "low",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "Fast"}
                ],
                "serviceTiers": [
                    {
                        "id": "priority",
                        "name": "Fast",
                        "description": "1.5x speed, increased usage",
                    }
                ],
            },
            {
                "model": "gpt-5.5",
                "displayName": "GPT-5.5",
                "isDefault": False,
                "defaultReasoningEffort": "medium",
            },
        ],
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.defaults["text"].model == "gpt-5.6-sol"
    assert catalog.defaults["text"].reasoning_effort == "low"
    assert catalog.text[0].default is True


def test_catalog_adds_configured_default_when_codex_does_not_list_it(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "custom-codex-model")
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: _status(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda _user_id: [{"model": "gpt-5.5", "displayName": "GPT-5.5"}],
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.text[0].provider == "openai_codex"
    assert catalog.text[0].model == "custom-codex-model"
    assert catalog.text[0].default is True


def test_catalog_disables_codex_options_until_account_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_CODEX_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: _status(configured=False),
    )
    monkeypatch.setattr(ai_model_catalog, "list_codex_models", lambda _user_id: [])

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.text
    assert {option.provider for option in catalog.text} == {"openai_codex", "deepseek"}
    assert all(not option.enabled and not option.configured for option in catalog.text)
    assert len(catalog.realtime) == 1
    assert catalog.realtime[0].model == "realtime-unavailable"
    assert catalog.realtime[0].enabled is False


def test_shared_deepseek_is_enabled_for_every_user_without_a_user_quota(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-shared-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: _status(configured=False),
    )
    monkeypatch.setattr(ai_model_catalog, "list_codex_models", lambda _user_id: [])

    guest_catalog = ai_model_catalog.build_model_catalog("guest_default")
    member_catalog = ai_model_catalog.build_model_catalog("user_member")

    for catalog in (guest_catalog, member_catalog):
        deepseek_options = [
            option for option in catalog.text if option.provider == "deepseek"
        ]
        assert deepseek_options
        assert all(option.enabled and option.configured for option in deepseek_options)
        assert catalog.defaults["text"].provider == "deepseek"
        assert catalog.defaults["text"].model == "deepseek-v4-flash"
