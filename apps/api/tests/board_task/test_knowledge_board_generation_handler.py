from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    ChatRequest,
    LearningRequirementSheet,
    ResourceReferenceContext,
    ResourceReferencePrompt,
)
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.knowledge_board_generation import (
    KnowledgeBoardMinimalGenerationDependencies,
    handle_knowledge_board_minimal_generation,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import InitialLearningWorkModeDecision
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
)


TEST_USER_ID = "user_knowledge_board_generation_handler"


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


def _base_requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="",
        learning_goal="",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _knowledge_board_decision() -> InitialLearningWorkModeDecision:
    return InitialLearningWorkModeDecision(
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
        topic="一个通用主题",
        reason="用户提出了一个聚焦的新知识学习请求。",
    )


def _success_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="已生成聚焦知识板书。",
        new_document=build_document(
            title="一个通用主题",
            content_text="# 一个通用主题\n\n## 核心概念\n\n这里是聚焦知识板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已生成聚焦知识板书。",
        section_titles=["核心概念"],
        changed=True,
        operation_status="succeeded",
    )


def _failed_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="板书生成失败，请稍后重试。",
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
) -> KnowledgeBoardMinimalGenerationDependencies:
    return KnowledgeBoardMinimalGenerationDependencies(
        minimal_initial_learning_state=chatbot_module._minimal_initial_learning_state,
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
        initial_learning_work_mode_metadata=chatbot_module._initial_learning_work_mode_metadata,
        reference_metadata=chatbot_module._reference_metadata,
        save_workspace_for_user=save_workspace_for_user,
        build_response=build_response,
    )


def test_handler_success_freezes_minimal_requirement_before_generate_commits_consumes_without_mode_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirement_history = _requirement_history(lesson.id)
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
        return "板书已生成，要不要从开头讲？", "chatbot_post_board_generation"

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
        response = handle_knowledge_board_minimal_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="直接为我讲解一个通用主题"),
            requirements=_base_requirements(),
            decision=_knowledge_board_decision(),
            resource_summary_for_turn="",
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
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
    assert captured["generate_kwargs"]["requirement_run_id"] == response.requirement_run_id
    assert captured["generate_kwargs"]["frozen_requirement_version_id"] == response.requirement_version_id
    assert response.chatbot_message == "板书已生成，要不要从开头讲？"
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert "聚焦知识板书" in lesson.board_document.content_text
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["frozen"]
    assert [row["event_type"] for row in events] == ["created", "frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.label == "Knowledge board generation"
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "knowledge_board_minimal_requirement"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["initial_learning_work_mode"]["work_mode"] == "knowledge_board"
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert commit.metadata["resource_resolution_status"] == "none"
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert NodeId.INITIAL_MODE_DECIDE.value not in _node_values(collector)
    assert collector.steps[2].commit_id == commit.id


def test_handler_generation_failure_preserves_retryable_frozen_run_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirement_history = _requirement_history(lesson.id)
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
        return _failed_outcome()

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        saved_statuses.append(kwargs["requirement_history"].snapshot.status)
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        return chatbot_module._response(**kwargs)

    with bind_workflow_trace_collector() as collector:
        response = handle_knowledge_board_minimal_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="直接为我讲解一个通用主题"),
            requirements=_base_requirements(),
            decision=_knowledge_board_decision(),
            resource_summary_for_turn="",
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
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
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.board_document_operation_status == "failed"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions] == ["frozen"]
    assert [row["event_type"] for row in events] == ["created", "frozen", "generation_failed"]
    assert json.loads(events[-1]["metadata_json"])["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert NodeId.INITIAL_MODE_DECIDE.value not in _node_values(collector)


def test_handler_rejects_non_knowledge_or_resource_context_before_side_effects(
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_trigger_reject")
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )

    def _unexpected(*args, **kwargs):
        raise AssertionError("invalid trigger contract must stop before dependencies run")

    deps = _handler_deps(
        generate_from_requirements=_unexpected,
        post_message=_unexpected,
        save_workspace_for_user=_unexpected,
        build_response=_unexpected,
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="work_mode=knowledge_board"):
            handle_knowledge_board_minimal_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="我想学点东西"),
                requirements=_base_requirements(),
                decision=InitialLearningWorkModeDecision(
                    work_mode="narrow_topic",
                    granularity="broad_topic",
                    topic="一个宽泛主题",
                    reason="主题仍然过宽。",
                    next_question="你想先聚焦哪里？",
                ),
                resource_summary_for_turn="",
                resource_resolution=ResourceResolution(matches=[], status="none"),
                selected_reference=None,
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=deps,
            )

    assert _node_values(collector) == []
    assert _requirement_run_rows(store, lesson.id) == []

    prompt = ResourceReferencePrompt(
        resource_id="resource_1",
        chapter_id="chapter_1",
        resource_name="资料",
        chapter_title="章节",
        question="是否参考资料？",
        reason="资源匹配。",
    )
    reference = ResourceReferenceContext(
        resource_id="resource_1",
        chapter_id="chapter_1",
        resource_name="资料",
        chapter_title="章节",
        summary="资料摘要。",
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(ValueError, match="resource prompt"):
            handle_knowledge_board_minimal_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="直接讲一个主题"),
                requirements=_base_requirements(),
                decision=_knowledge_board_decision(),
                resource_summary_for_turn="资料摘要",
                resource_resolution=ResourceResolution(matches=[], reference_prompt=prompt, status="prompt"),
                selected_reference=None,
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=deps,
            )
        with pytest.raises(ValueError, match="confirmed resource context"):
            handle_knowledge_board_minimal_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="直接讲一个主题"),
                requirements=_base_requirements(),
                decision=_knowledge_board_decision(),
                resource_summary_for_turn="资料摘要",
                resource_resolution=ResourceResolution(matches=[], selected_reference=reference, status="selected"),
                selected_reference=reference,
                requirement_history=requirement_history,
                track_initial_requirement_run=True,
                deps=deps,
            )

    assert _node_values(collector) == []
    assert _requirement_run_rows(store, lesson.id) == []
