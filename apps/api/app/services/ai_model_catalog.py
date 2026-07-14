from __future__ import annotations

import os
from typing import Any

from app.models import (
    AIModelCatalog,
    AIModelOption,
    AIModelSelection,
    AIReasoningEffortOption,
    AIServiceTierOption,
)
from app.services.codex_app_server import (
    CODEX_DEFAULT_MODELS,
    codex_provider_status,
    list_codex_models,
)


OPENAI_CODEX_DEFAULT_TEXT_MODEL = "gpt-5.5"
OPENAI_CODEX_REALTIME_UNAVAILABLE_MODEL = "realtime-unavailable"


def default_text_selection(
    *,
    model: str = OPENAI_CODEX_DEFAULT_TEXT_MODEL,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
) -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model=model,
        reasoning_effort=reasoning_effort,
        service_tier=service_tier,
    )


def unavailable_realtime_selection() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model=OPENAI_CODEX_REALTIME_UNAVAILABLE_MODEL,
    )


def _fallback_codex_models() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "model": model,
            "displayName": label.removeprefix("OpenAI Codex "),
        }
        for model, label in CODEX_DEFAULT_MODELS
    )


def _codex_text_models(user_id: str) -> tuple[dict[str, Any], ...]:
    try:
        models = list_codex_models(user_id)
    except Exception:
        return _fallback_codex_models()
    options: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or item.get("id") or "").strip()
        if not model:
            continue
        display_name = str(
            item.get("displayName") or item.get("display_name") or model
        ).strip()
        options.append({**item, "model": model, "displayName": display_name})
    return tuple(options) or _fallback_codex_models()


def _reasoning_efforts(item: dict[str, Any]) -> list[AIReasoningEffortOption]:
    raw_options = item.get("supportedReasoningEfforts")
    if not isinstance(raw_options, list):
        raw_options = item.get("supported_reasoning_efforts")
    options: list[AIReasoningEffortOption] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        effort = str(
            raw_option.get("reasoningEffort")
            or raw_option.get("reasoning_effort")
            or ""
        ).strip()
        if effort:
            options.append(
                AIReasoningEffortOption(
                    reasoning_effort=effort,
                    description=str(raw_option.get("description") or "").strip(),
                )
            )
    return options


def _service_tiers(item: dict[str, Any]) -> list[AIServiceTierOption]:
    raw_options = item.get("serviceTiers")
    if not isinstance(raw_options, list):
        raw_options = item.get("service_tiers")
    options: list[AIServiceTierOption] = []
    for raw_option in raw_options if isinstance(raw_options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        tier_id = str(raw_option.get("id") or "").strip()
        if tier_id:
            options.append(
                AIServiceTierOption(
                    id=tier_id,
                    name=str(raw_option.get("name") or tier_id).strip(),
                    description=str(raw_option.get("description") or "").strip(),
                )
            )
    return options


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _default_model_id(models: list[dict[str, Any]]) -> str:
    configured = (os.getenv("OPENAI_CODEX_MODEL") or "").strip()
    if configured:
        return configured
    for item in models:
        if item.get("isDefault") is True:
            return str(item["model"])
    return OPENAI_CODEX_DEFAULT_TEXT_MODEL


def build_model_catalog(user_id: str) -> AIModelCatalog:
    status = codex_provider_status(user_id, refresh=False)
    realtime_default = unavailable_realtime_selection()
    models = list(_codex_text_models(user_id))
    default_model_id = _default_model_id(models)
    if not any(item["model"] == default_model_id for item in models):
        models.insert(
            0,
            {"model": default_model_id, "displayName": default_model_id},
        )
    text_options = [
        AIModelOption(
            provider="openai_codex",
            model=str(item["model"]),
            label=f"OpenAI Codex {item['displayName']}",
            capability="text",
            enabled=status.configured,
            configured=status.configured,
            default=item["model"] == default_model_id,
            default_reasoning_effort=_optional_string(
                item.get("defaultReasoningEffort")
                or item.get("default_reasoning_effort")
            ),
            supported_reasoning_efforts=_reasoning_efforts(item),
            default_service_tier=_optional_string(
                item.get("defaultServiceTier")
                or item.get("default_service_tier")
            ),
            service_tiers=_service_tiers(item),
        )
        for item in models
    ]
    default_option = next(option for option in text_options if option.default)
    text_default = default_text_selection(
        model=default_option.model,
        reasoning_effort=default_option.default_reasoning_effort,
        service_tier=default_option.default_service_tier,
    )
    return AIModelCatalog(
        text=text_options,
        realtime=[
            AIModelOption(
                provider=realtime_default.provider,
                model=realtime_default.model,
                label="Codex 实时语音不可用",
                capability="realtime",
                enabled=False,
                configured=False,
                default=True,
            )
        ],
        defaults={"text": text_default, "realtime": realtime_default},
    )
