from __future__ import annotations

from app.models import CoursePackage, LibraryChapter, ResourceLibraryItem, ResourceSourceUnit, WorkspaceState
from app.services.course_store import SqliteCourseStore
from app.services.resource_page_structure import (
    build_page_structure_from_texts,
    enrich_source_units_with_page_structure,
    physical_page_candidates_for_printed_page,
)


def test_page_structure_maps_printed_page_from_body_start() -> None:
    pages = [
        "资料标题",
        "版权\nISBN 978-0-00-000000-0",
        "目录\n第一章 入门 1\n第四章 深入主题 139",
        "前言\n本书说明。",
        "前言\n阅读建议。",
        "第一章 入门\n正文第一页。",
    ]

    structure = build_page_structure_from_texts(pages, page_count=160)

    assert structure.body_start_page_no == 6
    assert structure.confidence >= 0.8
    assert physical_page_candidates_for_printed_page(structure, 139) == [144]
    mapped = next(entry for entry in structure.page_map if entry.printed_page == 139)
    assert mapped.page_idx == 143
    assert mapped.role == "body"


def test_page_structure_keeps_appendix_out_of_body_mapping() -> None:
    structure = build_page_structure_from_texts(
        [
            "目录\n第一章 正文 1",
            "第一章 正文\n关键定义。",
            "附录\n补充材料。",
            "参考文献\n资料来源。",
        ],
        page_count=4,
    )

    roles = [entry.role for entry in structure.page_map]

    assert roles == ["toc", "body", "appendix", "back_matter"]
    assert structure.page_map[1].printed_page == 1
    assert structure.page_map[2].printed_page is None
    assert structure.page_map[3].printed_page is None


def test_source_units_are_enriched_with_page_structure_metadata() -> None:
    structure = build_page_structure_from_texts(
        [
            "封面",
            "目录\n第一章 正文 1",
            "第一章 正文\n解析出的正文内容。",
        ],
        page_count=3,
    )
    units = [
        ResourceSourceUnit(content_type="text", text="封面", page_idx=0, page_no=1, order_index=0),
        ResourceSourceUnit(content_type="text", text="解析出的正文内容", page_idx=2, page_no=3, order_index=1),
    ]

    enriched = enrich_source_units_with_page_structure(units, structure)

    assert enriched[0].metadata["page_role"] == "cover"
    assert "printed_page" not in enriched[0].metadata
    assert enriched[1].metadata["page_role"] == "body"
    assert enriched[1].metadata["printed_page"] == 1
    assert enriched[1].metadata["body_start_page_no"] == 3


def test_sqlite_store_round_trips_resource_page_structure(tmp_path) -> None:
    structure = build_page_structure_from_texts(
        [
            "目录\n第一章 正文 1",
            "第一章 正文\n正文内容。",
        ],
        page_count=2,
    )
    chapter = LibraryChapter(
        title="第一章 正文",
        summary="正文章节。",
        keywords=["正文"],
        page_start=2,
        page_end=2,
    )
    workspace = WorkspaceState(
        packages=[
            CoursePackage(
                title="资料包",
                summary="",
                outline=[],
                lessons=[],
                resources=[
                    ResourceLibraryItem(
                        name="structured.pdf",
                        mime_type="application/pdf",
                        resource_type="document",
                        size_bytes=12,
                        outline=[chapter],
                        extracted_text_available=True,
                        page_structure=structure,
                    )
                ],
            )
        ]
    )
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)

    store.save(workspace)
    reloaded = store.load()

    restored = reloaded.packages[0].resources[0].page_structure
    assert restored is not None
    assert restored.body_start_page_no == 2
    assert restored.page_map[1].printed_page == 1
