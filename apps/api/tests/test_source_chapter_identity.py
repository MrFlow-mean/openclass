from app.models import SelectionRef, SourceChapter
from app.services.source_chapter_identity import rebind_stale_source_chapter_selection


def _chapter(
    *,
    chapter_id: str,
    number: str,
    title: str,
    path: list[str],
    page_start: int,
    page_end: int,
    excerpt: str = "",
) -> SourceChapter:
    return SourceChapter(
        id=chapter_id,
        package_id="package_1",
        source_ingestion_id="source_1",
        number=number,
        normalized_number=number,
        title=title,
        level=len(path),
        path=path,
        source_locator="pdf:outline:120",
        page_start=page_start,
        page_end=page_end,
        anchor_status="verified",
        confidence=0.93,
        excerpt=excerpt,
    )


def test_rebind_narrows_a_shared_locator_with_structural_anchors() -> None:
    chapters = [
        _chapter(
            chapter_id="chapter_4",
            number="4",
            title="第 4 章 通用主题",
            path=["第 4 章 通用主题"],
            page_start=120,
            page_end=149,
        ),
        _chapter(
            chapter_id="chapter_4_1",
            number="4.1",
            title="4.1 本章目标",
            path=["第 4 章 通用主题", "4.1 本章目标"],
            page_start=120,
            page_end=121,
        ),
        _chapter(
            chapter_id="chapter_4_2",
            number="4.2",
            title="4.2 核心方法",
            path=["第 4 章 通用主题", "4.2 核心方法"],
            page_start=120,
            page_end=125,
        ),
        _chapter(
            chapter_id="chapter_4_2_1",
            number="4.2.1",
            title="4.2.1 示例",
            path=["第 4 章 通用主题", "4.2 核心方法", "4.2.1 示例"],
            page_start=120,
            page_end=123,
        ),
    ]
    selection = SelectionRef(
        kind="source",
        excerpt="《通用资料》 · 第 4 章 通用主题 > 4.2 核心方法 · pp. 120-125",
        heading_path=["第 4 章 通用主题", "4.2 核心方法"],
        source_ingestion_id="source_1",
        source_title="通用资料",
        source_chapter_id="stale_chapter_id",
        source_chapter_number="4.2",
        source_chapter_title="4.2 核心方法",
        source_page_range="pp. 120-124",
        source_locator="pdf:outline:120",
        source_page_start=120,
        source_page_end=125,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=chapters,
    )

    assert rebound.chapter is not None
    assert rebound.chapter.id == "chapter_4_2"
    assert rebound.matched_anchors == (
        "source_locator",
        "chapter_number",
        "chapter_title",
        "heading_path",
        "page_bounds",
    )


def test_rebind_uses_content_relevance_after_structural_tie() -> None:
    chapters = [
        _chapter(
            chapter_id="chapter_a",
            number="2.1",
            title="2.1 通用方法",
            path=["第二部分", "2.1 通用方法"],
            page_start=120,
            page_end=122,
            excerpt="这一部分讨论观察、记录与分类。",
        ),
        _chapter(
            chapter_id="chapter_b",
            number="2.1",
            title="2.1 通用方法",
            path=["第二部分", "2.1 通用方法"],
            page_start=120,
            page_end=122,
            excerpt="这一部分讨论拆分任务、建立终止条件并逐步合并结果。",
        ),
    ]
    selection = SelectionRef(
        kind="source",
        excerpt="《通用资料》 · 第二部分 > 2.1 通用方法 · pp. 120-121",
        heading_path=["第二部分", "2.1 通用方法"],
        source_ingestion_id="source_1",
        source_title="通用资料",
        source_chapter_id="stale_chapter_id",
        source_chapter_number="2.1",
        source_chapter_title="2.1 通用方法",
        source_excerpt="拆分任务、建立终止条件并逐步合并结果",
        source_locator="pdf:outline:120",
        source_page_start=120,
        source_page_end=122,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=chapters,
    )

    assert rebound.chapter is not None
    assert rebound.chapter.id == "chapter_b"
    assert rebound.matched_anchors[-1] == "content_relevance"


def test_rebind_does_not_trust_a_unique_locator_over_stronger_structure() -> None:
    locator_chapter = _chapter(
        chapter_id="locator_chapter",
        number="1.1",
        title="1.1 旧位置",
        path=["第一部分", "1.1 旧位置"],
        page_start=120,
        page_end=121,
    )
    structural_chapter = _chapter(
        chapter_id="structural_chapter",
        number="2.1",
        title="2.1 当前位置",
        path=["第二部分", "2.1 当前位置"],
        page_start=130,
        page_end=132,
    ).model_copy(update={"source_locator": "pdf:outline:130"})
    selection = SelectionRef(
        kind="source",
        excerpt="《通用资料》 · 第二部分 > 2.1 当前位置",
        heading_path=["第二部分", "2.1 当前位置"],
        source_ingestion_id="source_1",
        source_title="通用资料",
        source_chapter_id="stale_chapter_id",
        source_chapter_number="2.1",
        source_chapter_title="2.1 当前位置",
        source_locator="pdf:outline:120",
        source_page_start=130,
        source_page_end=132,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=[locator_chapter, structural_chapter],
    )

    assert rebound.chapter is not None
    assert rebound.chapter.id == "structural_chapter"
    assert "source_locator" not in rebound.matched_anchors


def test_rebind_uses_a_single_page_boundary_as_an_anchor() -> None:
    target = _chapter(
        chapter_id="target",
        number="2.1",
        title="2.1 当前位置",
        path=["第二部分", "2.1 当前位置"],
        page_start=130,
        page_end=132,
    )
    selection = SelectionRef(
        kind="source",
        excerpt="2.1 当前位置",
        source_ingestion_id="source_1",
        source_chapter_id="stale_chapter_id",
        source_chapter_number="2.1",
        source_page_start=130,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=[target],
    )

    assert rebound.chapter is not None
    assert rebound.chapter.id == "target"
    assert rebound.matched_anchors == ("chapter_number", "page_bounds")


def test_rebind_keeps_ambiguous_candidates_when_content_does_not_separate_them() -> None:
    chapters = [
        _chapter(
            chapter_id="chapter_a",
            number="2.1",
            title="2.1 通用方法",
            path=["第二部分", "2.1 通用方法"],
            page_start=120,
            page_end=122,
            excerpt="相同的候选内容。",
        ),
        _chapter(
            chapter_id="chapter_b",
            number="2.1",
            title="2.1 通用方法",
            path=["第二部分", "2.1 通用方法"],
            page_start=120,
            page_end=122,
            excerpt="相同的候选内容。",
        ),
    ]
    selection = SelectionRef(
        kind="source",
        excerpt="《通用资料》 · 第二部分 > 2.1 通用方法",
        heading_path=["第二部分", "2.1 通用方法"],
        source_ingestion_id="source_1",
        source_title="通用资料",
        source_chapter_id="stale_chapter_id",
        source_chapter_number="2.1",
        source_chapter_title="2.1 通用方法",
        source_locator="pdf:outline:120",
        source_page_start=120,
        source_page_end=122,
    )

    rebound = rebind_stale_source_chapter_selection(
        selection=selection,
        source_ingestion_id="source_1",
        chapters=chapters,
    )

    assert rebound.chapter is None
    assert rebound.candidate_ids == ("chapter_a", "chapter_b")
