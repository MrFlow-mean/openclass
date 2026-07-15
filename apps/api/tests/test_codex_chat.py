from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import (
    AgentActivityEvent,
    ChatRequest,
    GuidedRequirementDiscovery,
    GuidedRequirementEntryPoint,
    SelectionRef,
)
from app.services import blank_board_intake, codex_app_server, codex_chat, workspace_state
from app.services.blank_board_intake import (
    BlankBoardAuxiliaryFactor,
    BlankBoardTurnDecision,
    OrdinaryChatTurnResponse,
    evaluate_blank_board_decision,
)
from app.services.codex_app_server import (
    CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES,
    CodexAppServerError,
    CodexTurnCancelledError,
    CodexTurnResult,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations, create_branch, current_head_commit
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.rich_document import build_document


TEST_USER_ID = "user_codex_chat"


def _thread_result(thread_id: str, cwd: Path) -> dict:
    return {
        "thread": {"id": thread_id},
        "activePermissionProfile": {"id": "openclass_board"},
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [str((cwd / "board.md").resolve())],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


def _seed_workspace(store: SqliteCourseStore, *, content_text: str = "# Existing board"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Codex document")
    document = build_document(
        title=lesson.board_document.title,
        content_text=content_text,
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.board_document = document
    lesson.history_graph.commits[0].snapshot = document
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_codex_turn_prompt_uses_mode_and_ignores_source_selection() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Explain the current document.",
            interaction_mode="ask",
            selection=SelectionRef(
                kind="source",
                excerpt="uploaded source excerpt must not be sent",
                source_title="Uploaded source title",
                source_chapter_title="Uploaded chapter",
                source_page_range="10-12",
            ),
        ),
        is_new_thread=True,
        board_state="empty",
    )

    assert "Interaction mode: ask" in prompt
    assert "Board state (computed by OpenClass): EMPTY." in prompt
    assert "Current user message:\nExplain the current document." in prompt
    assert "uploaded source excerpt" not in prompt
    assert "Uploaded source title" not in prompt
    assert "Uploaded chapter" not in prompt
    assert "10-12" not in prompt


def test_codex_turn_prompt_keeps_current_board_selection_for_editing() -> None:
    prompt = codex_chat._turn_prompt(
        ChatRequest(
            message="Rewrite this paragraph.",
            interaction_mode="direct_edit",
            selection=SelectionRef(
                kind="board",
                excerpt="Current board paragraph",
                heading_path=["Section"],
            ),
        ),
        is_new_thread=False,
        board_state="non_empty",
    )

    assert "Interaction mode: direct_edit" in prompt
    assert "Board state (computed by OpenClass): NON_EMPTY." in prompt
    assert "kind: board" in prompt
    assert "excerpt: Current board paragraph" in prompt
    assert "heading path: Section" in prompt


def test_board_state_detector_treats_whitespace_as_empty() -> None:
    assert codex_chat._board_state("") == "empty"
    assert codex_chat._board_state(" \n\t") == "empty"
    assert codex_chat._board_state("# Lesson") == "non_empty"


def test_blank_board_ordinary_chat_does_not_create_requirement_sheet() -> None:
    decision = BlankBoardTurnDecision(
        intent="ordinary_chat",
        chatbot_message="A conversational reply.",
        reason="The user is not asking to learn.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "ordinary_chat"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False
    assert outcome.clarification.reason == ""
    assert outcome.clarification.summary == ""
    assert outcome.clarification.next_question == ""
    assert outcome.guidance.is_empty()


def test_blank_board_unclear_intent_guides_without_creating_requirement_sheet() -> None:
    decision = BlankBoardTurnDecision(
        intent="unclear",
        chatbot_message="A recommendation-led clarification.",
        next_question="Which direction should we narrow first?",
        reason="A learning purpose is not confirmed yet.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False


def test_blank_board_learning_intent_without_teaching_type_guides_instead_of_failing() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A broad learning theme",
        content_is_specific=False,
        chatbot_message="Two contextual directions and one narrowing question.",
        next_question="Which direction should we narrow first?",
        reason="The learning intent is clear, but the teaching type cannot be selected yet.",
        guidance=GuidedRequirementDiscovery(
            strategy="level_discovery",
            selection_target="current_level",
            question_title="Which starting level is closest to you?",
            learning_map_summary="A short map of useful starting directions.",
            entry_point_options=[
                GuidedRequirementEntryPoint(
                    title="Start from a first foundation",
                    description="Build one dependable first step before expanding.",
                    answer_value="New to the prerequisites",
                    why_it_matters="The first board should establish the prerequisites.",
                    best_for="A learner without a dependable starting point.",
                ),
                GuidedRequirementEntryPoint(
                    title="Start from a real task",
                    description="Use a concrete outcome to choose the first topic.",
                    answer_value="Can complete a related task with support",
                    why_it_matters="A task can expose the next useful knowledge gap.",
                    best_for="A learner with some practical exposure.",
                ),
            ],
            recommended_entry_point="Start from a first foundation",
            reason_for_recommendation="No prior level or target task is confirmed yet.",
            learner_profile_inference="The learner may be at an early exploratory stage.",
        ),
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type is None
    assert outcome.requirement.learning_content == "A broad learning theme"
    assert outcome.requirement.work_mode == "unknown"
    assert outcome.requirement.granularity == "unclear"
    assert outcome.requirement_changed is True
    assert outcome.ready_for_board is False
    assert outcome.chatbot_message == "Two contextual directions and one narrowing question."
    assert outcome.clarification.next_question == "Which direction should we narrow first?"
    assert outcome.guidance.recommended_entry_point == "Start from a first foundation"
    assert outcome.guidance.selection_target == "current_level"


def test_untyped_learning_turn_merges_a_selected_level_before_mode_is_resolved() -> None:
    previous = build_requirements("A broad learning theme")
    previous.teaching_type = None
    previous.learning_content = "A broad learning theme"
    previous.current_level = ""
    previous.target_scenario = ""
    previous.theme = previous.learning_content
    previous.learning_goal = previous.learning_content
    previous.work_mode = "unknown"
    previous.granularity = "unclear"
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type=None,
        learning_content="A broad learning theme",
        current_level="Has some prerequisites but has not studied systematically",
        chatbot_message="That gives me your starting point. Now choose the first content direction.",
        next_question="Which content direction should we narrow first?",
        reason="The selected level is confirmed while the content is still broad.",
        guidance=GuidedRequirementDiscovery(
            strategy="entry_point_discovery",
            selection_target="learning_content",
            question_title="Which content direction should we narrow first?",
            learning_map_summary="The next step is to choose one focused content entry.",
            entry_point_options=[
                GuidedRequirementEntryPoint(
                    title="Build the conceptual foundation",
                    description="Choose one core idea and its meaning.",
                    answer_value="A core concept and its meaning",
                ),
                GuidedRequirementEntryPoint(
                    title="Start from a representative problem",
                    description="Use one problem to identify the required concept.",
                    answer_value="A representative problem and its method",
                ),
                GuidedRequirementEntryPoint(
                    title="Connect ideas through a structure map",
                    description="Narrow the broad direction through its main relationships.",
                    answer_value="A focused relationship between core ideas",
                ),
            ],
            recommended_entry_point="Build the conceptual foundation",
            reason_for_recommendation="It best matches the selected starting level.",
        ),
    )

    outcome = evaluate_blank_board_decision(
        decision,
        previous_requirement=previous,
    )

    assert outcome.requirement is not None
    assert outcome.requirement.current_level == (
        "Has some prerequisites but has not studied systematically"
    )
    assert outcome.requirement.learning_content == "A broad learning theme"
    assert outcome.clarification.missing_items == ["target_scenario", "teaching_type"]
    assert outcome.guidance.selection_target == "learning_content"


def test_unclear_turn_does_not_persist_an_unconfirmed_learning_topic() -> None:
    decision = BlankBoardTurnDecision(
        intent="unclear",
        learning_content="A possible topic",
        chatbot_message="A contextual direction and one question.",
        next_question="Which kind of learning outcome are you looking for?",
        reason="The user has not confirmed a learning request.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "guided_discovery"
    assert outcome.requirement is None
    assert outcome.requirement_changed is False
    assert outcome.ready_for_board is False


def test_untyped_learning_turn_preserves_a_previous_requirement_when_no_topic_is_confirmed() -> None:
    previous = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad learning theme",
            content_is_specific=False,
            chatbot_message="One focused question.",
            next_question="Which part should become the first focused topic?",
            reason="The theme is still broad.",
        ),
        previous_requirement=None,
    ).requirement
    assert previous is not None

    outcome = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type=None,
            chatbot_message="A recommendation-led clarification.",
            next_question="Which starting direction fits best?",
            reason="The current turn does not add a confirmed topic.",
        ),
        previous_requirement=previous,
    )

    assert outcome.route == "guided_discovery"
    assert outcome.requirement == previous
    assert outcome.requirement_changed is False


def test_broad_knowledge_direction_collects_until_content_is_specific() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A broad field",
        content_is_specific=False,
        chatbot_message="Several possible entry points and one narrowing question.",
        next_question="Which entry point do you want to understand first?",
        reason="The requested field is too broad for a focused board.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type == "knowledge_point"
    assert outcome.requirement.learning_content == "A broad field"
    assert outcome.clarification.missing_items == [
        "learning_content",
        "current_level",
        "target_scenario",
    ]
    assert outcome.ready_for_board is False


def test_blank_board_question_wording_does_not_create_a_requirement_version() -> None:
    first = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad field",
            content_is_specific=False,
            chatbot_message="First guidance.",
            next_question="Which part should we narrow first?",
            reason="The content is broad.",
        ),
        previous_requirement=None,
    )
    assert first.requirement is not None

    second = evaluate_blank_board_decision(
        BlankBoardTurnDecision(
            intent="learning_need",
            teaching_type="knowledge_point",
            learning_content="A broad field",
            content_is_specific=False,
            chatbot_message="Refined guidance.",
            next_question="What single concept matters most right now?",
            reason="The same core factor is still broad.",
        ),
        previous_requirement=first.requirement,
    )

    assert second.requirement == first.requirement
    assert second.requirement_changed is False
    assert second.clarification.next_question == (
        "What single concept matters most right now?"
    )


def test_specific_knowledge_point_requires_level_and_target_scenario() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A specific concept",
        content_is_specific=True,
        chatbot_message="One question about the learner context.",
        next_question="What is your current level and where will you use this?",
        reason="The current level and target scenario are still missing.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.requirement is not None
    assert outcome.requirement.teaching_type == "knowledge_point"
    assert outcome.requirement.learning_goal == "A specific concept"
    assert outcome.clarification.missing_items == ["current_level", "target_scenario"]
    assert outcome.ready_for_board is False


def test_specific_knowledge_point_is_ready_with_all_core_factors() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="knowledge_point",
        learning_content="A specific concept",
        content_is_specific=True,
        current_level="Can describe the basic idea with support",
        target_scenario="Preparing for an upcoming course",
        chatbot_message="The focused board is ready.",
        teaching_plan="Explain the concept through a definition, mechanism, and example.",
        reason="All core factors are resolved.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "generate_board"
    assert outcome.requirement is not None
    assert outcome.requirement.current_level == "Can describe the basic idea with support"
    assert outcome.requirement.target_scenario == "Preparing for an upcoming course"
    assert outcome.clarification.missing_items == []
    assert outcome.ready_for_board is True
    assert outcome.guidance.is_empty()


def test_skill_practice_auxiliary_factors_cannot_replace_missing_core_factors() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="skill_practice",
        learning_content="A specific skill",
        content_is_specific=True,
        auxiliary_factors=[
            BlankBoardAuxiliaryFactor(
                label="preferred_format",
                value="short rounds",
                evidence="Explicitly requested by the learner.",
            )
        ],
        chatbot_message="One question about the learner's current level.",
        next_question="What can you already do independently?",
        reason="Current level and target scenario are still missing.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "collect_requirements"
    assert outcome.clarification.missing_items == ["current_level", "target_scenario"]
    assert outcome.ready_for_board is False


def test_skill_practice_accepts_explicit_no_specific_scenario() -> None:
    decision = BlankBoardTurnDecision(
        intent="learning_need",
        teaching_type="skill_practice",
        learning_content="A specific skill",
        content_is_specific=True,
        current_level="Can complete the basic form with occasional help",
        target_scenario="无明确应用场景",
        chatbot_message="The practice board is ready.",
        teaching_plan="Create progressive practice with feedback checkpoints.",
        reason="All skill-practice core factors are resolved.",
    )

    outcome = evaluate_blank_board_decision(decision, previous_requirement=None)

    assert outcome.route == "generate_board"
    assert outcome.requirement is not None
    assert outcome.requirement.target_scenario == "无明确应用场景"
    assert outcome.ready_for_board is True


def test_empty_board_ordinary_chat_is_isolated_from_requirements_and_board_agent(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text=" \n")
    parse_calls: list[dict[str, object]] = []

    def fake_parse(_self, **kwargs):
        parse_calls.append(kwargs)
        if kwargs.get("allow_live_web_search"):
            return SimpleNamespace(
                output_parsed=OrdinaryChatTurnResponse(
                    chatbot_message="A live, network-backed conversational reply."
                )
            )
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="There is no learning request in this turn.",
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("ordinary chat must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Just chatting.", conversation=[]),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "A live, network-backed conversational reply."
    assert response.active_requirement_sheet is None
    assert response.board_document_operation_status == "none"
    assert response.learning_clarification.reason == ""
    assert response.learning_clarification.summary == ""
    assert len(parse_calls) == 2
    assert parse_calls[0].get("allow_live_web_search") is not True
    assert parse_calls[1]["allow_live_web_search"] is True
    assert "Current user message:\nJust chatting." in parse_calls[0]["user_prompt"]
    assert "Current user message:\nJust chatting." in parse_calls[1]["user_prompt"]
    assert "board summary" not in parse_calls[1]["user_prompt"].lower()
    assert "active learning requirement" not in parse_calls[1]["user_prompt"].lower()
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text.strip() == ""
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["requirement_changed"] is False
    assert commit.metadata["chatbot_web_search_mode"] == "live"
    assert commit.metadata["chatbot_raw_network_access"] is False
    assert "requirement_version_id" not in commit.metadata
    assert "active_requirement_sheet_after" not in commit.metadata
    assert "learning_clarification_after" not in commit.metadata


def test_empty_board_live_chat_failure_does_not_commit_or_change_requirements(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    before_head = current_head_commit(lesson).id
    parse_calls = 0

    def fake_parse(_self, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        if kwargs.get("allow_live_web_search"):
            raise CodexAppServerError("live web search failed")
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="There is no learning request in this turn.",
            )
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)

    with pytest.raises(CodexAppServerError, match="live web search failed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="What is happening right now?"),
            user_id=TEST_USER_ID,
        )

    assert parse_calls == 2
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == ""
    assert blank_board_intake.active_requirement_from_history(saved_lesson) is None


def test_empty_board_requirement_collection_persists_and_ordinary_chat_preserves_it(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    requirement_updates: list[dict[str, object]] = []
    parse_calls: list[dict[str, object]] = []
    decisions = iter(
        [
            BlankBoardTurnDecision(
                intent="learning_need",
                teaching_type="knowledge_point",
                learning_content="A broad field",
                content_is_specific=False,
                chatbot_message="Two contextual directions and one narrowing question.",
                next_question="Which direction should become the first focused concept?",
                reason="The learning content is still too broad.",
            ),
            BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="This turn does not change the learning request.",
            ),
            OrdinaryChatTurnResponse(
                chatbot_message="A separate live conversational reply."
            ),
        ]
    )

    def fake_parse(_self, **kwargs):
        parse_calls.append(kwargs)
        return SimpleNamespace(output_parsed=next(decisions))

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("an incomplete requirement must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    collecting = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="I want to learn a broad field."),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )
    preserved = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Unrelated small talk."),
        user_id=TEST_USER_ID,
    )

    assert collecting.active_requirement_sheet is not None
    assert collecting.active_requirement_sheet.teaching_type == "knowledge_point"
    assert collecting.learning_clarification.missing_items == [
        "learning_content",
        "current_level",
        "target_scenario",
    ]
    assert collecting.requirement_version_id is not None
    assert [update["requirement_phase"] for update in requirement_updates] == [
        "collecting"
    ]
    assert preserved.active_requirement_sheet == collecting.active_requirement_sheet
    assert preserved.chatbot_message == "A separate live conversational reply."
    assert preserved.learning_clarification == collecting.learning_clarification
    assert preserved.requirement_phase == "collecting"
    assert preserved.requirement_version_id is None
    assert parse_calls[2]["allow_live_web_search"] is True
    assert "A broad field" not in parse_calls[2]["user_prompt"]
    assert "Active learning requirement" not in parse_calls[2]["user_prompt"]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text == ""
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["requirement_changed"] is False
    assert commit.metadata["requirement_version_id"] is None
    assert commit.metadata["requirement_phase"] == "collecting"
    assert commit.metadata["active_requirement_sheet_after"] == (
        collecting.active_requirement_sheet.model_dump(mode="json")
    )
    assert commit.metadata["learning_clarification_after"] == (
        collecting.learning_clarification.model_dump(mode="json")
    )
    restored = blank_board_intake.active_requirement_from_history(saved_lesson)
    assert restored is not None
    assert restored.learning_content == "A broad field"


def test_empty_board_unclear_guidance_is_saved_as_displayable_basic_chat(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="unclear",
                chatbot_message="Contextual directions with one choice.",
                next_question="Which direction fits what you want right now?",
                reason="The learning purpose is not confirmed yet.",
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("unclear intent must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Maybe I should learn something useful."),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert response.active_requirement_sheet is None
    assert response.needs_clarification is True
    assert response.clarification_questions == [
        "Which direction fits what you want right now?"
    ]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["blank_board_route"] == "guided_discovery"
    assert "active_requirement_sheet_after" not in commit.metadata


def test_empty_board_untyped_learning_guidance_persists_confirmed_theme(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    requirement_updates: list[dict[str, object]] = []

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="learning_need",
                teaching_type=None,
                learning_content="A broad learning theme",
                chatbot_message="Here are two ways to begin, with one recommended first step.",
                next_question="Which starting direction feels closest to where you are now?",
                reason="The theme is clear but the learning mode is not yet resolved.",
                guidance=GuidedRequirementDiscovery(
                    strategy="level_discovery",
                    selection_target="current_level",
                    question_title="Which starting level is closest to you?",
                    learning_map_summary="A short map of the available starting directions.",
                    entry_point_options=[
                        GuidedRequirementEntryPoint(
                            title="Build a first foundation",
                            description="Establish one dependable entry point.",
                            answer_value="New to the prerequisites",
                            why_it_matters="The first board should establish the prerequisites.",
                            best_for="A learner without a dependable starting point.",
                        ),
                        GuidedRequirementEntryPoint(
                            title="Start from a concrete outcome",
                            description="Choose the entry point through a task you want to complete.",
                            answer_value="Can complete a related task with support",
                            why_it_matters="A task can expose the next useful gap.",
                            best_for="A learner with some practical exposure.",
                        ),
                    ],
                    recommended_entry_point="Build a first foundation",
                    reason_for_recommendation="No level or target outcome is confirmed yet.",
                ),
            )
        )

    def fail_if_board_agent_runs(**_kwargs):
        raise AssertionError("untyped learning guidance must not enter the board agent")

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fail_if_board_agent_runs)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="I want to learn a broad learning theme."),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.teaching_type is None
    assert response.active_requirement_sheet.work_mode == "unknown"
    assert response.requirement_phase == "collecting"
    assert response.requirement_version_id is not None
    assert [update["requirement_phase"] for update in requirement_updates] == ["collecting"]
    assert response.guided_requirement_discovery is not None
    assert response.guided_requirement_discovery.recommended_entry_point == "Build a first foundation"
    assert requirement_updates[0]["guided_requirement_discovery"] == {
        "strategy": "level_discovery",
        "selection_target": "current_level",
        "question_title": "Which starting level is closest to you?",
        "learning_map_summary": "A short map of the available starting directions.",
        "entry_point_options": [
            {
                "title": "Build a first foundation",
                "description": "Establish one dependable entry point.",
                "answer_value": "New to the prerequisites",
                "why_it_matters": "The first board should establish the prerequisites.",
                "best_for": "A learner without a dependable starting point.",
            },
            {
                "title": "Start from a concrete outcome",
                "description": "Choose the entry point through a task you want to complete.",
                "answer_value": "Can complete a related task with support",
                "why_it_matters": "A task can expose the next useful gap.",
                "best_for": "A learner with some practical exposure.",
            },
        ],
        "recommended_entry_point": "Build a first foundation",
        "reason_for_recommendation": "No level or target outcome is confirmed yet.",
        "learner_profile_inference": "",
    }
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["blank_board_route"] == "guided_discovery"
    assert commit.metadata["requirement_phase"] == "collecting"
    assert commit.metadata["guided_requirement_discovery"]["recommended_entry_point"] == (
        "Build a first foundation"
    )


def test_complete_empty_board_requirement_is_frozen_before_board_generation(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    generation_prompts: list[str] = []
    requirement_updates: list[dict[str, object]] = []

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(
            output_parsed=BlankBoardTurnDecision(
                intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A specific concept",
                    content_is_specific=True,
                    current_level="Can describe the basic idea with support",
                    target_scenario="Preparing for an upcoming course",
                    chatbot_message="The board has been prepared.",
                    teaching_plan="Build a focused explanation with checks for understanding.",
                    reason="All core factors are complete.",
            )
        )

    def fake_board_turn(**kwargs) -> CodexTurnResult:
        generation_prompts.append(kwargs["user_prompt"])
        assert [update["requirement_phase"] for update in requirement_updates] == [
            "ready",
            "frozen",
        ]
        frozen_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
        frozen_commit = current_head_commit(frozen_lesson)
        assert frozen_commit.metadata["kind"] == "learning_requirement_frozen"
        assert frozen_commit.metadata["requirement_phase"] == "frozen"
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Focused board\n\nBoard content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_frozen_generation",
            turn_id="turn_frozen_generation",
            final_response="Generated.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_board_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Start now, but this exact request text is not board input.",
            conversation=[],
        ),
        user_id=TEST_USER_ID,
        on_requirement_update=requirement_updates.append,
    )

    assert response.board_document_operation_status == "succeeded"
    assert response.requirement_phase == "consumed"
    assert response.active_requirement_sheet is None
    assert [update["requirement_phase"] for update in requirement_updates] == [
        "ready",
        "frozen",
    ]
    assert len(generation_prompts) == 1
    assert '"learning_content":"A specific concept"' in generation_prompts[0]
    assert '"teaching_plan":"Build a focused explanation with checks for understanding."' in generation_prompts[0]
    assert "this exact request text" not in generation_prompts[0]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    generation_commit = current_head_commit(saved_lesson)
    assert saved_lesson.board_document.content_text == "# Focused board\n\nBoard content."
    assert generation_commit.metadata["kind"] == "board_document_generation"
    assert generation_commit.metadata["requirement_phase"] == "consumed"
    assert generation_commit.metadata["board_generation_codex_thread_id"] == (
        "thread_frozen_generation"
    )
    assert "codex_thread_id" not in generation_commit.metadata
    assert codex_chat._thread_reference_for_current_branch(saved_lesson) == (None, None)
    assert blank_board_intake._latest_requirement_run_id(saved_lesson) is None
    frozen_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_frozen"
    )
    assert generation_commit.parent_ids == [frozen_commit.id]
    ready_commit = next(
        commit
        for commit in saved_lesson.history_graph.commits
        if commit.metadata.get("kind") == "learning_requirement_completed"
    )
    assert frozen_commit.parent_ids == [ready_commit.id]
    assert ready_commit.metadata["requirement_phase"] == "ready"
    assert frozen_commit.metadata["requirement_parent_version_id"] == (
        ready_commit.metadata["requirement_version_id"]
    )


def test_failed_empty_board_generation_keeps_frozen_requirement_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="")
    decisions = iter(
        [
            BlankBoardTurnDecision(
                intent="learning_need",
                    teaching_type="knowledge_point",
                    learning_content="A specific concept",
                    content_is_specific=True,
                    current_level="Can describe the basic idea with support",
                    target_scenario="Preparing for an upcoming course",
                    chatbot_message="Preparing the board.",
                    teaching_plan="Create a focused board.",
                reason="The requirement is complete.",
            ),
            BlankBoardTurnDecision(
                intent="ordinary_chat",
                chatbot_message="The offline intake reply must not be shown.",
                reason="This turn is unrelated to the frozen requirement.",
            ),
            OrdinaryChatTurnResponse(
                chatbot_message="A separate live conversational reply."
            ),
        ]
    )

    def fake_parse(_self, **_kwargs):
        return SimpleNamespace(output_parsed=next(decisions))

    generation_calls = 0

    def generate_with_one_failure(**kwargs):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            raise CodexAppServerError("generation failed")
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Retried board\n\nRecovered content.", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_retry_same_frozen_version",
            turn_id="turn_retry_same_frozen_version",
            final_response="The board is ready after the retry.",
        )

    monkeypatch.setattr(blank_board_intake.CodexAppServerTextClient, "parse", fake_parse)
    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", generate_with_one_failure)

    with pytest.raises(CodexAppServerError, match="generation failed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Generate the focused board."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    failure_commit = current_head_commit(saved_lesson)
    failed_version_id = failure_commit.metadata["requirement_version_id"]
    restored = blank_board_intake.active_requirement_from_history(saved_lesson)
    assert saved_lesson.board_document.content_text == ""
    assert failure_commit.metadata["kind"] == "learning_requirement_generation_failed"
    assert failure_commit.metadata["requirement_phase"] == "frozen"
    assert restored is not None
    assert restored.learning_content == "A specific concept"

    preserved = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Unrelated small talk after the failure."),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ordinary_commit = current_head_commit(saved_lesson)
    assert preserved.requirement_phase == "frozen"
    assert preserved.active_requirement_sheet is not None
    assert preserved.active_requirement_sheet.learning_content == "A specific concept"
    assert ordinary_commit.metadata["kind"] == "basic_chat"
    assert ordinary_commit.metadata["requirement_phase"] == "frozen"
    assert ordinary_commit.metadata["requirement_version_id"] is None
    assert ordinary_commit.metadata["active_requirement_version_id"] == failed_version_id
    assert ordinary_commit.metadata["active_requirement_sheet_after"] == (
        failure_commit.metadata["active_requirement_sheet_after"]
    )
    assert ordinary_commit.metadata["learning_clarification_after"] == (
        failure_commit.metadata["learning_clarification_after"]
    )

    retried = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Start board generation again.",
            board_generation_action="start",
        ),
        user_id=TEST_USER_ID,
    )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    retry_commit = current_head_commit(saved_lesson)
    assert generation_calls == 2
    assert retried.chatbot_message == "The board is ready after the retry."
    assert retry_commit.metadata["kind"] == "board_document_generation"
    assert retry_commit.metadata["requirement_retry"] is True
    assert retry_commit.metadata["requirement_version_id"] == failed_version_id
    assert saved_lesson.board_document.content_text == (
        "# Retried board\n\nRecovered content."
    )


def test_codex_instructions_separate_blank_intake_from_board_grounded_teaching() -> None:
    instructions = codex_chat.CODEX_DEVELOPER_INSTRUCTIONS
    normalized_instructions = " ".join(instructions.split())
    intake = blank_board_intake.BLANK_BOARD_INTAKE_INSTRUCTIONS
    normalized_intake = " ".join(intake.split())
    generation = codex_chat.BOARD_GENERATION_DEVELOPER_INSTRUCTIONS
    normalized_generation = " ".join(generation.split())

    assert "start of every turn, read the current `board.md`" in instructions
    assert "sole source of truth" in instructions
    assert "For a non-empty board" in instructions
    assert "Never put a standalone lesson" in normalized_instructions
    assert "do not duplicate the board's substantive content in chat" in normalized_instructions
    assert "use fenced code blocks only for executable or source code" in normalized_instructions
    assert "Write display formulas as `$$` on their own lines with LaTeX inside" in instructions
    assert "do not create or change the learning requirement sheet" in normalized_intake
    assert "Select exactly one teaching type before generation" in normalized_intake
    assert "they never compensate for a missing core factor" in normalized_intake
    assert "This intake role never writes `board.md` itself" in normalized_intake
    assert "Resolve exactly one blocking uncertainty per turn" in normalized_intake
    assert "selection_target=\"current_level\"" in normalized_intake
    assert "chatbot_message` must present the choices as plain chat text" in normalized_intake
    assert "Do not rely on clickable cards" in normalized_intake
    assert "exactly one short line" in normalized_intake
    assert "Do not show `description`, `why_it_matters`, `best_for`" in normalized_intake
    assert "Do not add a separate recommendation-reason paragraph" in normalized_intake
    assert "AI-generated compact field map" in normalized_intake
    assert "first guidance turn after a broad learning direction becomes known" in normalized_intake
    assert "actual parts of the current field" in normalized_intake
    assert "Do not repeat the map" in normalized_intake
    assert "rebuild it when the learner changes the learning content" in normalized_intake
    assert "frozen, structured learning requirement" in generation
    assert "Generate a self-contained teaching board from only that payload" in generation
    assert "Use fenced code blocks only for real code" in normalized_generation


@pytest.fixture
def codex_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setenv("OPENCLASS_CODEX_WORKSPACE_ROOT", str(tmp_path / "codex-workspaces"))
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda _thread_id, **_kwargs: None,
    )
    return store


def test_codex_chat_preserves_frontend_contract_and_persists_thread(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        activity = AgentActivityEvent(
            id=f"activity_{len(calls)}",
            turn_id=f"turn_{len(calls)}",
            stage="build_context",
            label="Codex 已完成思考",
            role="Codex",
            metadata={"kind": "reasoning", "detail": "Inspected the current board."},
        )
        return CodexTurnResult(
            thread_id="thread_codex_1",
            turn_id=f"turn_{len(calls)}",
            final_response="Codex reply",
            activity=[activity],
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        codex_chat,
        "build_model_catalog",
        lambda _user_id: SimpleNamespace(
            defaults={"text": SimpleNamespace(model="gpt-5.6-sol")}
        ),
    )

    first = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Explain this without editing it.",
            text_model={
                "provider": "openai_codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
        ),
        user_id=TEST_USER_ID,
    )
    second = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Continue."),
        user_id=TEST_USER_ID,
    )

    assert first.chatbot_message == "Codex reply"
    assert first.agent_activity[0].metadata["detail"] == "Inspected the current board."
    assert first.board_decision.action == "no_change"
    assert first.board_document_operation_status == "none"
    assert first.requirement_cleared is True
    assert first.active_requirement_sheet is None
    assert first.active_board_task_sheet is None
    assert first.learning_clarification.can_start is False
    assert first.learning_clarification.ready_for_board is False
    assert first.course_package.lessons[0].learning_requirements is None
    assert second.chatbot_message == "Codex reply"
    assert calls[0]["thread_id"] is None
    assert calls[0]["model"] == "gpt-5.6-sol"
    assert calls[0]["reasoning_effort"] == "xhigh"
    assert calls[0]["service_tier"] == "priority"
    assert calls[0]["service_tier_is_set"] is True
    assert calls[1]["thread_id"] == "thread_codex_1"
    assert calls[1]["last_turn_id"] == "turn_1"
    assert calls[1]["model"] == "gpt-5.6-sol"
    assert calls[1]["service_tier_is_set"] is False

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert saved_lesson.learning_requirements is None
    assert saved_lesson.board_task_requirements is None
    assert saved_lesson.active_interaction_session is None
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["assistant_message_source"] == "codex"
    assert commit.metadata["codex_thread_id"] == "thread_codex_1"
    assert commit.metadata["document_changed"] is False
    assert commit.metadata["requirement_cleared"] is True
    assert commit.metadata["agent_activity"][0]["id"] == "activity_2"
    configured_commit = next(
        item
        for item in saved_lesson.history_graph.commits
        if item.metadata.get("codex_turn_id") == "turn_1"
    )
    assert configured_commit.metadata["codex_model"] == "gpt-5.6-sol"
    assert configured_commit.metadata["codex_reasoning_effort"] == "xhigh"
    assert configured_commit.metadata["codex_service_tier"] == "priority"
    assert configured_commit.metadata["codex_service_tier_is_set"] is True
    assert commit.metadata["codex_service_tier_is_set"] is False


def test_codex_chat_writes_only_final_markdown_back_to_rich_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    original_document_id = lesson.board_document.id
    original_page_settings = lesson.board_document.page_settings

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Revised board\n\n- First\n- Second\n", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_codex_edit",
            turn_id="turn_edit",
            final_response="Updated the document.",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    response = codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(message="Rewrite the right document."),
        user_id=TEST_USER_ID,
    )

    document = response.course_package.lessons[0].board_document
    assert response.board_decision.action == "edit_board"
    assert response.board_document_operation_status == "succeeded"
    assert document.id == original_document_id
    assert document.page_settings == original_page_settings
    assert document.content_text == "# Revised board\n\n- First\n- Second"
    assert "<h1>Revised board</h1>" in document.content_html
    assert document.content_json["content"][0]["type"] == "heading"
    assert list(codex_chat.codex_workspace_root().iterdir()) == []

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    commit = current_head_commit(saved_lesson)
    assert commit.metadata["kind"] == "board_document_edit"
    assert commit.metadata["codex_turn_id"] == "turn_edit"
    assert commit.metadata["document_changed"] is True


def test_codex_chat_passes_formula_ink_as_image_input(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    calls: list[dict[str, object]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        calls.append(kwargs)
        return CodexTurnResult("thread_formula", "turn_formula", "formula reviewed")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    codex_chat.process_codex_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="Use this handwritten formula.",
            formula_ink={
                "image_data_url": "data:image/png;base64,YQ==",
                "source_latex": None,
                "action": "reference",
            },
        ),
        user_id=TEST_USER_ID,
    )

    assert calls[0]["image_urls"] == ["data:image/png;base64,YQ=="]


def test_codex_chat_rejects_unexpected_file_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    before = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    before_head = current_head_commit(before).id
    before_commit_count = len(before.history_graph.commits)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = Path(kwargs["cwd"])
        (workspace / "unexpected.txt").write_text("not allowed", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread_bad",
            turn_id="turn_bad",
            final_response="done",
        )

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="unexpected file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Create another file."),
            user_id=TEST_USER_ID,
        )

    after = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(after).id == before_head
    assert len(after.history_graph.commits) == before_commit_count
    assert after.board_document.content_text == "# Existing board"


def test_codex_chat_rejects_html_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("<h1>HTML is not allowed</h1>", encoding="utf-8")
        return CodexTurnResult("thread_html", "turn_html", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="contains HTML"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Use HTML."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Existing board"
    assert discarded_threads == ["thread_html"]


def test_codex_chat_rejects_symlink_board_without_reading_target(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
    tmp_path: Path,
) -> None:
    lesson = _seed_workspace(codex_store)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret outside content", encoding="utf-8")

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.unlink()
        board_path.symlink_to(outside)
        return CodexTurnResult("thread_symlink", "turn_symlink", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="regular file"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Read another file."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Existing board"


def test_codex_chat_detects_concurrent_document_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)

    def fake_turn(**kwargs) -> CodexTurnResult:
        workspace = codex_store.load_for_user(TEST_USER_ID)
        current_lesson = workspace.packages[0].lessons[0]
        changed_document = build_document(
            title=current_lesson.board_document.title,
            content_text="# Manual change",
            document_id=current_lesson.board_document.id,
            page_settings=current_lesson.board_document.page_settings,
        )
        commit_operations(
            current_lesson,
            [],
            label="Manual edit",
            message="Concurrent manual edit",
            new_document=changed_document,
            metadata={"kind": "manual_document_edit"},
        )
        codex_store.save_for_user(TEST_USER_ID, workspace)
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)

    with pytest.raises(CodexAppServerError, match="changed while Codex was working"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Rewrite it."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Manual change"
    assert current_head_commit(saved_lesson).metadata["kind"] == "manual_document_edit"


def test_codex_chat_serializes_turns_and_reloads_latest_document(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    calls: list[tuple[str | None, str]] = []

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        calls.append((kwargs["thread_id"], board_path.read_text(encoding="utf-8")))
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
            board_path.write_text("# First update", encoding="utf-8")
            return CodexTurnResult("thread_shared", "turn_first", "first done")
        second_entered.set()
        board_path.write_text("# Second update", encoding="utf-8")
        return CodexTurnResult("thread_shared", "turn_second", "second done")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    errors: list[BaseException] = []

    def run(message: str) -> None:
        try:
            codex_chat.process_codex_chat_on_lesson(
                lesson.id,
                ChatRequest(message=message),
                user_id=TEST_USER_ID,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert errors == []
    assert calls == [
        (None, "# Existing board"),
        ("thread_shared", "# First update"),
    ]
    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Second update"


def test_codex_chat_atomic_save_rejects_last_moment_target_change(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store)
    discarded_threads: list[str] = []
    monkeypatch.setattr(
        codex_chat,
        "delete_codex_thread",
        lambda thread_id, **_kwargs: discarded_threads.append(thread_id),
    )

    def fake_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_text("# Codex update", encoding="utf-8")
        return CodexTurnResult("thread_conflict", "turn_conflict", "done")

    original_atomic_save = workspace_state.save_lesson_for_user_if_head

    def conflicting_atomic_save(user_id, next_lesson, **kwargs) -> bool:
        concurrent_workspace = codex_store.load_for_user(TEST_USER_ID)
        _package, concurrent_lesson = workspace_state.find_lesson_package(
            concurrent_workspace,
            lesson.id,
        )
        concurrent_document = build_document(
            title=concurrent_lesson.board_document.title,
            content_text="# Concurrent user update",
            document_id=concurrent_lesson.board_document.id,
            page_settings=concurrent_lesson.board_document.page_settings,
        )
        commit_operations(
            concurrent_lesson,
            operations=[],
            label="Concurrent update",
            message="A different writer changed the lesson.",
            new_document=concurrent_document,
        )
        codex_store.save_for_user(TEST_USER_ID, concurrent_workspace)
        return original_atomic_save(user_id, next_lesson, **kwargs)

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", fake_turn)
    monkeypatch.setattr(
        workspace_state,
        "save_lesson_for_user_if_head",
        conflicting_atomic_save,
    )

    with pytest.raises(CodexAppServerError, match="lesson changed"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Update the document."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "# Concurrent user update"
    assert discarded_threads == ["thread_conflict"]


def test_lesson_atomic_save_preserves_concurrent_other_lesson_change(
    codex_store: SqliteCourseStore,
) -> None:
    target = _seed_workspace(codex_store)
    setup_workspace = codex_store.load_for_user(TEST_USER_ID)
    other = create_empty_lesson("Other lesson")
    setup_workspace.packages[0].lessons.append(other)
    codex_store.save_for_user(TEST_USER_ID, setup_workspace)

    codex_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, codex_target = workspace_state.find_lesson_package(codex_workspace, target.id)
    base_commit_id = current_head_commit(codex_target).id
    target_document = build_document(
        title=codex_target.board_document.title,
        content_text="# Codex target update",
        document_id=codex_target.board_document.id,
        page_settings=codex_target.board_document.page_settings,
    )
    commit_operations(
        codex_target,
        operations=[],
        label="Codex update",
        message="Update only the target lesson.",
        new_document=target_document,
    )

    latest_workspace = codex_store.load_for_user(TEST_USER_ID)
    _package, latest_other = workspace_state.find_lesson_package(latest_workspace, other.id)
    other_document = build_document(
        title=latest_other.board_document.title,
        content_text="# Other concurrent update",
        document_id=latest_other.board_document.id,
        page_settings=latest_other.board_document.page_settings,
    )
    commit_operations(
        latest_other,
        operations=[],
        label="Other update",
        message="Update a different lesson.",
        new_document=other_document,
    )
    codex_store.save_for_user(TEST_USER_ID, latest_workspace)

    assert codex_store.save_lesson_for_user_if_head(
        TEST_USER_ID,
        codex_target,
        expected_branch_name=codex_target.history_graph.current_branch,
        expected_head_commit_id=base_commit_id,
    )
    saved = codex_store.load_for_user(TEST_USER_ID)
    _package, saved_target = workspace_state.find_lesson_package(saved, target.id)
    _package, saved_other = workspace_state.find_lesson_package(saved, other.id)
    assert saved_target.board_document.content_text == "# Codex target update"
    assert saved_other.board_document.content_text == "# Other concurrent update"


def test_codex_chat_rejects_oversized_existing_board_before_turn(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="123456789")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "8")
    monkeypatch.setattr(
        codex_chat,
        "run_codex_thread_turn",
        lambda **_kwargs: pytest.fail("Codex must not start for an oversized board"),
    )

    with pytest.raises(CodexAppServerError, match="current board exceeds"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Continue."),
            user_id=TEST_USER_ID,
        )


def test_codex_chat_cancels_turn_when_board_exceeds_runtime_quota(
    monkeypatch: pytest.MonkeyPatch,
    codex_store: SqliteCourseStore,
) -> None:
    lesson = _seed_workspace(codex_store, content_text="# Board")
    monkeypatch.setenv("OPENCLASS_CODEX_BOARD_MAX_BYTES", "64")
    before_head = current_head_commit(
        codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    ).id

    def oversized_turn(**kwargs) -> CodexTurnResult:
        board_path = Path(kwargs["cwd"]) / codex_chat.BOARD_FILE_NAME
        board_path.write_bytes(b"x" * 1024)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if kwargs["is_cancelled"]():
                raise CodexTurnCancelledError("cancelled by quota")
            time.sleep(0.01)
        pytest.fail("quota monitor did not cancel the Codex turn")

    monkeypatch.setattr(codex_chat, "run_codex_thread_turn", oversized_turn)

    with pytest.raises(CodexAppServerError, match="configured size limit"):
        codex_chat.process_codex_chat_on_lesson(
            lesson.id,
            ChatRequest(message="Make it very large."),
            user_id=TEST_USER_ID,
        )

    saved_lesson = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert current_head_commit(saved_lesson).id == before_head
    assert saved_lesson.board_document.content_text == "# Board"


def test_board_quota_cannot_exceed_process_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "OPENCLASS_CODEX_BOARD_MAX_BYTES",
        str(CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES + 1),
    )

    with pytest.raises(CodexAppServerError, match="process hard limit"):
        codex_chat._board_max_bytes()


def test_codex_app_server_command_uses_exact_board_permission_profile() -> None:
    command = codex_app_server._codex_app_server_command("/usr/local/bin/codex")
    rendered = "\n".join(command)

    assert 'default_permissions="openclass_board"' in rendered
    assert '":workspace_roots"={"board.md"="write"}' in rendered
    assert "permissions.openclass_board.network.enabled=false" in rendered
    assert 'permissions.openclass_chat.filesystem={":minimal"="read"}' in rendered
    assert "permissions.openclass_chat.network.enabled=false" in rendered
    assert 'approval_policy="never"' in rendered
    assert 'web_search="disabled"' in rendered
    assert "mcp_servers={}" in rendered
    assert "apps={_default={enabled=false}}" in rendered
    assert "features.apps=false" in rendered
    assert "features.hooks=false" in rendered
    assert "features.plugins=false" in rendered
    assert "features.computer_use=false" in rendered
    assert "--strict-config" in command
    assert "danger-full-access" not in rendered


def test_codex_app_server_process_has_file_size_hard_limit() -> None:
    command = codex_app_server._codex_limited_process_command("/usr/local/bin/codex")

    assert command[:2] == ["/bin/sh", "-c"]
    assert "ulimit -f" in command[2]
    assert str(CODEX_PROCESS_FILE_SIZE_LIMIT_BYTES // 1024) in command
    assert command[5:8] == ["/usr/local/bin/codex", "app-server", "--strict-config"]
    assert command[-2:] == ["-c", "features.workspace_dependencies=false"]


def test_effective_codex_config_rejects_legacy_sandbox_override() -> None:
    disabled_features = {
        feature: False
        for feature in (
            "apps",
            "auth_elicitation",
            "browser_use",
            "browser_use_external",
            "browser_use_full_cdp_access",
            "code_mode_host",
            "computer_use",
            "goals",
            "hooks",
            "image_generation",
            "in_app_browser",
            "multi_agent",
            "plugin_sharing",
            "plugins",
            "remote_plugin",
            "skill_mcp_dependency_install",
            "tool_call_mcp_elicitation",
            "tool_suggest",
            "workspace_dependencies",
        )
    }
    valid = {
        "config": {
            "sandbox_mode": None,
            "default_permissions": "openclass_board",
            "approval_policy": "never",
            "web_search": "disabled",
            "permissions": {
                "openclass_board": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                        ":workspace_roots": {"board.md": "write"},
                    },
                    "network": {"enabled": False, "domains": None},
                },
                "openclass_chat": {
                    "filesystem": {
                        "glob_scan_max_depth": None,
                        ":minimal": "read",
                    },
                    "network": {"enabled": False, "domains": None},
                },
            },
            "shell_environment_policy": {"inherit": "none"},
            "features": disabled_features,
            "mcp_servers": {},
            "apps": {"_default": {"enabled": False}},
            "hooks": None,
            "plugins": {},
        }
    }

    codex_app_server._validate_effective_permission_config(valid)
    invalid = {"config": {**valid["config"], "sandbox_mode": "workspace-write"}}
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(invalid)

    external_tool = {
        "config": {
            **valid["config"],
            "features": {**disabled_features, "plugins": True},
        }
    }
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(external_tool)

    broad_chat_read = {
        "config": {
            **valid["config"],
            "permissions": {
                **valid["config"]["permissions"],
                "openclass_chat": {
                    **valid["config"]["permissions"]["openclass_chat"],
                    "filesystem": {":root": "read"},
                },
            },
        }
    }
    with pytest.raises(CodexAppServerError, match="exact board and isolated Chatbot profiles"):
        codex_app_server._validate_effective_permission_config(broad_chat_read)


def test_new_and_reloaded_lessons_hide_legacy_ai_runtime(
    codex_store: SqliteCourseStore,
) -> None:
    lesson = create_empty_lesson("Codex-only lesson")
    initial_metadata = current_head_commit(lesson).metadata

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.active_interaction_session is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None
    assert initial_metadata["active_requirement_sheet_after"] is None
    assert initial_metadata["active_board_task_sheet_after"] is None
    assert initial_metadata["active_interaction_session_after"] is None

    lesson.learning_requirements = build_requirements(lesson.title)
    workspace = build_initial_workspace_state()
    workspace.packages[0].lessons.append(lesson)
    codex_store.save_for_user(TEST_USER_ID, workspace)

    reloaded = codex_store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert reloaded.learning_requirements is None
    assert reloaded.board_task_requirements is None
    assert reloaded.active_interaction_session is None
    assert reloaded.board_teaching_guide is None
    assert reloaded.board_teaching_progress is None


def test_branch_restore_does_not_revive_legacy_ai_runtime() -> None:
    lesson = create_empty_lesson("Legacy runtime")
    lesson.history_graph.commits[0].metadata["active_requirement_sheet_after"] = (
        build_requirements(lesson.title).model_dump(mode="json")
    )
    lesson.learning_requirements = build_requirements(lesson.title)

    create_branch(lesson, "codex-only", lesson.history_graph.commits[0].id)

    assert lesson.learning_requirements is None
    assert lesson.board_task_requirements is None
    assert lesson.active_interaction_session is None
    assert lesson.board_teaching_guide is None
    assert lesson.board_teaching_progress is None


def test_thread_permission_response_rejects_broad_writable_root(tmp_path: Path) -> None:
    codex_app_server._validate_thread_permission_response(
        _thread_result("thread_safe", tmp_path),
        cwd=tmp_path,
    )
    unsafe = _thread_result("thread_unsafe", tmp_path)
    unsafe["sandbox"]["writableRoots"] = [str(tmp_path.resolve())]

    with pytest.raises(CodexAppServerError, match="exact board.md-only sandbox"):
        codex_app_server._validate_thread_permission_response(unsafe, cwd=tmp_path)


def test_structured_chat_permission_response_rejects_broad_file_read() -> None:
    safe = {
        "thread": {"id": "thread_chat"},
        "activePermissionProfile": {"id": "openclass_chat"},
        "sandbox": {
            "type": "readOnly",
            "networkAccess": False,
        },
    }
    codex_app_server._validate_chat_thread_permission_response(safe)

    unsafe = {
        **safe,
        "activePermissionProfile": {"id": ":read-only"},
    }
    with pytest.raises(CodexAppServerError, match="exact isolated Chatbot profile"):
        codex_app_server._validate_chat_thread_permission_response(unsafe)


def test_structured_codex_adapter_keeps_default_decision_turn_offline() -> None:
    captured: dict[str, object] = {}

    class StopSession:
        deadline_monotonic = time.monotonic() + 5

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            captured.update(params)
            raise CodexAppServerError("stop after capturing thread params")

    with pytest.raises(CodexAppServerError, match="stop after capturing"):
        codex_app_server._run_structured_turn(
            session=StopSession(),  # type: ignore[arg-type]
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatRequest,
        )

    assert captured["config"] == {"default_permissions": "openclass_chat"}
    assert "sandbox" not in captured
    assert "call tools" in captured["developerInstructions"]


def test_structured_codex_adapter_uses_live_web_chat_profile() -> None:
    captured: dict[str, object] = {}

    class StopSession:
        deadline_monotonic = time.monotonic() + 5

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            captured.update(params)
            raise CodexAppServerError("stop after capturing thread params")

    with pytest.raises(CodexAppServerError, match="stop after capturing"):
        codex_app_server._run_structured_turn(
            session=StopSession(),  # type: ignore[arg-type]
            model="gpt-5.5",
            system_prompt="system",
            user_prompt="user",
            schema=ChatRequest,
            allow_live_web_search=True,
        )

    assert captured["config"] == {
        "default_permissions": "openclass_chat",
        "web_search": "live",
    }
    assert "sandbox" not in captured
    assert "built-in web search" in captured["developerInstructions"]


def test_conversation_turn_collects_delta_and_final_message() -> None:
    class FakeSession:
        _next_id = 7

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()
            self.writes: list[dict] = []

        def _write(self, payload: dict) -> None:
            self.writes.append(payload)

        def _answer_server_request(self, message: dict) -> None:
            raise AssertionError(message)

    session = FakeSession()
    session._messages.put({"id": 7, "result": {"turn": {"id": "turn_7"}}})
    session._messages.put(
        {
            "method": "item/agentMessage/delta",
            "params": {"delta": "partial"},
        }
    )
    session._messages.put(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "final response"}},
        }
    )
    session._messages.put(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn_7", "status": "completed"}},
        }
    )
    deltas: list[str] = []

    result = codex_app_server._run_conversation_turn(
        session=session,  # type: ignore[arg-type]
        thread_id="thread_7",
        model="gpt-5.5",
        cwd=Path("/tmp/board-only"),
        user_prompt="hello",
        image_urls=["data:image/png;base64,YQ=="],
        deadline_monotonic=time.monotonic() + 5,
        on_delta=deltas.append,
        is_cancelled=None,
        reasoning_effort="xhigh",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert result.thread_id == "thread_7"
    assert result.turn_id == "turn_7"
    assert result.final_response == "final response"
    assert deltas == ["partial"]
    params = session.writes[0]["params"]
    assert params["cwd"] == "/tmp/board-only"
    assert params["approvalPolicy"] == "never"
    assert params["effort"] == "xhigh"
    assert params["serviceTier"] == "priority"
    assert params["input"][1] == {
        "type": "image",
        "url": "data:image/png;base64,YQ==",
        "detail": "original",
    }
    assert "sandboxPolicy" not in params


def test_conversation_turn_separates_work_activity_from_final_answer() -> None:
    class FakeSession:
        _next_id = 9

        def __init__(self) -> None:
            self._messages: queue.Queue[dict] = queue.Queue()

        def _write(self, _payload: dict) -> None:
            return None

        def _answer_server_request(self, message: dict) -> None:
            raise AssertionError(message)

    session = FakeSession()
    for message in [
        {"id": 9, "result": {"turn": {"id": "turn_activity"}}},
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "reasoning_1", "type": "reasoning", "summary": [], "content": []},
            },
        },
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "reasoning_1",
                "delta": "检查板书结构",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "reasoning_1",
                    "type": "reasoning",
                    "summary": ["检查板书结构"],
                    "content": ["private reasoning must not be persisted"],
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "commentary_1", "type": "agentMessage", "phase": "commentary", "text": ""},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "commentary_1",
                "delta": "正在读取当前板书",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "commentary_1",
                    "type": "agentMessage",
                    "phase": "commentary",
                    "text": "正在读取当前板书",
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "command_1",
                    "type": "commandExecution",
                    "command": "sed -n '1,80p' board.md",
                    "cwd": "/tmp/board-only",
                    "status": "inProgress",
                },
            },
        },
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "command_1",
                "delta": "# Existing board\n",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "command_1",
                    "type": "commandExecution",
                    "command": "sed -n '1,80p' board.md",
                    "cwd": "/tmp/board-only",
                    "status": "completed",
                    "aggregatedOutput": "# Existing board\n",
                    "exitCode": 0,
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                "turnId": "turn_activity",
                "item": {"id": "final_1", "type": "agentMessage", "phase": "final_answer", "text": ""},
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "turnId": "turn_activity",
                "itemId": "final_1",
                "delta": "最终回复",
            },
        },
        {
            "method": "item/completed",
            "params": {
                "turnId": "turn_activity",
                "item": {
                    "id": "final_1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "最终回复",
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn_activity", "status": "completed"}},
        },
    ]:
        session._messages.put(message)

    final_deltas: list[str] = []
    activity_updates: list[AgentActivityEvent] = []
    result = codex_app_server._run_conversation_turn(
        session=session,  # type: ignore[arg-type]
        thread_id="thread_activity",
        model="gpt-5.5",
        cwd=Path("/tmp/board-only"),
        user_prompt="Explain the board",
        image_urls=[],
        deadline_monotonic=time.monotonic() + 5,
        on_delta=final_deltas.append,
        on_activity=activity_updates.append,
        is_cancelled=None,
    )

    assert result.final_response == "最终回复"
    assert final_deltas == ["最终回复"]
    assert [event.id for event in result.activity] == ["reasoning_1", "commentary_1", "command_1"]
    assert result.activity[0].metadata["detail"] == "检查板书结构"
    assert "private reasoning" not in str(result.activity[0].metadata)
    assert result.activity[1].metadata["detail"] == "正在读取当前板书"
    assert result.activity[2].metadata["detail"] == "# Existing board\n"
    assert all(event.status == "completed" for event in result.activity)
    assert len(activity_updates) > len(result.activity)


def test_runtime_settings_distinguish_inherited_and_standard_speed() -> None:
    inherited = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=False,
        include_effort=True,
    )
    standard = codex_app_server._runtime_setting_params(
        reasoning_effort=None,
        service_tier=None,
        service_tier_is_set=True,
        include_effort=True,
    )

    assert inherited == {}
    assert standard == {"serviceTier": None}


def test_existing_codex_thread_is_forked_before_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            return _thread_result("thread_fork", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_fork", "turn_fork", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="recovery prompt",
        developer_instructions="board only",
        thread_id="thread_base",
        last_turn_id="turn_base",
        service_tier="priority",
        service_tier_is_set=True,
    )

    assert session.requests[0][0] == "thread/fork"
    assert session.requests[0][1]["threadId"] == "thread_base"
    assert session.requests[0][1]["lastTurnId"] == "turn_base"
    assert session.requests[0][1]["ephemeral"] is False
    assert session.requests[0][1]["serviceTier"] == "priority"
    assert observed_prompts == ["normal prompt"]
    assert result.thread_id == "thread_fork"
    assert result.parent_thread_id == "thread_base"
    assert result.replaced_stale_thread_id is None


def test_stale_codex_thread_starts_fresh_with_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, **_kwargs) -> dict:
            self.requests.append((method, params))
            if method == "thread/fork":
                raise CodexAppServerError("thread not found")
            return _thread_result("thread_recovered", tmp_path)

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    observed_prompts: list[str] = []
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_run_conversation_turn",
        lambda **kwargs: (
            observed_prompts.append(kwargs["user_prompt"])
            or CodexTurnResult("thread_recovered", "turn_recovered", "done")
        ),
    )

    result = codex_app_server.run_codex_thread_turn(
        user_id=TEST_USER_ID,
        model="gpt-5.5",
        cwd=tmp_path,
        user_prompt="normal prompt",
        fallback_user_prompt="conversation recovery prompt",
        developer_instructions="board only",
        thread_id="thread_missing",
        service_tier=None,
        service_tier_is_set=True,
    )

    assert [method for method, _params in session.requests] == ["thread/fork", "thread/start"]
    assert all(params["serviceTier"] is None for _method, params in session.requests)
    assert observed_prompts == ["conversation recovery prompt"]
    assert result.thread_id == "thread_recovered"
    assert result.parent_thread_id == "thread_missing"
    assert result.replaced_stale_thread_id == "thread_missing"


def test_non_stale_fork_error_is_not_retried_as_a_new_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.methods: list[str] = []

        def validate_board_permission_config(self, _cwd: Path) -> None:
            return None

        def request(self, method: str, _params: dict, **_kwargs) -> dict:
            self.methods.append(method)
            raise CodexAppServerError("authentication failed")

    class FakeManagedSession:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        def __enter__(self) -> FakeSession:
            return self.session

        def __exit__(self, *_args) -> None:
            return None

    session = FakeSession()
    monkeypatch.setattr(
        codex_app_server,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True, message=""),
    )
    monkeypatch.setattr(
        codex_app_server,
        "_managed_session",
        lambda **_kwargs: FakeManagedSession(session),
    )

    with pytest.raises(CodexAppServerError, match="authentication failed"):
        codex_app_server.run_codex_thread_turn(
            user_id=TEST_USER_ID,
            model="gpt-5.5",
            cwd=tmp_path,
            user_prompt="normal prompt",
            fallback_user_prompt="recovery prompt",
            developer_instructions="board only",
            thread_id="thread_base",
        )

    assert session.methods == ["thread/fork"]
