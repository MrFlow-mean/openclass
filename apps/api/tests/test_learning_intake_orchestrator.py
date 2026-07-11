import pytest

from app.models import ChatRequest, EvidenceBundle, LearningClarificationStatus, RetrievalEvidence
from app.services import learning_intake_orchestrator as orchestrator
from app.services.board_document_sensor import read_board_document_sensor
from app.services.learning_requirement_history import RequirementHistoryStamp
from app.services.learning_requirement_refiner import LearningRequirementRefinementOutcome
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, InitialLearningWorkModeDecision
from app.services.resource_resolver import ResourceResolutionOutcome


class _ResolverStore:
    def __init__(self) -> None:
        self.archive_calls: list[dict[str, str]] = []

    def archive_bundle(self, *, owner_user_id: str, bundle_id: str) -> None:
        self.archive_calls.append({"owner_user_id": owner_user_id, "bundle_id": bundle_id})


class _Resolver:
    def __init__(
        self,
        call_order: list[str],
        *,
        ready: bool,
        requested: bool = False,
        mentioned_source_ids: tuple[str, ...] = (),
    ) -> None:
        self.call_order = call_order
        self.ready = ready
        self.requested = requested
        self.mentioned_source_ids = mentioned_source_ids
        self.preview_calls = 0
        self.preview_requests: list[dict[str, object]] = []
        self.store = _ResolverStore()

    def should_use_sources(self, _message: str) -> bool:
        return self.requested

    def latest_requirement_bundle(self, **kwargs):
        raise AssertionError("no active requirement bundle is expected")

    def ready_source_ids_mentioned(self, **kwargs):
        return list(self.mentioned_source_ids)

    def has_ready_sources(self, **kwargs) -> bool:
        return self.ready

    def preview_for_learning_requirement(self, **kwargs):
        self.call_order.append("resource")
        self.preview_calls += 1
        self.preview_requests.append(kwargs)
        evidence = RetrievalEvidence(
            source_ingestion_id="source_1",
            source_title="通用资料",
            section_path=["目标章节"],
            chunk_ids=["chunk_1"],
            excerpt="资料摘录",
            expanded_text="资料正文",
            token_count=10,
        )
        return ResourceResolutionOutcome(
            status="matched",
            evidence_bundle=EvidenceBundle(
                owner_user_id="user_1",
                package_id="package_1",
                lesson_id="lesson_1",
                purpose="board_generation",
                evidence_items=[evidence],
                context_text="资料正文",
                token_count=10,
            ),
        )

    def bind_preview_bundle_to_requirement(self, *, bundle, requirement_run_id):
        self.call_order.append("bind")
        return bundle.model_copy(update={"requirement_run_id": requirement_run_id})


class _CourseAI:
    def __init__(self, call_order: list[str], *, route: str) -> None:
        self.call_order = call_order
        self.route = route

    def generate_initial_learning_work_mode(self, **kwargs):
        self.call_order.append("initial")
        return InitialLearningWorkModeDecision(
            route=self.route,
            work_mode="knowledge_board" if self.route == "learning_intake" else "unknown",
            granularity="single_knowledge_point" if self.route == "learning_intake" else "unclear",
            topic="目标知识点" if self.route == "learning_intake" else "",
        )

    def generate_learning_intake_reply(self, **kwargs):
        self.call_order.append("chatbot")
        return ChatbotReply(chatbot_message="基于资料生成的唯一回复。")


def test_ordinary_chat_skips_source_discovery_and_requirement_manager(monkeypatch) -> None:
    call_order: list[str] = []
    resolver = _Resolver(call_order, ready=True)
    course_ai = _CourseAI(call_order, route="ordinary_chat")
    lesson = create_empty_lesson("空白页")
    lesson.learning_requirements = None
    monkeypatch.setattr(
        orchestrator,
        "refine_blank_board_requirement",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ordinary chat must skip requirement manager")),
    )

    outcome = orchestrator.run_learning_intake_turn(
        owner_user_id="user_1",
        package_id="package_1",
        lesson=lesson,
        request=ChatRequest(message="今天随便聊聊。"),
        board_document_state=read_board_document_sensor(lesson.board_document),
        conversation_summary="",
        history_state=None,
        resolver=resolver,
        course_ai=course_ai,
    )

    assert outcome.route == "ordinary_chat"
    assert call_order == ["initial"]
    assert resolver.preview_calls == 0
    assert outcome.source_discovery is None


def test_learning_intake_without_source_intent_does_not_preview_or_inject_sources(monkeypatch) -> None:
    call_order: list[str] = []
    resolver = _Resolver(call_order, ready=True, requested=False)
    course_ai = _CourseAI(call_order, route="learning_intake")
    lesson = create_empty_lesson("空白页")
    lesson.id = "lesson_1"
    lesson.learning_requirements = None
    requirements = build_requirements("目标知识点").model_copy(
        update={
            "learning_goal": "目标知识点",
            "work_mode": "knowledge_board",
            "granularity": "single_knowledge_point",
            "board_workflow": "generate_from_scratch",
        }
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="目标已明确",
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )

    def _fake_refinement(**kwargs):
        call_order.append("requirement")
        assert kwargs["resource_summary"] == ""
        assert kwargs["source_requested_by_user"] is False
        assert kwargs["include_stream_result"] is True
        return LearningRequirementRefinementOutcome(
            route="requirement_refining",
            chatbot_message="需求管理器内部草稿。",
            active_requirement_sheet=requirements,
            learning_clarification=clarification,
            history_stamp=RequirementHistoryStamp(
                run_id="requirement_run_1",
                version_id="requirement_version_1",
                phase="ready",
            ),
            history_operations=[],
            guidance_metadata={"entry_point_options": []},
            changed=True,
        )

    monkeypatch.setattr(orchestrator, "refine_blank_board_requirement", _fake_refinement)

    outcome = orchestrator.run_learning_intake_turn(
        owner_user_id="user_1",
        package_id="package_1",
        lesson=lesson,
        request=ChatRequest(message="请帮我学习目标知识点。"),
        board_document_state=read_board_document_sensor(lesson.board_document),
        conversation_summary="",
        history_state=None,
        resolver=resolver,
        course_ai=course_ai,
    )

    assert call_order == ["initial", "requirement", "chatbot"]
    assert resolver.preview_calls == 0
    assert outcome.source_discovery is not None
    assert outcome.source_discovery.status == "not_needed"
    assert outcome.source_discovery.attempted is False
    assert outcome.source_discovery.context_text == ""
    assert outcome.evidence_bundle is None
    assert outcome.candidate_evidence_bundle is None


def test_learning_intake_runs_source_before_requirement_and_chatbot(monkeypatch) -> None:
    call_order: list[str] = []
    resolver = _Resolver(
        call_order,
        ready=True,
        requested=False,
        mentioned_source_ids=("source_1",),
    )
    course_ai = _CourseAI(call_order, route="learning_intake")
    lesson = create_empty_lesson("空白页")
    lesson.id = "lesson_1"
    lesson.learning_requirements = None
    requirements = build_requirements("目标知识点").model_copy(
        update={
            "learning_goal": "目标知识点",
            "work_mode": "knowledge_board",
            "granularity": "single_knowledge_point",
            "board_workflow": "generate_from_scratch",
        }
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="目标已明确",
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )

    def _fake_refinement(**kwargs):
        call_order.append("requirement")
        assert kwargs["resource_summary"] == "1. 通用资料 / 目标章节：资料摘录"
        assert kwargs["include_stream_result"] is True
        return LearningRequirementRefinementOutcome(
            route="requirement_refining",
            chatbot_message="需求管理器内部草稿。",
            active_requirement_sheet=requirements,
            learning_clarification=clarification,
            history_stamp=RequirementHistoryStamp(
                run_id="requirement_run_1",
                version_id="requirement_version_1",
                phase="ready",
            ),
            history_operations=[],
            guidance_metadata={"entry_point_options": []},
            changed=True,
        )

    monkeypatch.setattr(orchestrator, "refine_blank_board_requirement", _fake_refinement)

    outcome = orchestrator.run_learning_intake_turn(
        owner_user_id="user_1",
        package_id="package_1",
        lesson=lesson,
        request=ChatRequest(message="请根据 General Learning Notes 学习目标知识点。"),
        board_document_state=read_board_document_sensor(lesson.board_document),
        conversation_summary="",
        history_state=None,
        resolver=resolver,
        course_ai=course_ai,
    )

    assert call_order == ["initial", "resource", "requirement", "bind", "chatbot"]
    assert outcome.chatbot_message == "基于资料生成的唯一回复。"
    assert outcome.evidence_bundle is not None
    assert outcome.evidence_bundle.requirement_run_id == "requirement_run_1"
    assert outcome.candidate_evidence_bundle is not None
    assert resolver.preview_requests[0]["source_ingestion_ids"] == ("source_1",)


def test_learning_intake_rolls_back_bound_candidate_when_chatbot_fails(monkeypatch) -> None:
    call_order: list[str] = []
    resolver = _Resolver(call_order, ready=True, requested=True)
    course_ai = _CourseAI(call_order, route="learning_intake")
    lesson = create_empty_lesson("空白页")
    lesson.id = "lesson_1"
    requirements = build_requirements("目标知识点").model_copy(
        update={
            "learning_goal": "目标知识点",
            "work_mode": "knowledge_board",
            "granularity": "single_knowledge_point",
            "board_workflow": "generate_from_scratch",
        }
    )
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="目标已明确",
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )

    def _fake_refinement(**kwargs):
        call_order.append("requirement")
        return LearningRequirementRefinementOutcome(
            route="requirement_refining",
            chatbot_message="需求管理器内部草稿。",
            active_requirement_sheet=requirements,
            learning_clarification=clarification,
            history_stamp=RequirementHistoryStamp(
                run_id="requirement_run_1",
                version_id="requirement_version_1",
                phase="ready",
            ),
            history_operations=[],
            guidance_metadata={},
            changed=True,
        )

    def _failed_chatbot(**kwargs):
        call_order.append("chatbot")
        raise RuntimeError("chatbot generation failed")

    monkeypatch.setattr(orchestrator, "refine_blank_board_requirement", _fake_refinement)
    monkeypatch.setattr(course_ai, "generate_learning_intake_reply", _failed_chatbot)

    with pytest.raises(RuntimeError, match="chatbot generation failed"):
        orchestrator.run_learning_intake_turn(
            owner_user_id="user_1",
            package_id="package_1",
            lesson=lesson,
            request=ChatRequest(message="请帮我学习目标知识点。"),
            board_document_state=read_board_document_sensor(lesson.board_document),
            conversation_summary="",
            history_state=None,
            resolver=resolver,
            course_ai=course_ai,
        )

    assert call_order == ["initial", "resource", "requirement", "bind", "chatbot"]
    assert len(resolver.store.archive_calls) == 1
