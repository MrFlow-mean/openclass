from __future__ import annotations

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LibraryChapter,
    ResourceLibraryItem,
    ResourceSourceUnit,
)
from app.services import blank_board_generation, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BlankBoardRequirementRefinement, ChatbotReply, openai_course_ai
from app.services.rich_document import build_document


TEST_USER_ID = "user_resource_requirement_flow"


def _resource() -> ResourceLibraryItem:
    chapter = LibraryChapter(
        id="chapter_source_selection",
        title="Source selection workflow",
        summary="How a learning request is grounded in a selected source location.",
        keywords=["source", "selection", "workflow"],
        page_start=1,
        page_end=2,
    )
    return ResourceLibraryItem(
        id="resource_workflow",
        name="workflow-source.pdf",
        mime_type="application/pdf",
        resource_type="document",
        size_bytes=2048,
        outline=[chapter],
        concept_index={"selection": [chapter.id]},
        extracted_text_available=True,
        text_content=(
            "Source selection workflow\n\n"
            "A learning request should be matched to a specific source location before generation.\n"
            "The selected source location is handed to the document writer as evidence."
        ),
        parser_provider="raganything:mineru",
        source_units=[
            ResourceSourceUnit(
                content_type="text",
                text=(
                    "A learning request should be matched to a specific source location "
                    "before generation."
                ),
                page_idx=0,
                page_no=1,
                source_locator="raganything:text:item=0:page=1",
                order_index=0,
            ),
            ResourceSourceUnit(
                content_type="text",
                text="The selected source location is handed to the document writer as evidence.",
                page_idx=1,
                page_no=2,
                source_locator="raganything:text:item=1:page=2",
                order_index=1,
            ),
        ],
    )


def _seed_blank_lesson_with_resource(store: SqliteCourseStore) -> str:
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Resource grounded board")
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.resources.append(_resource().model_copy(update={"scope_lesson_id": lesson.id}))
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson.id


def _ready_refinement(**kwargs) -> BlankBoardRequirementRefinement:
    return BlankBoardRequirementRefinement(
        route="requirement_refining",
        chatbot_message="我先把这个学习目标收敛成一块可生成的板书。",
        learning_goal="Understand source selection workflow before generation",
        current_level="Has basic reading context",
        known_background="Can read structured notes",
        target_depth="Can explain why source evidence is selected before writing",
        output_preference="Board-style document",
        target_scenario="Use the generated board for study",
        summary="The learner wants a grounded board from a selected source location.",
        progress=100,
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )


def test_blank_requirement_refinement_suggests_matching_resource_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_blank_lesson_with_resource(store)
    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _ready_refinement)

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="I want to learn source selection workflow"),
        user_id=TEST_USER_ID,
    )

    assert response.reference_prompt is not None
    assert response.reference_prompt.resource_id == "resource_workflow"
    assert response.reference_prompt.chapter_id == "chapter_source_selection"
    assert response.resource_matches
    assert response.active_requirement_sheet is not None
    selected = response.active_requirement_sheet.selected_resource_reference
    assert selected is not None
    assert selected.status == "suggested"
    assert selected.source_locator == "raganything:text:item=0:page=1"

    history_state = store.load_learning_requirement_history_state(TEST_USER_ID, lesson_id)
    assert history_state is not None
    assert "selected_resource_reference" in str(history_state["latest_sheet_json"])


def test_confirmed_resource_reference_reaches_blank_board_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_blank_lesson_with_resource(store)
    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _ready_refinement)

    first = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="I want to learn source selection workflow"),
        user_id=TEST_USER_ID,
    )
    assert first.reference_prompt is not None

    confirmed = process_chat_on_lesson(
        lesson_id,
        ChatRequest(
            message="I want to learn source selection workflow",
            resource_reference_action="confirm",
            resource_reference_resource_id=first.reference_prompt.resource_id,
            resource_reference_chapter_id=first.reference_prompt.chapter_id,
        ),
        user_id=TEST_USER_ID,
    )

    assert confirmed.active_requirement_sheet is not None
    selected = confirmed.active_requirement_sheet.selected_resource_reference
    assert selected is not None
    assert selected.status == "confirmed"
    assert confirmed.selected_reference is not None
    assert confirmed.selected_reference.resource_id == "resource_workflow"

    captured: dict[str, object] = {}

    def _fake_generate_from_requirements(**kwargs):
        captured.update(kwargs)
        lesson = kwargs["lesson"]
        return BoardDocumentEditOutcome(
            chatbot_message="",
            new_document=build_document(
                title="Grounded board",
                content_text=(
                    "# Grounded board\n\n"
                    "## 1. Source selection\n\n"
                    "Source evidence is selected before writing."
                ),
                document_id=lesson.board_document.id,
                page_settings=lesson.board_document.page_settings,
            ),
            board_decision=BoardDecision(action="edit_board", reason="Generated from confirmed source."),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="Generated a grounded board.",
            section_titles=["1. Source selection"],
            changed=True,
            operation_status="succeeded",
        )

    monkeypatch.setattr(blank_board_generation, "generate_from_requirements", _fake_generate_from_requirements)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="Board generated from the selected source."),
    )

    generated = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    assert generated.board_document_operation_status == "succeeded"
    assert captured["reference_context"] is not None
    assert "workflow-source.pdf" in str(captured["resource_summary"])
    assert "Source selection workflow" in str(captured["resource_summary"])
    assert captured["requirements"].selected_resource_reference.status == "confirmed"

    saved = store.load_for_user(TEST_USER_ID)
    commit = saved.packages[0].lessons[-1].history_graph.commits[-1]
    assert commit.metadata["selected_resource_reference"]["status"] == "confirmed"
    assert commit.metadata["resource_reference_context"]["resource_id"] == "resource_workflow"
