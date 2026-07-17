from __future__ import annotations

import hashlib
import json
import threading

import pytest

from app.models import (
    LearningSourceGrounding,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services.lesson_factory import build_requirements
from app.services import source_visual_analysis as analysis_module


def _visual(index: int, content: bytes) -> tuple[SourceVisualEvidence, SourceVisualAsset]:
    visual_id = f"visual_{index}"
    content_hash = hashlib.sha256(content).hexdigest()
    position_hash = f"position_{index}"
    evidence = SourceVisualEvidence(
        visual_id=visual_id,
        package_id="package",
        source_ingestion_id="source",
        kind="diagram",
        anchor_status="verified",
        mime_type="image/png",
        content_hash=content_hash,
        position_hash=position_hash,
    )
    asset = SourceVisualAsset(
        id=visual_id,
        owner_user_id="user",
        package_id="package",
        source_ingestion_id="source",
        kind="diagram",
        anchor_status="verified",
        mime_type="image/png",
        content_hash=content_hash,
        position_hash=position_hash,
    )
    return evidence, asset


def test_every_visual_uses_one_parallel_codex_turn_and_then_reuses_cache(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_VISUAL_ANALYSIS_ENABLED", "true")
    content = b"\x89PNG\r\n\x1a\nvisual"
    pairs = [_visual(index, content) for index in range(4)]
    assets = {asset.id: asset for _evidence, asset in pairs}
    requirement = build_requirements("Visual analysis")
    requirement.source_grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        frozen_visual_evidence=[evidence for evidence, _asset in pairs],
    )

    monkeypatch.setenv("OPENCLASS_CODEX_VISUAL_ANALYSIS_CONCURRENCY", "4")
    monkeypatch.setattr(
        analysis_module.source_structure_store,
        "read_visual_bytes",
        lambda **kwargs: (assets[kwargs["visual_id"]], content),
    )

    def save_analysis(**kwargs):
        asset = assets[kwargs["visual_id"]]
        assets[asset.id] = asset.model_copy(
            update={
                "metadata": {
                    **asset.metadata,
                    "codex_visual_analysis": kwargs["analysis"],
                }
            }
        )
        return assets[asset.id]

    monkeypatch.setattr(
        analysis_module.source_structure_store,
        "save_visual_codex_analysis",
        save_analysis,
    )

    class ParallelAdapter:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.barrier = threading.Barrier(4)

        def analyze_image_batch(self, **kwargs):
            payload = json.loads(kwargs["prompt"])
            visual_id = payload["visuals"][0]["visual_id"]
            self.calls.append(visual_id)
            assert len(kwargs["image_inputs"]) == 1
            self.barrier.wait(timeout=3)
            return json.dumps(
                {
                    "visuals": [
                        {
                            "visual_id": visual_id,
                            "description": f"Description for {visual_id}",
                            "visible_text": "Label",
                            "relationships": "A points to B",
                            "recommended_handling": "original_asset",
                            "confidence": 0.92,
                        }
                    ]
                }
            )

    adapter = ParallelAdapter()
    prepared = analysis_module.analyze_frozen_source_visuals(
        adapter=adapter,
        requirement=requirement,
        owner_user_id="user",
        model="test-model",
        is_cancelled=None,
        on_activity=None,
    )

    assert sorted(adapter.calls) == [f"visual_{index}" for index in range(4)]
    assert all(
        item.metadata["codex_visual_analysis"]["status"] == "completed"
        for item in prepared.source_grounding.frozen_visual_evidence
    )

    cached_adapter = ParallelAdapter()
    cached_adapter.barrier = threading.Barrier(1)
    cached = analysis_module.analyze_frozen_source_visuals(
        adapter=cached_adapter,
        requirement=requirement,
        owner_user_id="user",
        model="test-model",
        is_cancelled=None,
        on_activity=None,
    )

    assert cached_adapter.calls == []
    assert all(
        item.extracted_text.startswith(f"Description for {item.visual_id}")
        for item in cached.source_grounding.frozen_visual_evidence
    )


def test_visual_analysis_concurrency_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_VISUAL_ANALYSIS_CONCURRENCY", "100")
    assert analysis_module._analysis_concurrency() == 8

    monkeypatch.setenv("OPENCLASS_CODEX_VISUAL_ANALYSIS_CONCURRENCY", "0")
    assert analysis_module._analysis_concurrency() == 1


def test_visual_analysis_rejects_a_result_for_another_image(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_VISUAL_ANALYSIS_ENABLED", "true")
    content = b"\x89PNG\r\n\x1a\nvisual"
    evidence, asset = _visual(1, content)
    requirement = build_requirements("Visual analysis")
    requirement.source_grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        frozen_visual_evidence=[evidence],
    )
    monkeypatch.setattr(
        analysis_module.source_structure_store,
        "read_visual_bytes",
        lambda **_kwargs: (asset, content),
    )

    class WrongImageAdapter:
        def analyze_image_batch(self, **_kwargs):
            return json.dumps(
                {
                    "visuals": [
                        {
                            "visual_id": "another_visual",
                            "description": "Unrelated result",
                            "recommended_handling": "original_asset",
                            "confidence": 0.9,
                        }
                    ]
                }
            )

    with pytest.raises(RuntimeError, match="exactly one result"):
        analysis_module.analyze_frozen_source_visuals(
            adapter=WrongImageAdapter(),
            requirement=requirement,
            owner_user_id="user",
            model="test-model",
            is_cancelled=None,
            on_activity=None,
        )
