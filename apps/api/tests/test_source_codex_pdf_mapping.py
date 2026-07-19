from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import AIModelSelection, SourceChapter, SourceIngestionRecord
from app.services import source_codex_pdf_mapping as mapping_module
from app.services.source_codex_pdf_mapping import (
    CodexPdfPageCalibration,
    CodexPdfPrintedPageAnchor,
    CodexPdfPrintedPageSegment,
    PdfPageCalibrationResult,
    PdfPrintedPageSequenceCandidate,
    SourceCodexPdfMappingError,
    generate_pdf_page_calibration,
    map_pdf_native_outline_ranges,
    map_pdf_printed_page_ranges,
    printed_page_from_locator,
)


def _record(path: Path) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id="source_pdf_mapping",
        owner_user_id="user_pdf_mapping",
        package_id="course_pdf_mapping",
        title="PDF mapping",
        source_type="local_file",
        file_name=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        status="parsing",
    )


def _model() -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model="pdf-mapping-test-model",
        reasoning_effort="low",
        service_tier="priority",
    )


class FakeCalibrationClient:
    def __init__(
        self,
        calibration: CodexPdfPageCalibration,
        *,
        source_sha256: str,
    ) -> None:
        self.calibration = calibration
        self.source_sha256 = source_sha256
        self.calls: list[dict[str, object]] = []

    def parse_source_file(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=self.calibration,
            output_text=self.calibration.model_dump_json(),
            source_sha256=self.source_sha256,
            source_turn_count=1,
            usage={},
            activity=[],
        )


def _calibration_model(*, final_printed: int = 514, final_pdf: int = 530):
    return CodexPdfPageCalibration(
        complete=True,
        continuous_arabic_numbering=True,
        printed_page_start=1,
        printed_page_end=final_printed,
        pdf_page_start=17,
        pdf_page_end=final_pdf,
        anchors=[
            CodexPdfPrintedPageAnchor(printed_page=1, pdf_page=17),
            CodexPdfPrintedPageAnchor(printed_page=100, pdf_page=116),
            CodexPdfPrintedPageAnchor(printed_page=final_printed, pdf_page=final_pdf),
        ],
    )


def _result() -> PdfPageCalibrationResult:
    anchors = tuple(_calibration_model().anchors)
    return PdfPageCalibrationResult(
        printed_page_start=1,
        printed_page_end=514,
        pdf_page_start=17,
        pdf_page_end=530,
        page_offset=16,
        page_count=540,
        anchors=anchors,
        turn_count=1,
        raw_output="{}",
        raw_output_sha256="a" * 64,
        audit_metadata={},
    )


def _sequence_candidate(*, final_printed: int = 514, final_pdf: int = 530):
    return PdfPrintedPageSequenceCandidate(
        printed_page_start=1,
        printed_page_end=final_printed,
        pdf_page_start=17,
        pdf_page_end=final_pdf,
    )


def _segmented_calibration_model() -> CodexPdfPageCalibration:
    return CodexPdfPageCalibration(
        complete=True,
        continuous_arabic_numbering=False,
        printed_page_start=1,
        printed_page_end=250,
        pdf_page_start=17,
        pdf_page_end=266,
        anchors=[
            CodexPdfPrintedPageAnchor(printed_page=1, pdf_page=17),
            CodexPdfPrintedPageAnchor(printed_page=201, pdf_page=217),
            CodexPdfPrintedPageAnchor(printed_page=204, pdf_page=218),
            CodexPdfPrintedPageAnchor(printed_page=215, pdf_page=229),
            CodexPdfPrintedPageAnchor(printed_page=216, pdf_page=232),
            CodexPdfPrintedPageAnchor(printed_page=250, pdf_page=266),
        ],
        segments=[
            CodexPdfPrintedPageSegment(
                printed_page_start=1,
                printed_page_end=201,
                pdf_page_start=17,
                pdf_page_end=217,
            ),
            CodexPdfPrintedPageSegment(
                printed_page_start=204,
                printed_page_end=215,
                pdf_page_start=218,
                pdf_page_end=229,
            ),
            CodexPdfPrintedPageSegment(
                printed_page_start=216,
                printed_page_end=250,
                pdf_page_start=232,
                pdf_page_end=266,
            ),
        ],
    )


def _chapter(
    chapter_id: str,
    *,
    title: str,
    locator: str,
    level: int,
    order_index: int,
    parent_id: str | None = None,
) -> SourceChapter:
    return SourceChapter(
        id=chapter_id,
        owner_user_id="user_pdf_mapping",
        package_id="course_pdf_mapping",
        source_ingestion_id="source_pdf_mapping",
        parent_id=parent_id,
        title=title,
        source_locator=locator,
        level=level,
        order_index=order_index,
        mapping_status="unmapped",
    )


def test_pdf_calibration_runs_one_isolated_turn_and_validates_constant_offset(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"pdf bytes")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeCalibrationClient(_calibration_model(), source_sha256=source_hash)
    monkeypatch.setattr(mapping_module, "_pdf_page_count", lambda _path: 540)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [_sequence_candidate()],
    )
    monkeypatch.setattr(mapping_module, "_printed_page_evidence_runs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(mapping_module, "_verify_printed_footer_anchors", lambda *_args, **_kwargs: None)

    result = generate_pdf_page_calibration(
        record=_record(path),
        source_path=path,
        source_content_hash=source_hash,
        required_printed_page_min=22,
        required_printed_page_max=487,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert len(client.calls) == 1
    assert "output_artifact_path" not in client.calls[0]
    assert result.page_offset == 16
    assert result.pdf_page_start == 17
    assert result.pdf_page_end == 530
    assert result.audit_metadata["pdf_page_calibration_status"] == "verified"
    assert "Visually inspect" in str(client.calls[0]["system_prompt"])


def test_pdf_calibration_accepts_arbitrary_verified_anchors_without_page_one(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"pdf bytes")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    calibration = CodexPdfPageCalibration(
        complete=True,
        continuous_arabic_numbering=True,
        printed_page_start=22,
        printed_page_end=244,
        pdf_page_start=29,
        pdf_page_end=251,
        anchors=[
            CodexPdfPrintedPageAnchor(printed_page=22, pdf_page=29),
            CodexPdfPrintedPageAnchor(printed_page=164, pdf_page=171),
            CodexPdfPrintedPageAnchor(printed_page=244, pdf_page=251),
        ],
    )
    candidate = PdfPrintedPageSequenceCandidate(
        printed_page_start=22,
        printed_page_end=244,
        pdf_page_start=29,
        pdf_page_end=251,
    )
    client = FakeCalibrationClient(calibration, source_sha256=source_hash)
    monkeypatch.setattr(mapping_module, "_pdf_page_count", lambda _path: 251)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [candidate],
    )
    monkeypatch.setattr(mapping_module, "_printed_page_evidence_runs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        mapping_module,
        "_verify_printed_footer_anchors",
        lambda *_args, **_kwargs: None,
    )

    result = generate_pdf_page_calibration(
        record=_record(path),
        source_path=path,
        source_content_hash=source_hash,
        required_printed_page_min=22,
        required_printed_page_max=224,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert result.page_offset == 7
    assert result.printed_page_start == 22
    assert all(anchor.printed_page != 1 for anchor in result.anchors)
    assert "Do not search specifically for printed page 1" in str(
        client.calls[0]["user_prompt"]
    )


def test_pdf_calibration_investigates_empty_mechanical_candidates_and_returns_segments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"pdf bytes")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeCalibrationClient(
        _segmented_calibration_model(),
        source_sha256=source_hash,
    )
    monkeypatch.setattr(mapping_module, "_pdf_page_count", lambda _path: 280)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [],
    )
    evidence_runs = [
        {
            "pdf_page_start": 17,
            "pdf_page_end": 217,
            "printed_page_start": 1,
            "printed_page_end": 201,
            "page_offset": 16,
            "observed_label_count": 180,
            "samples": [{"pdf_page": 17, "printed_page": 1}],
        },
        {
            "pdf_page_start": 218,
            "pdf_page_end": 229,
            "printed_page_start": 204,
            "printed_page_end": 215,
            "page_offset": 14,
            "observed_label_count": 12,
            "samples": [{"pdf_page": 218, "printed_page": 204}],
        },
    ]
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_evidence_runs",
        lambda *_args, **_kwargs: evidence_runs,
    )
    monkeypatch.setattr(
        mapping_module,
        "_verify_printed_footer_anchors",
        lambda *_args, **_kwargs: None,
    )

    result = generate_pdf_page_calibration(
        record=_record(path),
        source_path=path,
        source_content_hash=source_hash,
        required_printed_page_min=1,
        required_printed_page_max=246,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )

    assert len(client.calls) == 1
    assert result.page_offset is None
    assert len(result.segments) == 3
    assert result.audit_metadata["pdf_printed_page_offsets"] == [14, 16]
    assert "can be empty" in str(client.calls[0]["user_prompt"])
    assert '"page_offset":14' in str(client.calls[0]["user_prompt"])
    assert "unavailable PDF command-line tools" in str(client.calls[0]["user_prompt"])
    assert "instead of stopping" in str(client.calls[0]["system_prompt"])


def test_pdf_calibration_rejects_inconsistent_or_insufficient_coverage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"pdf bytes")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(mapping_module, "_pdf_page_count", lambda _path: 540)
    monkeypatch.setattr(mapping_module, "_verify_printed_footer_anchors", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mapping_module, "_printed_page_evidence_runs", lambda *_args, **_kwargs: [])
    inconsistent = _calibration_model(final_pdf=531)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [_sequence_candidate(final_pdf=531)],
    )
    client = FakeCalibrationClient(inconsistent, source_sha256=source_hash)

    with pytest.raises(SourceCodexPdfMappingError, match="segment does not share one offset"):
        generate_pdf_page_calibration(
            record=_record(path),
            source_path=path,
            source_content_hash=source_hash,
            required_printed_page_min=22,
            required_printed_page_max=487,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )

    client = FakeCalibrationClient(_calibration_model(), source_sha256=source_hash)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [_sequence_candidate()],
    )
    with pytest.raises(SourceCodexPdfMappingError, match="does not cover"):
        generate_pdf_page_calibration(
            record=_record(path),
            source_path=path,
            source_content_hash=source_hash,
            required_printed_page_min=22,
            required_printed_page_max=600,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_printed_page_locators_are_strict_but_accept_legacy_fullwidth_digits() -> None:
    assert printed_page_from_locator("printed-page:22") == 22
    assert printed_page_from_locator("p. １６４") == 164
    assert printed_page_from_locator("第 398 页") == 398
    assert printed_page_from_locator("chapter 22") is None
    assert printed_page_from_locator("pp. 22-30") is None


def test_native_pdf_outline_maps_exact_codex_hierarchy_without_a_model_turn(
    tmp_path: Path,
) -> None:
    import fitz

    path = tmp_path / "native-outline.pdf"
    document = fitz.open()
    for _ in range(6):
        document.new_page(width=500, height=700)
    document.set_toc(
        [
            [1, "Chapter 1", 1],
            [2, "Section 1", 1],
            [2, "Section 2", 3],
            [1, "Chapter 2", 5],
        ]
    )
    document.save(path)
    document.close()
    chapters = [
        _chapter("chapter-1", title="Chapter 1", locator="", level=1, order_index=0),
        _chapter(
            "section-1",
            title="Section 1",
            locator="",
            level=2,
            order_index=1,
            parent_id="chapter-1",
        ),
        _chapter(
            "section-2",
            title="Section 2",
            locator="",
            level=2,
            order_index=2,
            parent_id="chapter-1",
        ),
        _chapter("chapter-2", title="Chapter 2", locator="", level=1, order_index=3),
    ]

    result = map_pdf_native_outline_ranges(chapters, source_path=path)

    assert result.status == "verified"
    assert result.page_count == 6
    assert result.outline_entry_count == 4
    assert result.mapped_count == 4
    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (1, 4),
        (1, 2),
        (3, 4),
        (5, 6),
    ]
    assert [chapter.source_locator for chapter in result.chapters] == [
        "pdf:outline:1",
        "pdf:outline:1",
        "pdf:outline:3",
        "pdf:outline:5",
    ]
    assert all(chapter.mapping_status == "verified" for chapter in result.chapters)
    assert all(
        chapter.catalog_evidence[-1].method == "pdf_native_outline"
        for chapter in result.chapters
    )


def test_native_pdf_outline_requires_exact_title_level_preorder_alignment(
    tmp_path: Path,
) -> None:
    import fitz

    path = tmp_path / "native-outline-mismatch.pdf"
    document = fitz.open()
    document.new_page(width=500, height=700)
    document.set_toc([[1, "Authoritative title", 1]])
    document.save(path)
    document.close()
    chapters = [
        _chapter("chapter", title="Different title", locator="", level=1, order_index=0)
    ]

    result = map_pdf_native_outline_ranges(chapters, source_path=path)

    assert result.status == "structure_mismatch"
    assert result.mapped_count == 0
    assert result.audit_metadata["pdf_native_outline_first_mismatch_index"] == 0
    assert result.chapters[0].mapping_status == "unmapped"
    assert result.chapters[0].range is None


def test_native_pdf_outline_accepts_overlapping_navigation_branches(
    tmp_path: Path,
) -> None:
    import fitz

    path = tmp_path / "native-outline-overlap.pdf"
    document = fitz.open()
    for _ in range(8):
        document.new_page(width=500, height=700)
    document.set_toc(
        [
            [1, "Outline navigation", 1],
            [2, "Nested contents", 2],
            [3, "Late child", 5],
            [1, "Repeated content", 3],
            [1, "Next document", 8],
        ]
    )
    document.save(path)
    document.close()
    chapters = [
        _chapter("outline", title="Outline navigation", locator="", level=1, order_index=0),
        _chapter(
            "nested-contents",
            title="Nested contents",
            locator="",
            level=2,
            order_index=1,
            parent_id="outline",
        ),
        _chapter(
            "late-child",
            title="Late child",
            locator="",
            level=3,
            order_index=2,
            parent_id="nested-contents",
        ),
        _chapter("repeated", title="Repeated content", locator="", level=1, order_index=3),
        _chapter("next", title="Next document", locator="", level=1, order_index=4),
    ]

    result = map_pdf_native_outline_ranges(chapters, source_path=path)

    assert result.status == "verified"
    assert result.audit_metadata["pdf_native_outline_backward_jump_count"] == 1
    assert [(chapter.range.start, chapter.range.end) for chapter in result.chapters] == [
        (1, 7),
        (2, 7),
        (5, 7),
        (3, 7),
        (8, 8),
    ]


def test_mechanical_mapping_closes_ranges_by_next_same_or_shallower_node() -> None:
    chapters = [
        _chapter("chapter-1", title="Chapter 1", locator="printed-page:22", level=1, order_index=0),
        _chapter("section-1", title="Section 1", locator="p. 23", level=2, order_index=1),
        _chapter("preface", title="Unmapped", locator="", level=2, order_index=2),
        _chapter("chapter-2", title="Chapter 2", locator="p. １６４", level=1, order_index=3),
    ]

    mapped = map_pdf_printed_page_ranges(chapters, calibration=_result())

    assert mapped[0].range is not None
    assert (mapped[0].range.start, mapped[0].range.end) == (38, 179)
    assert (mapped[1].range.start, mapped[1].range.end) == (39, 179)
    assert mapped[2].range is None
    assert mapped[2].mapping_status == "unmapped"
    assert (mapped[3].range.start, mapped[3].range.end) == (180, 530)
    assert mapped[3].page_end == 531
    assert all(mapped[index].mapping_status == "verified" for index in (0, 1, 3))


def test_mechanical_mapping_keeps_shared_boundary_page_inside_parent() -> None:
    chapters = [
        _chapter("parent", title="Parent", locator="printed-page:63", level=2, order_index=0),
        _chapter("child", title="Child", locator="printed-page:64", level=3, order_index=1),
        _chapter("next", title="Next section", locator="printed-page:64", level=2, order_index=2),
    ]

    mapped = map_pdf_printed_page_ranges(chapters, calibration=_result())

    assert (mapped[0].range.start, mapped[0].range.end) == (79, 80)
    assert (mapped[1].range.start, mapped[1].range.end) == (80, 80)
    assert mapped[1].range.end <= mapped[0].range.end


def test_mechanical_mapping_derives_parent_range_from_boundary_children() -> None:
    chapters = [
        _chapter("chapter-4", title="Chapter 4", locator="", level=1, order_index=0),
        _chapter(
            "section-1",
            title="Constructing a Mobius strip",
            locator="printed-page:65",
            level=2,
            order_index=1,
            parent_id="chapter-4",
        ),
        _chapter(
            "section-2",
            title="The identification topology",
            locator="printed-page:66",
            level=2,
            order_index=2,
            parent_id="chapter-4",
        ),
        _chapter(
            "section-3",
            title="Topological groups",
            locator="printed-page:73",
            level=2,
            order_index=3,
            parent_id="chapter-4",
        ),
        _chapter(
            "section-4",
            title="Orbit spaces",
            locator="printed-page:78",
            level=2,
            order_index=4,
            parent_id="chapter-4",
        ),
        _chapter("chapter-5", title="Chapter 5", locator="", level=1, order_index=5),
        _chapter(
            "next-section",
            title="Homotopic maps",
            locator="printed-page:87",
            level=2,
            order_index=6,
            parent_id="chapter-5",
        ),
    ]

    mapped = map_pdf_printed_page_ranges(chapters, calibration=_result())

    parent = mapped[0]
    assert parent.range is not None
    assert (parent.range.start, parent.range.end) == (81, 102)
    assert parent.range.display_label == "PDF pp. 81-102"
    assert parent.mapping_status == "verified"
    assert parent.metadata["range_derived_from_children"] is True
    assert parent.catalog_evidence[-1].method == "verified_child_range_union"
    assert parent.page_end == 103


def test_parent_range_allows_an_unmapped_middle_child_but_requires_mapped_boundaries() -> None:
    chapters = [
        _chapter("parent", title="Parent", locator="", level=1, order_index=0),
        _chapter(
            "first",
            title="First",
            locator="printed-page:195",
            level=2,
            order_index=1,
            parent_id="parent",
        ),
        _chapter(
            "missing-middle",
            title="Missing middle",
            locator="printed-page:202",
            level=2,
            order_index=2,
            parent_id="parent",
        ),
        _chapter(
            "last",
            title="Last",
            locator="printed-page:210",
            level=2,
            order_index=3,
            parent_id="parent",
        ),
    ]
    model = _segmented_calibration_model()
    calibration = PdfPageCalibrationResult(
        printed_page_start=model.printed_page_start,
        printed_page_end=model.printed_page_end,
        pdf_page_start=model.pdf_page_start,
        pdf_page_end=model.pdf_page_end,
        page_offset=None,
        page_count=280,
        anchors=tuple(model.anchors),
        turn_count=1,
        raw_output="{}",
        raw_output_sha256="d" * 64,
        audit_metadata={},
        segments=tuple(model.segments),
    )

    mapped = map_pdf_printed_page_ranges(chapters, calibration=calibration)

    assert mapped[0].range is not None
    assert (mapped[0].range.start, mapped[0].range.end) == (211, 266)
    assert mapped[1].mapping_status == "verified"
    assert mapped[2].mapping_status == "unmapped"
    assert mapped[3].mapping_status == "verified"

    missing_first = [
        chapters[0],
        chapters[2].model_copy(update={"parent_id": "parent", "order_index": 1}),
        chapters[3].model_copy(update={"parent_id": "parent", "order_index": 2}),
    ]
    remapped = map_pdf_printed_page_ranges(missing_first, calibration=calibration)
    assert remapped[0].mapping_status == "unmapped"
    assert remapped[0].range is None


def test_segmented_mapping_uses_each_verified_offset_and_leaves_missing_pages_unmapped() -> None:
    model = _segmented_calibration_model()
    calibration = PdfPageCalibrationResult(
        printed_page_start=model.printed_page_start,
        printed_page_end=model.printed_page_end,
        pdf_page_start=model.pdf_page_start,
        pdf_page_end=model.pdf_page_end,
        page_offset=None,
        page_count=280,
        anchors=tuple(model.anchors),
        turn_count=1,
        raw_output="{}",
        raw_output_sha256="c" * 64,
        audit_metadata={},
        segments=tuple(model.segments),
    )
    chapters = [
        _chapter("before-gap", title="Before gap", locator="p. 201", level=1, order_index=0),
        _chapter("missing", title="Missing", locator="p. 202", level=1, order_index=1),
        _chapter("middle", title="Middle", locator="p. 204", level=1, order_index=2),
        _chapter("after-duplicate", title="After duplicate", locator="p. 216", level=1, order_index=3),
    ]

    mapped = map_pdf_printed_page_ranges(chapters, calibration=calibration)

    assert mapped[0].range is not None and mapped[0].range.start == 217
    assert mapped[1].range is None
    assert mapped[1].mapping_status == "unmapped"
    assert mapped[2].range is not None and mapped[2].range.start == 218
    assert mapped[3].range is not None and mapped[3].range.start == 232
    assert mapped[2].metadata["pdf_page_offset"] == 14
    assert mapped[3].metadata["pdf_page_offset"] == 16


def test_footer_page_number_verification_reads_only_margin_label(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "footer.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=700)
    page.insert_text((200, 350), "body number 999")
    page.insert_text((45, 680), "244")
    document.save(path)
    document.close()

    reopened = fitz.open(path)
    try:
        assert mapping_module._footer_page_numbers(reopened.load_page(0)) == {244}
    finally:
        reopened.close()


def test_page_number_verification_accepts_centered_footer_and_outer_header(
    tmp_path: Path,
) -> None:
    import fitz

    path = tmp_path / "margin-labels.pdf"
    document = fitz.open()
    centered_footer = document.new_page(width=500, height=700)
    centered_footer.insert_text((245, 680), "12")
    outer_header = document.new_page(width=500, height=700)
    outer_header.insert_text((440, 35), "13")
    document.save(path)
    document.close()

    reopened = fitz.open(path)
    try:
        assert mapping_module._footer_page_numbers(reopened.load_page(0)) == {12}
        assert mapping_module._footer_page_numbers(reopened.load_page(1)) == {13}
    finally:
        reopened.close()


def test_mechanical_footer_scan_discovers_continuous_printed_page_sequence(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "sequence.pdf"
    document = fitz.open()
    document.new_page(width=500, height=700).insert_text((200, 350), "front matter")
    for printed_page in range(1, 6):
        page = document.new_page(width=500, height=700)
        page.insert_text((45 if printed_page % 2 == 0 else 440, 680), str(printed_page))
    document.save(path)
    document.close()

    candidates = mapping_module._printed_page_sequence_candidates(
        path,
        page_count=6,
        required_printed_page_min=1,
        required_printed_page_max=4,
    )

    assert candidates == [
        PdfPrintedPageSequenceCandidate(
            printed_page_start=1,
            printed_page_end=5,
            pdf_page_start=2,
            pdf_page_end=6,
        )
    ]


def test_mechanical_footer_scan_derives_offset_without_printed_page_one(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "sequence-without-page-one.pdf"
    document = fitz.open()
    document.new_page(width=500, height=700).insert_text((200, 350), "front matter")
    for printed_page in range(22, 27):
        page = document.new_page(width=500, height=700)
        page.insert_text((45 if printed_page % 2 == 0 else 440, 680), str(printed_page))
    document.save(path)
    document.close()

    candidates = mapping_module._printed_page_sequence_candidates(
        path,
        page_count=6,
        required_printed_page_min=22,
        required_printed_page_max=25,
    )

    assert candidates == [
        PdfPrintedPageSequenceCandidate(
            printed_page_start=22,
            printed_page_end=26,
            pdf_page_start=2,
            pdf_page_end=6,
        )
    ]


def test_mechanical_evidence_runs_preserve_offset_transitions(tmp_path: Path) -> None:
    import fitz

    path = tmp_path / "segmented-sequence.pdf"
    document = fitz.open()
    document.new_page(width=500, height=700).insert_text((200, 350), "front matter")
    for printed_page in (1, 2, 4, 5, 5, 6):
        page = document.new_page(width=500, height=700)
        page.insert_text((245, 680), str(printed_page))
    document.save(path)
    document.close()

    runs = mapping_module._printed_page_evidence_runs(path, page_count=7)

    assert [run["page_offset"] for run in runs] == [1, 0, 1]
    assert [run["observed_label_count"] for run in runs] == [2, 2, 2]
    assert runs[1]["printed_page_start"] == 4
    assert runs[2]["pdf_page_start"] == 6
