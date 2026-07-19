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
    PdfPageCalibrationResult,
    PdfPrintedPageSequenceCandidate,
    SourceCodexPdfMappingError,
    generate_pdf_page_calibration,
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


def _chapter(
    chapter_id: str,
    *,
    title: str,
    locator: str,
    level: int,
    order_index: int,
) -> SourceChapter:
    return SourceChapter(
        id=chapter_id,
        owner_user_id="user_pdf_mapping",
        package_id="course_pdf_mapping",
        source_ingestion_id="source_pdf_mapping",
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


def test_pdf_calibration_rejects_inconsistent_or_insufficient_coverage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"pdf bytes")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(mapping_module, "_pdf_page_count", lambda _path: 540)
    monkeypatch.setattr(mapping_module, "_verify_printed_footer_anchors", lambda *_args, **_kwargs: None)
    inconsistent = _calibration_model(final_pdf=531)
    monkeypatch.setattr(
        mapping_module,
        "_printed_page_sequence_candidates",
        lambda *_args, **_kwargs: [_sequence_candidate(final_pdf=531)],
    )
    client = FakeCalibrationClient(inconsistent, source_sha256=source_hash)

    with pytest.raises(SourceCodexPdfMappingError, match="endpoints do not share one offset"):
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
