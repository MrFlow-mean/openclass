from __future__ import annotations

import json
import os
from typing import Any

from app.models import AIModelCatalog, AIModelOption, AIModelSelection, AIProvider
from app.services.codex_app_server import CODEX_DEFAULT_MODELS, codex_provider_status, list_codex_models

OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com/v1"
OPENAI_PREMIUM_TEXT_MODEL = "gpt-5.5"
OPENAI_ECONOMY_TEXT_MODEL = "gpt-5.4"
OPENAI_FAST_TEXT_MODEL = "gpt-5.4-mini"
OPENAI_CODEX_DEFAULT_TEXT_MODEL = "gpt-5.5"
OPENAI_DEFAULT_TEXT_MODEL = OPENAI_PREMIUM_TEXT_MODEL
OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
OPENAI_FAST_REALTIME_MODEL = "gpt-realtime-2.1-mini"
ANTHROPIC_ECONOMY_TEXT_MODEL = "claude-haiku-4-5"
ANTHROPIC_FAST_TEXT_MODEL = "claude-sonnet-4-6"
ANTHROPIC_DEFAULT_TEXT_MODEL = ANTHROPIC_FAST_TEXT_MODEL
GOOGLE_ECONOMY_TEXT_MODEL = "gemini-3.1-pro-preview"
GOOGLE_FAST_TEXT_MODEL = "gemini-3-flash-preview"
GOOGLE_DEFAULT_TEXT_MODEL = GOOGLE_FAST_TEXT_MODEL
GOOGLE_DEFAULT_REALTIME_MODEL = "gemini-3.1-flash-live-preview"
DEEPSEEK_ECONOMY_TEXT_MODEL = "deepseek-v4-flash"
DEEPSEEK_FAST_TEXT_MODEL = "deepseek-v4-pro"
DEEPSEEK_DEFAULT_TEXT_MODEL = DEEPSEEK_ECONOMY_TEXT_MODEL
KIMI_ECONOMY_TEXT_MODEL = "kimi-k2.5"
KIMI_FAST_TEXT_MODEL = "kimi-k2.6"
KIMI_DEFAULT_TEXT_MODEL = KIMI_FAST_TEXT_MODEL
MINIMAX_ECONOMY_TEXT_MODEL = "MiniMax-M2.7"
MINIMAX_FAST_TEXT_MODEL = "MiniMax-M2.7-highspeed"
MINIMAX_DEFAULT_TEXT_MODEL = MINIMAX_FAST_TEXT_MODEL
OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL = OPENAI_FAST_TEXT_MODEL
ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL = ANTHROPIC_FAST_TEXT_MODEL

PROVIDER_LABELS: dict[AIProvider, str] = {
    "openai": "OpenAI",
    "openai_codex": "OpenAI Codex",
    "anthropic": "Anthropic",
    "google": "Google",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "minimax": "MiniMax",
    "openai_compatible": "OpenAI 兼容",
    "anthropic_compatible": "Anthropic 兼容",
}

CURATED_TEXT_MODELS: dict[AIProvider, tuple[tuple[str, str], ...]] = {
    "openai": (
        (OPENAI_PREMIUM_TEXT_MODEL, "OpenAI GPT-5.5"),
    ),
    "openai_codex": CODEX_DEFAULT_MODELS,
    "anthropic": (
        (ANTHROPIC_ECONOMY_TEXT_MODEL, "Anthropic Claude Haiku 4.5"),
        (ANTHROPIC_FAST_TEXT_MODEL, "Anthropic Claude Sonnet 4.6"),
    ),
    "google": (
        (GOOGLE_ECONOMY_TEXT_MODEL, "Google Gemini 3.1 Pro Preview"),
        (GOOGLE_FAST_TEXT_MODEL, "Google Gemini 3 Flash Preview"),
    ),
    "deepseek": (
        (DEEPSEEK_ECONOMY_TEXT_MODEL, "DeepSeek V4 Flash"),
        (DEEPSEEK_FAST_TEXT_MODEL, "DeepSeek V4 Pro"),
    ),
    "kimi": (
        (KIMI_ECONOMY_TEXT_MODEL, "Kimi K2.5"),
        (KIMI_FAST_TEXT_MODEL, "Kimi K2.6"),
    ),
    "minimax": (
        (MINIMAX_ECONOMY_TEXT_MODEL, "MiniMax M2.7"),
        (MINIMAX_FAST_TEXT_MODEL, "MiniMax M2.7 Highspeed"),
    ),
}

CURATED_REALTIME_MODELS: dict[AIProvider, tuple[tuple[str, str, str], ...]] = {
    "openai": (
        (OPENAI_FAST_REALTIME_MODEL, "OpenAI GPT Realtime 2.1 Mini", "openai_webrtc"),
        (OPENAI_DEFAULT_REALTIME_MODEL, "OpenAI GPT Realtime 2.1", "openai_webrtc"),
    ),
}

SINGLE_KEY_TEXT_MODELS: tuple[tuple[str, str], ...] = CURATED_TEXT_MODELS["openai"]


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _single_api_key_mode() -> bool:
    return _env_truthy("AI_SINGLE_API_KEY_MODE")


def realtime_runtime_enabled() -> bool:
    return _env_truthy("OPENCLASS_REALTIME_ENABLED")


def _shared_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")


def _custom_openai_api_key() -> str | None:
    return _env_any("OPENAI_COMPATIBLE_API_KEY", "CUSTOM_OPENAI_API_KEY", "AI_OPENAI_COMPAT_API_KEY")


def _custom_openai_base_url() -> str | None:
    return _env_any("OPENAI_COMPATIBLE_BASE_URL", "CUSTOM_OPENAI_BASE_URL", "AI_OPENAI_COMPAT_BASE_URL")


def _custom_openai_model() -> str:
    return (
        _env_any("OPENAI_COMPATIBLE_MODEL", "CUSTOM_OPENAI_MODEL", "AI_OPENAI_COMPAT_MODEL")
        or OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL
    )


def _custom_anthropic_api_key() -> str | None:
    return _env_any("ANTHROPIC_COMPATIBLE_API_KEY", "CUSTOM_ANTHROPIC_API_KEY", "AI_ANTHROPIC_COMPAT_API_KEY")


def _custom_anthropic_base_url() -> str | None:
    return _env_any("ANTHROPIC_COMPATIBLE_BASE_URL", "CUSTOM_ANTHROPIC_BASE_URL", "AI_ANTHROPIC_COMPAT_BASE_URL")


def _custom_anthropic_model() -> str:
    return (
        _env_any("ANTHROPIC_COMPATIBLE_MODEL", "CUSTOM_ANTHROPIC_MODEL", "AI_ANTHROPIC_COMPAT_MODEL")
        or ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL
    )


def _deepseek_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_TEXT_MODEL)


def _kimi_model() -> str:
    return _env_any("KIMI_MODEL", "MOONSHOT_MODEL") or KIMI_DEFAULT_TEXT_MODEL


def _minimax_model() -> str:
    return os.getenv("MINIMAX_MODEL", MINIMAX_DEFAULT_TEXT_MODEL)


def _env_explicit_or_fallback(name: str, fallback_name: str) -> str | None:
    if name in os.environ:
        return _normalize_optional_secret(os.getenv(name))
    return _normalize_optional_secret(os.getenv(fallback_name))


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith("你的_") or normalized.startswith("your_"):
        return None
    return normalized


def _provider_enabled(provider: AIProvider) -> bool:
    if _single_api_key_mode():
        if provider == "openai":
            return bool(_normalize_optional_secret(_shared_api_key()))
        return False
    if provider == "openai":
        return bool(_normalize_optional_secret(_shared_api_key()))
    if provider == "openai_codex":
        return codex_provider_status().configured
    if provider == "anthropic":
        return bool(_normalize_optional_secret(_env_any("ANTHROPIC_API_KEY")))
    if provider == "google":
        return bool(_normalize_optional_secret(_env_any("GOOGLE_API_KEY", "GEMINI_API_KEY")))
    if provider == "deepseek":
        return bool(_normalize_optional_secret(_env_any("DEEPSEEK_API_KEY")))
    if provider == "kimi":
        return bool(_normalize_optional_secret(_env_any("KIMI_API_KEY", "MOONSHOT_API_KEY")))
    if provider == "minimax":
        return bool(_normalize_optional_secret(_env_any("MINIMAX_API_KEY")))
    if provider == "openai_compatible":
        return bool(_normalize_optional_secret(_custom_openai_api_key()) and _custom_openai_base_url())
    if provider == "anthropic_compatible":
        return bool(_normalize_optional_secret(_custom_anthropic_api_key()) and _custom_anthropic_base_url())
    return False


def _realtime_provider_enabled(provider: AIProvider) -> bool:
    if not realtime_runtime_enabled():
        return False
    if provider == "openai":
        return _provider_enabled("openai")
    return False


def _normalize_provider(value: str | None, default: AIProvider) -> AIProvider:
    normalized = (value or "").strip().lower()
    if normalized in PROVIDER_LABELS:
        return normalized  # type: ignore[return-value]
    return default


def default_text_selection() -> AIModelSelection:
    provider = _normalize_provider(os.getenv("AI_TEXT_PROVIDER"), "openai")
    if _single_api_key_mode():
        model = os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_TEXT_MODEL)
        return AIModelSelection(provider="openai", model=model)
    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_TEXT_MODEL)
    elif provider == "openai_codex":
        model = os.getenv("OPENAI_CODEX_MODEL", OPENAI_CODEX_DEFAULT_TEXT_MODEL)
    elif provider == "google":
        model = os.getenv("GOOGLE_TEXT_MODEL", GOOGLE_DEFAULT_TEXT_MODEL)
    elif provider == "deepseek":
        model = _deepseek_model()
    elif provider == "kimi":
        model = _kimi_model()
    elif provider == "minimax":
        model = _minimax_model()
    elif provider == "openai_compatible":
        model = _custom_openai_model()
    elif provider == "anthropic_compatible":
        model = _custom_anthropic_model()
    else:
        model = os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_TEXT_MODEL)
    return AIModelSelection(provider=provider, model=model)


def default_realtime_selection() -> AIModelSelection:
    provider = _normalize_provider(os.getenv("AI_REALTIME_PROVIDER"), "openai")
    if provider == "openai":
        return AIModelSelection(
            provider="openai",
            model=os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL),
        )
    return AIModelSelection(
        provider="google",
        model=os.getenv("GOOGLE_REALTIME_MODEL", GOOGLE_DEFAULT_REALTIME_MODEL),
    )


def provider_is_configured(provider: AIProvider) -> bool:
    return _provider_enabled(provider)


def _model_label(provider: AIProvider, model: str) -> str:
    return f"{PROVIDER_LABELS[provider]} {model}"


def _codex_text_models() -> tuple[tuple[str, str], ...]:
    try:
        models = list_codex_models()
    except Exception:
        return CODEX_DEFAULT_MODELS
    options: list[tuple[str, str]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or item.get("id") or "").strip()
        if not model:
            continue
        label = str(item.get("displayName") or item.get("display_name") or model).strip()
        options.append((model, f"OpenAI Codex {label}"))
    return tuple(options) or CODEX_DEFAULT_MODELS


def _option(
    *,
    provider: AIProvider,
    model: str,
    label: str | None = None,
    capability: str,
    default: bool = False,
    transport: str | None = None,
) -> AIModelOption:
    configured = _realtime_provider_enabled(provider) if capability == "realtime" else _provider_enabled(provider)
    return AIModelOption(
        provider=provider,
        model=model,
        label=label or _model_label(provider, model),
        capability=capability,  # type: ignore[arg-type]
        enabled=configured,
        configured=configured,
        default=default,
        transport=transport,  # type: ignore[arg-type]
    )


def _dedupe_options(options: list[AIModelOption]) -> list[AIModelOption]:
    seen: set[tuple[str, str, str]] = set()
    result: list[AIModelOption] = []
    for option in options:
        key = (option.capability, option.provider, option.model)
        if key in seen:
            continue
        seen.add(key)
        result.append(option)
    return result


def _custom_options(env_name: str, capability: str) -> list[AIModelOption]:
    raw = os.getenv(env_name)
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    options: list[AIModelOption] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        provider = _normalize_provider(str(value.get("provider") or ""), "openai")
        if capability == "realtime" and provider not in {"openai", "google"}:
            continue
        model = str(value.get("model") or "").strip()
        if not model:
            continue
        transport = value.get("transport") if isinstance(value.get("transport"), str) else None
        if capability == "realtime" and transport is None:
            transport = "openai_webrtc" if provider == "openai" else "gemini_live_websocket"
        options.append(
            _option(
                provider=provider,
                model=model,
                label=str(value.get("label") or "") or None,
                capability=capability,
                transport=transport,
            )
        )
    return options


def _option_matches_selection(option: AIModelOption, selection: AIModelSelection) -> bool:
    return option.provider == selection.provider and option.model == selection.model


def _catalog_default_selection(
    *,
    requested: AIModelSelection,
    options: list[AIModelOption],
    curated_models: dict[AIProvider, tuple[tuple[str, str], ...]],
    provider_enabled: Any = _provider_enabled,
) -> AIModelSelection:
    enabled_options = [option for option in options if option.enabled]
    if any(_option_matches_selection(option, requested) and option.enabled for option in options):
        return requested
    provider_models = curated_models.get(requested.provider) if provider_enabled(requested.provider) else None
    if provider_models:
        return AIModelSelection(provider=requested.provider, model=provider_models[-1][0])
    fallback = enabled_options[0] if enabled_options else (options[0] if options else None)
    if fallback:
        for model, _label in reversed(curated_models.get(fallback.provider, ())):
            if any(option.provider == fallback.provider and option.model == model and option.enabled for option in options):
                return AIModelSelection(provider=fallback.provider, model=model)
    return AIModelSelection(provider=fallback.provider, model=fallback.model) if fallback else requested


def build_model_catalog() -> AIModelCatalog:
    requested_text_default = default_text_selection()
    requested_realtime_default = default_realtime_selection()
    text_curated_models = {
        **CURATED_TEXT_MODELS,
        "openai_codex": _codex_text_models(),
    }
    if _single_api_key_mode():
        text_curated_models = {"openai": SINGLE_KEY_TEXT_MODELS}
        text_options = [
            _option(
                provider="openai",
                model=model,
                label=label,
                capability="text",
                default=False,
            )
            for model, label in SINGLE_KEY_TEXT_MODELS
        ]
    else:
        text_options = [
            _option(
                provider=provider,
                model=model,
                label=label,
                capability="text",
                default=False,
            )
            for provider, models in text_curated_models.items()
            for model, label in models
        ]
        if _provider_enabled("openai_compatible"):
            text_options.append(
                _option(
                    provider="openai_compatible",
                    model=_custom_openai_model(),
                    label="自定义 OpenAI 兼容模型",
                    capability="text",
                    default=False,
                )
            )
        if _provider_enabled("anthropic_compatible"):
            text_options.append(
                _option(
                    provider="anthropic_compatible",
                    model=_custom_anthropic_model(),
                    label="自定义 Anthropic 兼容模型",
                    capability="text",
                    default=False,
                )
            )
    text_options.extend(_custom_options("AI_TEXT_MODELS_JSON", "text"))
    text_options = _dedupe_options(text_options)
    text_default = _catalog_default_selection(
        requested=requested_text_default,
        options=text_options,
        curated_models=text_curated_models,
    )
    for option in text_options:
        option.default = _option_matches_selection(option, text_default)

    realtime_options: list[AIModelOption] = []
    realtime_default = requested_realtime_default
    if realtime_runtime_enabled():
        realtime_options = [
            _option(
                provider=provider,
                model=model,
                label=label,
                capability="realtime",
                default=False,
                transport=transport,
            )
            for provider, models in CURATED_REALTIME_MODELS.items()
            for model, label, transport in models
        ]
        realtime_options.extend(_custom_options("AI_REALTIME_MODELS_JSON", "realtime"))
        realtime_options = _dedupe_options(realtime_options)
        realtime_default = _catalog_default_selection(
            requested=requested_realtime_default,
            options=realtime_options,
            curated_models={
                provider: tuple((model, label) for model, label, _transport in models)
                for provider, models in CURATED_REALTIME_MODELS.items()
            },
            provider_enabled=_realtime_provider_enabled,
        )
        for option in realtime_options:
            option.default = _option_matches_selection(option, realtime_default)

    return AIModelCatalog(
        text=text_options,
        realtime=realtime_options,
        defaults={
            "text": text_default,
            "realtime": realtime_default,
        },
    )


def selection_from_raw(raw: Any, *, default: AIModelSelection) -> AIModelSelection:
    if raw is None:
        return default
    if isinstance(raw, AIModelSelection):
        return raw
    if isinstance(raw, dict):
        provider = _normalize_provider(str(raw.get("provider") or ""), default.provider)
        model = str(raw.get("model") or "").strip() or default.model
        return AIModelSelection(provider=provider, model=model)
    return default
