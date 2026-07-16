from __future__ import annotations

import json
import io
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from app.models import (
    EvidenceBundle,
    LearningSourceGrounding,
    LearningSourceReference,
    SourceIngestionRecord,
    SourceStructure,
    SourceVisualAsset,
    SourceVisualEvidence,
)
from app.services.lesson_factory import build_requirements
from app.services import source_visual_region_resolution as resolution
from app.services.source_visual_extraction_pdf import (
    render_pdf_normalized_region,
    render_pdf_visual_clue_page,
    trim_normalized_bbox_before_caption,
)
from app.services.source_structure_store import SourceStructureStore


def _visual_evidence(asset: SourceVisualAsset) -> SourceVisualEvidence:
    return SourceVisualEvidence(
        visual_id=asset.id,
        package_id=asset.package_id,
        source_ingestion_id=asset.source_ingestion_id,
        source_chapter_id=asset.chapter_id or "",
        kind=asset.kind,
        source_locator=asset.source_locator,
        page_start=asset.page_start,
        page_end=asset.page_end,
        bbox=asset.bbox,
        before_chunk_id=asset.before_chunk_id,
        after_chunk_id=asset.after_chunk_id,
        caption=asset.caption,
        extracted_text=asset.extracted_text,
        surrounding_text=asset.surrounding_text,
        anchor_status=asset.anchor_status,
        mime_type=asset.mime_type,
        order_index=asset.order_index,
        content_hash=asset.content_hash,
        position_hash=asset.position_hash,
        width=asset.width,
        height=asset.height,
        confidence=asset.confidence,
        metadata=asset.metadata,
    )


def _pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(600, 800))
    pdf.drawString(40, 735, "A paragraph before the figure.")
    pdf.line(150, 520, 150, 680)
    pdf.line(150, 520, 420, 520)
    pdf.line(170, 540, 390, 650)
    pdf.drawString(132, 680, "y")
    pdf.drawString(425, 510, "x")
    pdf.drawString(215, 485, "Figure 1 Complete chart")
    pdf.save()


def _clue(source_path: Path) -> tuple[SourceIngestionRecord, SourceVisualAsset]:
    source = SourceIngestionRecord(
        id="source_visual_clues",
        owner_user_id="user_visual_clues",
        package_id="package_visual_clues",
        title="Visual source",
        file_name=source_path.name,
        mime_type="application/pdf",
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    clue = SourceVisualAsset(
        id="fragment_red_line",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        structure_id="structure_visual_clues",
        structure_version=4,
        chapter_id="chapter_visual_clues",
        kind="image",
        source_locator="pdf:page:1:image:2:occurrence:0",
        page_start=1,
        page_end=1,
        bbox=[0.28, 0.18, 0.66, 0.36],
        anchor_status="unverified",
        order_index=2,
        metadata={
            "requires_full_visual_capture": True,
            "visual_completeness_verified": False,
            "pdf_region_type": "embedded_image",
        },
    )
    return source, clue


def test_pdf_clue_page_keeps_full_page_and_backend_crop(tmp_path: Path) -> None:
    source_path = tmp_path / "visual.pdf"
    _pdf(source_path)
    _source, clue = _clue(source_path)

    page = render_pdf_visual_clue_page(source_path, page_no=1, clues=[("C1", clue)])
    crop = render_pdf_normalized_region(
        source_path,
        page_no=1,
        bbox=[0.20, 0.13, 0.74, 0.42],
    )
    trimmed = trim_normalized_bbox_before_caption(
        source_path,
        page_no=1,
        bbox=[0.20, 0.13, 0.74, 0.42],
    )

    assert page is not None
    assert page.original_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert page.clue_map_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert page.original_png != page.clue_map_png
    assert crop is not None
    assert crop[0].startswith(b"\x89PNG\r\n\x1a\n")
    assert crop[1] < page.width
    assert crop[2] < page.height
    assert 0.35 < trimmed[3] < 0.40


def test_candidate_validation_rejects_fragment_and_verified_duplicate(tmp_path: Path) -> None:
    source_path = tmp_path / "visual.pdf"
    _pdf(source_path)
    _source, clue = _clue(source_path)
    clue_map = {"C1": clue}

    fragment = resolution._FigureRegionCandidate(
        complete=True,
        clue_ids=["C1"],
        bbox=[0.30, 0.20, 0.50, 0.30],
        confidence=0.95,
    )
    complete = resolution._FigureRegionCandidate(
        complete=True,
        clue_ids=["C1"],
        bbox=[0.20, 0.13, 0.74, 0.42],
        confidence=0.95,
    )
    existing = SourceVisualEvidence(
        visual_id="existing",
        source_ingestion_id="source_visual_clues",
        anchor_status="verified",
        bbox=[0.20, 0.13, 0.74, 0.42],
    )

    assert resolution._validated_candidate_bbox(
        fragment,
        clue_map=clue_map,
        existing=[],
        used_clues=set(),
    ) is None
    assert resolution._validated_candidate_bbox(
        complete,
        clue_map=clue_map,
        existing=[existing],
        used_clues=set(),
    ) is None
    assert resolution._validated_candidate_bbox(
        complete,
        clue_map=clue_map,
        existing=[],
        used_clues=set(),
    ) == [0.192, 0.122, 0.748, 0.428]


def test_partial_caption_fragment_touching_bottom_is_trimmed() -> None:
    image = Image.new("RGB", (240, 180), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((20, 20, 220, 125), outline="black", width=4)
    drawing.rectangle((65, 170, 175, 179), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")

    ratio = resolution._partial_bottom_content_trim_ratio(output.getvalue())

    assert ratio is not None
    assert 0.85 < ratio < 0.98


def test_resolved_asset_replaces_older_crop_from_same_clues(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "structure.sqlite3")
    source_path = tmp_path / "visual.pdf"
    _pdf(source_path)
    source, clue = _clue(source_path)
    structure = SourceStructure(
        id=clue.structure_id,
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        status="ready",
    )
    store.save_structure_bundle(structure=structure, chapters=[], chunks=[], visuals=[clue])

    first = clue.model_copy(
        update={
            "id": "resolved_old",
            "anchor_status": "verified",
            "bbox": [0.20, 0.13, 0.74, 0.42],
            "content_hash": "1" * 64,
            "position_hash": "2" * 64,
            "metadata": {
                "visual_completeness_verified": True,
                "component_visual_ids": [clue.id],
            },
        }
    )
    second = first.model_copy(
        update={
            "id": "resolved_new",
            "bbox": [0.20, 0.13, 0.74, 0.38],
            "content_hash": "3" * 64,
            "position_hash": "4" * 64,
        }
    )

    store.upsert_resolved_visual_asset(first)
    store.upsert_resolved_visual_asset(second)

    assert store.get_visual(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
        visual_id=first.id,
    ) is None
    assert store.get_visual(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_id=source.id,
        visual_id=second.id,
    ) is not None
    assert [item.id for item in store.visual_clues_for_scope(
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
    )] == [clue.id]


def test_requirement_resolution_promotes_only_complete_crop(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "visual.pdf"
    _pdf(source_path)
    source, clue = _clue(source_path)
    bundle = EvidenceBundle(
        id="bundle_visual_clues",
        owner_user_id=source.owner_user_id,
        package_id=source.package_id,
        lesson_id="lesson_visual_clues",
        purpose="board_generation",
        status="confirmed",
        confirmed_by_user=True,
    )

    class FakeEvidenceStore:
        saved_bundle = None

        def get_bundle(self, **_kwargs):
            return bundle

        def get_source(self, **_kwargs):
            return source

        def save_bundle(self, value):
            self.saved_bundle = value
            return value

    class FakeStructureStore:
        saved_asset = None

        def visual_clues_for_scope(self, **_kwargs):
            return [clue]

        def visual_evidence_for_scope(self, **_kwargs):
            return [_visual_evidence(self.saved_asset)] if self.saved_asset is not None else []

        def upsert_resolved_visual_asset(self, asset):
            self.saved_asset = asset
            return asset

    class FakeAdapter:
        calls = []

        def analyze_image_batch(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps(
                {
                    "figures": [
                        {
                            "complete": True,
                            "clue_ids": ["C1"],
                            "bbox": [0.20, 0.13, 0.74, 0.42],
                            "caption": "Figure 1 Complete chart",
                            "description": "Axes and a rising line",
                            "confidence": 0.96,
                            "reason": "The axes, labels, and line are all enclosed.",
                        }
                    ]
                }
            )

    fake_evidence = FakeEvidenceStore()
    fake_structure = FakeStructureStore()
    adapter = FakeAdapter()
    monkeypatch.setattr(resolution, "source_evidence_store", fake_evidence)
    monkeypatch.setattr(resolution, "source_structure_store", fake_structure)
    monkeypatch.setattr(resolution, "source_local_path", lambda _source: source_path)
    monkeypatch.setattr(resolution, "source_download_path", lambda _source: None)
    monkeypatch.setattr(
        resolution,
        "persist_source_visual_asset",
        lambda _content, *, mime_type: ("blobs/aa/resolved.png", "a" * 64),
    )
    monkeypatch.setattr(
        resolution,
        "resolve_source_visual_storage_key",
        lambda _key: tmp_path / "resolved.png",
    )

    requirement = build_requirements("Visual clue board")
    requirement.source_grounding = LearningSourceGrounding(
        requested_by_user=True,
        confirmation_status="confirmed",
        confirmed_bundle_id=bundle.id,
        confirmed_references=[
            LearningSourceReference(
                evidence_bundle_id=bundle.id,
                source_ingestion_id=source.id,
                source_chapter_id=clue.chapter_id or "",
                page_start=1,
                page_end=2,
                source_structure_id=clue.structure_id,
            )
        ],
    )

    prepared = resolution.resolve_visual_clues_for_requirement(
        adapter=adapter,
        requirement=requirement,
        owner_user_id=source.owner_user_id,
        is_cancelled=None,
        on_activity=None,
    )

    assert len(adapter.calls) == 1
    assert len(adapter.calls[0]["image_inputs"]) == 2
    assert fake_structure.saved_asset is not None
    assert fake_structure.saved_asset.anchor_status == "verified"
    assert fake_structure.saved_asset.metadata["component_visual_ids"] == [clue.id]
    assert fake_structure.saved_asset.metadata["visual_completeness_verified"] is True
    assert fake_structure.saved_asset.bbox[3] < 0.40
    assert prepared.source_grounding.frozen_visual_evidence[0].visual_id == fake_structure.saved_asset.id
    assert prepared.source_grounding.confirmed_references[0].visual_ids == [fake_structure.saved_asset.id]
    assert fake_evidence.saved_bundle.visual_items[0].visual_id == fake_structure.saved_asset.id

    cached = resolution.resolve_visual_clues_for_requirement(
        adapter=adapter,
        requirement=requirement,
        owner_user_id=source.owner_user_id,
        is_cancelled=None,
        on_activity=None,
    )

    assert len(adapter.calls) == 1
    assert cached.source_grounding.frozen_visual_evidence[0].visual_id == fake_structure.saved_asset.id
