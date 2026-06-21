from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    ChatbotReply,
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.resource_library import build_resource_item
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
)


TEST_USER_ID = "user_confirmed_resource_generation_contract"
GENERATION_RESOURCE_PROMPT_MESSAGE = "根据上传资料生成板书"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_resource(tmp_path: Path):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id

    resource_path = tmp_path / "resource.md"
    resource_path.write_text(
        "# 上传资料\n这一章包含上传资料生成板书的通用参考内容。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "resource.md")
    resource.scope_lesson_id = lesson.id
    package.resources.append(resource)
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _ready_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="The learner wants a first board from a confirmed resource.",
        key_facts=[
            LearningRequirementKeyFact(
                label="Learning content",
                value="A generic topic from an uploaded resource",
                evidence="The learner asked to generate from the resource.",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="Learning content is clear",
                is_clear=True,
                evidence="The learner asked to generate from the resource.",
            )
        ],
        missing_items=[],
        next_question="",
        ready_for_board=True,
        action_type="generate_board",
        action_instruction="Generate the first board from the confirmed resource.",
    )


def _success_outcome(*, title: str = "Confirmed resource board") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="Generated a board from the confirmed resource.",
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## Start\n\nThis board came from the confirmed resource.",
        ),
        board_decision=BoardDecision(action="edit_board", reason="Board generated."),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="Generated a board from the confirmed resource.",
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


def _patch_confirmed_resource_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="The requirement is clear."),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _ready_requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="The board is ready."),
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


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


def _prompt_for_resource_confirmation(lesson_id: str):
    return chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )


def _run_confirmed_resource_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    name: str,
    outcome_factory,
):
    workspace, lesson_id = _workspace_with_resource(tmp_path)
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name=name)
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_confirmed_resource_common(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: pytest.fail("BoardEditor should wait for resource confirmation."),
    )

    first_response = _prompt_for_resource_confirmation(lesson_id)
    ready_versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)

    captured: dict[str, Any] = {}

    def _fake_generate_from_requirements(**kwargs):
        captured["kwargs"] = kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
        )
        return outcome_factory()

    monkeypatch.setattr(chatbot_module, "generate_from_requirements", _fake_generate_from_requirements)

    with bind_workflow_trace_collector() as collector:
        confirmed_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(
                message=GENERATION_RESOURCE_PROMPT_MESSAGE,
                resource_reference_action="confirm",
                resource_reference_resource_id=first_response.reference_prompt.resource_id,
                resource_reference_chapter_id=first_response.reference_prompt.chapter_id,
            ),
            user_id=TEST_USER_ID,
        )

    return SimpleNamespace(
        captured=captured,
        collector=collector,
        confirmed_response=confirmed_response,
        first_response=first_response,
        lesson_id=lesson_id,
        original_board=original_board,
        ready_versions=ready_versions,
        store=store,
    )


def test_confirmed_resource_generation_success_preserves_ready_frozen_commit_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_confirmed_resource_generation(
        monkeypatch,
        tmp_path,
        name="confirmed_resource_success",
        outcome_factory=_success_outcome,
    )
    confirmed_response = result.confirmed_response
    store = result.store
    lesson_id = result.lesson_id
    lesson = confirmed_response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)

    assert result.first_response.reference_prompt is not None
    assert [row["change_kind"] for row in result.ready_versions] == ["completed"]
    assert confirmed_response.requirement_phase == "consumed"
    assert confirmed_response.requirement_cleared is True
    assert confirmed_response.active_requirement_sheet is None
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions[:1]] == ["completed"]
    assert versions[1]["status"] == "frozen"
    assert versions[1]["change_kind"] in {"frozen", "forced_frozen"}
    assert [row["event_type"] for row in events[:2]] == ["created", "completed"]
    assert events[2]["event_type"] in {"frozen", "forced_frozen"}
    assert events[3]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert result.captured["state_before_board"]["status"] == "frozen"
    assert result.captured["kwargs"]["requirement_run_id"] == confirmed_response.requirement_run_id
    assert result.captured["kwargs"]["frozen_requirement_version_id"] == confirmed_response.requirement_version_id
    assert result.captured["kwargs"]["reference_context"] is not None
    assert result.captured["kwargs"]["reference_context"].resource_id == confirmed_response.selected_reference.resource_id
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["resource_backed_generation"] is True
    assert commit.metadata["board_generation_action"] == "resource_reference_confirm"
    assert commit.metadata["selected_reference"]["resource_id"] == confirmed_response.selected_reference.resource_id
    assert commit.metadata["selected_reference"]["chapter_id"] == confirmed_response.selected_reference.chapter_id
    assert commit.metadata["resource_resolution_status"] == "selected"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert TRACE_KEYS.isdisjoint(_all_keys(confirmed_response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_confirmed_resource_generation_failure_preserves_retryable_frozen_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_confirmed_resource_generation(
        monkeypatch,
        tmp_path,
        name="confirmed_resource_failure",
        outcome_factory=_failed_outcome,
    )
    confirmed_response = result.confirmed_response
    store = result.store
    lesson_id = result.lesson_id
    lesson = confirmed_response.course_package.lessons[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)

    assert result.first_response.reference_prompt is not None
    assert [row["change_kind"] for row in result.ready_versions] == ["completed"]
    assert confirmed_response.requirement_phase == "frozen"
    assert confirmed_response.requirement_cleared is False
    assert confirmed_response.active_requirement_sheet is not None
    assert confirmed_response.board_document_operation_status == "failed"
    assert confirmed_response.board_document_operation_failure_reason == "The board editor did not return generated content."
    assert lesson.board_document.model_dump(mode="json") == result.original_board
    assert runs[0]["status"] == "frozen"
    assert runs[0]["frozen_version_id"] == confirmed_response.requirement_version_id
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions[:1]] == ["completed"]
    assert versions[1]["status"] == "frozen"
    assert versions[1]["change_kind"] in {"frozen", "forced_frozen"}
    assert [row["event_type"] for row in events[:2]] == ["created", "completed"]
    assert events[2]["event_type"] in {"frozen", "forced_frozen"}
    assert events[3]["event_type"] == "generation_failed"
    failure_metadata = json.loads(events[-1]["metadata_json"])
    assert failure_metadata["reason"] == "The board editor did not return generated content."
    assert result.captured["state_before_board"]["status"] == "frozen"
    assert result.captured["kwargs"]["requirement_run_id"] == confirmed_response.requirement_run_id
    assert result.captured["kwargs"]["frozen_requirement_version_id"] == confirmed_response.requirement_version_id
    assert result.captured["kwargs"]["reference_context"] is not None
    assert result.captured["kwargs"]["reference_context"].resource_id == confirmed_response.selected_reference.resource_id
    assert lesson.history_graph.commits[-1].metadata.get("kind") != "board_document_generation"
    assert TRACE_KEYS.isdisjoint(_all_keys(confirmed_response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(lesson.history_graph.commits[-1].metadata))


def test_confirmed_resource_generation_success_traces_ready_generation_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_confirmed_resource_generation(
        monkeypatch,
        tmp_path,
        name="confirmed_resource_success_trace",
        outcome_factory=_success_outcome,
    )
    response = result.confirmed_response
    commit = response.course_package.lessons[-1].history_graph.commits[-1]

    assert _node_values(result.collector) == [
        *_trace_prefix(),
        NodeId.RESOURCE_CONFIRMED_GENERATE.value,
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    resource_step = result.collector.steps[6]
    ready_step = result.collector.steps[7]
    freeze_step = result.collector.steps[8]
    generate_step = result.collector.steps[9]
    commit_step = result.collector.steps[10]
    assert resource_step.decision == "confirmed"
    assert resource_step.run_id == response.requirement_run_id
    assert resource_step.version_id == result.ready_versions[0]["id"]
    assert ready_step.decision == "ready"
    assert ready_step.run_id == response.requirement_run_id
    assert ready_step.version_id == result.ready_versions[0]["id"]
    assert freeze_step.decision == "frozen"
    assert freeze_step.run_id == response.requirement_run_id
    assert freeze_step.version_id == response.requirement_version_id
    assert generate_step.decision == "board_editor"
    assert generate_step.run_id == response.requirement_run_id
    assert generate_step.version_id == response.requirement_version_id
    assert commit_step.decision == "committed"
    assert commit_step.run_id == response.requirement_run_id
    assert commit_step.version_id == response.requirement_version_id
    assert commit_step.commit_id == commit.id


def test_confirmed_resource_generation_failure_traces_retryable_frozen_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_confirmed_resource_generation(
        monkeypatch,
        tmp_path,
        name="confirmed_resource_failure_trace",
        outcome_factory=_failed_outcome,
    )
    response = result.confirmed_response

    assert _node_values(result.collector) == [
        *_trace_prefix(),
        NodeId.RESOURCE_CONFIRMED_GENERATE.value,
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    resource_step = result.collector.steps[6]
    ready_step = result.collector.steps[7]
    freeze_step = result.collector.steps[8]
    generate_step = result.collector.steps[9]
    failure_step = result.collector.steps[10]
    assert resource_step.decision == "confirmed"
    assert resource_step.run_id == response.requirement_run_id
    assert resource_step.version_id == result.ready_versions[0]["id"]
    assert ready_step.decision == "ready"
    assert ready_step.run_id == response.requirement_run_id
    assert ready_step.version_id == result.ready_versions[0]["id"]
    assert freeze_step.decision == "frozen"
    assert freeze_step.run_id == response.requirement_run_id
    assert freeze_step.version_id == response.requirement_version_id
    assert generate_step.decision == "board_editor"
    assert generate_step.run_id == response.requirement_run_id
    assert generate_step.version_id == response.requirement_version_id
    assert failure_step.decision == "generation_failed"
    assert failure_step.reason == "The board editor did not return generated content."
    assert failure_step.run_id == response.requirement_run_id
    assert failure_step.version_id == response.requirement_version_id
