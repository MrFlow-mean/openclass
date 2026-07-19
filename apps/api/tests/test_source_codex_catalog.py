from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import (
    AIModelSelection,
    SourceCatalogRun,
    SourceChapter,
    SourceIngestionRecord,
    SourceRange,
)
from app.services import source_directory_processor as directory_processor_module
from app.services.source_codex_catalog import (
    CodexDirectCatalog,
    CodexDirectCatalogNode,
    SourceCodexCatalogError,
    generate_codex_direct_catalog,
    materialize_stored_codex_catalog,
)
from app.services.source_codex_pdf_mapping import (
    CodexPdfPrintedPageAnchor,
    PdfPageCalibrationResult,
)
from app.services.source_directory_processor import (
    SourceDirectoryProcessingError,
    SourceDirectoryProcessor,
)
from app.services.source_structure_store import SourceStructureStore


def _node(
    key: str,
    *,
    title: str | None = None,
    parent_key: str | None = None,
    level: int = 1,
    number: str | None = None,
    source_locator: str = "",
) -> CodexDirectCatalogNode:
    return CodexDirectCatalogNode(
        key=key,
        parent_key=parent_key,
        number=key.removeprefix("n") if number is None else number,
        title=title or f"Title {key}",
        level=level,
        source_locator=source_locator,
    )


def _catalog(*nodes: CodexDirectCatalogNode) -> CodexDirectCatalog:
    return CodexDirectCatalog(complete=True, nodes=list(nodes))


class FakeSourceCodexClient:
    def __init__(
        self,
        output_parsed: object,
        *,
        raw_output: str | None = None,
        source_sha256: str = "a" * 64,
        source_turn_count: int = 1,
    ) -> None:
        self.output_parsed = output_parsed
        self.raw_output = (
            output_parsed.model_dump_json()
            if raw_output is None and isinstance(output_parsed, CodexDirectCatalog)
            else raw_output
        )
        self.source_sha256 = source_sha256
        self.source_turn_count = source_turn_count
        self.calls: list[dict[str, object]] = []

    def parse_source_file(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=self.output_parsed,
            output_text=self.raw_output,
            usage={"input_tokens": 1, "output_tokens": 1},
            activity=[],
            source_sha256=self.source_sha256,
            source_turn_count=self.source_turn_count,
        )


def _record(path: Path, *, mime_type: str = "application/pdf") -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id="source_direct_catalog",
        owner_user_id="user_direct_catalog",
        package_id="course_direct_catalog",
        title="Direct catalog",
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="parsing",
    )


def _model(*, reasoning_effort: str | None = "low") -> AIModelSelection:
    return AIModelSelection(
        provider="openai_codex",
        model="catalog-test-model",
        reasoning_effort=reasoning_effort,
        service_tier="priority",
    )


def _generate(
    tmp_path: Path,
    catalog: CodexDirectCatalog,
    *,
    suffix: str = ".pdf",
    mime_type: str = "application/pdf",
    raw_output: str | None = None,
    source_turn_count: int = 1,
):
    path = tmp_path / f"source{suffix}"
    path.write_bytes(b"source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    client = FakeSourceCodexClient(
        catalog,
        raw_output=raw_output,
        source_sha256=content_hash,
        source_turn_count=source_turn_count,
    )
    result = generate_codex_direct_catalog(
        record=_record(path, mime_type=mime_type),
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    return result, client, path, content_hash


def test_source_codex_runs_once_and_materializes_unmapped_hierarchy(tmp_path: Path) -> None:
    result, client, path, content_hash = _generate(
        tmp_path,
        _catalog(
            _node("chapter-1", title="Chapter One", number="1", source_locator="nav:1"),
            _node(
                "section-1-1",
                title="First section",
                number="1.1",
                parent_key="chapter-1",
                level=2,
            ),
        ),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["source_path"] == path
    assert call["reasoning_effort"] == "low"
    assert call["output_artifact_path"] == "scratch/catalog.json"
    assert "source ranges" in str(call["system_prompt"])
    assert result.turn_count == 1
    assert [chapter.title for chapter in result.chapters] == [
        "Chapter One",
        "First section",
    ]
    assert result.chapters[1].parent_id == result.chapters[0].id
    assert all(chapter.range is None for chapter in result.chapters)
    assert all(chapter.mapping_status == "unmapped" for chapter in result.chapters)
    assert all(chapter.anchor_status == "unverified" for chapter in result.chapters)
    assert all(chapter.source_content_hash == content_hash for chapter in result.chapters)
    assert result.audit_metadata["host_directory_transform"] == (
        "mechanical_materialization_only"
    )
    assert result.audit_metadata["body_text_extracted_by_host"] is False


@pytest.mark.parametrize(
    "nodes, message",
    [
        (
            [_node("same"), _node("same")],
            "keys must be unique",
        ),
        (
            [_node("child", parent_key="missing", level=2)],
            "parent must appear before",
        ),
        (
            [_node("root"), _node("child", parent_key="root", level=3)],
            "exactly one deeper",
        ),
        (
            [
                _node("root"),
                _node("child", parent_key="root", level=2),
                _node("other"),
                _node("late", parent_key="root", level=2),
            ],
            "parent-consistent preorder",
        ),
        (
            [_node("root", title=" padded")],
            "leading or trailing whitespace",
        ),
    ],
)
def test_source_codex_rejects_invalid_directory_structure(
    tmp_path: Path,
    nodes: list[CodexDirectCatalogNode],
    message: str,
) -> None:
    with pytest.raises(SourceCodexCatalogError, match=message):
        _generate(tmp_path, _catalog(*nodes))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "",
                    "level": 1,
                    "source_locator": "",
                }
            ],
        },
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "Title",
                    "level": "1",
                    "source_locator": "",
                }
            ],
        },
        {
            "complete": True,
            "nodes": [
                {
                    "key": "n1",
                    "parent_key": None,
                    "number": "1",
                    "title": "Title",
                    "level": 1,
                    "source_locator": "",
                    "unexpected": True,
                }
            ],
        },
    ],
)
def test_source_codex_rejects_invalid_raw_schema(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    raw_output = json.dumps(payload)
    client = FakeSourceCodexClient(
        payload,
        raw_output=raw_output,
        source_sha256=content_hash,
    )

    with pytest.raises(SourceCodexCatalogError, match="invalid auditable"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: client,
        )


def test_source_codex_rejects_duplicate_raw_json_keys(tmp_path: Path) -> None:
    catalog = _catalog(_node("n1"))
    with pytest.raises(SourceCodexCatalogError, match="invalid auditable"):
        _generate(
            tmp_path,
            catalog,
            raw_output=(
                '{"complete":true,"complete":true,"nodes":'
                '[{"key":"n1","parent_key":null,"number":"1",'
                '"title":"Title n1","level":1,"source_locator":""}]}'
            ),
        )


def test_source_codex_rejects_fingerprint_change_and_extra_turns(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"source bytes")
    catalog = _catalog(_node("n1"))
    client = FakeSourceCodexClient(
        catalog,
        source_sha256="b" * 64,
        source_turn_count=1,
    )
    with pytest.raises(SourceCodexCatalogError, match="fingerprint"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            selection=_model(),
            client_factory=lambda _user_id: client,
        )

    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    extra_turn_client = FakeSourceCodexClient(
        catalog,
        source_sha256=content_hash,
        source_turn_count=2,
    )
    with pytest.raises(SourceCodexCatalogError, match="exactly one"):
        generate_codex_direct_catalog(
            record=_record(path),
            source_path=path,
            source_content_hash=content_hash,
            selection=_model(),
            client_factory=lambda _user_id: extra_turn_client,
        )


def test_source_codex_rejects_empty_complete_catalog(tmp_path: Path) -> None:
    with pytest.raises(SourceCodexCatalogError, match="empty directory"):
        _generate(tmp_path, _catalog())


def test_stored_source_codex_catalog_is_revalidated_before_rematerialization(
    tmp_path: Path,
) -> None:
    result, _client, path, content_hash = _generate(
        tmp_path,
        _catalog(_node("n1", title="Stored", source_locator="printed-page:1")),
    )

    restored = materialize_stored_codex_catalog(
        record=_record(path),
        payload=result.audit_metadata["codex_directory_payload"],
        source_content_hash=content_hash,
        expected_payload_sha256=result.audit_metadata["codex_directory_payload_sha256"],
    )

    assert restored.turn_count == 0
    assert [chapter.title for chapter in restored.chapters] == ["Stored"]
    assert restored.audit_metadata["catalog_authority"] == "source_codex_reused_audit"

    with pytest.raises(SourceCodexCatalogError, match="fingerprint"):
        materialize_stored_codex_catalog(
            record=_record(path),
            payload=result.audit_metadata["codex_directory_payload"],
            source_content_hash=content_hash,
            expected_payload_sha256="0" * 64,
        )


def test_source_codex_rejects_unsupported_or_mismatched_suffix(tmp_path: Path) -> None:
    path = tmp_path / "source.bin"
    path.write_bytes(b"source")
    with pytest.raises(SourceCodexCatalogError, match="not supported"):
        generate_codex_direct_catalog(
            record=_record(path, mime_type="application/octet-stream"),
            source_path=path,
            source_content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
            selection=_model(),
        )

    stored = tmp_path / "stored.epub"
    stored.write_bytes(b"source")
    mismatched = _record(stored, mime_type="application/pdf").model_copy(
        update={"file_name": "source.pdf"}
    )
    with pytest.raises(SourceCodexCatalogError, match="suffix does not match"):
        generate_codex_direct_catalog(
            record=mismatched,
            source_path=stored,
            source_content_hash=hashlib.sha256(stored.read_bytes()).hexdigest(),
            selection=_model(),
        )


def test_production_processor_publishes_unmapped_catalog_without_indexes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"direct source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(
        update={"metadata": {"content_hash": content_hash}}
    )
    client = FakeSourceCodexClient(
        _catalog(
            _node("n1", title="First"),
            _node("n1-1", title="Child", parent_key="n1", level=2),
        ),
        source_sha256=content_hash,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: direct_result,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "extract_directory",
        lambda *_args, **_kwargs: pytest.fail(
            "The production path must not run the host directory extractor"
        ),
    )
    database = tmp_path / "openclass.sqlite3"
    store = SourceStructureStore(database)

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    view = store.get_catalog_view(source=record)

    assert structure.status == "ready"
    assert structure.catalog_version == 1
    assert structure.has_verified_toc is False
    assert structure.chapter_count == 2
    assert structure.chunk_count == 0
    assert structure.visual_count == 0
    assert structure.metadata["source_chunks_created"] is False
    assert structure.metadata["vector_index_created"] is False
    assert structure.metadata["visual_index_created"] is False
    assert [chapter.title for chapter in view.chapters] == ["First", "Child"]
    assert all(chapter.mapping_status == "unmapped" for chapter in view.chapters)
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert runs[-1].turn_count == 1
    assert "validating_directory" in runs[-1].stage_history
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_visual_assets").fetchone()[0] == 0


def test_production_pdf_processor_uses_a_second_turn_for_verified_page_ranges(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"direct source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _catalog(
            _node("chapter-1", title="First", source_locator="printed-page:22"),
            _node("chapter-2", title="Second", source_locator="printed-page:164"),
        ),
        source_sha256=content_hash,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    anchors = (
        CodexPdfPrintedPageAnchor(printed_page=1, pdf_page=17),
        CodexPdfPrintedPageAnchor(printed_page=100, pdf_page=116),
        CodexPdfPrintedPageAnchor(printed_page=514, pdf_page=530),
    )
    calibration = PdfPageCalibrationResult(
        printed_page_start=1,
        printed_page_end=514,
        pdf_page_start=17,
        pdf_page_end=530,
        page_offset=16,
        page_count=540,
        anchors=anchors,
        turn_count=1,
        raw_output="{}",
        raw_output_sha256="b" * 64,
        audit_metadata={"pdf_page_calibration_status": "verified"},
    )
    calibration_calls: list[dict[str, object]] = []

    def fake_pdf_page_calibration(**kwargs):
        calibration_calls.append(kwargs)
        return calibration

    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: direct_result,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_pdf_page_calibration",
        fake_pdf_page_calibration,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    view = store.get_catalog_view(source=record)
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert structure.has_verified_toc is True
    assert structure.metadata["pdf_page_calibration_status"] == "verified"
    assert [(chapter.range.start, chapter.range.end) for chapter in view.chapters] == [
        (38, 179),
        (180, 530),
    ]
    assert runs[-1].turn_count == 2
    assert calibration_calls[0]["required_printed_page_min"] == 22
    assert calibration_calls[0]["required_printed_page_max"] == 164
    assert "calibrating_pdf_pages" in runs[-1].stage_history
    assert "validating_directory_ranges" in runs[-1].stage_history


def test_pdf_mapping_retry_reuses_failed_complete_directory_without_first_turn(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"direct source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(update={"metadata": {"content_hash": content_hash}})
    client = FakeSourceCodexClient(
        _catalog(_node("chapter-1", title="First", source_locator="printed-page:1")),
        source_sha256=content_hash,
    )
    direct_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    failed_run = SourceCatalogRun(
        id="catalogrun_reusable",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        status="failed",
        error="PDF page calibration failed",
        metadata={
            **direct_result.audit_metadata,
            "source_content_hash": content_hash,
        },
    )
    store.save_catalog_run(failed_run)
    anchors = (
        CodexPdfPrintedPageAnchor(printed_page=1, pdf_page=17),
        CodexPdfPrintedPageAnchor(printed_page=100, pdf_page=116),
        CodexPdfPrintedPageAnchor(printed_page=514, pdf_page=530),
    )
    calibration = PdfPageCalibrationResult(
        printed_page_start=1,
        printed_page_end=514,
        pdf_page_start=17,
        pdf_page_end=530,
        page_offset=16,
        page_count=540,
        anchors=anchors,
        turn_count=1,
        raw_output="{}",
        raw_output_sha256="d" * 64,
        audit_metadata={"pdf_page_calibration_status": "verified"},
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: pytest.fail("A mapping retry must reuse the failed complete directory"),
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_pdf_page_calibration",
        lambda **_kwargs: calibration,
    )

    published = SourceDirectoryProcessor(store=store).process(
        record=record,
        path=path,
        catalog_model=_model(),
    )
    runs = store.list_catalog_runs(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert published.has_verified_toc is True
    assert runs[0].turn_count == 1
    assert runs[0].metadata["directory_reused_from_catalog_run"] == failed_run.id
    assert "reusing_directory_catalog" in runs[0].stage_history


def test_failed_rebuild_preserves_previous_catalog(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"direct source bytes")
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    record = _record(path).model_copy(
        update={"metadata": {"content_hash": content_hash}}
    )
    client = FakeSourceCodexClient(
        _catalog(_node("n1", title="Published")),
        source_sha256=content_hash,
    )
    successful_result = generate_codex_direct_catalog(
        record=record,
        source_path=path,
        source_content_hash=content_hash,
        selection=_model(),
        client_factory=lambda _user_id: client,
    )
    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        lambda **_kwargs: successful_result,
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceDirectoryProcessor(store=store)
    first = processor.process(record=record, path=path, catalog_model=_model())

    def fail_catalog(**_kwargs):
        raise SourceCodexCatalogError("single-turn catalog failed")

    monkeypatch.setattr(
        directory_processor_module,
        "generate_codex_direct_catalog",
        fail_catalog,
    )
    with pytest.raises(SourceDirectoryProcessingError, match="single-turn"):
        processor.process(record=record, path=path, catalog_model=_model())

    after = store.get_catalog_view(source=record)
    after_structure = store.get_structure(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert after_structure is not None
    assert after_structure.catalog_version == first.catalog_version
    assert [chapter.title for chapter in after.chapters] == ["Published"]


def test_successful_directory_only_rebuild_preserves_exact_verified_ranges() -> None:
    source_hash = "c" * 64
    previous = SourceChapter(
        id="stable-chapter",
        owner_user_id="user_direct_catalog",
        package_id="course_direct_catalog",
        source_ingestion_id="source_direct_catalog",
        title="Stable chapter",
        source_locator="printed-page:22",
        anchor_status="verified",
        range=SourceRange(kind="pdf_pages", start=38, end=179),
        mapping_status="verified",
        source_content_hash=source_hash,
        catalog_version=4,
        confidence=0.98,
    )
    current = previous.model_copy(
        update={
            "anchor_status": "unverified",
            "range": None,
            "mapping_status": "unmapped",
            "catalog_version": 0,
            "confidence": 0.0,
        }
    )

    preserved, count = directory_processor_module._preserve_verified_ranges(
        [current],
        previous_chapters=[previous],
        source_content_hash=source_hash,
    )

    assert count == 1
    assert preserved[0].mapping_status == "verified"
    assert preserved[0].range == previous.range
    assert preserved[0].metadata["range_preserved_from_catalog_version"] == 4
