from __future__ import annotations

from pathlib import Path

from app.models import (
    SelectionRef,
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceStructure,
    SourceStructureQuality,
)
from app.services.source_chapter_identity import (
    rebind_stale_source_chapter_selection,
    stable_source_chapter_id,
)
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_quality import evaluate_source_structure_quality
from app.services.source_structure_store import SourceStructureStore


def _chapter(
    chapter_id: str,
    *,
    start: int | None,
    end: int | None,
    title: str | None = None,
    parent_id: str | None = None,
    level: int = 1,
    order_index: int = 0,
    locator: str | None = None,
    verified: bool = True,
) -> SourceChapter:
    return SourceChapter(
        id=chapter_id,
        owner_user_id="user_1",
        package_id="package_1",
        source_ingestion_id="source_1",
        parent_id=parent_id,
        title=title or chapter_id,
        level=level,
        order_index=order_index,
        source_locator=locator or f"page:{order_index + 1}",
        body_start_offset=start,
        body_end_offset=end,
        anchor_status="verified" if verified else "unverified",
        confidence=0.9 if verified else 0.5,
        metadata={"source": "markdown_heading"},
    )


def test_complete_non_overlapping_structure_is_fully_verified() -> None:
    text = "A" * 200
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("chapter_1", start=0, end=100, order_index=0),
            _chapter("chapter_2", start=100, end=200, order_index=1),
        ],
        text=text,
    )

    assert result.quality.level == "fully_verified"
    assert result.quality.verified_ratio == 1.0
    assert result.quality.body_coverage_ratio == 1.0
    assert result.warnings == []


def test_long_document_with_only_coarse_leaf_ranges_is_partial() -> None:
    text = "A" * 360_000
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("volume_1", start=0, end=240_000, order_index=0),
            _chapter("volume_2", start=240_000, end=len(text), order_index=1),
        ],
        text=text,
        strategy="pdf_outline",
        metadata={"parser": "pdf", "page_count": 120},
    )

    assert result.quality.level == "partially_verified"
    assert result.quality.verified_leaf_count == 2
    assert result.quality.expected_leaf_count == 3
    assert result.quality.oversized_leaf_count == 1
    assert "当前章节粒度相对资料长度过粗。" in result.quality.diagnostics


def test_invalid_and_duplicate_ranges_are_demoted_before_chunk_assignment() -> None:
    text = "A" * 300
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("chapter_1", start=0, end=100, order_index=0),
            _chapter("chapter_duplicate", start=0, end=100, order_index=1),
            _chapter("chapter_reversed", start=180, end=160, order_index=2),
            _chapter("chapter_candidate", start=None, end=None, order_index=3, verified=False),
        ],
        text=text,
    )

    assert result.quality.verified_chapter_count == 1
    assert result.quality.demoted_chapter_count == 2
    assert result.quality.duplicate_range_count == 1
    assert [chapter.anchor_status for chapter in result.chapters] == [
        "verified",
        "unverified",
        "unverified",
        "unverified",
    ]
    assert result.chapters[1].metadata["anchor_validation"]["reasons"] == [
        "duplicate_grounded_range"
    ]


def test_parent_child_nesting_is_not_counted_as_sibling_overlap() -> None:
    text = "A" * 200
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("parent", start=0, end=200, order_index=0),
            _chapter(
                "child_1",
                start=0,
                end=100,
                parent_id="parent",
                level=2,
                order_index=1,
            ),
            _chapter(
                "child_2",
                start=100,
                end=200,
                parent_id="parent",
                level=2,
                order_index=2,
            ),
        ],
        text=text,
    )

    assert result.quality.overlap_ratio == 0.0
    assert result.quality.level == "fully_verified"


def test_repeated_native_locator_is_diagnostic_but_distinct_ranges_remain_usable() -> None:
    text = "A" * 200
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("chapter_1", start=0, end=100, order_index=0, locator="epub:file.xhtml"),
            _chapter("chapter_2", start=100, end=200, order_index=1, locator="epub:file.xhtml"),
        ],
        text=text,
    )

    assert result.quality.duplicate_locator_ratio == 0.5
    assert all(chapter.anchor_status == "verified" for chapter in result.chapters)
    assert result.quality.level == "fully_verified"


def test_unresolved_navigation_candidates_are_unverified() -> None:
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("candidate", start=None, end=None, verified=False),
        ],
        text="searchable body",
    )

    assert result.quality.level == "unverified"
    assert result.quality.verified_chapter_count == 0
    assert result.warnings[0].startswith("资料目录候选尚未形成")


def test_text_without_navigation_candidates_is_search_only() -> None:
    result = evaluate_source_structure_quality(
        chapters=[],
        text="searchable body",
    )

    assert result.quality.level == "search_only"
    assert result.warnings == ["未验证到可安全引用的章节正文，资料已保留全文片段检索。"]


def test_empty_text_without_navigation_candidates_is_unverified() -> None:
    result = evaluate_source_structure_quality(chapters=[], text="")

    assert result.quality.level == "unverified"
    assert result.quality.text_readiness == "empty"
    assert "资料没有提取到可检索正文。" in result.quality.diagnostics
    assert result.warnings == [
        "资料没有提取到可检索正文；目录引用与全文检索当前均不可用。"
    ]


def test_unicode_text_counts_as_usable_body_content() -> None:
    text = "مرحبا بالعالم"
    result = evaluate_source_structure_quality(
        chapters=[_chapter("chapter_1", start=0, end=len(text))],
        text=text,
    )

    assert result.quality.level == "fully_verified"
    assert result.chapters[0].anchor_status == "verified"


def test_overlapping_sibling_ranges_are_demoted() -> None:
    text = "A" * 200
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("chapter_1", start=0, end=120, order_index=0),
            _chapter("chapter_2", start=80, end=200, order_index=1),
        ],
        text=text,
    )

    assert result.quality.level == "unverified"
    assert result.quality.overlap_ratio == 1.0
    assert result.quality.demoted_chapter_count == 2
    assert all(chapter.anchor_status == "unverified" for chapter in result.chapters)
    assert result.chapters[0].metadata["anchor_validation"]["reasons"] == [
        "overlapping_sibling_range"
    ]


def test_sparse_pdf_text_layer_cannot_be_marked_fully_verified() -> None:
    text = "A" * 300
    result = evaluate_source_structure_quality(
        chapters=[
            _chapter("chapter_1", start=0, end=150, order_index=0),
            _chapter("chapter_2", start=150, end=300, order_index=1),
        ],
        text=text,
        strategy="pdf_outline",
        metadata={"parser": "pdf", "page_count": 20},
    )

    assert result.quality.text_readiness == "very_sparse"
    assert result.quality.meaningful_characters_per_page == 15
    assert result.quality.level == "partially_verified"


def test_short_native_table_and_slide_content_is_not_treated_as_empty() -> None:
    for parser in ("pptx", "xlsx"):
        result = evaluate_source_structure_quality(
            chapters=[_chapter("chapter_1", start=0, end=1)],
            text="A",
            metadata={"parser": parser},
        )

        assert result.quality.level == "fully_verified"
        assert result.quality.expected_leaf_count == 1


def test_missing_anchor_provenance_cannot_claim_full_verification() -> None:
    text = "A" * 100
    chapter = _chapter("chapter_1", start=0, end=len(text)).model_copy(
        update={"metadata": {}}
    )

    result = evaluate_source_structure_quality(chapters=[chapter], text=text)

    assert result.quality.independent_anchor_ratio == 0.0
    assert result.quality.level == "partially_verified"


def test_semantic_chapter_identity_ignores_parser_locator_changes() -> None:
    common = {
        "source_ingestion_id": "source_1",
        "parent_path": ["Part I"],
        "normalized_number": "1.2",
        "title": "A stable section",
        "level": 2,
        "order_index": 0,
    }

    outline_id = stable_source_chapter_id(
        **common,
        source_locator="pdf:outline:12",
    )
    toc_id = stable_source_chapter_id(
        **common,
        source_locator="pdf:toc-page:3:printed:7",
    )

    assert outline_id == toc_id


def test_stale_selection_narrows_duplicate_locator_with_title_and_path() -> None:
    shared_locator = "epub:OEBPS/chapter.xhtml"
    first = _chapter(
        "current_1",
        start=0,
        end=100,
        title="First section",
        order_index=0,
        locator=shared_locator,
    ).model_copy(update={"path": ["Part I", "First section"]})
    second = _chapter(
        "current_2",
        start=100,
        end=200,
        title="Second section",
        order_index=1,
        locator=shared_locator,
    ).model_copy(update={"path": ["Part I", "Second section"]})
    selection = SelectionRef(
        kind="source",
        excerpt="Second section",
        source_ingestion_id="source_1",
        source_chapter_id="sourcechapter_v1_stale",
        source_chapter_title="Second section",
        source_locator=shared_locator,
        heading_path=["Part I", "Second section"],
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=[first, second],
    )

    assert rebound.chapter == second
    assert rebound.matched_anchors == (
        "source_locator",
        "chapter_title",
        "heading_path",
    )


def test_stale_selection_uses_title_path_consensus_when_page_bounds_change() -> None:
    chapter = _chapter(
        "current_1",
        start=0,
        end=100,
        title="Stable section",
        locator="pdf:outline:9",
    ).model_copy(
        update={
            "path": ["Part I", "Stable section"],
            "page_start": 9,
            "page_end": 12,
        }
    )
    selection = SelectionRef(
        kind="source",
        excerpt="Stable section",
        source_ingestion_id="source_1",
        source_chapter_id="sourcechapter_v1_stale",
        source_chapter_title="Stable section",
        source_locator="pdf:outline:8",
        heading_path=["Part I", "Stable section"],
        source_page_start=8,
        source_page_end=10,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=[chapter],
    )

    assert rebound.chapter == chapter
    assert rebound.matched_anchors == ("chapter_title", "heading_path")


def test_partial_structure_persists_quality_and_keeps_verified_chapter_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "openclass.sqlite3"
    source_store = SourceEvidenceStore(database)
    structure_store = SourceStructureStore(database)
    source = SourceIngestionRecord(
        id="source_1",
        owner_user_id="user_1",
        package_id="package_1",
        title="Source",
        file_name="source.md",
        mime_type="text/markdown",
        status="ready",
    )
    source_store.save_source(source)
    chapter = _chapter("chapter_1", start=0, end=20, title="Section")
    quality = SourceStructureQuality(
        level="partially_verified",
        confidence=0.78,
        total_chapter_count=2,
        verified_chapter_count=1,
        unverified_chapter_count=1,
        verified_ratio=0.5,
        boundary_valid_ratio=1.0,
        body_coverage_ratio=0.5,
    )
    structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id="package_1",
            source_ingestion_id="source_1",
            status="ready",
            strategy="markdown_heading",
            quality=quality,
        ),
        chapters=[chapter],
        chunks=[
            SourceChunk(
                owner_user_id="user_1",
                package_id="package_1",
                source_ingestion_id="source_1",
                chapter_id=chapter.id,
                text="Section body content",
                start_offset=0,
                end_offset=20,
                token_count=5,
            )
        ],
    )

    saved = structure_store.get_structure(
        owner_user_id="user_1",
        package_id="package_1",
        source_id="source_1",
    )
    evidence = structure_store.chapter_evidence_by_id(
        owner_user_id="user_1",
        package_id="package_1",
        chapter_id=chapter.id,
        limit=4,
        token_budget=100,
    )

    assert saved is not None
    assert saved.quality.level == "partially_verified"
    assert saved.quality.verified_chapter_count == 1
    assert [item.chapter_id for item in evidence] == [chapter.id]
