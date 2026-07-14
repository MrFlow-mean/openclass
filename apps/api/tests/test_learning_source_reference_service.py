from pathlib import Path

import pytest

from app.models import BoardDecision, ChatRequest, EvidenceBundle, LearningClarificationStatus, SourceIngestionRecord
from app.services import blank_board_generation, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.learning_source_reference_service import apply_evidence_confirmation, source_evidence_store
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


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
        )
    )
    return store, user_id, lesson.id, source, chapter, bundle


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
