import json

import pytest

from app.models import ChatRequest, EvidenceBundle, RetrievalEvidence, SourceIngestionRecord
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_source_discovery import (
    bind_learning_source_discovery,
    discover_learning_sources,
    rollback_learning_source_discovery,
)
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import (
    BlankBoardRequirementRefinement,
    ChatbotReply,
    InitialLearningWorkModeDecision,
    OpenAICourseAI,
    openai_course_ai,
)
from app.services.resource_resolver import ResourceResolutionOutcome, resource_resolver
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


class _DiscoveryResolver:
    def __init__(
        self,
        *,
        ready: bool,
        requested: bool,
        resolution: ResourceResolutionOutcome,
    ) -> None:
        self.ready = ready
        self.requested = requested
        self.resolution = resolution
        self.preview_calls: list[dict[str, object]] = []
        self.bind_calls: list[dict[str, object]] = []
        self.store = _DiscoveryStore()

    def should_use_sources(self, _message: str) -> bool:
        return self.requested

    def has_ready_sources(self, *, owner_user_id: str, package_id: str) -> bool:
        assert owner_user_id == "user_1"
        assert package_id == "package_1"
        return self.ready

    def preview_for_learning_requirement(self, **kwargs):
        self.preview_calls.append(kwargs)
        return self.resolution

    def bind_preview_bundle_to_requirement(self, *, bundle: EvidenceBundle, requirement_run_id: str):
        self.bind_calls.append({"bundle": bundle, "requirement_run_id": requirement_run_id})
        return bundle.model_copy(update={"requirement_run_id": requirement_run_id})


class _DiscoveryStore:
    def __init__(self) -> None:
        self.archive_calls: list[dict[str, str]] = []

    def archive_bundle(self, *, owner_user_id: str, bundle_id: str) -> None:
        self.archive_calls.append({"owner_user_id": owner_user_id, "bundle_id": bundle_id})


def _requirements():
    return build_requirements("学习主题").model_copy(
        update={
            "learning_goal": "理解目标章节",
            "board_workflow": "generate_from_scratch",
            "work_mode": "knowledge_board",
            "granularity": "single_knowledge_point",
        }
    )


def _bundle() -> EvidenceBundle:
    evidence = RetrievalEvidence(
        source_ingestion_id="source_1",
        source_title="资料 A",
        section_path=["2.1 目标章节"],
        chunk_ids=["chunk_1"],
        excerpt="与学习需求相关的短摘录。",
        expanded_text="与学习需求相关的正文内容。",
        token_count=20,
    )
    return EvidenceBundle(
        package_id="package_1",
        lesson_id="lesson_1",
        purpose="board_generation",
        evidence_items=[evidence],
        context_text="资料证据上下文",
        token_count=20,
    )


def test_learning_source_discovery_previews_ready_sources_without_persisting() -> None:
    resolver = _DiscoveryResolver(
        ready=True,
        requested=False,
        resolution=ResourceResolutionOutcome(status="matched", evidence_bundle=_bundle()),
    )

    outcome = discover_learning_sources(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        retrieval_user_message="我想学习目标章节。",
        requirements=_requirements(),
        active_requirement_run_id=None,
        topic_hint="目标章节",
        resolver=resolver,
    )

    assert outcome.status == "matched"
    assert outcome.attempted is True
    assert outcome.provisional_bundle is True
    assert outcome.persisted_this_turn is False
    assert outcome.evidence_bundle is not None
    assert outcome.evidence_bundle.requirement_run_id is None
    assert outcome.source_requested_by_user is False
    assert outcome.metadata["auto_triggered"] is True
    assert resolver.preview_calls[0]["purpose"] == "board_generation"
    assert resolver.preview_calls[0]["topic_hint"] == "目标章节"
    assert resolver.bind_calls == []


def test_learning_source_discovery_binds_preview_after_requirement_run_exists() -> None:
    resolver = _DiscoveryResolver(
        ready=True,
        requested=True,
        resolution=ResourceResolutionOutcome(status="matched", evidence_bundle=_bundle()),
    )
    preview = discover_learning_sources(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        retrieval_user_message="按资料学习目标章节。",
        requirements=_requirements(),
        active_requirement_run_id=None,
        resolver=resolver,
    )

    bound = bind_learning_source_discovery(
        preview,
        requirement_run_id="requirement_run_1",
        resolver=resolver,
    )

    assert bound.evidence_bundle is not None
    assert bound.evidence_bundle.requirement_run_id == "requirement_run_1"
    assert bound.provisional_bundle is False
    assert bound.persisted_this_turn is True
    assert bound.metadata["evidence_bundle_id"] == bound.evidence_bundle.id
    assert resolver.bind_calls[0]["requirement_run_id"] == "requirement_run_1"


def test_learning_source_discovery_rolls_back_bundle_persisted_by_failed_turn() -> None:
    resolver = _DiscoveryResolver(
        ready=True,
        requested=True,
        resolution=ResourceResolutionOutcome(status="matched", evidence_bundle=_bundle()),
    )
    preview = discover_learning_sources(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        retrieval_user_message="按资料学习目标章节。",
        requirements=_requirements(),
        active_requirement_run_id=None,
        resolver=resolver,
    )
    bound = bind_learning_source_discovery(
        preview,
        requirement_run_id="requirement_run_1",
        resolver=resolver,
    )

    rollback_learning_source_discovery(bound, resolver=resolver)

    assert bound.evidence_bundle is not None
    assert resolver.store.archive_calls == [
        {
            "owner_user_id": bound.evidence_bundle.owner_user_id,
            "bundle_id": bound.evidence_bundle.id,
        }
    ]


def test_learning_source_discovery_reports_no_match_without_chatbot_generation() -> None:
    resolver = _DiscoveryResolver(
        ready=True,
        requested=False,
        resolution=ResourceResolutionOutcome(status="no_match"),
    )

    outcome = discover_learning_sources(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        retrieval_user_message="我想学习另一个主题。",
        requirements=_requirements(),
        active_requirement_run_id=None,
        resolver=resolver,
    )

    assert outcome.status == "no_match"
    assert outcome.evidence_bundle is None
    assert outcome.evidence_references == ""
    assert len(resolver.preview_calls) == 1


def test_learning_source_discovery_skips_when_no_sources_exist() -> None:
    resolver = _DiscoveryResolver(
        ready=False,
        requested=False,
        resolution=ResourceResolutionOutcome(status="no_match"),
    )

    outcome = discover_learning_sources(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        retrieval_user_message="我想学习一个主题。",
        requirements=_requirements(),
        active_requirement_run_id=None,
        resolver=resolver,
    )

    assert outcome.status == "not_needed"
    assert outcome.attempted is False
    assert resolver.preview_calls == []


def test_learning_intake_reply_prompt_preserves_role_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured.update(
            {
                "role": role,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
            }
        )
        return ChatbotReply(chatbot_message="已完成资料检索，请确认是否使用命中章节。")

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    result = ai.generate_learning_intake_reply(
        requirement_reply_draft="需求已经明确。",
        user_message="为我讲解这个章节。",
        requirement_context={"learning_goal": "理解目标章节"},
        clarification_context={"ready_for_board": True},
        guidance_context={"entry_point_options": []},
        initial_work_mode_decision={"route": "learning_intake"},
        discovery_status="matched",
        evidence_references="资料 A / 2.1 目标章节：短摘录",
        source_requested_by_user=True,
        requires_confirmation=True,
    )

    assert result == ChatbotReply(chatbot_message="已完成资料检索，请确认是否使用命中章节。")
    assert captured["role"] == "chatbot"
    assert "本轮唯一面向用户发言" in str(captured["system_prompt"])
    assert "板书正文" in str(captured["system_prompt"])
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["source_discovery"]["status"] == "matched"
    assert payload["source_discovery"]["source_requested_by_user"] is True
    assert payload["source_discovery"]["requires_confirmation"] is True


def test_blank_learning_requirement_searches_sources_before_requirement_and_chatbot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_proactive_source_discovery"
    database_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(database_path, legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("主动资料检索")
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)

    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source_path = tmp_path / "reference.md"
    source_path.write_text("# 2.1 Target Section\n\nGrounded body for the requested learning goal.", encoding="utf-8")
    source = SourceIngestionRecord(
        owner_user_id=user_id,
        package_id=package.id,
        title="Reference Book",
        source_type="local_file",
        file_name="reference.md",
        mime_type="text/markdown",
        size_bytes=source_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(source_path)},
    )
    source_store.save_source(source)
    SourceStructureIndexer(store=structure_store).rebuild_structure(source)
    monkeypatch.setattr(resource_resolver, "store", source_store)
    monkeypatch.setattr(resource_resolver, "structure_store", structure_store)
    call_order: list[str] = []
    captured: dict[str, object] = {}
    original_preview = resource_resolver.preview_for_learning_requirement

    def _fake_initial(**kwargs):
        call_order.append("initial")
        return InitialLearningWorkModeDecision(
            route="learning_intake",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            topic="2.1 的核心内容",
        )

    def _traced_preview(**kwargs):
        call_order.append("resource")
        return original_preview(**kwargs)

    def _fake_refinement(**kwargs):
        call_order.append("requirement")
        captured["refinement"] = kwargs
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="需求已经明确。",
            progress=100,
            summary="用户想学习 2.1。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="理解 2.1 的核心内容",
            ready_for_board=True,
        )

    def _fake_final_reply(**kwargs):
        call_order.append("chatbot")
        captured["final_reply"] = kwargs
        return ChatbotReply(chatbot_message="我已找到 2.1 的相关正文，接下来确认学习起点。")

    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", _fake_initial)
    monkeypatch.setattr(resource_resolver, "preview_for_learning_requirement", _traced_preview)
    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_learning_intake_reply", _fake_final_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学习 2.1 的核心内容。"),
        user_id=user_id,
    )

    assert call_order == ["initial", "resource", "requirement", "chatbot"]
    refinement_call = captured["refinement"]
    assert isinstance(refinement_call, dict)
    assert refinement_call["include_stream_result"] is False
    assert "Grounded body" in str(refinement_call["resource_summary"])
    final_reply_call = captured["final_reply"]
    assert isinstance(final_reply_call, dict)
    assert final_reply_call["discovery_status"] == "matched"
    assert "Reference Book" in str(final_reply_call["evidence_references"])
    assert response.chatbot_message == "我已找到 2.1 的相关正文，接下来确认学习起点。"
    assert response.candidate_evidence_bundle is not None
    assert response.candidate_evidence_bundle.requirement_run_id == response.requirement_run_id
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.source_grounding.requested_by_user is False
    saved = store.load_for_user(user_id).packages[0].lessons[0]
    commit = saved.history_graph.commits[-1]
    assert commit.metadata["assistant_message"] == response.chatbot_message
    assert commit.metadata["assistant_message_source"] == "chatbot_learning_intake"
    assert commit.metadata["visible_reply_owner"] == "chatbot"
    assert commit.metadata["learning_source_discovery"]["status"] == "matched"
    assert commit.metadata["evidence_bundle_id"] == response.candidate_evidence_bundle.id
