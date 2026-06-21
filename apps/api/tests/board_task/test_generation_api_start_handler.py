from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.generation_api_start import (
    GenerationApiStartDependencies,
    handle_generation_api_start,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.workflow_trace import WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_generation_api_start_handler"


def _workspace_with_blank_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("Blank learning page")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _workspace_with_existing_board():
    workspace, package, lesson = _workspace_with_blank_lesson()
    refresh_lesson_runtime(
        lesson,
        document=build_document(title="Existing board", content_text="# Existing board\n\nReusable content."),
    )
    return workspace, package, lesson


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _incomplete_state():
    requirements = build_requirements("generic topic")
    requirements.learning_goal = "The learner wants a first board about a generic topic."
    requirements.level = "Some prior context is available."
    requirements.output_preference = "Board document"
    clarification = LearningClarificationStatus(
        progress=60,
        label="Needs one more detail",
        reason="The learner supplied enough context to force-start, but the sheet is not ideal yet.",
        missing_items=["preferred depth"],
        can_start=False,
        summary="The learner wants a first board about a generic topic.",
        key_facts=[
            LearningRequirementKeyFact(
                label="Learning content",
                value="generic topic",
                evidence="From the learner message.",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="Learning content is present",
                is_clear=True,
                evidence="From the learner message.",
            )
        ],
        next_question="What depth should the first board use?",
        ready_for_board=False,
    )
    return requirements, clarification


def _success_outcome(*, title: str = "Generated board") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="Generated the first board.",
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## Start\n\nThis is a generic first board.",
        ),
        board_decision=BoardDecision(action="edit_board", reason="Board generated."),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="Generated the first board.",
        section_titles=["Start"],
        changed=True,
        operation_status="succeeded",
    )


def _failed_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="Board generation failed.",
        new_document=build_document(title="Blank learning page", content_text=""),
        board_decision=BoardDecision(action="no_change", reason="Board generation failed."),
        assistant_message_source="board_document_editor_ai",
        operation=None,
        summary="The board editor did not return generated content.",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="The board editor did not return generated content.",
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _requirement_run_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_requirement_runs
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, id
            """,
            (TEST_USER_ID, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(TEST_USER_ID, lesson_id),
    )


def _handler_deps(
    *,
    latest_learning_clarification,
    generate_from_requirements,
    post_message,
    save_workspace_for_user,
    build_response,
    checkpoint_initial_requirement_before_generation=(
        chatbot_module._checkpoint_initial_requirement_before_generation
    ),
    commit_operations=chatbot_module.commit_operations,
) -> GenerationApiStartDependencies:
    return GenerationApiStartDependencies(
        latest_learning_clarification=latest_learning_clarification,
        with_task_details=chatbot_module._with_task_details,
        prepare_initial_requirement_for_board_generation=(
            chatbot_module._prepare_initial_requirement_for_board_generation
        ),
        checkpoint_initial_requirement_before_generation=checkpoint_initial_requirement_before_generation,
        generate_from_requirements=generate_from_requirements,
        refresh_lesson_runtime=chatbot_module.refresh_lesson_runtime,
        build_board_teaching_guide=chatbot_module.build_board_teaching_guide,
        post_initial_board_generation_message=post_message,
        commit_operations=commit_operations,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        board_document_failure_metadata=chatbot_module._board_document_failure_metadata,
        board_document_quality_metadata=chatbot_module._board_document_quality_metadata,
        requirement_history_metadata=chatbot_module._requirement_history_metadata,
        task_metadata=chatbot_module._task_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def test_handler_success_freezes_generates_commits_consumes_saves_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []
    saved_statuses: list[str | None] = []
    captured: dict[str, Any] = {}

    def _checkpoint(**kwargs):
        order.append("checkpoint")
        return chatbot_module._checkpoint_initial_requirement_before_generation(**kwargs)

    def _generate_from_requirements(**kwargs):
        order.append("generate")
        captured["state_before_generate"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        )
        captured["generate_kwargs"] = kwargs
        return _success_outcome()

    def _post_message(**kwargs):
        order.append("post_message")
        return "The board is ready.", "chatbot_post_board_generation"

    def _commit_operations(*args, **kwargs):
        order.append("commit")
        return chatbot_module.commit_operations(*args, **kwargs)

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        saved_statuses.append(kwargs["requirement_history"].snapshot.status)
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        return chatbot_module._response(**kwargs)

    with bind_workflow_trace_collector() as collector:
        response = handle_generation_api_start(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="Start the board", board_generation_action="start"),
            requirements=requirements,
            resource_summary="All visible resource summary.",
            selected_reference=None,
            requirement_history=requirement_history,
            track_initial_requirement_run=True,
            deps=_handler_deps(
                latest_learning_clarification=lambda *args, **kwargs: clarification,
                checkpoint_initial_requirement_before_generation=_checkpoint,
                generate_from_requirements=_generate_from_requirements,
                post_message=_post_message,
                commit_operations=_commit_operations,
                save_workspace_for_user=_save_workspace_for_user,
                build_response=_build_response,
            ),
        )

    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    commit = lesson.history_graph.commits[-1]

    assert order == ["checkpoint", "generate", "post_message", "commit", "save", "response"]
    assert saved_statuses == ["consumed"]
    assert captured["state_before_generate"]["status"] == "frozen"
    assert captured["generate_kwargs"]["resource_summary"] == "All visible resource summary."
    assert captured["generate_kwargs"]["reference_context"] is None
    assert captured["generate_kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["generate_kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["forced_frozen"]
    assert [row["event_type"] for row in events] == ["created", "forced_frozen", "consumed"]
    assert json.loads(events[1]["metadata_json"]) == {"forced": True}
    assert commit.label == "Board document generation"
    assert commit.message == "Generated board document from the learning requirement sheet"
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "start"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"]["action_type"] == "generate_board"
    assert commit.metadata["requirement_cleared"] is True
    assert _node_values(collector) == []


def test_handler_generation_failure_keeps_frozen_run_retryable_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    initial_commit_count = len(lesson.history_graph.commits)
    order: list[str] = []
    saved_statuses: list[str | None] = []

    def _checkpoint(**kwargs):
        order.append("checkpoint")
        return chatbot_module._checkpoint_initial_requirement_before_generation(**kwargs)

    def _generate_from_requirements(**kwargs):
        order.append("generate")
        return _failed_outcome()

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        saved_statuses.append(kwargs["requirement_history"].snapshot.status)
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        return chatbot_module._response(**kwargs)

    with bind_workflow_trace_collector() as collector:
        response = handle_generation_api_start(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="Start the board", board_generation_action="start"),
            requirements=requirements,
            resource_summary="",
            selected_reference=None,
            requirement_history=requirement_history,
            track_initial_requirement_run=True,
            deps=_handler_deps(
                latest_learning_clarification=lambda *args, **kwargs: clarification,
                checkpoint_initial_requirement_before_generation=_checkpoint,
                generate_from_requirements=_generate_from_requirements,
                post_message=lambda **kwargs: pytest.fail("failure path must not build post message"),
                save_workspace_for_user=_save_workspace_for_user,
                build_response=_build_response,
            ),
        )

    runs = _requirement_run_rows(store, lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)

    assert order == ["checkpoint", "generate", "save", "response"]
    assert saved_statuses == ["frozen"]
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.board_document_operation_status == "failed"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["consumed_commit_id"] is None
    assert [row["event_type"] for row in events] == ["created", "forced_frozen", "generation_failed"]
    assert json.loads(events[-1]["metadata_json"])["reason"] == "The board editor did not return generated content."
    assert _node_values(collector) == []


def test_handler_save_failure_stops_before_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        raise RuntimeError("save failed")

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            handle_generation_api_start(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="Start the board", board_generation_action="start"),
                requirements=requirements,
                resource_summary="",
                selected_reference=None,
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=_handler_deps(
                    latest_learning_clarification=lambda *args, **kwargs: clarification,
                    generate_from_requirements=lambda **kwargs: _success_outcome(),
                    post_message=lambda **kwargs: ("The board is ready.", "chatbot_post_board_generation"),
                    save_workspace_for_user=_save_workspace_for_user,
                    build_response=lambda **kwargs: pytest.fail("save failure must not build response"),
                ),
            )

    assert order == ["save"]
    assert _node_values(collector) == []


def test_handler_response_build_failure_does_not_record_response_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []

    def _build_response(**kwargs):
        order.append("response")
        raise RuntimeError("response build failed")

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response build failed"):
            handle_generation_api_start(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="Start the board", board_generation_action="start"),
                requirements=requirements,
                resource_summary="",
                selected_reference=None,
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=_handler_deps(
                    latest_learning_clarification=lambda *args, **kwargs: clarification,
                    generate_from_requirements=lambda **kwargs: _success_outcome(),
                    post_message=lambda **kwargs: ("The board is ready.", "chatbot_post_board_generation"),
                    save_workspace_for_user=chatbot_module._save_workspace_for_user,
                    build_response=_build_response,
                ),
            )

    assert order == ["response"]
    assert _node_values(collector) == []


def test_handler_rejects_non_start_generation_action(tmp_path: Path) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="non_start")
    requirements, _ = _incomplete_state()
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )

    def _unexpected(*args, **kwargs):
        pytest.fail("non-start request must be rejected before dependencies run")

    with pytest.raises(ValueError, match="board_generation_action='start'"):
        handle_generation_api_start(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="Start the board"),
            requirements=requirements,
            resource_summary="",
            selected_reference=None,
            requirement_history=requirement_history,
            track_initial_requirement_run=True,
            deps=_handler_deps(
                latest_learning_clarification=_unexpected,
                generate_from_requirements=_unexpected,
                post_message=_unexpected,
                save_workspace_for_user=_unexpected,
                build_response=_unexpected,
            ),
        )

    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id) == []


def test_caller_does_not_route_existing_board_start_into_api_start_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, _, lesson = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="existing_board_start")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_chatbot_reply", lambda **kwargs: ChatbotReply(chatbot_message="继续。"))
    monkeypatch.setattr(
        chatbot_module,
        "handle_generation_api_start",
        lambda **kwargs: pytest.fail("existing-board start must not enter API-start handler"),
    )

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    updated_lesson = response.course_package.lessons[-1]
    assert updated_lesson.board_document.content_text == "# Existing board\n\nReusable content."
    assert response.chatbot_message == ""
    assert response.board_decision.action == "no_change"
    assert response.board_document_operation_status == "failed"
