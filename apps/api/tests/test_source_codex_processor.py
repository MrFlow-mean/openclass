from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import pytest

from app.models import SourceChapter, SourceIngestionRecord, SourceStructure
from app.services.ai_execution_adapter import StructuredExecutionResult
from app.services.codex_app_server import CodexAppServerError
from app.services.source_codex_processor import (
    SourceCatalogError,
    SourceCatalogPlan,
    SourceCodexProcessor,
    SourceShardResult,
)
from app.services.source_codex_models import SourceCatalogImagePage
from app.services.source_structure_store import SourceStructureStore


@dataclass
class Page:
    page_no: int
    text: str
    start_offset: int
    end_offset: int
    content_start_offset: int


class CatalogAdapter:
    def __init__(
        self,
        *,
        omit_candidate: bool = False,
        omit_worker_anchor: bool = False,
        afterword_page: int | None = None,
    ) -> None:
        self.omit_candidate = omit_candidate
        self.omit_worker_anchor = omit_worker_anchor
        self.afterword_page = afterword_page
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.image_input_counts: list[int] = []

    def parse_structured(self, *, user_prompt: str, schema, image_inputs=None, **_kwargs):
        payload = json.loads(user_prompt.split("\n", 1)[1])
        with self.lock:
            self.calls.append(schema.__name__)
            self.image_input_counts.append(len(image_inputs or []))
            call_number = len(self.calls)
        if schema is SourceCatalogPlan:
            candidates = payload["candidate_chapters"]
            if self.omit_candidate:
                candidates = candidates[:-1]
            nodes = [
                {
                    "local_key": f"node-{index}",
                    "candidate_id": candidate["candidate_id"],
                    "decision": "keep",
                    "parent_local_key": "",
                    "number": candidate["number"],
                    "title": candidate["title"],
                    "level": candidate["level"],
                    "order_index": index,
                    "body_heading": f"{candidate['number']} {candidate['title']}".strip(),
                    "body_page_hint": candidate["page_start"],
                    "confidence": 0.95,
                    "evidence_page_numbers": [candidate["page_start"]],
                }
                for index, candidate in enumerate(candidates)
            ]
            document_parts = [
                {
                    "kind": "body",
                    "title": "正文",
                    "page_start": 1,
                    "page_end_exclusive": self.afterword_page or payload["page_count"] + 1,
                    "confidence": 0.98,
                    "evidence_page_numbers": [1],
                }
            ]
            if self.afterword_page is not None:
                document_parts.append(
                    {
                        "kind": "afterword",
                        "title": "后记",
                        "page_start": self.afterword_page,
                        "page_end_exclusive": payload["page_count"] + 1,
                        "confidence": 0.98,
                        "evidence_page_numbers": [self.afterword_page],
                    }
                )
            return StructuredExecutionResult(
                output_parsed=SourceCatalogPlan.model_validate(
                    {
                        "input_manifest_hash": payload["input_manifest_hash"],
                        "document_parts": document_parts,
                        "directory_nodes": nodes,
                        "uncertainties": [],
                    }
                ),
                thread_id=f"thread-{call_number}",
                turn_id=f"turn-{call_number}",
                usage={"input_tokens": 10},
            )
        assert schema is SourceShardResult
        anchors = [
            {
                "directory_node_key": node["local_key"],
                "status": "located",
                "page_no": node["body_page_hint"],
                "heading_excerpt": node["body_heading"],
                "confidence": 0.94,
                "reason": "Exact heading is visible in the assigned shard.",
            }
            for node in payload["directory_nodes"]
        ]
        if self.omit_worker_anchor:
            anchors = anchors[:-1]
        return StructuredExecutionResult(
            output_parsed=SourceShardResult.model_validate(
                {
                    "shard_id": payload["shard_id"],
                    "plan_hash": payload["plan_hash"],
                    "input_hash": payload["input_hash"],
                    "anchors": anchors,
                    "unlisted_headings": [],
                    "warnings": [],
                }
            ),
            thread_id=f"thread-{call_number}",
            turn_id=f"turn-{call_number}",
        )


class FailedTurnAdapter(CatalogAdapter):
    def __init__(self, *, fail_role: str) -> None:
        super().__init__()
        self.fail_role = fail_role

    def parse_structured(self, *, schema, **kwargs):
        role = "coordinator" if schema is SourceCatalogPlan else "worker"
        if role == self.fail_role:
            raise CodexAppServerError(
                f"{role} turn failed",
                thread_id=f"failed-{role}-thread",
                turn_id=f"failed-{role}-turn",
            )
        return super().parse_structured(schema=schema, **kwargs)


def test_every_source_gets_an_auditable_codex_catalog_run(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    adapter = CatalogAdapter()
    processor = SourceCodexProcessor(adapter=adapter, model="codex-test", store=store.codex_store)
    text = "1 Start\nBody text\n"
    record = _record(content_hash="hash-small")
    chapter = _chapter(record, title="Start", number="1", start=0, end=len(text), page=1)

    result = processor.process(
        record=record,
        text=text,
        pages=[Page(1, text, 0, len(text), 0)],
        candidate_chapters=[chapter],
        parser_metadata={"parser": "text"},
        image_pages=[
            SourceCatalogImagePage(
                page_no=1,
                data_url="data:image/png;base64,YQ==",
                sha256="image-hash",
            )
        ],
    )
    structure = store.save_structure_bundle(
        structure=SourceStructure(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            status="ready",
            strategy="codex_catalog",
        ),
        parts=result.parts,
        chapters=result.chapters,
        chunks=[],
        processing_run=result.run,
    )
    completed = store.codex_store.latest_run(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    assert completed is not None
    assert completed.status == "ready"
    assert completed.published_structure_id == structure.id
    assert completed.coordinator_thread_id == "thread-1"
    assert completed.coordinator_turn_id == "turn-1"
    assert adapter.image_input_counts == [1]
    assert [part.kind for part in store.get_structure_view(source=record).parts] == ["body"]
    assert result.chapters[0].anchor_status == "verified"
    tasks = store.codex_store.tasks_for_run(result.run.id)
    assert [(task.role, task.status, task.accepted) for task in tasks] == [
        ("coordinator", "completed", True)
    ]


def test_structure_publish_rolls_back_when_codex_run_cannot_be_finalized(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceCodexProcessor(
        adapter=CatalogAdapter(),
        model="codex-test",
        store=store.codex_store,
    )
    text = "1 Start\nBody text\n"
    record = _record(content_hash="hash-atomic-rollback")
    chapter = _chapter(record, title="Start", number="1", start=0, end=len(text), page=1)
    result = processor.process(
        record=record,
        text=text,
        pages=[Page(1, text, 0, len(text), 0)],
        candidate_chapters=[chapter],
        parser_metadata={"parser": "text"},
    )
    store.codex_store.delete_for_source(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )

    with pytest.raises(RuntimeError, match="disappeared before atomic publication"):
        store.save_structure_bundle(
            structure=SourceStructure(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                status="ready",
                strategy="codex_catalog",
            ),
            parts=result.parts,
            chapters=result.chapters,
            chunks=[],
            processing_run=result.run,
        )

    assert store.get_structure(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    ) is None


def test_last_chapter_is_clamped_to_its_document_part(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceCodexProcessor(
        adapter=CatalogAdapter(afterword_page=6),
        model="codex-test",
        store=store.codex_store,
    )
    page_texts = [
        "Title\n",
        "Contents\n",
        "Front matter\n",
        "1 Foundations\nBody\n",
        "1.1 Evidence\nBody\n",
        "Afterword\nClosing text\n",
    ]
    pages: list[Page] = []
    pieces: list[str] = []
    offset = 0
    for page_no, page_text in enumerate(page_texts, start=1):
        pieces.append(page_text)
        pages.append(Page(page_no, page_text, offset, offset + len(page_text), offset))
        offset += len(page_text)
    text = "".join(pieces)
    record = _record(content_hash="hash-part-clamp")
    chapters = [
        _chapter(record, title="Foundations", number="1", start=pages[3].start_offset, end=len(text), page=4),
        _chapter(record, title="Evidence", number="1.1", start=pages[4].start_offset, end=len(text), page=5),
    ]

    result = processor.process(
        record=record,
        text=text,
        pages=pages,
        candidate_chapters=chapters,
        parser_metadata={"parser": "pdf", "page_count": 6},
    )

    assert [chapter.page_end for chapter in result.chapters] == [6, 6]
    assert all("Afterword" not in chapter.excerpt for chapter in result.chapters)


def test_large_source_uses_disjoint_parallel_codex_workers(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    adapter = CatalogAdapter()
    processor = SourceCodexProcessor(
        adapter=adapter,
        model="codex-test",
        store=store.codex_store,
        max_workers=3,
    )
    pages: list[Page] = []
    pieces: list[str] = []
    offset = 0
    for page_no in range(1, 121):
        page_text = f"{page_no} Heading {page_no}\n" + ("body " * 1_500)
        pieces.append(page_text)
        pages.append(Page(page_no, page_text, offset, offset + len(page_text), offset))
        offset += len(page_text)
    text = "".join(pieces)
    record = _record(content_hash="hash-large")
    chapters = [
        _chapter(record, title=f"Heading {page}", number=str(page), start=pages[page - 1].start_offset, end=len(text), page=page)
        for page in (1, 61, 120)
    ]

    result = processor.process(
        record=record,
        text=text,
        pages=pages,
        candidate_chapters=chapters,
        parser_metadata={"parser": "pdf", "page_count": 120},
    )

    tasks = [task for task in store.codex_store.tasks_for_run(result.run.id) if task.role == "worker"]
    assert len(tasks) >= 2
    assert all(task.status == "completed" and task.thread_id for task in tasks)
    ranges = sorted((task.page_start, task.page_end) for task in tasks)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))
    assert result.run.worker_count == len(tasks)
    assert result.run.completed_worker_count == len(tasks)
    assert len([name for name in adapter.calls if name == "SourceShardResult"]) == len(tasks)


def test_codex_catalog_rejects_incomplete_candidate_decisions(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceCodexProcessor(
        adapter=CatalogAdapter(omit_candidate=True),
        model="codex-test",
        store=store.codex_store,
    )
    text = "1 First\nBody\n2 Second\nBody"
    record = _record(content_hash="hash-invalid")
    chapters = [
        _chapter(record, title="First", number="1", start=0, end=15, page=1),
        _chapter(record, title="Second", number="2", start=15, end=len(text), page=1),
    ]

    with pytest.raises(SourceCatalogError, match="Codex 资料编目失败"):
        processor.process(
            record=record,
            text=text,
            pages=[Page(1, text, 0, len(text), 0)],
            candidate_chapters=chapters,
            parser_metadata={"parser": "text"},
        )

    run = store.codex_store.latest_run(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert run is not None
    assert run.status == "retryable_failed"
    assert "did not decide every candidate chapter" in run.error
    [coordinator_task] = store.codex_store.tasks_for_run(run.id)
    assert coordinator_task.status == "failed"
    assert coordinator_task.thread_id == "thread-1"
    assert coordinator_task.turn_id == "turn-1"


def test_worker_must_decide_every_assigned_directory_node(tmp_path) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceCodexProcessor(
        adapter=CatalogAdapter(omit_worker_anchor=True),
        model="codex-test",
        store=store.codex_store,
    )
    pages: list[Page] = []
    pieces: list[str] = []
    offset = 0
    for page_no in range(1, 81):
        page_text = f"{page_no} Heading {page_no}\nbody\n"
        pieces.append(page_text)
        pages.append(Page(page_no, page_text, offset, offset + len(page_text), offset))
        offset += len(page_text)
    text = "".join(pieces)
    record = _record(content_hash="hash-worker-incomplete")
    chapter = _chapter(record, title="Heading 1", number="1", start=0, end=len(text), page=1)

    with pytest.raises(SourceCatalogError, match="did not decide every assigned node"):
        processor.process(
            record=record,
            text=text,
            pages=pages,
            candidate_chapters=[chapter],
            parser_metadata={"parser": "pdf", "page_count": 80},
        )

    run = store.codex_store.latest_run(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert run is not None
    assert run.status == "retryable_failed"
    failed_workers = [
        task
        for task in store.codex_store.tasks_for_run(run.id)
        if task.role == "worker" and task.status == "failed"
    ]
    assert failed_workers
    assert all(task.thread_id and task.turn_id for task in failed_workers)


@pytest.mark.parametrize("fail_role", ["coordinator", "worker"])
def test_failed_codex_turn_identity_is_persisted(tmp_path, fail_role: str) -> None:
    store = SourceStructureStore(tmp_path / "openclass.sqlite3")
    processor = SourceCodexProcessor(
        adapter=FailedTurnAdapter(fail_role=fail_role),
        model="codex-test",
        store=store.codex_store,
    )
    pages: list[Page] = []
    pieces: list[str] = []
    offset = 0
    page_total = 80 if fail_role == "worker" else 1
    for page_no in range(1, page_total + 1):
        page_text = f"{page_no} Heading {page_no}\nbody\n"
        pieces.append(page_text)
        pages.append(Page(page_no, page_text, offset, offset + len(page_text), offset))
        offset += len(page_text)
    text = "".join(pieces)
    record = _record(content_hash=f"hash-failed-{fail_role}")
    chapter = _chapter(
        record,
        title="Heading 1",
        number="1",
        start=0,
        end=len(text),
        page=1,
    )

    with pytest.raises(SourceCatalogError, match=f"{fail_role} turn failed"):
        processor.process(
            record=record,
            text=text,
            pages=pages,
            candidate_chapters=[chapter],
            parser_metadata={"parser": "pdf", "page_count": page_total},
        )

    run = store.codex_store.latest_run(
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_id=record.id,
    )
    assert run is not None
    failed_tasks = [
        task
        for task in store.codex_store.tasks_for_run(run.id)
        if task.role == fail_role and task.status == "failed"
    ]
    assert failed_tasks
    assert all(task.thread_id == f"failed-{fail_role}-thread" for task in failed_tasks)
    assert all(task.turn_id == f"failed-{fail_role}-turn" for task in failed_tasks)


def _record(*, content_hash: str) -> SourceIngestionRecord:
    return SourceIngestionRecord(
        id=f"source-{content_hash}",
        owner_user_id="user-1",
        package_id="package-1",
        title="General source",
        file_name="source.pdf",
        mime_type="application/pdf",
        status="parsing",
        metadata={"content_hash": content_hash},
    )


def _chapter(
    record: SourceIngestionRecord,
    *,
    title: str,
    number: str,
    start: int,
    end: int,
    page: int,
) -> SourceChapter:
    return SourceChapter(
        id=f"chapter-{number}",
        owner_user_id=record.owner_user_id,
        package_id=record.package_id,
        source_ingestion_id=record.id,
        number=number,
        normalized_number=number,
        title=title,
        level=number.count(".") + 1,
        path=[title],
        order_index=int(number.split(".", 1)[0]),
        source_locator=f"page:{page}",
        body_start_offset=start,
        body_end_offset=end,
        page_start=page,
        page_end=page + 1,
        anchor_status="verified",
        confidence=0.95,
    )
