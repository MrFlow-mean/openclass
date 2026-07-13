from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.models import SourceIngestionRecord
from app.services import source_structure_indexer as indexer_module
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


def _record(path: Path, *, mime_type: str) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        owner_user_id="user_formats",
        package_id="package_formats",
        title=path.name,
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        status="ready",
        metadata={"local_source_path": str(path), "adapter": "openclass_native"},
    )


def test_native_pptx_parser_indexes_slide_text(tmp_path: Path) -> None:
    path = tmp_path / "slides.pptx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Overview</a:t><a:t>First slide evidence</a:t></p:sld>',
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Details</a:t><a:t>Second slide evidence</a:t></p:sld>',
        )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert [chapter.title for chapter in view.chapters] == ["Overview", "Details"]
    assert "Second slide evidence" in "\n".join(chunk.text for chunk in view.chunks)


def test_native_xlsx_parser_indexes_sheet_cells(tmp_path: Path) -> None:
    path = tmp_path / "table.xlsx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="x"><si><t>Metric</t></si><si><t>Value</t></si><si><t>Retention</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="x"><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
            '<row><c t="s"><v>2</v></c><c><v>0.82</v></c></row></sheetData></worksheet>',
        )
    record = _record(path, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert view.chapters[0].title == "Sheet 1"
    assert "Retention\t0.82" in "\n".join(chunk.text for chunk in view.chunks)


def test_native_html_and_image_parsers_preserve_searchable_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "page.html"
    html_path.write_text("<h1>Heading</h1><p>HTML evidence body.</p>", encoding="utf-8")
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"image-placeholder")
    monkeypatch.setattr(indexer_module, "extract_image_text", lambda _path: "OCR evidence body")
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    indexer = SourceStructureIndexer(store=store)

    html_record = _record(html_path, mime_type="text/html")
    image_record = _record(image_path, mime_type="image/png")
    assert indexer.rebuild_structure(html_record).status == "ready"
    assert indexer.rebuild_structure(image_record).status == "linear_only"
    assert "HTML evidence body" in store.get_structure_view(source=html_record).chunks[0].text
    assert "OCR evidence body" in store.get_structure_view(source=image_record).chunks[0].text
