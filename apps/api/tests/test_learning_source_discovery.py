import json

import pytest

from app.models import (
    ChatRequest,
    EvidenceBundle,
    LearningClarificationStatus,
    RetrievalEvidence,
    SourceIngestionRecord,
)
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_source_discovery import run_learning_source_discovery
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import BlankBoardRequirementRefinement, ChatbotReply, OpenAICourseAI, openai_course_ai
from app.services.resource_resolver import resource_resolver
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


class _DiscoveryResolver:
    def __init__(self, *, ready: bool, requested: bool, bundle: EvidenceBundle | None) -> None:
        self.ready = ready
        self.requested = requested
        self.bundle = bundle
        self.resolve_calls: list[dict[str, object]] = []

    def should_use_sources(self, _message: str) -> bool:
        return self.requested

    def has_ready_sources(self, *, owner_user_id: str, package_id: str) -> bool:
        assert owner_user_id == "user_1"
        assert package_id == "package_1"
        return self.ready

    def resolve_for_learning_requirement(self, **kwargs):
        self.resolve_calls.append(kwargs)
        return self.bundle


class _DiscoveryAI:
    def __init__(self, message: str = "完成资料比对后的回复。") -> None:
        self.message = message
        self.calls: list[dict[str, object]] = []

    def generate_learning_source_discovery_reply(self, **kwargs):
        self.calls.append(kwargs)
        return ChatbotReply(chatbot_message=self.message)


def _requirements():
    return build_requirements("学习主题").model_copy(
        update={
            "learning_goal": "理解目标章节",
            "board_workflow": "generate_from_scratch",
            "work_mode": "knowledge_board",
            "granularity": "single_knowledge_point",
        }
    )


def _clarification(*, ready: bool) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100 if ready else 60,
        label="ready" if ready else "collecting",
        reason="学习目标已明确。" if ready else "仍需确认学习深度。",
        can_start=ready,
        ready_for_board=ready,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
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
        requirement_run_id="requirement_run_1",
        purpose="board_generation",
        evidence_items=[evidence],
        context_text="资料证据上下文",
        token_count=20,
    )


def test_learning_source_discovery_searches_ready_sources_without_source_words() -> None:
    resolver = _DiscoveryResolver(ready=True, requested=False, bundle=_bundle())
    course_ai = _DiscoveryAI()

    outcome = run_learning_source_discovery(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        visible_user_message="我想学习目标章节。",
        retrieval_user_message="我想学习目标章节。",
        requirements=_requirements(),
        clarification=_clarification(ready=True),
        requirement_run_id="requirement_run_1",
        base_chatbot_message="需求已经明确。",
        resolver=resolver,
        course_ai=course_ai,
    )

    assert outcome.status == "matched"
    assert outcome.attempted is True
    assert outcome.evidence_bundle is not None
    assert outcome.chatbot_message == "完成资料比对后的回复。"
    assert resolver.resolve_calls[0]["purpose"] == "board_generation"
    assert resolver.resolve_calls[0]["requirement_run_id"] == "requirement_run_1"
    assert course_ai.calls[0]["discovery_status"] == "matched"
    assert course_ai.calls[0]["requires_confirmation"] is True
    assert "资料 A" in str(course_ai.calls[0]["evidence_references"])


def test_learning_source_discovery_reports_no_match_after_search() -> None:
    resolver = _DiscoveryResolver(ready=True, requested=False, bundle=None)
    course_ai = _DiscoveryAI("已完成检索，但当前资料没有足够相关内容。")

    outcome = run_learning_source_discovery(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        visible_user_message="我想学习另一个主题。",
        retrieval_user_message="我想学习另一个主题。",
        requirements=_requirements(),
        clarification=_clarification(ready=False),
        requirement_run_id="requirement_run_1",
        base_chatbot_message="还需要确认学习深度。",
        resolver=resolver,
        course_ai=course_ai,
    )

    assert outcome.status == "no_match"
    assert outcome.attempted is True
    assert outcome.evidence_bundle is None
    assert resolver.resolve_calls[0]["purpose"] == "chat"
    assert course_ai.calls[0]["discovery_status"] == "no_match"
    assert course_ai.calls[0]["requires_confirmation"] is False


def test_learning_source_discovery_skips_when_no_sources_exist() -> None:
    resolver = _DiscoveryResolver(ready=False, requested=False, bundle=None)
    course_ai = _DiscoveryAI()

    outcome = run_learning_source_discovery(
        owner_user_id="user_1",
        package_id="package_1",
        lesson_id="lesson_1",
        visible_user_message="我想学习一个主题。",
        retrieval_user_message="我想学习一个主题。",
        requirements=_requirements(),
        clarification=_clarification(ready=False),
        requirement_run_id="requirement_run_1",
        base_chatbot_message="原始需求回复。",
        resolver=resolver,
        course_ai=course_ai,
    )

    assert outcome.status == "not_needed"
    assert outcome.attempted is False
    assert outcome.chatbot_message == "原始需求回复。"
    assert resolver.resolve_calls == []
    assert course_ai.calls == []


def test_learning_source_discovery_reply_prompt_preserves_role_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = ai.generate_learning_source_discovery_reply(
        base_chatbot_message="需求已经明确。",
        user_message="为我讲解这个章节。",
        requirement_context={"learning_goal": "理解目标章节"},
        clarification_context={"ready_for_board": True},
        discovery_status="matched",
        evidence_references="资料 A / 2.1 目标章节：短摘录",
        requires_confirmation=True,
    )

    assert result == ChatbotReply(chatbot_message="已完成资料检索，请确认是否使用命中章节。")
    assert captured["role"] == "chatbot"
    assert "ResourceResolver" in str(captured["system_prompt"])
    assert "不得生成右侧板书正文" in str(captured["system_prompt"])
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["source_discovery"]["status"] == "matched"
    assert payload["source_discovery"]["requires_confirmation"] is True


def test_blank_learning_requirement_searches_sources_before_chatbot_reply(
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
    captured: dict[str, object] = {}

    def _fake_refinement(**kwargs):
        captured["include_stream_result"] = kwargs["include_stream_result"]
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

    def _fake_discovery_reply(**kwargs):
        captured["discovery_status"] = kwargs["discovery_status"]
        captured["evidence_references"] = kwargs["evidence_references"]
        return ChatbotReply(chatbot_message="我已在上传资料中找到 2.1 的相关正文，请确认是否用于生成板书。")

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_learning_source_discovery_reply", _fake_discovery_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学习 2.1 的核心内容。"),
        user_id=user_id,
    )

    assert captured["include_stream_result"] is False
    assert captured["discovery_status"] == "matched"
    assert "Reference Book" in str(captured["evidence_references"])
    assert response.chatbot_message == "我已在上传资料中找到 2.1 的相关正文，请确认是否用于生成板书。"
    assert response.candidate_evidence_bundle is not None
    assert response.candidate_evidence_bundle.purpose == "board_generation"
    assert response.candidate_evidence_bundle.requirement_run_id == response.requirement_run_id
    assert response.candidate_evidence_bundle.evidence_items[0].chapter_id
    saved = store.load_for_user(user_id).packages[0].lessons[0]
    commit = saved.history_graph.commits[-1]
    assert commit.metadata["learning_source_discovery"]["status"] == "matched"
    assert commit.metadata["evidence_bundle_id"] == response.candidate_evidence_bundle.id
