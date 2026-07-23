from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.routers.sources import _parse_catalog_model


def test_catalog_model_parser_accepts_supported_non_codex_text_model() -> None:
    selection = _parse_catalog_model(
        json.dumps({"provider": "deepseek", "model": "deepseek-chat"})
    )

    assert selection is not None
    assert selection.provider == "deepseek"
    assert selection.model == "deepseek-chat"


def test_catalog_model_parser_rejects_provider_without_directory_adapter() -> None:
    with pytest.raises(HTTPException, match="supported text model"):
        _parse_catalog_model(
            json.dumps({"provider": "google", "model": "gemini-test"})
        )
