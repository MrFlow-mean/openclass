from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from app.models import AIModelCatalog, AIModelOption, AIModelSelection, AIProvider

OPENAI_GATEWAY_BASE_URL = "https://api.bupt8.com/v1"
OPENAI_DEFAULT_TEXT_MODEL = "gpt-5-mini"
OPENAI_DEFAULT_REALTIME_MODEL = "gpt-4o-realtime-preview"
ANTHROPIC_DEFAULT_TEXT_MODEL = "claude-opus-4-7"
GOOGLE_DEFAULT_TEXT_MODEL = "gemini-3.1-pro-preview"
GOOGLE_DEFAULT_REALTIME_MODEL = "gemini-3.1-flash-live-preview"
DEEPSEEK_DEFAULT_TEXT_MODEL = "deepseek-v4-pro"
KIMI_DEFAULT_TEXT_MODEL = "kimi-k2.6"
MINIMAX_DEFAULT_TEXT_MODEL = "MiniMax-M2.7"
OPENAI_COMPATIBLE_DEFAULT_TEXT_MODEL = "gpt-5-mini"
ANTHROPIC_COMPATIBLE_DEFAULT_TEXT_MODEL = "claude-opus-4-7"


PROVIDER_LABELS: dict[AIProvider, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "minimax": "MiniMax",
    "openai_compatible": "OpenAI 兼容",
    "anthropic_compatible": "Anthropic 兼容",
}

OPENAI_MODEL_DISCOVERY_TIMEOUT_SECONDS = 4
OPENAI_MODEL_DISCOVERY_DISABLED_VALUES = {"0", "false", "off", "no"}
TEXT_MODEL_EXCLUDED_FRAGMENTS = (
    "audio",
    "dall-e",
    "embedding",
    "image",
    "imagen",
    "moderation",
    "realtime",
    "sora",
    "speech",
    "transcribe",
    "tts",
    "veo",
    "whisper",
)


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


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
    if provider == "openai":
        return bool(_normalize_optional_secret(_env_any("OPENAI_API_KEY")))
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


def _normalize_provider(value: str | None, default: AIProvider) -> AIProvider:
    normalized = (value or "").strip().lower()
    if normalized in PROVIDER_LABELS:
        return normalized  # type: ignore[return-value]
    return default


def default_text_selection() -> AIModelSelection:
    provider = _normalize_provider(os.getenv("AI_TEXT_PROVIDER"), "openai")
    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_TEXT_MODEL)
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
    if provider == "google":
        model = os.getenv("GOOGLE_REALTIME_MODEL", GOOGLE_DEFAULT_REALTIME_MODEL)
    else:
        model = os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL)
        provider = "openai"
    return AIModelSelection(provider=provider, model=model)


def provider_is_configured(provider: AIProvider) -> bool:
    return _provider_enabled(provider)


def _model_label(provider: AIProvider, model: str) -> str:
    return f"{PROVIDER_LABELS[provider]} {model}"


def _option(
    *,
    provider: AIProvider,
    model: str,
    label: str | None = None,
    capability: str,
    default: bool = False,
    transport: str | None = None,
) -> AIModelOption:
    if capability == "realtime" and provider == "openai":
        configured = bool(_env_explicit_or_fallback("OPENAI_REALTIME_API_KEY", "OPENAI_API_KEY"))
    else:
        configured = _provider_enabled(provider)
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


def _env_flag_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in OPENAI_MODEL_DISCOVERY_DISABLED_VALUES


def _openai_models_url(base_url: str | None = None) -> str:
    base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).strip() or "https://api.openai.com/v1"
    return f"{base_url.rstrip('/')}/models"


def _read_openai_compatible_models(*, api_key: str | None, base_url: str | None) -> list[str]:
    if not api_key or not _env_flag_enabled("AI_MODEL_DISCOVERY_ENABLED"):
        return []

    request = urllib.request.Request(
        _openai_models_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    timeout = float(os.getenv("AI_MODEL_DISCOVERY_TIMEOUT_SECONDS", str(OPENAI_MODEL_DISCOVERY_TIMEOUT_SECONDS)))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return []

    values = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return []

    model_ids: list[str] = []
    for value in values:
        if isinstance(value, str):
            model_id = value
        elif isinstance(value, dict):
            model_id = str(value.get("id") or value.get("name") or "").strip()
        else:
            continue
        if model_id:
            model_ids.append(model_id)
    return sorted(set(model_ids), key=str.lower)


def _looks_like_realtime_model(model: str) -> bool:
    normalized = model.lower()
    return "realtime" in normalized or normalized.startswith("gpt-realtime")


def _looks_like_text_model(model: str) -> bool:
    normalized = model.lower()
    return not any(fragment in normalized for fragment in TEXT_MODEL_EXCLUDED_FRAGMENTS)


def _discovered_openai_text_options(model_ids: list[str], *, provider: AIProvider = "openai") -> list[AIModelOption]:
    return [
        _option(provider=provider, model=model, capability="text")
        for model in model_ids
        if _looks_like_text_model(model)
    ]


def _discovered_openai_realtime_options(model_ids: list[str]) -> list[AIModelOption]:
    return [
        _option(provider="openai", model=model, capability="realtime", transport="openai_webrtc")
        for model in model_ids
        if _looks_like_realtime_model(model)
    ]


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
        model = str(value.get("model") or "").strip()
        if not model:
            continue
        options.append(
            _option(
                provider=provider,
                model=model,
                label=str(value.get("label") or "") or None,
                capability=capability,
                transport=value.get("transport") if isinstance(value.get("transport"), str) else None,
            )
        )
    return options


def build_model_catalog() -> AIModelCatalog:
    text_default = default_text_selection()
    realtime_default = default_realtime_selection()
    discovered_openai_models = _read_openai_compatible_models(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    custom_openai_base_url = _custom_openai_base_url()
    discovered_custom_openai_models = _read_openai_compatible_models(
        api_key=_custom_openai_api_key() if custom_openai_base_url else None,
        base_url=custom_openai_base_url,
    )

    text_options = [
        _option(
            provider="openai",
            model=os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_TEXT_MODEL),
            label="OpenAI 默认文本模型",
            capability="text",
            default=text_default.provider == "openai",
        ),
        _option(provider="openai", model="gpt-5.3", capability="text"),
        _option(provider="openai", model="gpt-5-mini", capability="text"),
        _option(
            provider="anthropic",
            model=os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_TEXT_MODEL),
            label="Anthropic Claude Opus 4.7",
            capability="text",
            default=text_default.provider == "anthropic",
        ),
        _option(provider="anthropic", model="claude-opus-4-7", label="Anthropic Claude Opus 4.7", capability="text"),
        _option(provider="anthropic", model="claude-sonnet-4-6", label="Anthropic Claude Sonnet 4.6", capability="text"),
        _option(provider="anthropic", model="claude-opus-4-1-20250805", capability="text"),
        _option(provider="anthropic", model="claude-sonnet-4-20250514", capability="text"),
        _option(provider="anthropic", model="claude-3-5-haiku-20241022", capability="text"),
        _option(
            provider="google",
            model=os.getenv("GOOGLE_TEXT_MODEL", GOOGLE_DEFAULT_TEXT_MODEL),
            label="Google Gemini 3.1 Pro Preview",
            capability="text",
            default=text_default.provider == "google",
        ),
        _option(provider="google", model="gemini-3.1-pro-preview", label="Google Gemini 3.1 Pro Preview", capability="text"),
        _option(provider="google", model="gemini-3-flash-preview", label="Google Gemini 3 Flash Preview", capability="text"),
        _option(provider="google", model="gemini-2.5-flash", capability="text"),
        _option(
            provider="deepseek",
            model=_deepseek_model(),
            label="DeepSeek 默认文本模型",
            capability="text",
            default=text_default.provider == "deepseek",
        ),
        _option(provider="deepseek", model="deepseek-v4-pro", capability="text"),
        _option(provider="deepseek", model="deepseek-v4-flash", capability="text"),
        _option(provider="deepseek", model="deepseek-chat", capability="text"),
        _option(provider="deepseek", model="deepseek-reasoner", capability="text"),
        _option(
            provider="kimi",
            model=_kimi_model(),
            label="Kimi 默认文本模型",
            capability="text",
            default=text_default.provider == "kimi",
        ),
        _option(provider="kimi", model="kimi-k2.6", capability="text"),
        _option(provider="kimi", model="kimi-k2-0905-preview", capability="text"),
        _option(provider="kimi", model="kimi-k2-turbo-preview", capability="text"),
        _option(
            provider="minimax",
            model=_minimax_model(),
            label="MiniMax 默认文本模型",
            capability="text",
            default=text_default.provider == "minimax",
        ),
        _option(provider="minimax", model="MiniMax-M2.7", capability="text"),
        _option(provider="minimax", model="MiniMax-M2", capability="text"),
        _option(
            provider="openai_compatible",
            model=_custom_openai_model(),
            label="自定义 OpenAI 兼容模型",
            capability="text",
            default=text_default.provider == "openai_compatible",
        ),
        _option(
            provider="anthropic_compatible",
            model=_custom_anthropic_model(),
            label="自定义 Anthropic 兼容模型",
            capability="text",
            default=text_default.provider == "anthropic_compatible",
        ),
    ]
    text_options.extend(_discovered_openai_text_options(discovered_openai_models))
    text_options.extend(_discovered_openai_text_options(discovered_custom_openai_models, provider="openai_compatible"))
    text_options.extend(_custom_options("AI_TEXT_MODELS_JSON", "text"))

    realtime_options = [
        _option(
            provider="openai",
            model=os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL),
            label="OpenAI 默认实时语音",
            capability="realtime",
            default=realtime_default.provider == "openai",
            transport="openai_webrtc",
        ),
        _option(
            provider="openai",
            model="gpt-4o-realtime-preview",
            capability="realtime",
            transport="openai_webrtc",
        ),
        _option(
            provider="google",
            model=os.getenv("GOOGLE_REALTIME_MODEL", GOOGLE_DEFAULT_REALTIME_MODEL),
            label="Google 默认实时语音",
            capability="realtime",
            default=realtime_default.provider == "google",
            transport="gemini_live_websocket",
        ),
        _option(
            provider="google",
            model="gemini-3.1-flash-live-preview",
            capability="realtime",
            transport="gemini_live_websocket",
        ),
        _option(
            provider="google",
            model="gemini-2.5-flash-live-preview",
            capability="realtime",
            transport="gemini_live_websocket",
        ),
    ]
    realtime_options.extend(_discovered_openai_realtime_options(discovered_openai_models))
    realtime_options.extend(_custom_options("AI_REALTIME_MODELS_JSON", "realtime"))

    return AIModelCatalog(
        text=_dedupe_options(text_options),
        realtime=_dedupe_options(realtime_options),
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
