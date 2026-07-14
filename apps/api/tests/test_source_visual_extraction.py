from __future__ import annotations

import base64
import sqlite3
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from PIL import Image, ImageDraw, ImageOps
from docx import Document
from docx.shared import Inches
from fastapi.testclient import TestClient
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import app.main as main_module
from app.models import SourceIngestionRecord, SourceStructure, UserView
from app.routers import auth as auth_router
from app.routers import sources as sources_router
from app.services import source_archive as source_archive_module
from app.services import source_structure_indexer as indexer_module
from app.services import source_visual_extraction as extraction_module
from app.services import source_visual_extraction_budget as extraction_budget_module
from app.services import source_visual_extraction_markup as markup_module
from app.services import source_visual_extraction_pdf as pdf_extraction_module
from app.services import workspace_state
from app.services.board_document_editor import _visual_manifest_payload
from app.services.board_visual_insertion import build_board_insertion_plan
from app.services.course_store import SqliteCourseStore
from app.services.source_archive import SafeSourceArchive, SourceArchiveError
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore
from app.services.source_visual_extraction import SourceVisualExtractor
from app.services.source_visual_extraction_office import extract_office_visuals
from app.services.source_visual_extraction_types import RawSourceVisual, SourceVisualAdapterResult
from app.services.source_visual_libreoffice import LibreOfficeRenderError, LibreOfficeRenderer
from app.services.source_visual_storage import source_visual_asset_root


@pytest.fixture(autouse=True)
def _fast_visual_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexer_module, "extract_image_text", lambda _path: "")
    monkeypatch.setattr(extraction_module, "extract_image_text", lambda _path: "visual labels")


def _record(path: Path, *, mime_type: str, source_id: str | None = None) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=source_id or f"source_{path.stem}",
        owner_user_id="user_visual",
        package_id="package_visual",
        title=path.name,
        source_type="local_file",
        file_name=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(path)},
    )


def _png_bytes(color: tuple[int, int, int] = (20, 90, 160)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (96, 64), color).save(output, format="PNG")
    return output.getvalue()


def _png_data_uri(color: tuple[int, int, int] = (20, 90, 160)) -> str:
    encoded = base64.b64encode(_png_bytes(color)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _write_docx_floating_fixture(
    path: Path,
    image_path: Path,
    *,
    include_rendered_page_break: bool,
    horizontal_reference: str = "page",
    as_chart: bool = False,
    include_second_inline: bool = False,
) -> None:
    document = Document()
    document.sections[0].page_width = Inches(10)
    document.sections[0].page_height = Inches(10)
    document.add_paragraph("Rendered first page.")
    drawing_paragraph = document.add_paragraph()
    run = drawing_paragraph.add_run()
    run.add_picture(str(image_path), width=Inches(3), height=Inches(4))
    if include_second_inline:
        run.add_picture(str(image_path), width=Inches(1), height=Inches(1))
    document.save(path)

    with ZipFile(path) as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    document_xml = next(content for item, content in entries if item.filename == "word/document.xml")
    root = ElementTree.fromstring(document_xml)
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    chart_namespace = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    relationship_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    inline = next(node for node in root.iter() if node.tag == f"{{{drawing_namespace}}}inline")
    inline.tag = f"{{{drawing_namespace}}}anchor"
    inline.attrib["simplePos"] = "0"
    extent = next(node for node in list(inline) if node.tag == f"{{{drawing_namespace}}}extent")
    extent.attrib.update({"cx": "2743200", "cy": "3657600"})
    simple_position = ElementTree.Element(
        f"{{{drawing_namespace}}}simplePos",
        {"x": "0", "y": "0"},
    )
    horizontal = ElementTree.Element(
        f"{{{drawing_namespace}}}positionH",
        {"relativeFrom": horizontal_reference},
    )
    ElementTree.SubElement(horizontal, f"{{{drawing_namespace}}}posOffset").text = "914400"
    vertical = ElementTree.Element(
        f"{{{drawing_namespace}}}positionV",
        {"relativeFrom": "page"},
    )
    ElementTree.SubElement(vertical, f"{{{drawing_namespace}}}posOffset").text = "1828800"
    inline.insert(0, simple_position)
    inline.insert(1, horizontal)
    inline.insert(2, vertical)
    if include_rendered_page_break:
        first_run = next(node for node in root.iter() if node.tag == f"{{{word_namespace}}}r")
        first_run.append(ElementTree.Element(f"{{{word_namespace}}}lastRenderedPageBreak"))
    if as_chart:
        blip = next(node for node in inline.iter() if node.tag.endswith("}blip"))
        blip.tag = f"{{{chart_namespace}}}chart"
        blip.attrib.clear()
        blip.attrib[f"{{{relationship_namespace}}}id"] = "rIdChart"
    rewritten = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    temporary = path.with_suffix(".rewritten.docx")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        for item, content in entries:
            archive.writestr(item, rewritten if item.filename == "word/document.xml" else content)
    temporary.replace(path)


def _write_grouped_pptx_fixture(path: Path, *, valid_group_transform: bool = True) -> None:
    child_extent_x = "1000" if valid_group_transform else "0"
    slide = (
        '<p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r" xmlns:c="c" xmlns:dgm="dgm">'
        '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Grouped visual</a:t>'
        '</a:r></a:p></p:txBody></p:sp>'
        '<p:grpSp><p:nvGrpSpPr/><p:grpSpPr><a:xfrm><a:off x="1000" y="2000"/>'
        '<a:ext cx="4000" cy="3000"/><a:chOff x="100" y="200"/>'
        '<a:chExt cx="1000" cy="1000"/></a:xfrm></p:grpSpPr>'
        '<p:grpSp><p:nvGrpSpPr/><p:grpSpPr><a:xfrm><a:off x="350" y="400"/>'
        '<a:ext cx="500" cy="400"/><a:chOff x="0" y="0"/>'
        f'<a:chExt cx="{child_extent_x}" cy="1000"/></a:xfrm></p:grpSpPr>'
        '<p:pic><p:nvPicPr><p:cNvPr id="2" name="Grouped image"/></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="rId1"/></p:blipFill><p:spPr><a:xfrm>'
        '<a:off x="200" y="250"/><a:ext cx="400" cy="500"/></a:xfrm></p:spPr></p:pic>'
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="3" name="SmartArt"/>'
        '</p:nvGraphicFramePr><p:xfrm><a:off x="600" y="100"/><a:ext cx="200" cy="200"/>'
        '</p:xfrm><a:graphic><a:graphicData><dgm:relIds r:dm="rId2"/>'
        '</a:graphicData></a:graphic></p:graphicFrame>'
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Chart"/>'
        '</p:nvGraphicFramePr><p:xfrm><a:off x="600" y="400"/><a:ext cx="200" cy="200"/>'
        '</p:xfrm><a:graphic><a:graphicData><c:chart r:id="rId3"/>'
        '</a:graphicData></a:graphic></p:graphicFrame>'
        '</p:grpSp></p:grpSp></p:spTree></p:cSld></p:sld>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="10000" cy="10000"/></p:presentation>',
        )
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/></Relationships>',
        )
        archive.writestr("ppt/media/image1.png", _png_bytes())


def _write_pptx_table_fixture(path: Path, *, cell_attributes: str = "") -> None:
    slide = (
        '<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree>'
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="2" name="Table"/>'
        '</p:nvGraphicFramePr><p:xfrm><a:off x="1000" y="1000"/>'
        '<a:ext cx="6000" cy="3000"/></p:xfrm><a:graphic><a:graphicData>'
        '<a:tbl><a:tr><a:tc'
        f'{cell_attributes}><a:txBody><a:p><a:r><a:t>Metric</a:t></a:r></a:p>'
        '</a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>Value</a:t>'
        '</a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>'
        '</a:graphicData></a:graphic></p:graphicFrame>'
        '</p:spTree></p:cSld></p:sld>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="10000" cy="10000"/>'
            '</p:presentation>',
        )
        archive.writestr("ppt/slides/slide1.xml", slide)


def _write_xlsx_chart_fixture(path: Path, *, fit_to_single_page: bool) -> None:
    setup = (
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        if fit_to_single_page
        else ""
    )
    page_setup = '<pageSetup fitToWidth="1" fitToHeight="1"/>' if fit_to_single_page else ""
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="x" xmlns:r="r">'
            f'{setup}<sheetData><row r="1"><c r="A1"><v>1</v></c></row>'
            '<row r="10"><c r="D10"><v>2</v></c></row></sheetData>'
            f'<drawing r:id="rId1"/>{page_setup}</worksheet>',
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../drawings/drawing1.xml" Type="drawing"/></Relationships>',
        )
        archive.writestr(
            "xl/drawings/drawing1.xml",
            '<xdr:wsDr xmlns:xdr="xdr" xmlns:a="a" xmlns:c="c" xmlns:r="r">'
            '<xdr:twoCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:row>0</xdr:row></xdr:from>'
            '<xdr:to><xdr:col>1</xdr:col><xdr:row>1</xdr:row></xdr:to>'
            '<xdr:graphicFrame><a:graphic><a:graphicData><c:chart r:id="rId2"/>'
            '</a:graphicData></a:graphic></xdr:graphicFrame></xdr:twoCellAnchor></xdr:wsDr>',
        )


def _rewrite_zip_member(
    path: Path,
    member_name: str,
    rewrite: Callable[[bytes], bytes],
) -> None:
    with ZipFile(path) as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    temporary = path.with_name(f"{path.stem}.rewritten{path.suffix}")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        for item, content in entries:
            if item.filename == member_name:
                content = rewrite(content)
            archive.writestr(item, content)
    temporary.replace(path)


def _write_docx_cropped_image_fixture(path: Path, image_path: Path) -> None:
    document = Document()
    document.add_heading("Visual section", level=1)
    document.add_paragraph("Before cropped picture.")
    document.add_picture(str(image_path), width=Inches(2))
    document.add_paragraph("After cropped picture.")
    document.save(path)

    def add_crop(content: bytes) -> bytes:
        marker = b"<a:stretch>"
        assert marker in content
        return content.replace(marker, b'<a:srcRect l="12000"/>' + marker, 1)

    _rewrite_zip_member(path, "word/document.xml", add_crop)


def _write_pptx_transformed_image_fixture(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="1000" cy="800"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r"><p:cSld><p:spTree>'
            '<p:sp><p:txBody><a:p><a:r><a:t>Transformed visual</a:t></a:r></a:p>'
            '</p:txBody></p:sp><p:pic><p:nvPicPr><p:cNvPr id="2" name="Visual"/>'
            '</p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/>'
            '</a:stretch></p:blipFill><p:spPr><a:xfrm rot="60000" flipH="1">'
            '<a:off x="100" y="100"/><a:ext cx="600" cy="400"/></a:xfrm>'
            '<a:prstGeom prst="ellipse"/><a:effectLst><a:blur rad="1000"/></a:effectLst>'
            '</p:spPr></p:pic></p:spTree></p:cSld></p:sld>',
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/>'
            '</Relationships>',
        )
        archive.writestr("ppt/media/image1.png", _png_bytes())


def _write_xlsx_transformed_image_fixture(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="x" xmlns:r="r"><sheetData><row r="1"><c r="A1" '
            't="inlineStr"><is><t>Transformed visual</t></is></c></row></sheetData>'
            '<drawing r:id="rId1"/></worksheet>',
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../drawings/drawing1.xml" Type="drawing"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/drawings/drawing1.xml",
            '<xdr:wsDr xmlns:xdr="xdr" xmlns:a="a" xmlns:r="r"><xdr:twoCellAnchor>'
            '<xdr:from><xdr:col>0</xdr:col><xdr:row>0</xdr:row></xdr:from>'
            '<xdr:to><xdr:col>2</xdr:col><xdr:row>2</xdr:row></xdr:to>'
            '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="Visual"/></xdr:nvPicPr>'
            '<xdr:blipFill><a:blip r:embed="rId1"><a:grayscl/></a:blip>'
            '<a:srcRect t="5000"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            '<xdr:spPr><a:xfrm flipV="1"><a:off x="0" y="0"/><a:ext cx="2" cy="2"/>'
            '</a:xfrm><a:prstGeom prst="rect"/></xdr:spPr></xdr:pic>'
            '</xdr:twoCellAnchor></xdr:wsDr>',
        )
        archive.writestr(
            "xl/drawings/_rels/drawing1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/>'
            '</Relationships>',
        )
        archive.writestr("xl/media/image1.png", _png_bytes())


def _write_docx_inline_chart_fixture(path: Path, image_path: Path) -> None:
    document = Document()
    document.add_heading("Chart section", level=1)
    document.add_paragraph("Before chart.")
    document.add_picture(str(image_path), width=Inches(2))
    document.add_paragraph("After chart.")
    document.save(path)

    chart_namespace = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    relationship_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    def replace_blip(content: bytes) -> bytes:
        root = ElementTree.fromstring(content)
        blip = next(node for node in root.iter() if node.tag.endswith("}blip"))
        blip.tag = f"{{{chart_namespace}}}chart"
        blip.attrib.clear()
        blip.set(f"{{{relationship_namespace}}}id", "rIdInlineChart")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_zip_member(path, "word/document.xml", replace_blip)


def _patterned_cross_page_image(width: int = 240, height: int = 320) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = []
    for y in range(height):
        for x in range(width):
            pixels.append(
                (
                    35 + int(80 * x / max(1, width - 1)) + int(18 * y / max(1, height - 1)),
                    65 + int(105 * x / max(1, width - 1)),
                    175 - int(70 * x / max(1, width - 1)) + int(12 * y / max(1, height - 1)),
                )
            )
    image.putdata(pixels)
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((15, 15, min(width - 15, 55), 55), fill=(230, 35, 35))
    drawing.rectangle(
        (max(15, width - 55), height - 55, width - 15, height - 15),
        fill=(25, 55, 230),
    )
    for x in range(12, width, 36):
        drawing.line((x, 70, min(width - 1, x + 28), height - 70), fill=(25, 25, 25), width=3)
    return image


def _image_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_cross_page_pdf(
    path: Path,
    *,
    unrelated_second_half: bool = False,
    ambiguous_bottom: bool = False,
    similar_grid_different_content: bool = False,
) -> None:
    image_width = 120 if ambiguous_bottom else 240
    source = _patterned_cross_page_image(width=image_width)
    first_half = source.crop((0, 0, image_width, 160))
    second_half = source.crop((0, 160, image_width, 320))
    if unrelated_second_half:
        second_half = ImageOps.invert(second_half)
    if similar_grid_different_content:
        alternate_source = source.copy()
        alternate_drawing = ImageDraw.Draw(alternate_source)
        alternate_drawing.ellipse(
            (20, 210, min(image_width - 20, 110), 300),
            fill=(245, 200, 25),
        )
        second_half = alternate_source.crop((0, 160, image_width, 320))

    pdf = canvas.Canvas(str(path), pagesize=(300, 400))
    first_x = 20 if ambiguous_bottom else 30
    shares_native_object = not (
        unrelated_second_half or ambiguous_bottom or similar_grid_different_content
    )
    shared_reader = ImageReader(BytesIO(_image_bytes(source))) if shares_native_object else None
    pdf.drawString(30, 200, "Context before the visual")
    if shared_reader is not None:
        pdf.drawImage(shared_reader, first_x, -160, width=image_width, height=320)
    else:
        pdf.drawImage(
            ImageReader(BytesIO(_image_bytes(first_half))),
            first_x,
            0,
            width=image_width,
            height=160,
        )
    if ambiguous_bottom:
        pdf.drawImage(
            ImageReader(BytesIO(_png_bytes((180, 120, 40)))),
            160,
            0,
            width=120,
            height=160,
        )
    pdf.showPage()
    if shared_reader is not None:
        pdf.drawImage(shared_reader, first_x, 240, width=image_width, height=320)
    else:
        pdf.drawImage(
            ImageReader(BytesIO(_image_bytes(second_half))),
            first_x,
            240,
            width=image_width,
            height=160,
        )
    pdf.drawString(30, 210, "Cross-page visual caption")
    pdf.save()


@pytest.fixture
def source_api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, UserView]:
    store = SqliteCourseStore(tmp_path / "api.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "api-uploads")
    monkeypatch.setattr(workspace_state, "EXPORT_DIR", tmp_path / "api-exports")
    workspace_state.ensure_data_dirs()
    user = UserView(
        id="user_visual_api",
        email="visual-api@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: user
    try:
        yield TestClient(main_module.app), user
    finally:
        main_module.app.dependency_overrides.clear()


def test_standalone_image_visual_is_stable_persistent_and_ocr_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    observed_paths: list[Path] = []

    def fake_ocr(path: Path) -> str:
        assert path.is_file()
        observed_paths.append(path)
        return "axis and legend"

    monkeypatch.setattr(extraction_module, "extract_image_text", fake_ocr)
    image_path = tmp_path / "visual.png"
    content = _png_bytes()
    image_path.write_bytes(content)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(image_path, mime_type="image/png")
    indexer = SourceStructureIndexer(store=store)

    first = indexer.rebuild_structure(record)
    first_view = store.get_structure_view(source=record)
    second = indexer.rebuild_structure(record)
    second_view = store.get_structure_view(source=record)

    assert first.visual_index_status == "ready"
    assert first.visual_index_version == 1
    assert first.visual_count == 1
    assert second.visual_count == 1
    assert first_view.visuals[0].id == second_view.visuals[0].id
    assert first_view.visuals[0].anchor_status == "verified"
    assert first_view.visuals[0].metadata["standalone_image"] is True
    assert first_view.visuals[0].ocr_text == "axis and legend"
    assert first_view.visuals[0].width == 96
    assert first_view.visuals[0].height == 64
    loaded = store.read_visual_bytes(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        visual_id=first_view.visuals[0].id,
    )
    assert loaded is not None
    assert loaded[1] == content
    assert observed_paths and all(not path.exists() for path in observed_paths)


@pytest.mark.parametrize(
    ("suffix", "image_format", "mime_type"),
    [
        (".jpg", "JPEG", "image/jpeg"),
        (".webp", "WEBP", "image/webp"),
        (".gif", "GIF", "image/gif"),
    ],
)
def test_supported_standalone_raster_formats_are_indexed_as_whole_visuals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    image_format: str,
    mime_type: str,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / f"whole-visual{suffix}"
    Image.new("RGB", (80, 48), (30, 100, 170)).save(path, format=image_format)
    store = SourceStructureStore(tmp_path / f"{image_format.lower()}.sqlite3")
    record = _record(path, mime_type=mime_type)

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.visual_index_status == "ready"
    assert structure.visual_count == 1
    assert view.visuals[0].mime_type == mime_type
    assert view.visuals[0].bbox == [0.0, 0.0, 1.0, 1.0]
    assert view.visuals[0].anchor_status == "verified"
    assert view.visuals[0].metadata["standalone_image"] is True


def test_markdown_image_and_table_follow_source_order_and_keep_editable_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    plot_uri = _png_data_uri()
    path = tmp_path / "reference.md"
    path.write_text(
        "# Section\n\nBefore the table.\n\n"
        "| Label | Value |\n| --- | --- |\n| A | 1 |\n\n"
        f"Between the table and image.\n\n![Original plot]({plot_uri})\n\nAfter the image.",
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "markdown.sqlite3")
    record = _record(path, mime_type="text/markdown")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert structure.visual_index_status == "ready"
    assert [visual.kind for visual in visuals] == ["table", "image"]
    assert visuals[0].table_data == [["Label", "Value"], ["A", "1"]]
    assert visuals[0].anchor_status == "verified"
    assert visuals[1].caption == "Original plot"
    assert visuals[1].metadata["image_source"] == "data:image"
    assert visuals[1].anchor_status == "verified"


def test_csv_is_indexed_as_one_editable_table(tmp_path: Path) -> None:
    path = tmp_path / "records.csv"
    path.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    store = SourceStructureStore(tmp_path / "csv.sqlite3")
    record = _record(path, mime_type="text/csv")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert structure.visual_index_status == "ready"
    assert len(visuals) == 1
    assert visuals[0].kind == "table"
    assert visuals[0].table_data == [["name", "value"], ["alpha", "1"], ["beta", "2"]]
    assert visuals[0].anchor_status == "verified"


@pytest.mark.parametrize(
    ("suffix", "mime_type", "content"),
    [
        (".txt", "text/plain", "Plain source text."),
        (".json", "application/json", '{"items": [1, 2]}'),
        (".xml", "application/xml", "<root><item>value</item></root>"),
    ],
)
def test_text_only_static_formats_do_not_invent_visual_objects(
    tmp_path: Path,
    suffix: str,
    mime_type: str,
    content: str,
) -> None:
    path = tmp_path / f"text-only{suffix}"
    path.write_text(content, encoding="utf-8")
    store = SourceStructureStore(tmp_path / f"{suffix[1:]}.sqlite3")
    record = _record(path, mime_type=mime_type)

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.visual_index_status == "ready"
    assert structure.visual_count == 0
    assert view.visuals == []


def test_docx_visuals_preserve_inline_image_and_editable_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "embedded.png"
    image_path.write_bytes(_png_bytes())
    document_path = tmp_path / "document.docx"
    document = Document()
    document.add_heading("Visual section", level=1)
    document.add_paragraph("Text before the visual.")
    document.add_picture(str(image_path), width=Inches(2))
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "10"
    document.add_paragraph("Text after the visual.")
    document.save(document_path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        document_path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.visual_index_status == "ready"
    assert {visual.kind for visual in view.visuals} == {"image", "table"}
    assert all(visual.anchor_status == "verified" for visual in view.visuals)
    table_visual = next(visual for visual in view.visuals if visual.kind == "table")
    assert table_visual.table_data == [["Metric", "Value"], ["A", "10"]]
    image_visual = next(visual for visual in view.visuals if visual.kind == "image")
    assert image_visual.storage_key.startswith("blobs/")
    assert image_visual.before_chunk_id


def test_docx_merged_cells_are_partial_unverified_and_excluded_from_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    document_path = tmp_path / "merged-table.docx"
    document = Document()
    document.add_heading("Merged table section", level=1)
    document.add_paragraph("Context before the table.")
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "Wide metric"
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 2).text = "Value"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "10"
    table.cell(1, 2).text = "Vertical value"
    table.cell(1, 2).merge(table.cell(2, 2))
    table.cell(2, 0).text = "B"
    table.cell(2, 1).text = "20"
    document.add_paragraph("Context after the table.")
    document.save(document_path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        document_path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    table_visual = store.get_structure_view(source=record).visuals[0]
    verified_visuals = store.list_visuals(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        verified_only=True,
    )

    assert structure.visual_index_status == "partial"
    assert table_visual.anchor_status == "unverified"
    assert table_visual.metadata["force_unverified"] is True
    assert table_visual.metadata["table_merge_semantics"] == "unrepresented"
    assert table_visual.metadata["table_merge_markers"] == ["gridSpan", "vMerge"]
    assert verified_visuals == []


@pytest.mark.parametrize(
    ("marker", "cell_attributes"),
    [
        ("gridSpan", ' gridSpan="2"'),
        ("rowSpan", ' rowSpan="2"'),
        ("hMerge", ' hMerge="1"'),
        ("vMerge", ' vMerge="true"'),
    ],
)
def test_pptx_merged_table_cells_are_partial_and_unverified(
    tmp_path: Path,
    marker: str,
    cell_attributes: str,
) -> None:
    path = tmp_path / f"merged-{marker}.pptx"
    _write_pptx_table_fixture(path, cell_attributes=cell_attributes)

    result = extract_office_visuals(path)

    assert result.status == "partial"
    assert len(result.visuals) == 1
    assert result.visuals[0].metadata["force_unverified"] is True
    assert result.visuals[0].metadata["table_merge_semantics"] == "unrepresented"
    assert result.visuals[0].metadata["table_merge_markers"] == [marker]


def test_pptx_default_single_cell_spans_remain_ready_and_editable(tmp_path: Path) -> None:
    path = tmp_path / "plain-table.pptx"
    _write_pptx_table_fixture(
        path,
        cell_attributes=' gridSpan="1" rowSpan="1" hMerge="0" vMerge="false"',
    )

    result = extract_office_visuals(path)

    assert result.status == "ready"
    assert result.visuals[0].table_data == [["Metric", "Value"]]
    assert result.visuals[0].metadata["force_unverified"] is False
    assert "table_merge_semantics" not in result.visuals[0].metadata


def test_docx_table_cell_images_follow_cell_paragraph_order_and_table_has_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "cell-image.png"
    image_path.write_bytes(_png_bytes())
    document_path = tmp_path / "table-cell-image.docx"
    document = Document()
    document.add_heading("Visual section", level=1)
    document.add_paragraph("Before the table. " + "preceding context " * 130)
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(0, 0).add_paragraph("Cell visual").add_run().add_picture(
        str(image_path),
        width=Inches(1),
    )
    table.cell(0, 1).add_paragraph("Later visual").add_run().add_picture(
        str(image_path),
        width=Inches(1),
    )
    document.add_paragraph("After the table. " + "following context " * 130)
    document.save(document_path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        document_path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert structure.visual_index_status == "ready"
    assert [visual.kind for visual in visuals] == ["table", "image", "image"]
    table_visual, first_image, second_image = visuals
    assert table_visual.table_data == [["Metric Cell visual", "Value Later visual"]]
    assert table_visual.caption == ""
    assert table_visual.ocr_text == "Metric Cell visual | Value Later visual"
    assert len(table_visual.ocr_text) <= 1200
    assert table_visual.before_chunk_id
    assert table_visual.after_chunk_id
    assert first_image.source_locator.endswith(
        "row:0:cell:0:paragraph:1:drawing:0:image:0"
    )
    assert second_image.source_locator.endswith(
        "row:0:cell:1:paragraph:1:drawing:0:image:0"
    )
    assert first_image.metadata["table_row_index"] == 0
    assert first_image.metadata["table_cell_index"] == 0
    assert second_image.metadata["table_cell_index"] == 1
    assert all(visual.anchor_status == "verified" for visual in visuals)
    plan = build_board_insertion_plan(visuals, nonce="table-position")
    table_manifest = next(
        item for item in _visual_manifest_payload(plan, visuals) if item["kind"] == "table"
    )
    assert table_manifest["ocr_text"] == "Metric Cell visual | Value Later visual"
    assert table_manifest["source_before_chunk_id"] == table_visual.before_chunk_id
    assert table_manifest["source_after_chunk_id"] == table_visual.after_chunk_id


def test_docx_cropped_image_is_partial_unverified_and_excluded_from_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "cropped.png"
    image_path.write_bytes(_png_bytes())
    document_path = tmp_path / "cropped.docx"
    _write_docx_cropped_image_fixture(document_path, image_path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        document_path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert structure.visual_index_status == "partial"
    assert visual.anchor_status == "unverified"
    assert visual.metadata["office_image_display_fidelity"] == "unverified"
    assert visual.metadata["office_image_display_transform_reasons"] == ["crop"]
    assert store.list_visuals(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        verified_only=True,
    ) == []


def test_docx_inline_native_chart_is_explicitly_unverified_without_rendered_bbox(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "chart-placeholder.png"
    image_path.write_bytes(_png_bytes())
    document_path = tmp_path / "inline-chart.docx"
    _write_docx_inline_chart_fixture(document_path, image_path)

    result = extract_office_visuals(document_path)

    assert result.status == "partial"
    assert result.visuals == []
    assert len(result.native_chart_anchors) == 1
    anchor = result.native_chart_anchors[0]
    assert anchor.kind == "chart"
    assert anchor.bbox == []
    assert anchor.metadata["rendered_bbox_reliable"] is False
    assert anchor.metadata["rendered_mapping_reason"] == "inline_or_unresolved_page_bbox"
    assert anchor.metadata["force_unverified"] is True


def test_docx_floating_drawing_uses_rendered_page_and_page_relative_bbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "floating.png"
    image_path.write_bytes(_png_bytes())
    path = tmp_path / "floating.docx"
    _write_docx_floating_fixture(
        path,
        image_path,
        include_rendered_page_break=True,
    )
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert visual.page_no == 2
    assert visual.bbox == [0.1, 0.2, 0.4, 0.6]
    assert visual.anchor_status == "verified"
    assert visual.metadata["floating_drawing"] is True
    assert visual.metadata["page_position_reliable"] is True


@pytest.mark.parametrize(
    ("include_rendered_page_break", "horizontal_reference"),
    [(False, "page"), (True, "column")],
)
def test_docx_floating_drawing_without_deterministic_page_position_is_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_rendered_page_break: bool,
    horizontal_reference: str,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "floating.png"
    image_path.write_bytes(_png_bytes())
    path = tmp_path / "floating-uncertain.docx"
    _write_docx_floating_fixture(
        path,
        image_path,
        include_rendered_page_break=include_rendered_page_break,
        horizontal_reference=horizontal_reference,
    )
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert visual.bbox == []
    assert visual.anchor_status == "unverified"
    assert visual.metadata["force_unverified"] is True


def test_docx_drawing_extent_is_scoped_per_anchor_or_inline_object(tmp_path: Path) -> None:
    image_path = tmp_path / "drawing.png"
    image_path.write_bytes(_png_bytes())
    path = tmp_path / "drawing-order.docx"
    _write_docx_floating_fixture(
        path,
        image_path,
        include_rendered_page_break=True,
        include_second_inline=True,
    )

    result = extract_office_visuals(path)

    assert len(result.visuals) == 2
    assert result.visuals[0].bbox == [0.1, 0.2, 0.4, 0.6]
    assert result.visuals[0].metadata["position_mode"] == "floating"
    assert result.visuals[1].bbox == []
    assert result.visuals[1].metadata["position_mode"] == "inline"


def test_docx_native_chart_is_page_mappable_only_with_rendered_pagination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "chart-placeholder.png"
    image_path.write_bytes(_png_bytes())
    mapped_path = tmp_path / "mapped-chart.docx"
    uncertain_path = tmp_path / "uncertain-chart.docx"
    _write_docx_floating_fixture(
        mapped_path,
        image_path,
        include_rendered_page_break=True,
        as_chart=True,
    )
    _write_docx_floating_fixture(
        uncertain_path,
        image_path,
        include_rendered_page_break=False,
        as_chart=True,
    )
    mapped_anchor = extract_office_visuals(mapped_path).native_chart_anchors[0]
    uncertain_anchor = extract_office_visuals(uncertain_path).native_chart_anchors[0]

    assert mapped_anchor.kind == "chart"
    assert mapped_anchor.page_no == 2
    assert mapped_anchor.bbox == [0.1, 0.2, 0.4, 0.6]
    assert mapped_anchor.metadata["force_unverified"] is False
    assert uncertain_anchor.page_no is None
    assert uncertain_anchor.bbox == []
    assert uncertain_anchor.metadata["force_unverified"] is True

    class FakeRenderer:
        available = True

        def render_pdf(self, _source_path: Path, *, output_dir: Path) -> Path:
            output = output_dir / "rendered.pdf"
            output.write_bytes(b"%PDF-1.4")
            return output

    monkeypatch.setattr(
        extraction_module,
        "extract_pdf_visuals",
        lambda _path: SourceVisualAdapterResult(
            visuals=[
                RawSourceVisual(
                    kind="diagram",
                    source_locator="pdf:page:2:vector:0",
                    native_order=0,
                    page_no=2,
                    bbox=[0.1, 0.2, 0.4, 0.6],
                    content=_png_bytes(),
                    mime_type="image/png",
                )
            ]
        ),
    )
    extractor = SourceVisualExtractor(office_renderer=FakeRenderer())  # type: ignore[arg-type]

    mapped = extractor._render_native_office_visuals(
        source_path=mapped_path,
        anchors=[mapped_anchor],
    )
    uncertain = extractor._render_native_office_visuals(
        source_path=uncertain_path,
        anchors=[uncertain_anchor],
    )

    assert mapped.visuals[0].metadata["force_unverified"] is False
    assert uncertain.visuals[0].metadata["force_unverified"] is True


def test_pdf_embedded_image_is_cropped_and_page_anchored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    pdf_path = tmp_path / "visual.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(612, 792))
    drawing.drawString(72, 730, "Text before the visual")
    drawing.drawImage(ImageReader(BytesIO(_png_bytes())), 72, 480, width=240, height=160)
    drawing.drawString(72, 450, "Figure caption")
    drawing.save()
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(pdf_path, mime_type="application/pdf")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)

    images = [visual for visual in view.visuals if visual.kind == "image"]
    assert structure.visual_index_status == "ready"
    assert images
    assert images[0].page_no == 1
    assert images[0].anchor_status == "verified"
    assert images[0].bbox[2] > images[0].bbox[0]
    assert store.read_visual_bytes(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        visual_id=images[0].id,
    ) is not None


def test_pdf_merged_table_matrix_is_unverified_instead_of_flattened_as_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    pdf_path = tmp_path / "merged-table.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(400, 400))
    drawing.rect(80, 180, 240, 100)
    drawing.line(80, 230, 320, 230)
    drawing.line(200, 180, 200, 230)
    drawing.drawCentredString(200, 250, "Merged header")
    drawing.drawCentredString(140, 200, "A")
    drawing.drawCentredString(260, 200, "B")
    drawing.save()

    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(pdf_path, mime_type="application/pdf")
    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = next(
        item
        for item in store.get_structure_view(source=record).visuals
        if item.kind == "table"
    )

    assert visual.table_data == [["Merged header", ""], ["A", "B"]]
    assert structure.visual_index_status == "partial"
    assert visual.anchor_status == "unverified"
    assert visual.metadata["pdf_table_matrix_complete"] is False
    assert visual.metadata["force_unverified"] is True
    assert "null_span_placeholder" in visual.metadata[
        "pdf_table_matrix_ambiguity_reasons"
    ]
    assert "physical_cell_count_mismatch" in visual.metadata[
        "pdf_table_matrix_ambiguity_reasons"
    ]


def test_pdf_simple_rectangular_table_remains_editable_and_usable(tmp_path: Path) -> None:
    pdf_path = tmp_path / "simple-table.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(400, 400))
    drawing.rect(80, 180, 240, 100)
    drawing.line(80, 230, 320, 230)
    drawing.line(200, 180, 200, 280)
    drawing.drawCentredString(140, 250, "Label")
    drawing.drawCentredString(260, 250, "Value")
    drawing.drawCentredString(140, 200, "A")
    drawing.drawCentredString(260, 200, "10")
    drawing.save()

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    visual = next(item for item in result.visuals if item.kind == "table")

    assert result.status == "ready"
    assert visual.table_data == [["Label", "Value"], ["A", "10"]]
    assert visual.metadata["pdf_table_matrix_complete"] is True
    assert visual.metadata["force_unverified"] is False
    assert visual.metadata["pdf_table_matrix_ambiguity_reasons"] == []


def test_pdf_vector_crop_includes_deterministically_attached_title_and_axis_labels(
    tmp_path: Path,
) -> None:
    import fitz

    pdf_path = tmp_path / "labeled-vector.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(500, 500))
    drawing.setFont("Helvetica-Bold", 14)
    drawing.drawCentredString(250, 430, "Vector layout title")
    drawing.setFont("Helvetica", 10)
    drawing.line(120, 130, 120, 390)
    drawing.line(120, 130, 420, 130)
    for index, height in enumerate((80, 140, 210)):
        x_position = 170 + index * 90
        drawing.rect(x_position, 130, 40, height, stroke=1, fill=0)
        drawing.drawCentredString(x_position + 20, 110, f"Group {index + 1}")
    for value, y_position in ((0, 130), (50, 195), (100, 260), (150, 325), (200, 390)):
        drawing.drawRightString(110, y_position - 3, str(value))
    drawing.drawCentredString(270, 80, "Horizontal axis")
    drawing.save()

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    visual = next(item for item in result.visuals if item.kind == "diagram")

    assert result.status == "ready"
    assert visual.metadata["vector_text_layout_verified"] is True
    assert visual.metadata["force_unverified"] is False
    assert visual.metadata["vector_text_lines_included"] >= 10
    assert "Vector layout title" in visual.ocr_text
    assert "Horizontal axis" in visual.ocr_text

    with fitz.open(pdf_path) as document:
        page = document[0]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        for label in ("Vector layout title", "200", "Horizontal axis"):
            label_rect = page.search_for(label)[0]
            normalized_label = (
                float(label_rect.x0) / page_width,
                float(label_rect.y0) / page_height,
                float(label_rect.x1) / page_width,
                float(label_rect.y1) / page_height,
            )
            assert visual.bbox[0] <= normalized_label[0]
            assert visual.bbox[1] <= normalized_label[1]
            assert visual.bbox[2] >= normalized_label[2]
            assert visual.bbox[3] >= normalized_label[3]


def test_pdf_vector_with_ambiguous_adjacent_prose_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    pdf_path = tmp_path / "ambiguous-vector.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(500, 500))
    drawing.rect(120, 150, 260, 200, stroke=1, fill=0)
    drawing.line(120, 150, 380, 350)
    drawing.setFont("Helvetica", 10)
    drawing.drawString(120, 135, "Adjacent text spans most of the drawing width for the first line.")
    drawing.drawString(120, 121, "A second nearby line makes ownership a layout ambiguity.")
    drawing.drawString(120, 107, "A third nearby line must not be guessed as a chart label.")
    drawing.save()

    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(pdf_path, mime_type="application/pdf")
    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = next(
        item
        for item in store.get_structure_view(source=record).visuals
        if item.kind == "diagram"
    )

    assert structure.visual_index_status == "partial"
    assert visual.anchor_status == "unverified"
    assert visual.metadata["force_unverified"] is True
    assert "nearby_text_ownership_ambiguous" in visual.metadata[
        "vector_text_ambiguity_reasons"
    ]


def test_pdf_unlabeled_vector_cluster_remains_usable(tmp_path: Path) -> None:
    pdf_path = tmp_path / "plain-vector.pdf"
    drawing = canvas.Canvas(str(pdf_path), pagesize=(500, 500))
    drawing.rect(120, 150, 260, 200, stroke=1, fill=0)
    drawing.line(120, 150, 380, 350)
    drawing.save()

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    visual = next(item for item in result.visuals if item.kind == "diagram")

    assert result.status == "ready"
    assert visual.content
    assert visual.metadata["vector_text_layout_verified"] is True
    assert visual.metadata["force_unverified"] is False
    assert visual.metadata["vector_text_lines_included"] == 0
    assert visual.bbox == pytest.approx([0.24, 0.3, 0.76, 0.7])


def test_pdf_render_budget_rejects_huge_region_before_pixmap_allocation() -> None:
    import fitz

    class _Page:
        rect = fitz.Rect(0.0, 0.0, 100_000.0, 100_000.0)

        @staticmethod
        def get_pixmap(**_kwargs):
            pytest.fail("Oversized PDF region must be rejected before rendering.")

    budget = pdf_extraction_module._PdfRenderBudget()

    rendered, width, height = pdf_extraction_module._render_region(
        _Page(),
        (0.0, 0.0, 100_000.0, 100_000.0),
        budget=budget,
    )

    assert rendered == b""
    assert width is None and height is None
    assert budget.exhausted is True
    assert budget.regions == 0


def test_huge_pdf_vector_region_fails_before_pymupdf_allocates_pixmap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fitz

    path = tmp_path / "huge-vector.pdf"
    document = fitz.open()
    page = document.new_page(width=100_000, height=100_000)
    page.draw_rect(fitz.Rect(5_000, 5_000, 95_000, 95_000), color=(0, 0, 0), width=2)
    document.save(path)
    document.close()
    pixmap_calls = 0

    def _reject_pixmap(_page, **_kwargs):
        nonlocal pixmap_calls
        pixmap_calls += 1
        pytest.fail("Resource budget must reject the huge region before get_pixmap.")

    monkeypatch.setattr(fitz.Page, "get_pixmap", _reject_pixmap)

    result = pdf_extraction_module.extract_pdf_visuals(path)

    assert result.status == "failed"
    assert result.visuals == []
    assert pixmap_calls == 0
    assert any("resource budget" in warning for warning in result.warnings)


def test_pdf_cross_page_visual_stitches_verified_regions_in_reading_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cross-page-continuous.pdf"
    _write_cross_page_pdf(pdf_path)

    first_result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    second_result = pdf_extraction_module.extract_pdf_visuals(pdf_path)

    assert first_result.status == "ready"
    assert first_result.warnings == []
    assert len(first_result.visuals) == 1
    visual = first_result.visuals[0]
    assert visual.kind == "image"
    assert visual.page_no == 1
    assert visual.source_locator.startswith("pdf:cross-page:1-2:")
    assert visual.source_locator == second_result.visuals[0].source_locator
    assert visual.content == second_result.visuals[0].content
    assert visual.metadata["cross_page"] is True
    assert visual.metadata["page_start"] == 1
    assert visual.metadata["page_end"] == 2
    assert visual.metadata["segment_count"] == 2
    assert [item["kind"] for item in visual.metadata["continuation_evidence"]] == [
        "native_object_identity"
    ]
    assert [span["page_no"] for span in visual.metadata["page_spans"]] == [1, 2]
    assert visual.metadata["page_spans"][0]["bbox"] == pytest.approx([0.1, 0.6, 0.9, 1.0])
    assert visual.metadata["page_spans"][1]["bbox"] == pytest.approx([0.1, 0.0, 0.9, 0.4])
    assert all(
        span["source_locator"].startswith(f"pdf:page:{page_no}:image:")
        for page_no, span in zip((1, 2), visual.metadata["page_spans"], strict=True)
    )

    with Image.open(BytesIO(visual.content)) as stitched:
        assert stitched.size == (480, 640)
        top_marker = stitched.convert("RGB").getpixel((70, 70))
        bottom_marker = stitched.convert("RGB").getpixel((410, 570))
    assert top_marker[0] > top_marker[1] * 3 and top_marker[0] > top_marker[2] * 3
    assert bottom_marker[2] > bottom_marker[0] * 3 and bottom_marker[2] > bottom_marker[1] * 3


def test_pdf_cross_page_visual_keeps_unrelated_boundary_regions_independent(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cross-page-unrelated.pdf"
    _write_cross_page_pdf(pdf_path, unrelated_second_half=True)

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    images = [visual for visual in result.visuals if visual.kind == "image"]

    assert result.status == "ready"
    assert len(images) == 2
    assert [visual.page_no for visual in images] == [1, 2]
    assert all(not visual.metadata.get("cross_page") for visual in images)
    assert images[0].bbox == pytest.approx([0.1, 0.6, 0.9, 1.0])
    assert images[1].bbox == pytest.approx([0.1, 0.0, 0.9, 0.4])


def test_pdf_cross_page_visual_rejects_similar_grid_without_shared_native_identity(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cross-page-similar-grid.pdf"
    _write_cross_page_pdf(pdf_path, similar_grid_different_content=True)

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    images = [visual for visual in result.visuals if visual.kind == "image"]

    assert result.status == "ready"
    assert len(images) == 2
    assert [visual.page_no for visual in images] == [1, 2]
    assert all(not visual.metadata.get("cross_page") for visual in images)
    native_identities = [visual.metadata.get("native_object_identity") for visual in images]
    assert all(native_identities)
    assert len(set(native_identities)) == 2
    pixel_score = pdf_extraction_module._pixel_seam_continuity_score(
        images[0].content,
        images[1].content,
    )
    assert pixel_score is not None and pixel_score >= 0.95


def test_pdf_cross_page_visual_rejects_ambiguous_page_edge_candidates(tmp_path: Path) -> None:
    pdf_path = tmp_path / "cross-page-ambiguous.pdf"
    _write_cross_page_pdf(pdf_path, ambiguous_bottom=True)

    result = pdf_extraction_module.extract_pdf_visuals(pdf_path)
    images = [visual for visual in result.visuals if visual.kind == "image"]

    assert result.status == "ready"
    assert len(images) == 3
    assert [visual.page_no for visual in images] == [1, 1, 2]
    assert all(not visual.metadata.get("cross_page") for visual in images)


def test_pptx_embedded_image_is_bound_to_its_slide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "slides.pptx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="1000" cy="800"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r"><p:cSld><p:spTree>'
            '<p:sp><p:txBody><a:p><a:r><a:t>Slide title</a:t></a:r></a:p></p:txBody></p:sp>'
            '<p:pic><p:nvPicPr><p:cNvPr id="2" name="Visual" descr="Slide visual"/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="rId1"/></p:blipFill>'
            '<p:spPr><a:xfrm><a:off x="100" y="100"/><a:ext cx="600" cy="400"/></a:xfrm></p:spPr>'
            '</p:pic></p:spTree></p:cSld></p:sld>',
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/></Relationships>',
        )
        archive.writestr("ppt/media/image1.png", _png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert visual.kind == "image"
    assert visual.slide_no == 1
    assert visual.chapter_id
    assert visual.anchor_status == "verified"
    assert visual.bbox == [0.1, 0.125, 0.7, 0.625]


def test_pptx_transformed_image_is_partial_unverified_and_excluded_from_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "transformed.pptx"
    _write_pptx_transformed_image_fixture(path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert structure.visual_index_status == "partial"
    assert visual.anchor_status == "unverified"
    assert visual.metadata["office_image_display_fidelity"] == "unverified"
    assert set(visual.metadata["office_image_display_transform_reasons"]) == {
        "rotation",
        "flip",
        "mask",
        "effect",
    }
    assert store.list_visuals(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        verified_only=True,
    ) == []


def test_pptx_nested_group_transform_and_smartart_kind_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "grouped.pptx"
    _write_grouped_pptx_fixture(path)

    result = extract_office_visuals(path)

    assert len(result.visuals) == 1
    assert result.visuals[0].bbox == [0.24, 0.29, 0.32, 0.35]
    assert result.visuals[0].metadata["group_transform_reliable"] is True
    assert [anchor.kind for anchor in result.native_chart_anchors] == ["diagram", "chart"]
    assert [anchor.bbox for anchor in result.native_chart_anchors] == [
        [0.32, 0.272, 0.36, 0.296],
        [0.32, 0.308, 0.36, 0.332],
    ]
    assert [anchor.native_order for anchor in result.native_chart_anchors] == [1, 2]


def test_pptx_invalid_group_transform_forces_visual_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "grouped-invalid.pptx"
    _write_grouped_pptx_fixture(path, valid_group_transform=False)
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = next(item for item in store.get_structure_view(source=record).visuals if item.kind == "image")

    assert visual.bbox == []
    assert visual.anchor_status == "unverified"
    assert visual.metadata["force_unverified"] is True


def test_pptx_rendered_smartart_and_chart_keep_distinct_kinds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "grouped-kinds.pptx"
    _write_grouped_pptx_fixture(path)
    anchors = extract_office_visuals(path).native_chart_anchors

    class FakeRenderer:
        available = True

        def render_pdf(self, _source_path: Path, *, output_dir: Path) -> Path:
            output = output_dir / "rendered.pdf"
            output.write_bytes(b"%PDF-1.4")
            return output

    monkeypatch.setattr(
        extraction_module,
        "extract_pdf_visuals",
        lambda _path: SourceVisualAdapterResult(
            visuals=[
                RawSourceVisual(
                    kind="diagram",
                    source_locator=f"pdf:page:1:vector:{index}",
                    native_order=index,
                    page_no=1,
                    bbox=anchor.bbox,
                    content=_png_bytes((20 + index * 30, 90, 160)),
                    mime_type="image/png",
                )
                for index, anchor in enumerate(anchors)
            ]
        ),
    )
    extractor = SourceVisualExtractor(office_renderer=FakeRenderer())  # type: ignore[arg-type]

    rendered = extractor._render_native_office_visuals(source_path=path, anchors=anchors)

    assert [visual.kind for visual in rendered.visuals] == ["diagram", "chart"]
    assert all(
        visual.metadata["office_anchor_mapping_verified"] is True
        and visual.metadata["force_unverified"] is False
        for visual in rendered.visuals
    )


def test_pptx_pure_group_shapes_become_one_diagram_anchor(tmp_path: Path) -> None:
    path = tmp_path / "pure-group.pptx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="10000" cy="10000"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree><p:grpSp>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name="Grouped diagram"/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="1000" y="2000"/><a:ext cx="4000" cy="3000"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="1000" cy="1000"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="400" cy="400"/>'
            '</a:xfrm></p:spPr></p:sp><p:cxnSp/></p:grpSp></p:spTree></p:cSld></p:sld>',
        )

    result = extract_office_visuals(path)

    assert result.visuals == []
    assert len(result.native_chart_anchors) == 1
    assert result.native_chart_anchors[0].kind == "diagram"
    assert result.native_chart_anchors[0].bbox == [0.1, 0.2, 0.5, 0.5]
    assert result.native_chart_anchors[0].metadata["group_shape_diagram"] is True


def test_pptx_mixed_group_is_one_composite_diagram_without_child_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "mixed-group.pptx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="p"><p:sldSz cx="10000" cy="10000"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a" xmlns:r="r"><p:cSld><p:spTree><p:grpSp>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name="Composite diagram"/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="1000" y="2000"/><a:ext cx="4000" cy="3000"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="1000" cy="1000"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:txBody><a:p><a:r><a:t>Label</a:t></a:r></a:p></p:txBody></p:sp>'
            '<p:pic><p:nvPicPr><p:cNvPr id="2" name="Image"/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="rId1"/></p:blipFill><p:spPr><a:xfrm>'
            '<a:off x="100" y="100"/><a:ext cx="500" cy="500"/></a:xfrm></p:spPr>'
            '</p:pic></p:grpSp></p:spTree></p:cSld></p:sld>',
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/></Relationships>',
        )
        archive.writestr("ppt/media/image1.png", _png_bytes())

    result = extract_office_visuals(path)

    assert result.visuals == []
    assert len(result.native_chart_anchors) == 1
    assert result.native_chart_anchors[0].kind == "diagram"
    assert result.native_chart_anchors[0].bbox == [0.1, 0.2, 0.5, 0.5]


def test_xlsx_images_and_used_range_table_share_sheet_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "workbook.xlsx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="x"><si><t>Metric</t></si><si><t>Value</t></si><si><t>A</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="x" xmlns:r="r"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>10</v></c></row>'
            '</sheetData><drawing r:id="rId1"/><tableParts count="1">'
            '<tablePart r:id="rId2"/></tableParts></worksheet>',
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../drawings/drawing1.xml" Type="drawing"/>'
            '<Relationship Id="rId2" Target="../tables/table1.xml" Type="table"/></Relationships>',
        )
        archive.writestr(
            "xl/tables/table1.xml",
            '<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'name="Metrics" displayName="Metrics" ref="A1:B2"/>',
        )
        archive.writestr(
            "xl/drawings/drawing1.xml",
            '<xdr:wsDr xmlns:xdr="xdr" xmlns:a="a" xmlns:r="r"><xdr:twoCellAnchor>'
            '<xdr:from><xdr:col>0</xdr:col><xdr:row>0</xdr:row></xdr:from>'
            '<xdr:to><xdr:col>1</xdr:col><xdr:row>1</xdr:row></xdr:to>'
            '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="Visual"/></xdr:nvPicPr>'
            '<xdr:blipFill><a:blip r:embed="rId1"/></xdr:blipFill></xdr:pic>'
            '</xdr:twoCellAnchor></xdr:wsDr>',
        )
        archive.writestr(
            "xl/drawings/_rels/drawing1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/></Relationships>',
        )
        archive.writestr("xl/media/image1.png", _png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert {visual.kind for visual in visuals} == {"image", "table"}
    assert all(visual.chapter_id and visual.anchor_status == "verified" for visual in visuals)
    table_visual = next(visual for visual in visuals if visual.kind == "table")
    assert table_visual.table_data == [["Metric", "Value"], ["A", "10"]]
    assert table_visual.metadata["native_table"] is True


def test_xlsx_transformed_image_is_partial_unverified_and_excluded_from_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "transformed.xlsx"
    _write_xlsx_transformed_image_fixture(path)
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]

    assert structure.visual_index_status == "partial"
    assert visual.anchor_status == "unverified"
    assert visual.metadata["office_image_display_fidelity"] == "unverified"
    assert set(visual.metadata["office_image_display_transform_reasons"]) == {
        "crop",
        "flip",
        "effect",
    }
    assert store.list_visuals(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        verified_only=True,
    ) == []


def test_xlsx_chart_page_mapping_requires_single_sheet_fit_to_one_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped_path = tmp_path / "mapped-chart.xlsx"
    uncertain_path = tmp_path / "uncertain-chart.xlsx"
    _write_xlsx_chart_fixture(mapped_path, fit_to_single_page=True)
    _write_xlsx_chart_fixture(uncertain_path, fit_to_single_page=False)
    mapped_anchor = extract_office_visuals(mapped_path).native_chart_anchors[0]
    uncertain_anchor = extract_office_visuals(uncertain_path).native_chart_anchors[0]

    assert mapped_anchor.page_no == 1
    assert mapped_anchor.bbox == [0.0, 0.0, 0.5, 0.2]
    assert mapped_anchor.metadata["rendered_page_mapping"] == "single_sheet_fit_to_one_page"
    assert mapped_anchor.metadata["force_unverified"] is False
    assert uncertain_anchor.page_no is None
    assert uncertain_anchor.metadata["force_unverified"] is True

    class FakeRenderer:
        available = True

        def render_pdf(self, _source_path: Path, *, output_dir: Path) -> Path:
            output = output_dir / "rendered.pdf"
            output.write_bytes(b"%PDF-1.4")
            return output

    monkeypatch.setattr(
        extraction_module,
        "extract_pdf_visuals",
        lambda _path: SourceVisualAdapterResult(
            visuals=[
                RawSourceVisual(
                    kind="diagram",
                    source_locator="pdf:page:1:vector:0",
                    native_order=0,
                    page_no=1,
                    bbox=[0.0, 0.0, 0.5, 0.2],
                    content=_png_bytes(),
                    mime_type="image/png",
                )
            ]
        ),
    )
    extractor = SourceVisualExtractor(office_renderer=FakeRenderer())  # type: ignore[arg-type]
    mapped = extractor._render_native_office_visuals(
        source_path=mapped_path,
        anchors=[mapped_anchor],
    )
    uncertain = extractor._render_native_office_visuals(
        source_path=uncertain_path,
        anchors=[uncertain_anchor],
    )

    assert mapped.visuals[0].metadata["force_unverified"] is False
    assert uncertain.visuals[0].metadata["force_unverified"] is True


def test_xlsx_plain_used_range_is_not_promoted_to_editable_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "plain-workbook.xlsx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="x"><sheetData><row r="1">'
            '<c r="A1" t="inlineStr"><is><t>Metric</t></is></c>'
            '<c r="B1"><v>10</v></c></row></sheetData></worksheet>',
        )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    SourceStructureIndexer(store=store).rebuild_structure(record)

    assert store.get_structure_view(source=record).visuals == []


@pytest.mark.parametrize(
    ("entries", "limits", "message"),
    [
        (
            [("one.xml", b"1"), ("two.xml", b"2"), ("three.xml", b"3")],
            {"MAX_SOURCE_ARCHIVE_ENTRIES": 2},
            "too many entries",
        ),
        (
            [("large.xml", b"123456")],
            {"MAX_SOURCE_ARCHIVE_ENTRY_BYTES": 5},
            "entry exceeds",
        ),
        (
            [("one.xml", b"123456"), ("two.xml", b"123456")],
            {"MAX_SOURCE_ARCHIVE_TOTAL_BYTES": 10},
            "total decompression",
        ),
    ],
)
def test_safe_source_archive_rejects_declared_resource_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: list[tuple[str, bytes]],
    limits: dict[str, int],
    message: str,
) -> None:
    path = tmp_path / "budget.zip"
    with ZipFile(path, "w", ZIP_STORED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    for name, value in limits.items():
        monkeypatch.setattr(source_archive_module, name, value)

    with pytest.raises(SourceArchiveError, match=message):
        with SafeSourceArchive(path):
            pass


@pytest.mark.parametrize(
    ("suffix", "mime_type", "entry_name", "prefix", "suffix_xml"),
    [
        (
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "word/document.xml",
            b'<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>',
            b"</w:t></w:r></w:p></w:body></w:document>",
        ),
        (
            ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ppt/slides/slide1.xml",
            b'<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree><a:t>',
            b"</a:t></p:spTree></p:cSld></p:sld>",
        ),
        (
            ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xl/worksheets/sheet1.xml",
            b'<worksheet xmlns="x"><sheetData><row><c t="inlineStr"><is><t>',
            b"</t></is></c></row></sheetData></worksheet>",
        ),
        (
            ".epub",
            "application/epub+zip",
            "OEBPS/content.opf",
            b'<package xmlns="http://www.idpf.org/2007/opf"><metadata><title>',
            b"</title></metadata></package>",
        ),
    ],
)
def test_office_and_epub_compression_bombs_fail_closed(
    tmp_path: Path,
    suffix: str,
    mime_type: str,
    entry_name: str,
    prefix: bytes,
    suffix_xml: bytes,
) -> None:
    path = tmp_path / f"compression-bomb{suffix}"
    repetitive_text = b"A" * (2 * 1024 * 1024)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(entry_name, prefix + repetitive_text + suffix_xml)
    record = _record(path, mime_type=mime_type)

    adapter_result = (
        markup_module.extract_markup_visuals(path, record)
        if suffix == ".epub"
        else extract_office_visuals(path)
    )
    structure = SourceStructureIndexer(
        store=SourceStructureStore(tmp_path / f"openclass-{suffix[1:]}.sqlite3")
    ).rebuild_structure(record)

    assert path.stat().st_size < 100_000
    assert adapter_result.status == "failed"
    assert any("compression ratio" in warning for warning in adapter_result.warnings)
    assert structure.status == "failed"
    assert structure.visual_index_status == "failed"
    assert "compression ratio" in structure.error


def test_markup_visual_object_budget_counts_images_tables_and_diagrams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction_budget_module, "MAX_SOURCE_VISUAL_OBJECTS", 2)
    image_uri = _png_data_uri()

    html_path = tmp_path / "object-budget.html"
    html_path.write_text(
        f'<img src="{image_uri}"/><table><tr><td>A</td></tr></table>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>',
        encoding="utf-8",
    )
    markdown_path = tmp_path / "object-budget.md"
    markdown_path.write_text(
        "| A |\n| --- |\n| 1 |\n\n"
        "| B |\n| --- |\n| 2 |\n\n"
        "| C |\n| --- |\n| 3 |\n",
        encoding="utf-8",
    )
    epub_path = tmp_path / "object-budget.epub"
    with ZipFile(epub_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="image" href="image.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html><body><img src="image.png"/><table><tr><td>A</td></tr></table>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
            '</body></html>',
        )
        archive.writestr("OEBPS/image.png", _png_bytes())

    results = [
        markup_module.extract_markup_visuals(
            html_path,
            _record(html_path, mime_type="text/html"),
        ),
        markup_module.extract_markup_visuals(
            markdown_path,
            _record(markdown_path, mime_type="text/markdown"),
        ),
        markup_module.extract_markup_visuals(
            epub_path,
            _record(epub_path, mime_type="application/epub+zip"),
        ),
    ]

    assert all(result.status == "failed" for result in results)
    assert all(result.visuals == [] for result in results)
    assert all(
        any("visual object budget" in warning for warning in result.warnings)
        for result in results
    )


def test_cumulative_image_bytes_count_repeated_epub_and_office_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _png_bytes()
    monkeypatch.setattr(
        extraction_budget_module,
        "MAX_SOURCE_VISUAL_ALL_IMAGE_BYTES",
        len(content) + 1,
    )

    epub_path = tmp_path / "repeated-media.epub"
    with ZipFile(epub_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="image" href="image.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html><body><img src="image.png"/><img src="image.png"/></body></html>',
        )
        archive.writestr("OEBPS/image.png", content)

    image_path = tmp_path / "shared.png"
    image_path.write_bytes(content)
    docx_path = tmp_path / "repeated-media.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(str(image_path))
    paragraph.add_run().add_picture(str(image_path))
    document.save(docx_path)

    epub_result = markup_module.extract_markup_visuals(
        epub_path,
        _record(epub_path, mime_type="application/epub+zip"),
    )
    office_result = extract_office_visuals(docx_path)

    assert epub_result.status == "failed"
    assert office_result.status == "failed"
    assert epub_result.visuals == []
    assert office_result.visuals == []
    assert any("cumulative image byte budget" in warning for warning in epub_result.warnings)
    assert any("cumulative image byte budget" in warning for warning in office_result.warnings)


def test_cumulative_image_bytes_cover_data_uris_and_standalone_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _png_bytes()
    uri = f"data:image/png;base64,{base64.b64encode(content).decode('ascii')}"
    html_path = tmp_path / "repeated-data.html"
    html_path.write_text(
        f'<html><body><img src="{uri}"/><img src="{uri}"/></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        extraction_budget_module,
        "MAX_SOURCE_VISUAL_ALL_IMAGE_BYTES",
        len(content) + 1,
    )

    html_result = markup_module.extract_markup_visuals(
        html_path,
        _record(html_path, mime_type="text/html"),
    )

    image_path = tmp_path / "standalone.png"
    image_path.write_bytes(content)
    monkeypatch.setattr(
        extraction_budget_module,
        "MAX_SOURCE_VISUAL_ALL_IMAGE_BYTES",
        len(content) - 1,
    )
    standalone_result = markup_module.extract_standalone_image(
        image_path,
        _record(image_path, mime_type="image/png"),
    )

    assert html_result.status == "failed"
    assert html_result.visuals == []
    assert standalone_result.status == "failed"
    assert standalone_result.visuals == []


@pytest.mark.parametrize(
    ("budget_name", "budget_value", "warning_fragment"),
    [
        ("MAX_SOURCE_VISUAL_TOTAL_PIXELS", 1_500_000, "decompressed pixel budget"),
        ("MAX_SOURCE_VISUAL_OCR_OBJECTS", 2, "OCR object budget"),
    ],
)
def test_pixel_and_ocr_budgets_fail_before_any_visual_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_name: str,
    budget_value: int,
    warning_fragment: str,
) -> None:
    monkeypatch.setattr(extraction_budget_module, budget_name, budget_value)
    compressed = BytesIO()
    Image.new("RGB", (1_000, 1_000), (255, 255, 255)).save(compressed, format="PNG")
    content = compressed.getvalue()
    assert len(content) < 10_000
    raw_visuals = [
        RawSourceVisual(
            kind="image",
            source_locator=f"budget:image:{index}",
            native_order=index,
            content=content,
            mime_type="image/png",
        )
        for index in range(3)
    ]
    path = tmp_path / "budget-source.html"
    path.write_text("<h1>Budget</h1><p>Text.</p>", encoding="utf-8")
    record = _record(path, mime_type="text/html")
    structure = SourceStructure(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
    )
    extractor = SourceVisualExtractor()
    monkeypatch.setattr(
        extractor,
        "_adapter_result",
        lambda **_kwargs: SourceVisualAdapterResult(visuals=raw_visuals, status="ready"),
    )
    ocr_calls = 0

    def count_ocr(_content: bytes, _mime_type: str) -> str:
        nonlocal ocr_calls
        ocr_calls += 1
        return ""

    monkeypatch.setattr(extraction_module, "_ocr_visual_content", count_ocr)

    result = extractor.extract(
        record=record,
        path=path,
        structure=structure,
        chapters=[],
        chunks=[],
    )

    assert result.status == "failed"
    assert result.visuals == []
    assert any(warning_fragment in warning for warning in result.warnings)
    assert ocr_calls == 0


def test_table_cell_and_text_budgets_fail_the_whole_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_path = tmp_path / "table-cells.html"
    html_path.write_text(
        "<table><tr><td>A</td><td>B</td><td>C</td></tr></table>",
        encoding="utf-8",
    )
    monkeypatch.setattr(extraction_budget_module, "MAX_SOURCE_VISUAL_TABLE_CELLS", 2)
    cell_result = markup_module.extract_markup_visuals(
        html_path,
        _record(html_path, mime_type="text/html"),
    )

    monkeypatch.setattr(extraction_budget_module, "MAX_SOURCE_VISUAL_TABLE_CELLS", 10)
    monkeypatch.setattr(extraction_budget_module, "MAX_SOURCE_VISUAL_TABLE_TEXT_CHARS", 3)
    csv_path = tmp_path / "table-text.csv"
    csv_path.write_text("value\nlong\n", encoding="utf-8")
    text_result = markup_module.extract_markup_visuals(
        csv_path,
        _record(csv_path, mime_type="text/csv"),
    )

    assert cell_result.status == "failed"
    assert cell_result.visuals == []
    assert any("table cell budget" in warning for warning in cell_result.warnings)
    assert text_result.status == "failed"
    assert text_result.visuals == []
    assert any("table text budget" in warning for warning in text_result.warnings)


def test_epub_archive_image_and_table_are_extracted_without_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "book.epub"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="image" href="images/visual.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html><body><h1>Visual chapter</h1><p>Before.</p>'
            '<img src="images/visual.png" alt="Diagram"/>'
            '<table><tr><th>Metric</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr></table>'
            '<p>After.</p></body></html>',
        )
        archive.writestr("OEBPS/images/visual.png", _png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="application/epub+zip")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert {visual.kind for visual in visuals} == {"image", "table"}
    assert all(visual.anchor_status == "verified" for visual in visuals)
    assert next(visual for visual in visuals if visual.kind == "image").caption == "Diagram"


@pytest.mark.parametrize(
    ("suffix", "mime_type", "span_attribute"),
    [
        (".html", "text/html", 'colspan="2"'),
        (".html", "text/html", 'rowspan="2"'),
        (".epub", "application/epub+zip", 'colspan="2"'),
        (".epub", "application/epub+zip", 'rowspan="2"'),
    ],
)
def test_markup_merged_table_cells_are_partial_and_unverified(
    tmp_path: Path,
    suffix: str,
    mime_type: str,
    span_attribute: str,
) -> None:
    table_markup = (
        f'<table><tr><th {span_attribute}>Metric</th><th>Value</th></tr>'
        '<tr><td>A</td><td>10</td></tr></table>'
    )
    path = tmp_path / f"merged-{span_attribute.split('=')[0]}{suffix}"
    if suffix == ".html":
        path.write_text(
            f"<html><body><h1>Section</h1>{table_markup}</body></html>",
            encoding="utf-8",
        )
    else:
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                '</rootfiles></container>',
            )
            archive.writestr(
                "OEBPS/content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="chapter" href="chapter.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                '<spine><itemref idref="chapter"/></spine></package>',
            )
            archive.writestr(
                "OEBPS/chapter.xhtml",
                f"<html><body><h1>Section</h1>{table_markup}</body></html>",
            )

    result = markup_module.extract_markup_visuals(path, _record(path, mime_type=mime_type))

    assert result.status == "partial"
    assert len(result.visuals) == 1
    assert result.visuals[0].metadata["force_unverified"] is True
    assert result.visuals[0].metadata["table_merge_semantics"] == "unrepresented"
    assert result.visuals[0].metadata["table_merge_markers"] == [
        "colspan_or_rowspan"
    ]


def test_epub_spine_visuals_keep_their_own_chapter_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "multi-spine.epub"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="second" href="second.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="first-image" href="images/first.png" media-type="image/png"/>'
            '<item id="second-image" href="images/second.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="first"/><itemref idref="second"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/first.xhtml",
            '<html><body><h1>First chapter</h1><p>First text.</p>'
            '<img src="images/first.png" alt="First visual"/></body></html>',
        )
        archive.writestr(
            "OEBPS/second.xhtml",
            '<html><body><h1>Second chapter</h1><p>Second text.</p>'
            '<img src="images/second.png" alt="Second visual"/></body></html>',
        )
        archive.writestr("OEBPS/images/first.png", _png_bytes((10, 80, 140)))
        archive.writestr("OEBPS/images/second.png", _png_bytes((150, 70, 20)))
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="application/epub+zip")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)
    chapters_by_id = {chapter.id: chapter for chapter in view.chapters}
    visuals_by_caption = {visual.caption: visual for visual in view.visuals}

    assert chapters_by_id[visuals_by_caption["First visual"].chapter_id].title == "First chapter"
    assert chapters_by_id[visuals_by_caption["Second visual"].chapter_id].title == "Second chapter"
    assert visuals_by_caption["First visual"].chapter_id != visuals_by_caption["Second visual"].chapter_id
    assert all(visual.before_chunk_id is not None for visual in visuals_by_caption.values())
    assert all(visual.anchor_status == "verified" for visual in visuals_by_caption.values())


def test_html_visuals_follow_document_order_across_images_and_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    first_uri = _png_data_uri((10, 80, 140))
    second_uri = _png_data_uri((150, 70, 20))
    path = tmp_path / "ordered.html"
    path.write_text(
        '<script>const ignored = "<svg width=\'1\' height=\'1\'></svg>";</script>'
        f'<h1>Ordered section</h1><p>Before.</p><img src="{first_uri}" alt="First"/>'
        '<p>Between.</p><table><tr><th>Metric</th><th>Value</th></tr>'
        '<tr><td>A</td><td>10</td></tr></table><p>After table.</p>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<rect width="80" height="40" fill="blue"/></svg><p>After diagram.</p>'
        f'<img src="{second_uri}" alt="Second"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    visuals = store.get_structure_view(source=record).visuals

    assert [visual.kind for visual in visuals] == ["image", "table", "diagram", "image"]
    assert [visual.caption for visual in visuals if visual.kind == "image"] == ["First", "Second"]
    assert [visual.metadata["image_source"] for visual in visuals if visual.kind == "image"] == [
        "data:image",
        "data:image",
    ]


def test_html_canonical_offsets_bind_two_images_to_their_own_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    first_uri = _png_data_uri((10, 80, 140))
    second_uri = _png_data_uri((150, 70, 20))
    path = tmp_path / "two-chapters.html"
    path.write_text(
        f'<h1>First chapter</h1><p>First explanation.</p>'
        f'<img src="{first_uri}" alt="First figure"/><p>First conclusion.</p>'
        f'<h1>Second chapter</h1><p>Second explanation.</p>'
        f'<img src="{second_uri}" alt="Second figure"/><p>Second conclusion.</p>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "html-canonical.sqlite3")
    record = _record(path, mime_type="text/html")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)
    chapters_by_id = {chapter.id: chapter for chapter in view.chapters}
    visuals_by_caption = {visual.caption: visual for visual in view.visuals}

    assert chapters_by_id[visuals_by_caption["First figure"].chapter_id].title == "First chapter"
    assert chapters_by_id[visuals_by_caption["Second figure"].chapter_id].title == "Second chapter"
    assert all(visual.anchor_status == "verified" for visual in visuals_by_caption.values())
    assert all(visual.before_chunk_id is not None for visual in visuals_by_caption.values())
    assert all("\ufffc" not in chapter.excerpt for chapter in view.chapters)
    assert all("\ufffc" not in chunk.text for chunk in view.chunks)


def test_single_xhtml_epub_offsets_bind_two_images_to_their_own_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "two-chapters.epub"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="first" href="first.png" media-type="image/png"/>'
            '<item id="second" href="second.png" media-type="image/png"/>'
            '</manifest><spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<html><body><h1>First chapter</h1><p>First explanation.</p>'
            '<img src="first.png" alt="First figure"/><p>First conclusion.</p>'
            '<h1>Second chapter</h1><p>Second explanation.</p>'
            '<img src="second.png" alt="Second figure"/><p>Second conclusion.</p>'
            '</body></html>',
        )
        archive.writestr("OEBPS/first.png", _png_bytes((10, 80, 140)))
        archive.writestr("OEBPS/second.png", _png_bytes((150, 70, 20)))
    store = SourceStructureStore(tmp_path / "epub-canonical.sqlite3")
    record = _record(path, mime_type="application/epub+zip")

    SourceStructureIndexer(store=store).rebuild_structure(record)
    view = store.get_structure_view(source=record)
    chapters_by_id = {chapter.id: chapter for chapter in view.chapters}
    visuals_by_caption = {visual.caption: visual for visual in view.visuals}

    assert chapters_by_id[visuals_by_caption["First figure"].chapter_id].title == "First chapter"
    assert chapters_by_id[visuals_by_caption["Second figure"].chapter_id].title == "Second chapter"
    assert all(visual.anchor_status == "verified" for visual in visuals_by_caption.values())
    assert all(visual.before_chunk_id is not None for visual in visuals_by_caption.values())
    assert all("\ufffc" not in chapter.excerpt for chapter in view.chapters)
    assert all("\ufffc" not in chunk.text for chunk in view.chunks)


def test_remote_image_connection_is_pinned_to_the_prevalidated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def getheader(self, name: str) -> str:
            return "image/png" if name.lower() == "content-type" else ""

        def read(self, _size: int) -> bytes:
            if requested.get("read"):
                return b""
            requested["read"] = True
            return _png_bytes()

    class FakeConnection:
        def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
            requested.update(method=method, target=target, headers=headers)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            requested["closed"] = True

    monkeypatch.setattr(
        markup_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (markup_module.socket.AF_INET, markup_module.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ],
    )

    def fake_open(parsed: object, address: str, *, timeout: float) -> FakeConnection:
        requested.update(host=getattr(parsed, "hostname"), address=address, timeout=timeout)
        return FakeConnection()

    monkeypatch.setattr(markup_module, "_open_pinned_http_connection", fake_open)

    result = markup_module._fetch_public_image("http://example.test/visual.png?version=1")

    assert result is not None and result[0].startswith(b"\x89PNG")
    assert requested["host"] == "example.test"
    assert requested["address"] == "93.184.216.34"
    assert requested["target"] == "/visual.png?version=1"
    assert requested["headers"]["Host"] == "example.test"
    assert requested["closed"] is True


def test_remote_image_without_content_length_stops_at_stream_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(markup_module, "MAX_SOURCE_VISUAL_BYTES", 100)
    read_sizes: list[int] = []

    class OversizedResponse:
        status = 200

        def __init__(self) -> None:
            self.remaining = 101

        def getheader(self, name: str) -> str:
            return "image/png" if name.lower() == "content-type" else ""

        def read(self, size: int) -> bytes:
            read_sizes.append(size)
            amount = min(size, self.remaining)
            self.remaining -= amount
            return b"x" * amount

    class FakeConnection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> OversizedResponse:
            return OversizedResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        markup_module,
        "_resolve_public_http_url",
        lambda _url: (markup_module.urlparse("http://example.test/visual.png"), ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        markup_module,
        "_open_pinned_http_connection",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    result = markup_module._fetch_public_image("http://example.test/visual.png")

    assert result is None
    assert read_sizes == [101]


def test_remote_request_attempt_budget_stops_before_the_third_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        extraction_budget_module,
        "MAX_SOURCE_VISUAL_REMOTE_REQUEST_ATTEMPTS",
        2,
    )
    content = _png_bytes()
    request_count = 0

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.sent = False

        def getheader(self, name: str) -> str:
            return "image/png" if name.lower() == "content-type" else ""

        def read(self, _size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return content

    class FakeConnection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            nonlocal request_count
            request_count += 1

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        markup_module,
        "_resolve_public_http_url",
        lambda url: (markup_module.urlparse(url), ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        markup_module,
        "_open_pinned_http_connection",
        lambda *_args, **_kwargs: FakeConnection(),
    )
    path = tmp_path / "three-remote-images.html"
    path.write_text(
        "".join(f'<img src="https://example.test/{index}.png"/>' for index in range(3)),
        encoding="utf-8",
    )

    result = markup_module.extract_markup_visuals(path, _record(path, mime_type="text/html"))

    assert result.status == "failed"
    assert result.visuals == []
    assert request_count == 2
    assert any("remote request attempt budget" in warning for warning in result.warnings)


def test_cumulative_remote_download_budget_discards_prior_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _png_bytes()
    monkeypatch.setattr(
        extraction_budget_module,
        "MAX_SOURCE_VISUAL_REMOTE_DOWNLOAD_BYTES",
        len(content) + 1,
    )

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.sent = False

        def getheader(self, name: str) -> str:
            return "image/png" if name.lower() == "content-type" else ""

        def read(self, _size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return content

    class FakeConnection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        markup_module,
        "_resolve_public_http_url",
        lambda url: (markup_module.urlparse(url), ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        markup_module,
        "_open_pinned_http_connection",
        lambda *_args, **_kwargs: FakeConnection(),
    )
    path = tmp_path / "remote-byte-budget.html"
    path.write_text(
        '<img src="https://example.test/one.png"/>'
        '<img src="https://example.test/two.png"/>',
        encoding="utf-8",
    )

    result = markup_module.extract_markup_visuals(path, _record(path, mime_type="text/html"))

    assert result.status == "failed"
    assert result.visuals == []
    assert any("remote download byte budget" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("suffix", "mime_type", "document_text"),
    [
        (".html", "text/html", '<img src="secret.png" alt="secret"/>'),
        (".md", "text/markdown", "![secret](secret.png)"),
    ],
)
def test_markup_files_never_read_filesystem_relative_images(
    tmp_path: Path,
    suffix: str,
    mime_type: str,
    document_text: str,
) -> None:
    shared_source_directory = tmp_path / "sources"
    shared_source_directory.mkdir()
    (shared_source_directory / "secret.png").write_bytes(_png_bytes())
    path = shared_source_directory / f"public{suffix}"
    path.write_text(document_text, encoding="utf-8")

    result = markup_module.extract_markup_visuals(path, _record(path, mime_type=mime_type))

    assert result.status == "partial"
    assert result.visuals == []


def test_resolved_snapshot_uri_is_the_base_for_relative_remote_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []
    path = tmp_path / "snapshot.html"
    path.write_text('<img src="images/chart.png" alt="chart"/>', encoding="utf-8")
    record = _record(path, mime_type="text/html").model_copy(
        update={
            "source_uri": "https://origin.example/requested/page.html",
            "metadata": {
                "local_source_path": str(path),
                "requested_source_uri": "https://origin.example/requested/page.html",
                "resolved_source_uri": "https://cdn.example/final/page.html",
            },
        }
    )

    def fake_fetch(
        url: str,
        *,
        budget: object,
    ) -> tuple[bytes, str, str]:
        del budget
        requested_urls.append(url)
        return _png_bytes(), "image/png", url

    monkeypatch.setattr(markup_module, "_fetch_public_image", fake_fetch)

    result = markup_module.extract_markup_visuals(path, record)

    assert result.status == "ready"
    assert requested_urls == ["https://cdn.example/final/images/chart.png"]
    assert result.visuals[0].metadata["image_source"] == requested_urls[0]


def test_html_private_network_image_is_rejected_without_network_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "page.html"
    path.write_text(
        '<h1>Section</h1><p>Body.</p><img src="http://127.0.0.1/private.png" alt="private"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert store.get_structure_view(source=record).visuals == []


def test_declared_image_mime_with_non_image_bytes_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    payload = base64.b64encode(b"<html>not an image</html>").decode("ascii")
    path = tmp_path / "unsafe.html"
    path.write_text(
        f'<h1>Section</h1><img src="data:image/png;base64,{payload}"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert store.get_structure_view(source=record).visuals == []


def test_decompressed_pixel_limit_rejects_oversized_raster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(extraction_module, "MAX_SOURCE_VISUAL_PIXELS", 100)
    path = tmp_path / "large.png"
    path.write_bytes(_png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="image/png")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert store.get_structure_view(source=record).visuals == []


def test_svg_is_never_served_when_safe_rasterization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(extraction_module, "render_svg_to_png", lambda _content: None)
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>').decode("ascii")
    path = tmp_path / "svg.html"
    path.write_text(
        f'<h1>Section</h1><img src="data:image/svg+xml;base64,{svg}"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert store.get_structure_view(source=record).visuals == []


def test_svg_is_rasterized_to_png_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    svg = base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        b'<rect width="80" height="40" fill="blue"/></svg>'
    ).decode("ascii")
    path = tmp_path / "svg-safe.html"
    path.write_text(
        f'<h1>Section</h1><img src="data:image/svg+xml;base64,{svg}"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)
    visual = store.get_structure_view(source=record).visuals[0]
    loaded = store.read_visual_bytes(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
        visual_id=visual.id,
    )

    assert structure.visual_index_status == "ready"
    assert visual.mime_type == "image/png"
    assert visual.metadata["original_mime_type"] == "image/svg+xml"
    assert loaded is not None and loaded[1].startswith(b"\x89PNG")


def test_utf16_svg_dtd_and_entity_expansion_are_rejected_before_rendering() -> None:
    svg = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE svg ['
        '<!ENTITY seed "entity-expansion">'
        '<!ENTITY branch "&seed;&seed;&seed;&seed;&seed;&seed;&seed;&seed;">'
        '<!ENTITY bomb "&branch;&branch;&branch;&branch;&branch;&branch;&branch;&branch;">'
        ']>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<text>&bomb;</text></svg>'
    ).encode("utf-16")

    assert markup_module.render_svg_to_png(svg) is None


def test_embedded_tracking_pixel_is_filtered_as_non_content_visual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    pixel = BytesIO()
    Image.new("RGB", (1, 1), (255, 255, 255)).save(pixel, format="PNG")
    payload = base64.b64encode(pixel.getvalue()).decode("ascii")
    path = tmp_path / "tracking.html"
    path.write_text(
        f'<h1>Section</h1><p>Text.</p><img src="data:image/png;base64,{payload}"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "ready"
    assert store.get_structure_view(source=record).visuals == []


def test_repeated_midpage_visuals_with_captions_are_not_treated_as_decoration() -> None:
    content = _png_bytes()
    visuals = [
        RawSourceVisual(
            kind="image",
            source_locator=f"pdf:page:{page_no}:image:0",
            native_order=page_no,
            page_no=page_no,
            bbox=[0.4, 0.4, 0.5, 0.5],
            content=content,
            mime_type="image/png",
            caption=f"Caption {page_no}",
        )
        for page_no in range(1, 4)
    ]
    repetitions = extraction_module._raw_visual_repetitions(visuals)

    reasons = [
        extraction_module._visual_content_shape_filter_reason(
            visual,
            repetitions=repetitions,
        )
        for visual in visuals
    ]

    assert reasons == ["", "", ""]


def test_svg_external_resources_are_rejected_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    svg = base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
        b'<image href="http://127.0.0.1/private.png" width="20" height="10"/></svg>'
    ).decode("ascii")
    path = tmp_path / "svg-external.html"
    path.write_text(
        f'<h1>Section</h1><img src="data:image/svg+xml;base64,{svg}"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert store.get_structure_view(source=record).visuals == []


def test_audio_and_video_sources_never_extract_frames(tmp_path: Path) -> None:
    path = tmp_path / "transcript.txt"
    path.write_text("Transcript only.", encoding="utf-8")
    record = _record(path, mime_type="video/mp4")
    record = record.model_copy(update={"source_type": "video_file"})
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")

    structure = SourceStructureIndexer(store=store).rebuild_structure(record)

    assert structure.visual_index_status == "unsupported"
    assert structure.visual_count == 0
    assert store.get_structure_view(source=record).visuals == []


def test_failed_rebuild_rolls_back_visual_rows_with_text_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    image_path = tmp_path / "stable.png"
    image_path.write_bytes(_png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(image_path, mime_type="image/png")
    indexer = SourceStructureIndexer(store=store)
    indexer.rebuild_structure(record)
    before = store.get_structure_view(source=record)
    initial_assets = sorted(source_visual_asset_root().rglob("*.png"))
    image_path.write_bytes(_png_bytes((160, 40, 40)))
    monkeypatch.setattr(store.native_index, "index_chunks", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("index failed")))

    recovered = indexer.rebuild_structure(record)
    after = store.get_structure_view(source=record)

    assert recovered.error == "index failed"
    assert [visual.id for visual in after.visuals] == [visual.id for visual in before.visuals]
    assert [visual.content_hash for visual in after.visuals] == [visual.content_hash for visual in before.visuals]
    assert sorted(source_visual_asset_root().rglob("*.png")) == initial_assets


def test_failed_adapter_result_keeps_previous_visual_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "stable.png"
    path.write_bytes(_png_bytes())
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="image/png")
    indexer = SourceStructureIndexer(store=store)
    indexer.rebuild_structure(record)
    previous = store.get_structure_view(source=record)

    def fail_closed(**_kwargs: object) -> SourceVisualAdapterResult:
        return SourceVisualAdapterResult(
            status="failed",
            warnings=["visual adapter failed"],
            visuals=[
                RawSourceVisual(
                    kind="image",
                    source_locator="failed:partial-image",
                    native_order=0,
                    content=_png_bytes((200, 20, 20)),
                    mime_type="image/png",
                )
            ],
        )

    monkeypatch.setattr(indexer.visual_extractor, "_adapter_result", fail_closed)
    rebuilt = indexer.rebuild_structure(record)
    current = store.get_structure_view(source=record)

    assert rebuilt.error == "visual adapter failed"
    assert [visual.id for visual in current.visuals] == [visual.id for visual in previous.visuals]
    assert current.structure.visual_index_status == "ready"


def test_visual_object_budget_failure_keeps_previous_visual_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    uri = _png_data_uri()
    path = tmp_path / "budget-rebuild.html"
    path.write_text(
        f'<h1>Stable</h1><p>Text.</p><img src="{uri}" alt="stable"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")
    indexer = SourceStructureIndexer(store=store)
    indexer.rebuild_structure(record)
    previous = store.get_structure_view(source=record)
    monkeypatch.setattr(extraction_budget_module, "MAX_SOURCE_VISUAL_OBJECTS", 1)
    path.write_text(
        f'<h1>Changed</h1><img src="{uri}" alt="one"/><img src="{uri}" alt="two"/>',
        encoding="utf-8",
    )

    rebuilt = indexer.rebuild_structure(record)
    current = store.get_structure_view(source=record)

    assert "visual object budget" in rebuilt.error
    assert [visual.id for visual in current.visuals] == [visual.id for visual in previous.visuals]
    assert [chunk.id for chunk in current.chunks] == [chunk.id for chunk in previous.chunks]
    assert current.structure.visual_index_status == "ready"


def test_initial_visual_failure_preserves_text_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "text-first.html"
    path.write_text('<h1>Section</h1><p>Text remains searchable.</p>', encoding="utf-8")
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")
    indexer = SourceStructureIndexer(store=store)

    def fail_closed(**_kwargs: object) -> SourceVisualAdapterResult:
        return SourceVisualAdapterResult(
            status="failed",
            warnings=["visual adapter failed"],
            visuals=[
                RawSourceVisual(
                    kind="image",
                    source_locator="failed:partial-image",
                    native_order=0,
                    content=_png_bytes(),
                    mime_type="image/png",
                )
            ],
        )

    monkeypatch.setattr(indexer.visual_extractor, "_adapter_result", fail_closed)
    structure = indexer.rebuild_structure(record)
    view = store.get_structure_view(source=record)

    assert structure.status == "ready"
    assert structure.visual_index_status == "failed"
    assert view.chapters and view.chunks
    assert view.visuals == []
    assert list((tmp_path / "uploads" / "source-visuals").rglob("*")) == []


def test_libreoffice_explicit_invalid_path_fails_validation(tmp_path: Path) -> None:
    renderer = LibreOfficeRenderer(str(tmp_path / "missing-soffice"))

    with pytest.raises(LibreOfficeRenderError, match="OPENCLASS_LIBREOFFICE_PATH"):
        renderer.validate_configuration()


@pytest.mark.parametrize(
    ("target", "target_mode_attribute"),
    [
        ("https://example.test/linked-template.dotx", ' TargetMode="External"'),
        ("file:///tmp/linked-object.xlsx", ' TargetMode="External"'),
        ("https://example.test/relationship-without-mode", ""),
    ],
)
def test_libreoffice_rejects_external_ooxml_relationships_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    target_mode_attribute: str,
) -> None:
    executable = tmp_path / "soffice"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)
    source_path = tmp_path / "external-relationship.docx"
    with ZipFile(source_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="linked-resource" Target="{target}"'
            f"{target_mode_attribute}/></Relationships>",
        )

    def unexpected_subprocess(*_args: object, **_kwargs: object) -> object:
        pytest.fail("LibreOffice subprocess must not start for an external relationship")

    monkeypatch.setattr(
        "app.services.source_visual_libreoffice.subprocess.run",
        unexpected_subprocess,
    )

    with pytest.raises(LibreOfficeRenderError, match="external relationship"):
        LibreOfficeRenderer(str(executable)).render_pdf(
            source_path,
            output_dir=tmp_path / "rendered",
        )

    assert not (tmp_path / "rendered").exists()


def test_libreoffice_allows_internal_ooxml_relationships_with_restricted_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "soffice"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)
    source_path = tmp_path / "internal-relationship.docx"
    with ZipFile(source_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/media/image1.png", _png_bytes())
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="image" Target="media/image1.png" '
            'TargetMode="Internal"/></Relationships>',
        )
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test")
    monkeypatch.setenv("https_proxy", "http://proxy.example.test")
    captured: dict[str, object] = {}

    class CompletedConversion:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_subprocess(command: list[str], **kwargs: object) -> CompletedConversion:
        captured["command"] = command
        captured.update(kwargs)
        (tmp_path / "rendered" / "internal-relationship.pdf").write_bytes(b"%PDF-1.7\n")
        return CompletedConversion()

    monkeypatch.setattr(
        "app.services.source_visual_libreoffice.subprocess.run",
        fake_subprocess,
    )

    output_path = LibreOfficeRenderer(str(executable)).render_pdf(
        source_path,
        output_dir=tmp_path / "rendered",
    )

    assert output_path == tmp_path / "rendered" / "internal-relationship.pdf"
    command = captured["command"]
    assert isinstance(command, list)
    assert {
        "--headless",
        "--invisible",
        "--nologo",
        "--nofirststartwizard",
    }.issubset(command)
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert not {key.casefold() for key in environment} & {
        "all_proxy",
        "ftp_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    expected_home = (tmp_path / "rendered" / ".libreoffice-profile").resolve()
    assert environment["HOME"] == str(expected_home)
    assert captured["cwd"] == tmp_path / "rendered"


def test_app_startup_rejects_explicit_invalid_libreoffice_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_LIBREOFFICE_PATH", str(tmp_path / "missing-soffice"))

    with pytest.raises(LibreOfficeRenderError, match="OPENCLASS_LIBREOFFICE_PATH"):
        with TestClient(main_module.app):
            pass


def test_existing_structure_schema_migrates_visual_index_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE source_structures (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                source_ingestion_id TEXT NOT NULL,
                status TEXT NOT NULL,
                strategy TEXT NOT NULL,
                has_verified_toc INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                confidence REAL NOT NULL,
                error TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_structures VALUES (
                'structure_old', 'user_visual', 'package_visual', 'source_old',
                'linear_only', 'linear_text', 0, 0, 0, 0.0, '', '[]',
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '{}'
            )
            """
        )
    store = SourceStructureStore(database_path)

    structure = store.get_structure(
        owner_user_id="user_visual",
        package_id="package_visual",
        source_id="source_old",
    )

    assert structure is not None
    assert structure.visual_count == 0
    assert structure.visual_index_status == "pending"
    assert structure.visual_index_version == 0


def test_legacy_visual_index_upgrade_preserves_existing_chapter_and_chunk_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    visual_uri = _png_data_uri()
    path = tmp_path / "legacy.html"
    path.write_text(
        '<h1>Stable chapter</h1><p>Stable searchable text.</p>'
        f'<img src="{visual_uri}" alt="Stable visual"/>',
        encoding="utf-8",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    record = _record(path, mime_type="text/html")
    indexer = SourceStructureIndexer(store=store)
    indexer.rebuild_structure(record)
    before = store.get_structure_view(source=record)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "DELETE FROM source_visuals WHERE source_ingestion_id = ?",
            (record.id,),
        )
        connection.execute(
            """
            UPDATE source_structures
            SET visual_count = 0, visual_index_status = 'pending', visual_index_version = 0
            WHERE source_ingestion_id = ?
            """,
            (record.id,),
        )

    upgraded = indexer.ensure_structure(record)
    after = store.get_structure_view(source=record)

    assert upgraded is not None and upgraded.visual_index_version == 1
    assert upgraded.visual_index_status == "ready"
    assert [chapter.id for chapter in after.chapters] == [chapter.id for chapter in before.chapters]
    assert [chunk.id for chunk in after.chunks] == [chunk.id for chunk in before.chunks]
    assert len(after.visuals) == 1


def test_native_office_chart_without_renderer_is_partial_not_guessed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    path = tmp_path / "chart.pptx"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a" xmlns:c="c" xmlns:r="r"><p:cSld><p:spTree>'
            '<p:sp><p:txBody><a:p><a:r><a:t>Slide title</a:t></a:r></a:p></p:txBody></p:sp>'
            '<p:graphicFrame><p:xfrm><a:off x="100" y="100"/><a:ext cx="600" cy="400"/></p:xfrm>'
            '<a:graphic><a:graphicData><c:chart r:id="rId1"/></a:graphicData></a:graphic>'
            '</p:graphicFrame></p:spTree></p:cSld></p:sld>',
        )
    record = _record(
        path,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    extractor = SourceVisualExtractor(
        office_renderer=LibreOfficeRenderer(str(tmp_path / "missing-soffice"))
    )

    structure = SourceStructureIndexer(store=store, visual_extractor=extractor).rebuild_structure(record)

    assert structure.visual_index_status == "partial"
    assert structure.visual_count == 0
    assert any("OPENCLASS_LIBREOFFICE_PATH" in warning for warning in structure.warnings)


def test_office_chart_page_match_without_bbox_overlap_stays_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRenderer:
        available = True

        def render_pdf(self, _source_path: Path, *, output_dir: Path) -> Path:
            output = output_dir / "rendered.pdf"
            output.write_bytes(b"%PDF-1.4")
            return output

    monkeypatch.setattr(
        extraction_module,
        "extract_pdf_visuals",
        lambda _path: SourceVisualAdapterResult(
            visuals=[
                RawSourceVisual(
                    kind="diagram",
                    source_locator="pdf:page:1:vector:0",
                    native_order=0,
                    page_no=1,
                    bbox=[0.7, 0.7, 0.9, 0.9],
                    content=_png_bytes(),
                    mime_type="image/png",
                )
            ]
        ),
    )
    extractor = SourceVisualExtractor(office_renderer=FakeRenderer())  # type: ignore[arg-type]
    source_path = tmp_path / "chart.pptx"
    source_path.write_bytes(b"not read by fake renderer")
    anchor = RawSourceVisual(
        kind="chart",
        source_locator="pptx:slide:1:native-chart:0:0",
        native_order=0,
        page_no=1,
        slide_no=1,
        bbox=[0.1, 0.1, 0.3, 0.3],
    )

    result = extractor._render_native_office_visuals(source_path=source_path, anchors=[anchor])

    assert len(result.visuals) == 1
    assert result.visuals[0].metadata["office_anchor_mapping_verified"] is False
    assert result.visuals[0].metadata["force_unverified"] is True


def test_source_visual_api_lists_and_reads_only_owner_assets(
    source_api_client: tuple[TestClient, UserView],
) -> None:
    client, _user = source_api_client
    created = client.post("/api/packages", json={"title": "Visual package", "summary": ""})
    assert created.status_code == 200
    package_id = created.json()["active_package_id"]
    content = _png_bytes()
    imported = client.post(
        f"/api/packages/{package_id}/sources",
        files={"file": ("visual.png", content, "image/png")},
    )
    assert imported.status_code == 200
    source_id = imported.json()["id"]

    listed = client.get(f"/api/packages/{package_id}/sources/{source_id}/visuals")
    assert listed.status_code == 200
    visual = listed.json()[0]
    asset = client.get(
        f"/api/packages/{package_id}/sources/{source_id}/visuals/{visual['id']}/asset"
    )
    assert asset.status_code == 200
    assert asset.content == content
    assert asset.headers["content-type"] == "image/png"
    assert asset.headers["x-content-type-options"] == "nosniff"

    other_user = UserView(
        id="user_visual_other",
        email="other@example.com",
        role="user",
        created_at="2026-01-01T00:00:00+00:00",
    )
    main_module.app.dependency_overrides[auth_router.current_user] = lambda: other_user
    denied = client.get(f"/api/packages/{package_id}/sources/{source_id}/visuals")
    assert denied.status_code == 404
    denied_asset = client.get(
        f"/api/packages/{package_id}/sources/{source_id}/visuals/{visual['id']}/asset"
    )
    assert denied_asset.status_code == 404


def test_source_upload_stream_is_rejected_at_configured_byte_limit(
    source_api_client: tuple[TestClient, UserView],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user = source_api_client
    created = client.post("/api/packages", json={"title": "Upload limit", "summary": ""})
    assert created.status_code == 200
    package_id = created.json()["active_package_id"]
    monkeypatch.setattr(sources_router, "MAX_SOURCE_UPLOAD_BYTES", 8)

    response = client.post(
        f"/api/packages/{package_id}/sources",
        files={"file": ("oversized.txt", b"0123456789", "text/plain")},
    )

    assert response.status_code == 413
    listed = client.get(f"/api/packages/{package_id}/sources")
    assert listed.status_code == 200
    assert listed.json() == []
