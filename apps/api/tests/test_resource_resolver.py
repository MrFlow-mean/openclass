from pathlib import Path

import pytest

from app.models import (
    BoardTaskRequirementSheet,
    ChatRequest,
    SourceChapter,
    SourceIngestionRecord,
    SourceStructure,
)
from app.services import source_chapter_evidence as source_chapter_evidence_module
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BlankBoardRequirementRefinement, ChatbotReply, openai_course_ai
from app.services.resource_resolver import ResourceResolver, resource_resolver
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


class _FakeSearchAdapter:
    def search(self, *, notebook_id: str, query: str, limit: int, source_ids: list[str]):
        assert notebook_id == "nb_1"
        assert "补写" in query
        assert source_ids == ["src_1"]
        return [
            {
                "source_id": "src_1",
                "chunk_id": "chunk_a",
                "title": "资料 A",
                "url": "https://example.com/a",
                "section_path": ["第一章", "概念"],
                "page": 3,
                "text": "第一段命中内容。",
                "expanded_text": "第一段命中内容，包含上下文。",
                "score": 0.92,
            },
            {
                "source_id": "src_1",
                "chunk_id": "chunk_a",
                "title": "资料 A",
                "text": "第一段命中内容。",
                "expanded_text": "重复结果应该去重。",
                "score": 0.91,
            },
            {
                "source_id": "src_1",
                "chunk_id": "chunk_b",
                "title": "资料 A",
                "text": "第二段命中内容。",
                "expanded_text": "第二段命中内容，继续提供上下文。",
                "score": 0.81,
            },
            {
                "source_id": "src_other",
                "chunk_id": "chunk_other",
                "title": "其他课程包资料",
                "text": "不应该进入本课程包的证据包。",
                "expanded_text": "不应该进入本课程包的证据包。",
                "score": 0.99,
            },
        ]


def _save_outline_only_source(
    *,
    tmp_path: Path,
    store: SourceEvidenceStore,
    structure_store: SourceStructureStore,
    package_id: str,
    title: str,
    chapter_title: str,
) -> tuple[SourceIngestionRecord, SourceChapter]:
    local_path = tmp_path / f"{title}.pdf"
    local_path.write_bytes(b"outline-only scanned pdf placeholder")
    source = SourceIngestionRecord(
        owner_user_id="user_1",
        package_id=package_id,
        title=title,
        source_type="local_file",
        file_name=local_path.name,
        mime_type="application/pdf",
        status="ready",
        metadata={"local_source_path": str(local_path), "adapter": "openclass_local"},
    )
    chapter = SourceChapter(
        owner_user_id="user_1",
        package_id=package_id,
        source_ingestion_id=source.id,
        title=chapter_title,
        path=[chapter_title],
        page_start=9,
        page_end=20,
        anchor_status="verified",
        confidence=0.93,
        source_locator="pdf:outline:9",
    )
    store.save_source(source)
    structure_store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id="user_1",
            package_id=package_id,
            source_ingestion_id=source.id,
            status="ready",
            strategy="pdf_outline",
            has_verified_toc=True,
            confidence=0.93,
        ),
        chapters=[chapter],
        chunks=[],
    )
    return source, chapter


def _seed_empty_lesson(store: SqliteCourseStore, *, user_id: str):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("资料学习页")
    lesson.learning_requirements = None
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)
    return package, lesson


def test_resource_resolver_builds_candidate_evidence_bundle(tmp_path) -> None:
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    store.upsert_notebook(owner_user_id="user_1", package_id="pkg_1", notebook_id="nb_1", title="资料容器")
    store.save_source(
        SourceIngestionRecord(
            owner_user_id="user_1",
            package_id="pkg_1",
            title="资料 A",
            source_type="web_url",
            source_uri="https://example.com/a",
            status="ready",
            open_notebook_notebook_id="nb_1",
            open_notebook_source_id="src_1",
        )
    )
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="结合上传资料补写第一节。",
        board_task=BoardTaskRequirementSheet(
            location_kind="insertion_anchor",
            target_hint="第一节",
            requested_action="write",
            question_or_topic="补写资料中的概念",
            progress=100,
        ),
        board_task_run_id="task_run_1",
        purpose="board_edit",
    )

    assert bundle is not None
    assert bundle.status == "candidate"
    assert bundle.purpose == "board_edit"
    assert bundle.board_task_run_id == "task_run_1"
    assert [item.chunk_ids for item in bundle.evidence_items] == [["chunk_a"], ["chunk_b"]]
    assert bundle.evidence_items[0].source_ingestion_id
    assert "第一章" in bundle.context_text
    assert bundle.token_count > 0


def test_resource_resolver_returns_none_without_ready_sources(tmp_path) -> None:
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="结合上传资料补写。",
        board_task=BoardTaskRequirementSheet(requested_action="write", question_or_topic="补写", progress=100),
        purpose="board_edit",
    )

    assert bundle is None


def test_resource_resolver_detects_video_source_intent(tmp_path) -> None:
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=SourceEvidenceStore(tmp_path / "openclass.sqlite3"))

    assert resolver.should_use_sources("结合这个 YouTube 视频讲一下")
    assert resolver.should_use_sources("根据视频字幕解释这一段")


def test_resource_resolver_falls_back_to_local_chunk_index(tmp_path) -> None:
    local_path = tmp_path / "local.md"
    local_path.write_text("# Cache Policy\n\nLocal cache policy explains write back behavior.", encoding="utf-8")
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    structure_store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    source = SourceIngestionRecord(
        owner_user_id="user_1",
        package_id="pkg_1",
        title="Local source",
        source_type="local_file",
        file_name="local.md",
        mime_type="text/markdown",
        status="ready",
        metadata={"local_source_path": str(local_path), "adapter": "openclass_local"},
    )
    store.save_source(source)
    SourceStructureIndexer(store=structure_store).rebuild_structure(source)
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_for_board_task(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="结合上传资料讲 cache policy",
        board_task=BoardTaskRequirementSheet(requested_action="explain", question_or_topic="cache policy", progress=100),
        purpose="board_explain",
    )

    assert bundle is not None
    assert bundle.metadata["retrieval_mode"] == "local_chunk_search"
    assert bundle.evidence_items[0].source_ingestion_id == source.id
    assert bundle.evidence_items[0].open_notebook_source_id == ""
    assert "write back behavior" in bundle.evidence_items[0].expanded_text


def test_explicit_chapter_locator_uses_unique_source_title_and_scan_ocr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    selected_source, selected_chapter = _save_outline_only_source(
        tmp_path=tmp_path,
        store=store,
        structure_store=structure_store,
        package_id="pkg_1",
        title="通用学习手册.pdf",
        chapter_title="第四章 核心方法",
    )
    _save_outline_only_source(
        tmp_path=tmp_path,
        store=store,
        structure_store=structure_store,
        package_id="pkg_1",
        title="通用实践手册.pdf",
        chapter_title="第四章 实践方法",
    )
    ocr_calls: list[dict[str, object]] = []

    def _fake_ocr(path: Path, **kwargs) -> str:
        ocr_calls.append({"path": path, **kwargs})
        return "第四章 核心方法\n这是扫描页 OCR 得到的真实章节正文。"

    monkeypatch.setattr(source_chapter_evidence_module, "extract_pdf_pages_text", _fake_ocr)
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_explicit_source_reference(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="我想学通用学习手册的第四章",
        purpose="chat",
    )

    assert bundle is not None
    assert bundle.evidence_items[0].source_ingestion_id == selected_source.id
    assert bundle.evidence_items[0].chapter_id == selected_chapter.id
    assert bundle.evidence_items[0].metadata["retrieval_mode"] == "verified_chapter_ocr"
    assert "真实章节正文" in bundle.context_text
    assert bundle.metadata["source_reference_resolution"]["matched_rules"] == [
        "source_title_and_chapter_number"
    ]
    assert bundle.metadata["source_reference_resolution"]["body_retrieval"] == "macos_vision_ocr"
    assert ocr_calls == [
        {
            "path": Path(selected_source.metadata["local_source_path"]),
            "page_start": 9,
            "page_end": 12,
            "max_pages": 4,
        }
    ]


def test_chapter_locator_does_not_choose_between_ambiguous_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    for title in ("通用学习手册.pdf", "通用实践手册.pdf"):
        _save_outline_only_source(
            tmp_path=tmp_path,
            store=store,
            structure_store=structure_store,
            package_id="pkg_1",
            title=title,
            chapter_title="第四章 核心方法",
        )
    monkeypatch.setattr(
        source_chapter_evidence_module,
        "extract_pdf_pages_text",
        lambda *args, **kwargs: pytest.fail("ambiguous chapter locator must not OCR an arbitrary source"),
    )
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store, structure_store=structure_store)

    bundle = resolver.resolve_explicit_source_reference(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="我想学第四章",
        purpose="chat",
    )

    assert bundle is None


def test_non_chapter_ordinal_does_not_trigger_source_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = SourceEvidenceStore(tmp_path / "openclass.sqlite3")
    resolver = ResourceResolver(adapter=_FakeSearchAdapter(), store=store)
    monkeypatch.setattr(
        source_chapter_evidence_module,
        "extract_pdf_pages_text",
        lambda *args, **kwargs: pytest.fail("ordinary ordinal must not trigger OCR"),
    )

    bundle = resolver.resolve_explicit_source_reference(
        owner_user_id="user_1",
        package_id="pkg_1",
        lesson_id="lesson_1",
        user_message="这是第四次练习，先正常聊聊。",
        purpose="chat",
    )

    assert bundle is None


def test_blank_requirement_discovers_matched_source_before_visible_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    course_store = SqliteCourseStore(database_path, legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", course_store)
    package, lesson = _seed_empty_lesson(course_store, user_id="user_1")
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source, chapter = _save_outline_only_source(
        tmp_path=tmp_path,
        store=source_store,
        structure_store=structure_store,
        package_id=package.id,
        title="通用学习手册.pdf",
        chapter_title="第四章 核心方法",
    )
    monkeypatch.setattr(resource_resolver, "store", source_store)
    monkeypatch.setattr(resource_resolver, "structure_store", structure_store)
    monkeypatch.setattr(
        source_chapter_evidence_module,
        "extract_pdf_pages_text",
        lambda *args, **kwargs: "第四章 核心方法\n这是需求收敛前读取到的扫描正文。",
    )
    captured: dict[str, dict[str, object]] = {}

    def _fake_refinement(**kwargs):
        captured["refinement"] = kwargs
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="我已找到对应资料章节，接下来从章内核心内容选择起点。",
            progress=60,
            summary="已定位资料章节，仍需收敛章内学习起点。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="通用学习手册第四章",
            next_question="你想先从本章哪个核心内容开始？",
            ready_for_board=False,
        )

    def _fake_discovery_reply(**kwargs):
        captured["discovery_reply"] = kwargs
        return ChatbotReply(chatbot_message="我找到了资料中的第四章“核心方法”，先从章内选择一个起点。")

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_learning_source_discovery_reply", _fake_discovery_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学通用学习手册的第四章"),
        user_id="user_1",
    )

    assert captured["refinement"]["include_stream_result"] is False
    assert captured["discovery_reply"]["discovery_status"] == "matched"
    assert "需求收敛前读取到的扫描正文" in str(captured["discovery_reply"]["evidence_references"])
    assert response.chatbot_message == "我找到了资料中的第四章“核心方法”，先从章内选择一个起点。"
    assert response.candidate_evidence_bundle is not None
    assert response.candidate_evidence_bundle.evidence_items[0].source_ingestion_id == source.id
    assert response.candidate_evidence_bundle.evidence_items[0].chapter_id == chapter.id
    commit = course_store.load_for_user("user_1").packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["learning_source_discovery"]["status"] == "matched"
    resolution = commit.metadata["source_reference_resolution"]
    assert resolution["selected_action"] == "resolve_source_chapter"
    assert resolution["role_executed"] == "resource_resolver"
    assert resolution["document_changed"] is False
