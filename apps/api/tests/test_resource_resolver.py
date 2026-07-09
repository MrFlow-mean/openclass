from app.models import BoardTaskRequirementSheet, SourceIngestionRecord
from app.services.resource_resolver import ResourceResolver
from app.services.source_evidence_store import SourceEvidenceStore


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
        ]


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
