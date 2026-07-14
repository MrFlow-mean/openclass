from __future__ import annotations

import os

from app.models import AIModelCatalog, AIModelOption, AIModelSelection
from app.services.codex_app_server import (
    CODEX_DEFAULT_MODELS,
    codex_provider_status,
    list_codex_models,
)


OPENAI_CODEX_DEFAULT_TEXT_MODEL = "gpt-5.5"


def default_text_selection() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model=os.getenv("OPENAI_CODEX_MODEL", OPENAI_CODEX_DEFAULT_TEXT_MODEL),
    )


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
        display_name = str(
            item.get("displayName") or item.get("display_name") or model
        ).strip()
        options.append((model, f"OpenAI Codex {display_name}"))
    return tuple(options) or CODEX_DEFAULT_MODELS


def build_model_catalog() -> AIModelCatalog:
    status = codex_provider_status(refresh=False)
    text_default = default_text_selection()
    models = list(_codex_text_models())
    if not any(model == text_default.model for model, _label in models):
        models.insert(0, (text_default.model, f"OpenAI Codex {text_default.model}"))
    text_options = [
        AIModelOption(
            provider="openai_codex",
            model=model,
            label=label,
            capability="text",
            enabled=status.configured,
            configured=status.configured,
            default=model == text_default.model,
        )
        for model, label in models
    ]
    return AIModelCatalog(
        text=text_options,
        realtime=[],
        defaults={"text": text_default, "realtime": text_default},
    )
