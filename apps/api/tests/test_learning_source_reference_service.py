from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    EvidenceBundle,
    LearningClarificationStatus,
    RetrievalEvidence,
    SourceChapter,
    SourceIngestionRecord,
    SourceVisual,
)
from app.services import blank_board_generation, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.confirmed_source_context import ConfirmedSourceContextError, load_confirmed_source_context
from app.services.learning_source_reference_service import (
    LearningSourceReferenceError,
    apply_evidence_confirmation,
    source_evidence_store,
)
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import BoardDocumentEditResult, ChatbotReply, openai_course_ai
from app.services.resource_resolver import ResourceResolver
from app.services.rich_document import build_document
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore
from app.services.source_visual_storage import persist_source_visual_asset


def _seed_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user_id = "user_source_confirmation"
    database_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(database_path, legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("资料确认页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id

    requirements = build_requirements("核心方法")
    requirements.learning_goal = "理解第四章的核心方法"
    requirements.work_mode = "knowledge_board"
    requirements.granularity = "single_knowledge_point"
    lesson.learning_requirements = requirements
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="学习目标已明确。",
        can_start=True,
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=user_id,
        lesson_id=lesson.id,
        state=None,
    )
    stamp = recorder.record_update(requirements=requirements, clarification=clarification)
    store.save_for_user_with_learning_requirement_history(
        user_id,
        workspace,
        learning_requirement_history_operations=recorder.operations,
    )

    source_path = tmp_path / "reference.md"
    source_path.write_text("# 第四章 核心方法\n\n这是用户确认后应交给板书编辑器的正文。", encoding="utf-8")
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source = SourceIngestionRecord(
        owner_user_id=user_id,
        package_id=package.id,
        title="通用参考资料",
        source_type="local_file",
        file_name=source_path.name,
        mime_type="text/markdown",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_store.save_source(source)
    SourceStructureIndexer(store=structure_store).rebuild_structure(source)
    view = structure_store.get_structure_view(source=source, chunk_limit=0)
    assert view.structure is not None
    chapter = view.chapters[0]
    evidence = structure_store.chapter_evidence_by_id(
        owner_user_id=user_id,
        package_id=package.id,
        chapter_id=chapter.id,
        limit=8,
        token_budget=6000,
    )
    bundle = source_store.save_bundle(
        EvidenceBundle(
            owner_user_id=user_id,
            package_id=package.id,
            lesson_id=lesson.id,
            requirement_run_id=stamp.run_id,
            purpose="board_generation",
            status="candidate",
            query="学习第四章核心方法",
            evidence_items=evidence,
            context_text="正文证据上下文",
            token_count=sum(item.token_count for item in evidence),
            metadata={
                "source_structure_snapshots": {
                    source.id: {
                        "structure_id": view.structure.id,
                        "structure_updated_at": view.structure.updated_at,
                        "visual_index_version": view.structure.visual_index_version,
                    }
                },
                "visual_manifest_hash": "",
                "visual_count": 0,
            },
        )
    )
    return store, user_id, lesson.id, source, chapter, bundle


def _seed_visual_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, user_id, lesson_id, source, chapter, original_bundle = _seed_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    structure_store = SourceStructureStore(store.path)
    view = structure_store.get_structure_view(source=source, chunk_limit=20)
    assert view.structure is not None
    assert view.chunks
    image_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0"
        b"\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    storage_key, content_hash = persist_source_visual_asset(image_bytes, mime_type="image/png")
    visual = SourceVisual(
        owner_user_id=user_id,
        package_id=source.package_id,
        source_ingestion_id=source.id,
        structure_id=view.structure.id,
        structure_version=1,
        chapter_id=chapter.id,
        kind="chart",
        order_index=1,
        source_locator="markdown:block:2",
        page_no=1,
        bbox=[0.1, 0.2, 0.8, 0.6],
        before_chunk_id=view.chunks[0].id,
        after_chunk_id=view.chunks[0].id,
        caption="资料中的原始图表",
        anchor_status="verified",
        confidence=0.99,
        storage_key=storage_key,
        mime_type="image/png",
        content_hash=content_hash,
        position_hash=hashlib.sha256(b"markdown:block:2|chunk").hexdigest(),
        width=1,
        height=1,
    )
    structure_store.save_structure_bundle(
        structure=view.structure.model_copy(
            update={"visual_index_status": "ready", "visual_index_version": 1}
        ),
        chapters=view.chapters,
        chunks=view.chunks,
        visuals=[visual],
    )
    evidence = structure_store.chapter_evidence_by_id(
        owner_user_id=user_id,
        package_id=source.package_id,
        chapter_id=chapter.id,
        limit=8,
        token_budget=6000,
    )
    resolver = ResourceResolver(
        store=SourceEvidenceStore(store.path),
        structure_store=structure_store,
    )
    bundle = resolver._save_bundle(
        owner_user_id=user_id,
        package_id=source.package_id,
        lesson_id=lesson_id,
        query="使用已验证资料生成板书",
        purpose="board_generation",
        evidence=evidence,
        requirement_run_id=original_bundle.requirement_run_id,
    )
    assert len(bundle.visual_items) == 1
    return store, user_id, lesson_id, source, chapter, bundle, visual, structure_store


def test_confirmed_evidence_is_written_to_new_requirement_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, source, chapter, bundle = _seed_candidate(tmp_path, monkeypatch)

    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )

    assert result.evidence_bundle.status == "confirmed"
    assert result.active_requirement_sheet is not None
    grounding = result.active_requirement_sheet.source_grounding
    assert grounding.confirmation_status == "confirmed"
    assert grounding.confirmed_bundle_id == bundle.id
    assert grounding.confirmed_references[0].source_ingestion_id == source.id
    assert grounding.confirmed_references[0].source_chapter_id == chapter.id
    assert grounding.confirmed_references[0].content_hash

    versions = store.list_learning_requirement_versions(user_id, lesson_id)
    assert [version["change_kind"] for version in versions] == ["completed", "source_reference_confirmed"]
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[-1]
    assert saved_lesson.learning_requirements is not None
    assert saved_lesson.learning_requirements.source_grounding.confirmed_bundle_id == bundle.id
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "source_reference_confirmed"
    assert commit.metadata["chapter_ids"] == [chapter.id]


def test_confirmation_freezes_visual_manifest_and_generation_rehydrates_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, source, _chapter, bundle, visual, _structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )

    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )

    assert result.active_requirement_sheet is not None
    references = result.active_requirement_sheet.source_grounding.confirmed_references
    frozen_visuals = [item for reference in references for item in reference.visual_references]
    assert [item.visual_id for item in frozen_visuals] == [visual.id]
    assert frozen_visuals[0].asset_hash == visual.content_hash
    assert frozen_visuals[0].anchor_hash == visual.position_hash
    assert all(reference.source_visual_index_version == 1 for reference in references)
    assert any(reference.visual_manifest_hash for reference in references)

    context = load_confirmed_source_context(
        owner_user_id=user_id,
        package_id=source.package_id,
        lesson_id=lesson_id,
        requirement_run_id=bundle.requirement_run_id,
        requirements=result.active_requirement_sheet,
    )
    assert [item.visual_id for item in context.visual_items] == [visual.id]
    persisted = SourceEvidenceStore(store.path).get_bundle(owner_user_id=user_id, bundle_id=bundle.id)
    assert persisted is not None
    assert [item.visual_id for item in persisted.visual_items] == [visual.id]


def test_confirmation_rejects_legacy_candidate_without_visual_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, _source, _chapter, bundle, visual, structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    legacy_bundle = bundle.model_copy(
        deep=True,
        update={"visual_items": [], "metadata": {}},
    )
    SourceEvidenceStore(store.path).save_bundle(legacy_bundle)

    with pytest.raises(LearningSourceReferenceError, match="视觉索引版本"):
        apply_evidence_confirmation(
            owner_user_id=user_id,
            lesson_id=lesson_id,
            bundle_id=legacy_bundle.id,
            action="confirm",
        )

    assert [item.id for item in structure_store.list_visuals(
        owner_user_id=user_id,
        package_id=legacy_bundle.package_id,
        source_id=visual.source_ingestion_id,
    )] == [visual.id]


def test_generation_rejects_confirmed_reference_with_legacy_visual_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _store, user_id, lesson_id, source, _chapter, bundle, _visual, _structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )
    assert result.active_requirement_sheet is not None
    grounding = result.active_requirement_sheet.source_grounding
    legacy_requirements = result.active_requirement_sheet.model_copy(
        deep=True,
        update={
            "source_grounding": grounding.model_copy(
                deep=True,
                update={
                    "confirmed_references": [
                        reference.model_copy(update={"source_visual_index_version": 0})
                        for reference in grounding.confirmed_references
                    ]
                },
            )
        },
    )

    with pytest.raises(ConfirmedSourceContextError) as exc_info:
        load_confirmed_source_context(
            owner_user_id=user_id,
            package_id=source.package_id,
            lesson_id=lesson_id,
            requirement_run_id=bundle.requirement_run_id,
            requirements=legacy_requirements,
        )

    assert exc_info.value.stale is True


def test_generation_rejects_legacy_confirmed_bundle_that_omits_visual_freeze(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, source, _chapter, bundle, _visual, _structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    confirmed = SourceEvidenceStore(store.path).confirm_bundle(
        owner_user_id=user_id,
        bundle_id=bundle.id,
    )
    assert confirmed is not None
    lesson = store.load_for_user(user_id).packages[0].lessons[-1]
    assert lesson.learning_requirements is not None

    with pytest.raises(ConfirmedSourceContextError) as exc_info:
        load_confirmed_source_context(
            owner_user_id=user_id,
            package_id=source.package_id,
            lesson_id=lesson_id,
            requirement_run_id=bundle.requirement_run_id,
            requirements=lesson.learning_requirements,
        )

    assert exc_info.value.stale is True


def test_visual_resolution_unions_full_chapter_and_partial_chunk_scopes() -> None:
    source_id = "source_mixed_scope"
    chapter_a = SourceChapter(
        id="chapter_a",
        owner_user_id="user",
        package_id="package",
        source_ingestion_id=source_id,
        title="Section A",
        anchor_status="verified",
    )
    chapter_b = chapter_a.model_copy(update={"id": "chapter_b", "title": "Section B"})
    chapter_c = chapter_a.model_copy(update={"id": "chapter_c", "title": "Section C"})
    visuals = [
        SourceVisual(
            id="visual_a",
            owner_user_id="user",
            package_id="package",
            source_ingestion_id=source_id,
            chapter_id=chapter_a.id,
            source_locator="page:1:image:1",
            order_index=1,
            anchor_status="verified",
        ),
        SourceVisual(
            id="visual_b",
            owner_user_id="user",
            package_id="package",
            source_ingestion_id=source_id,
            chapter_id=chapter_b.id,
            source_locator="page:2:image:1",
            order_index=2,
            before_chunk_id="chunk_b",
            after_chunk_id="chunk_b",
            anchor_status="verified",
        ),
        SourceVisual(
            id="visual_c",
            owner_user_id="user",
            package_id="package",
            source_ingestion_id=source_id,
            chapter_id=chapter_c.id,
            source_locator="page:3:image:1",
            order_index=3,
            before_chunk_id="chunk_c",
            after_chunk_id="chunk_c",
            anchor_status="verified",
        ),
    ]

    class _SourceStore:
        @staticmethod
        def get_source(**_kwargs):
            return SimpleNamespace(id=source_id)

    class _StructureStore:
        @staticmethod
        def get_structure_view(**_kwargs):
            return SimpleNamespace(chapters=[chapter_a, chapter_b, chapter_c])

        @staticmethod
        def list_visuals(**_kwargs):
            return visuals

    resolver = ResourceResolver(store=_SourceStore(), structure_store=_StructureStore())  # type: ignore[arg-type]
    evidence = [
        RetrievalEvidence(
            source_ingestion_id=source_id,
            source_title="Reference",
            chapter_id=chapter_a.id,
            chunk_ids=["chunk_a"],
            metadata={"scope_kind": "chapter", "scope_chapter_id": chapter_a.id},
        ),
        RetrievalEvidence(
            source_ingestion_id=source_id,
            source_title="Reference",
            chapter_id=chapter_b.id,
            chunk_ids=["chunk_b"],
            metadata={"scope_kind": "chunk"},
        ),
    ]

    resolved = resolver.visual_items_for_evidence(
        owner_user_id="user",
        package_id="package",
        evidence=evidence,
    )

    assert [item.visual_id for item in resolved] == ["visual_a", "visual_b"]


def test_confirmation_rejects_visual_index_rebuilt_after_candidate_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _store, user_id, lesson_id, source, _chapter, bundle, visual, structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    view = structure_store.get_structure_view(source=source, chunk_limit=20)
    assert view.structure is not None
    structure_store.save_structure_bundle(
        structure=view.structure.model_copy(update={"visual_index_version": 2}),
        chapters=view.chapters,
        chunks=view.chunks,
        visuals=[visual.model_copy(update={"structure_version": 2})],
    )

    with pytest.raises(LearningSourceReferenceError, match="重新选择"):
        apply_evidence_confirmation(
            owner_user_id=user_id,
            lesson_id=lesson_id,
            bundle_id=bundle.id,
            action="confirm",
        )

    current = SourceEvidenceStore(structure_store.path).get_bundle(
        owner_user_id=user_id,
        bundle_id=bundle.id,
    )
    assert current is not None
    assert current.status == "candidate"


def test_generation_marks_confirmed_visual_reference_stale_after_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _store, user_id, lesson_id, source, _chapter, bundle, visual, structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )
    assert result.active_requirement_sheet is not None
    view = structure_store.get_structure_view(source=source, chunk_limit=20)
    assert view.structure is not None
    structure_store.save_structure_bundle(
        structure=view.structure.model_copy(update={"visual_index_version": 2}),
        chapters=view.chapters,
        chunks=view.chunks,
        visuals=[
            visual.model_copy(
                update={
                    "structure_version": 2,
                    "position_hash": hashlib.sha256(b"moved").hexdigest(),
                }
            )
        ],
    )

    with pytest.raises(ConfirmedSourceContextError) as exc_info:
        load_confirmed_source_context(
            owner_user_id=user_id,
            package_id=source.package_id,
            lesson_id=lesson_id,
            requirement_run_id=bundle.requirement_run_id,
            requirements=result.active_requirement_sheet,
        )

    assert exc_info.value.stale is True


def test_confirming_a_chapter_scope_preserves_every_confirmed_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, source, chapter, bundle = _seed_candidate(tmp_path, monkeypatch)
    scoped_items = [
        item.model_copy(
            update={
                "metadata": {
                    **item.metadata,
                    "scope_kind": "chapter",
                    "scope_chapter_id": "scope_root",
                    "scope_chapter_number": "4",
                    "scope_chapter_title": "Chapter scope",
                }
            }
        )
        for item in bundle.evidence_items
    ]
    scoped_bundle = bundle.model_copy(update={"evidence_items": scoped_items})
    source_evidence_store.save_bundle(scoped_bundle)

    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=scoped_bundle.id,
        action="confirm",
    )

    assert result.active_requirement_sheet is not None
    assert result.active_requirement_sheet.learning_goal == "Chapter scope"
    assert result.active_requirement_sheet.boundary == "Chapter scope"
    assert result.active_requirement_sheet.granularity == "source_chapter"
    reference = result.active_requirement_sheet.source_grounding.confirmed_references[0]
    assert reference.scope_kind == "chapter"
    assert reference.scope_chapter_id == "scope_root"
    assert reference.scope_chapter_title == "Chapter scope"


def test_skipped_evidence_records_event_without_writing_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, _source, _chapter, bundle = _seed_candidate(tmp_path, monkeypatch)

    result = apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="skip",
    )

    assert result.evidence_bundle.status == "archived"
    assert result.active_requirement_sheet is not None
    assert result.active_requirement_sheet.source_grounding.confirmation_status == "skipped"
    assert result.active_requirement_sheet.source_grounding.confirmed_references == []
    versions = store.list_learning_requirement_versions(user_id, lesson_id)
    assert len(versions) == 2
    assert versions[-1]["change_kind"] == "source_reference_declined"
    events = store.list_learning_requirement_events(user_id, lesson_id)
    assert events[-1]["event_type"] == "source_reference_declined"


def test_board_generation_hydrates_body_from_confirmed_requirement_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, _source, _chapter, bundle = _seed_candidate(tmp_path, monkeypatch)
    apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )
    captured: dict[str, object] = {}

    def _fake_generate_from_requirements(**kwargs):
        captured.update(kwargs)
        lesson = kwargs["lesson"]
        return BoardDocumentEditOutcome(
            chatbot_message="",
            new_document=build_document(
                title="资料板书",
                content_text="# 资料板书\n\n已根据确认正文生成。",
                document_id=lesson.board_document.id,
                page_settings=lesson.board_document.page_settings,
            ),
            board_decision=BoardDecision(action="edit_board", reason="已生成。"),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="按确认资料生成板书。",
            section_titles=["资料板书"],
            changed=True,
            operation_status="succeeded",
        )

    monkeypatch.setattr(blank_board_generation, "generate_from_requirements", _fake_generate_from_requirements)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已根据确认资料生成。"),
    )

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=user_id,
    )

    assert response.board_document_operation_status == "succeeded"
    assert "这是用户确认后应交给板书编辑器的正文" in str(captured["resource_summary"])
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[-1]
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["evidence_bundle_id"] == bundle.id
    assert commit.metadata["source_grounding"]["confirmed_bundle_id"] == bundle.id
    assert commit.metadata["legacy_evidence_fallback"] is False


def test_blank_generation_never_exposes_unverified_visual_success_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, user_id, lesson_id, source, _chapter, bundle, visual, _structure_store = _seed_visual_candidate(
        tmp_path,
        monkeypatch,
    )
    apply_evidence_confirmation(
        owner_user_id=user_id,
        lesson_id=lesson_id,
        bundle_id=bundle.id,
        action="confirm",
    )
    captured_post_reply: dict[str, object] = {}

    def _fake_board_edit(**_kwargs):
        return BoardDocumentEditResult(
            operation="replace_document",
            title="Generated board",
            content_text="# Generated board\n\nThe text board was generated without a visual marker.",
            summary="Two source charts were inserted successfully.",
            chatbot_message="Two source charts were inserted successfully.",
            section_titles=["Generated board"],
            visual_placements=[],
        )

    def _fake_post_reply(**kwargs):
        captured_post_reply.update(kwargs)
        return ChatbotReply(
            chatbot_message="The board is ready. Would you like to start from the beginning?"
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    monkeypatch.setattr(openai_course_ai, "generate_post_board_generation_reply", _fake_post_reply)

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=user_id,
    )

    assert response.board_document_operation_status == "succeeded"
    assert response.chatbot_message == "The board is ready. Would you like to start from the beginning?"
    assert "chart" not in response.chatbot_message.casefold()
    assert captured_post_reply["editor_summary"] == ""
    assert captured_post_reply["applied_visual_ids"] == []
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[-1]
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["board_document_editor_summary"] == ""
    assert commit.metadata["applied_visual_ids"] == []
    assert commit.metadata["skipped_visual_placements"][0]["visual_id"] == visual.id
    assert commit.metadata["skipped_visual_placements"][0]["reason"] == "placement_missing"
    assert commit.metadata["source_ids"] == [source.id]


def test_board_generation_waits_for_candidate_evidence_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _store, user_id, lesson_id, _source, _chapter, bundle = _seed_candidate(tmp_path, monkeypatch)

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=user_id,
    )

    assert response.board_document_operation_status == "failed"
    assert "确认或跳过" in str(response.board_document_operation_failure_reason)
    assert response.candidate_evidence_bundle is not None
    assert response.candidate_evidence_bundle.id == bundle.id
    current = SourceEvidenceStore(workspace_state.get_store().path).get_bundle(
        owner_user_id=user_id,
        bundle_id=bundle.id,
    )
    assert current is not None
    assert current.status == "candidate"
