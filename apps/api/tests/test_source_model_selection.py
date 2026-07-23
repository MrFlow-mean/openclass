from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.models import AIModelSelection
from app.routers.sources import _parse_catalog_model
from app.services.source_ingestion_service import (
    SourceIngestionError,
    _require_source_codex_selection,
)


def test_catalog_model_parser_accepts_openai_codex() -> None:
    selection = _parse_catalog_model(
        json.dumps({"provider": "openai_codex", "model": "gpt-5.6-sol"})
    )

    assert selection is not None
    assert selection.provider == "openai_codex"
    assert selection.model == "gpt-5.6-sol"


def test_catalog_model_parser_rejects_deepseek() -> None:
    with pytest.raises(HTTPException, match="OpenAI Codex text model"):
        _parse_catalog_model(
            json.dumps({"provider": "deepseek", "model": "deepseek-v4-flash"})
        )


def test_source_ingestion_service_rejects_deepseek_catalog_model() -> None:
    with pytest.raises(SourceIngestionError, match="OpenAI Codex text model"):
        _require_source_codex_selection(
            AIModelSelection(provider="deepseek", model="deepseek-v4-flash")
        )
