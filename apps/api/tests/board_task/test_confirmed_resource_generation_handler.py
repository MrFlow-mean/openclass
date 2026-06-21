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
    ResourceContextChunk,
    ResourceMatch,
    ResourceReferenceContext,
)
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.confirmed_resource_generation import (
    ConfirmedResourceGenerationDependencies,
    handle_confirmed_resource_generation,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_confirmed_resource_generation_handler"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_blank_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("空白学习页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _ready_state():
    requirements = build_requirements("通用资料主题")
    requirements.learning_goal = "用户想根据已确认资料生成第一版板书。"
    requirements.level = "已有一些背景。"
    requirements.output_preference = "右侧板书"
    requirements.action_type = "generate_board"
    requirements.action_instruction = "根据已确认资料生成第一版板书"
    clarification = LearningClarificationStatus(
        progress=100,
        label="准备生成资料板书",
        reason="学习需求已经足够根据确认资料生成第一版板书。",
        missing_items=[],
        can_start=True,
        summary="用户想根据已确认资料生成第一版板书。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="已确认资料中的通用主题",
                evidence="来自用户确认资料引用。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="确认资料引用",
                is_clear=True,
                evidence="用户已经确认资料章节。",
            )
        ],
        next_question="",
        ready_for_board=True,
    )
    return requirements, clarification


def _resource_resolution() -> ResourceResolution:
    match = ResourceMatch(
        resource_id="res_confirmed",
        chapter_id="chap_intro",
        resource_name="通用资料",
        chapter_title="起点章节",
        reason="用户确认了这个资料章节。",
        score=0.91,
        is_high_overlap=True,
    )
    reference = ResourceReferenceContext(
        resource_id=match.resource_id,
        chapter_id=match.chapter_id,
        resource_name=match.resource_name,
        chapter_title=match.chapter_title,
        summary="这份资料提供了生成第一版板书所需的通用参考内容。",
        teaching_points=["提取关键概念", "组织成可讲解板书"],
        chunks=[
            ResourceContextChunk(
                title="起点章节",
                excerpt="这里是一段通用资料摘录，用来验证已选资料来源会传给 BoardEditor。",
                teaching_hint="围绕资料摘录组织第一版板书。",
            )
        ],
        full_text="完整资料正文不应进入 commit metadata。",
    )
    return ResourceResolution(matches=[match], selected_reference=reference, status="selected")


def _success_outcome(*, title: str = "确认资料板书") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="已根据确认资料生成板书。",
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## 起点\n\n这是一段根据确认资料生成的通用板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已根据确认资料生成板书。",
        section_titles=["起点"],
        changed=True,
        operation_status="succeeded",
    )


def _failed_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="根据确认资料生成板书失败。",
        new_document=build_document(title="空白学习页", content_text=""),
        board_decision=BoardDecision(action="no_change", reason="板书生成失败。"),
        assistant_message_source="board_document_editor_ai",
        operation=None,
        summary="板书文档编辑 AI 没有返回生成结果。",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="板书文档编辑 AI 没有返回生成结果。",
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


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


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(TEST_USER_ID, lesson_id),
    )


def _handler_deps(
    *,
    generate_from_requirements,
    post_message,
    save_workspace_for_user,
    build_response,
    commit_operations=chatbot_module.commit_operations,
) -> ConfirmedResourceGenerationDependencies:
    return ConfirmedResourceGenerationDependencies(
        with_task_details=chatbot_module._with_task_details,
        prepare_initial_requirement_for_board_generation=(
            chatbot_module._prepare_initial_requirement_for_board_generation
        ),
        checkpoint_initial_requirement_before_generation=(
            chatbot_module._checkpoint_initial_requirement_before_generation
        ),
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
        reference_metadata=chatbot_module._reference_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def test_handler_success_freezes_before_generate_commits_consumes_saves_then_assembles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
    resource_resolution = _resource_resolution()
    requirement_history = _requirement_history(lesson.id)
    ready_stamp = requirement_history.record_update(requirements=requirements, clarification=clarification)
    order: list[str] = []
    saved_statuses: list[str | None] = []
    captured: dict[str, Any] = {}

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
        return "板书已经就绪，要我按它从开头讲起吗？", "chatbot_post_board_generation"

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
        response = handle_confirmed_resource_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="根据确认资料生成板书", resource_reference_action="confirm"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=resource_resolution,
            resource_summary_for_turn="已确认资料摘要",
            requirement_history=requirement_history,
            track_initial_requirement_run=True,
            deps=_handler_deps(
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

    assert order == ["generate", "post_message", "commit", "save", "response"]
    assert saved_statuses == ["consumed"]
    assert captured["state_before_generate"]["status"] == "frozen"
    assert captured["generate_kwargs"]["reference_context"] == resource_resolution.selected_reference
    assert captured["generate_kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["generate_kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.selected_reference == resource_resolution.selected_reference
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["resource_backed_generation"] is True
    assert commit.metadata["board_generation_action"] == "resource_reference_confirm"
    assert commit.metadata["resource_reference_action"] == "confirm"
    assert commit.metadata["selected_reference"]["resource_id"] == resource_resolution.selected_reference.resource_id
    assert commit.metadata["selected_reference"]["chapter_id"] == resource_resolution.selected_reference.chapter_id
    assert "full_text" not in commit.metadata["selected_reference"]
    assert commit.metadata["resource_resolution_status"] == "selected"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert _node_values(collector) == [
        NodeId.RESOURCE_CONFIRMED_GENERATE.value,
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "confirmed"
    assert collector.steps[0].version_id == ready_stamp.version_id
    assert collector.steps[1].version_id == ready_stamp.version_id
    assert collector.steps[2].version_id == response.requirement_version_id
    assert collector.steps[3].version_id == response.requirement_version_id
    assert collector.steps[4].commit_id == commit.id


def test_handler_generation_failure_persists_retryable_frozen_run_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    original_board = lesson.board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name="handler_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
    resource_resolution = _resource_resolution()
    requirement_history = _requirement_history(lesson.id)
    ready_stamp = requirement_history.record_update(requirements=requirements, clarification=clarification)
    initial_commit_count = len(lesson.history_graph.commits)
    order: list[str] = []
    saved_statuses: list[str | None] = []
    captured: dict[str, Any] = {}

    def _generate_from_requirements(**kwargs):
        order.append("generate")
        captured["state_before_generate"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        )
        captured["generate_kwargs"] = kwargs
        return _failed_outcome()

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        saved_statuses.append(kwargs["requirement_history"].snapshot.status)
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        return chatbot_module._response(**kwargs)

    with bind_workflow_trace_collector() as collector:
        response = handle_confirmed_resource_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="根据确认资料生成板书", resource_reference_action="confirm"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=resource_resolution,
            resource_summary_for_turn="已确认资料摘要",
            requirement_history=requirement_history,
            track_initial_requirement_run=True,
            deps=_handler_deps(
                generate_from_requirements=_generate_from_requirements,
                post_message=lambda **kwargs: pytest.fail("failure path must not build a post-generation message"),
                save_workspace_for_user=_save_workspace_for_user,
                build_response=_build_response,
            ),
        )

    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)

    assert order == ["generate", "save", "response"]
    assert saved_statuses == ["frozen"]
    assert captured["state_before_generate"]["status"] == "frozen"
    assert captured["generate_kwargs"]["reference_context"] == resource_resolution.selected_reference
    assert captured["generate_kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["generate_kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.active_requirement_sheet is not None
    assert response.selected_reference == resource_resolution.selected_reference
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "板书文档编辑 AI 没有返回生成结果。"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["frozen_version_id"] == response.requirement_version_id
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    failure_metadata = json.loads(events[-1]["metadata_json"])
    assert failure_metadata["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(lesson.history_graph.commits[-1].metadata))
    assert _node_values(collector) == [
        NodeId.RESOURCE_CONFIRMED_GENERATE.value,
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "confirmed"
    assert collector.steps[0].version_id == ready_stamp.version_id
    assert collector.steps[1].version_id == ready_stamp.version_id
    assert collector.steps[2].version_id == response.requirement_version_id
    assert collector.steps[3].version_id == response.requirement_version_id
    assert collector.steps[4].reason == "板书文档编辑 AI 没有返回生成结果。"


def test_handler_response_build_failure_does_not_record_response_assemble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
    requirement_history = _requirement_history(lesson.id)
    requirement_history.record_update(requirements=requirements, clarification=clarification)
    order: list[str] = []

    def _generate_from_requirements(**kwargs):
        order.append("generate")
        return _success_outcome()

    def _post_message(**kwargs):
        order.append("post_message")
        return "板书已经就绪，要我按它从开头讲起吗？", "chatbot_post_board_generation"

    def _commit_operations(*args, **kwargs):
        order.append("commit")
        return chatbot_module.commit_operations(*args, **kwargs)

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        raise RuntimeError("response build failed")

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response build failed"):
            handle_confirmed_resource_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="根据确认资料生成板书", resource_reference_action="confirm"),
                requirements=requirements,
                learning_clarification=clarification,
                resource_resolution=_resource_resolution(),
                resource_summary_for_turn="已确认资料摘要",
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=_handler_deps(
                    generate_from_requirements=_generate_from_requirements,
                    post_message=_post_message,
                    commit_operations=_commit_operations,
                    save_workspace_for_user=_save_workspace_for_user,
                    build_response=_build_response,
                ),
            )

    assert order == ["generate", "post_message", "commit", "save", "response"]
    assert NodeId.INITIAL_BOARD_COMMIT.value in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_handler_save_failure_does_not_record_commit_or_response_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
    requirement_history = _requirement_history(lesson.id)
    requirement_history.record_update(requirements=requirements, clarification=clarification)
    order: list[str] = []

    def _generate_from_requirements(**kwargs):
        order.append("generate")
        return _success_outcome()

    def _post_message(**kwargs):
        order.append("post_message")
        return "板书已经就绪，要我按它从开头讲起吗？", "chatbot_post_board_generation"

    def _commit_operations(*args, **kwargs):
        order.append("commit")
        return chatbot_module.commit_operations(*args, **kwargs)

    def _raise_save(**kwargs):
        order.append("save")
        raise RuntimeError("save failed")

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            handle_confirmed_resource_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="根据确认资料生成板书", resource_reference_action="confirm"),
                requirements=requirements,
                learning_clarification=clarification,
                resource_resolution=_resource_resolution(),
                resource_summary_for_turn="已确认资料摘要",
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=_handler_deps(
                    generate_from_requirements=_generate_from_requirements,
                    post_message=_post_message,
                    commit_operations=_commit_operations,
                    save_workspace_for_user=_raise_save,
                    build_response=lambda **kwargs: pytest.fail("response must not be built after save failure"),
                ),
            )

    assert order == ["generate", "post_message", "commit", "save"]
    assert NodeId.INITIAL_BOARD_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)
