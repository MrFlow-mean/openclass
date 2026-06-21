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
    ResourceReferencePrompt,
)
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.text_triggered_generation import (
    TextTriggeredGenerationDependencies,
    classify_text_triggered_generation_request,
    handle_text_triggered_generation_request,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_text_triggered_generation_handler"


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


def _draft_state():
    requirements = build_requirements("通用主题")
    requirements.learning_goal = "用户想围绕一个通用主题产出学习材料。"
    requirements.level = "已有一些背景。"
    requirements.output_preference = "右侧板书"
    clarification = LearningClarificationStatus(
        progress=65,
        label="继续澄清",
        reason="用户已有可执行的学习上下文，但还有细节未补齐。",
        missing_items=["使用场景"],
        can_start=False,
        summary="用户想围绕一个通用主题产出学习材料。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用主题",
                evidence="来自用户输入。",
                category="learning",
            ),
            LearningRequirementKeyFact(
                label="当前水平",
                value="已有一些背景",
                evidence="来自用户输入。",
                category="level",
            ),
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        next_question="你希望面向什么使用场景？",
        ready_for_board=False,
    )
    return requirements, clarification


def _success_outcome(*, title: str = "生成后的板书") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="已生成右侧板书。",
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用学习材料。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已生成右侧板书。",
        section_titles=["起点"],
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


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=workspace_state.load_learning_requirement_history_state_for_user(TEST_USER_ID, lesson_id),
    )


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


def _handler_deps(
    *,
    generate_from_requirements,
    post_message,
    save_workspace_for_user,
    build_response,
    commit_operations=chatbot_module.commit_operations,
) -> TextTriggeredGenerationDependencies:
    return TextTriggeredGenerationDependencies(
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


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def test_classifier_owns_only_blank_board_text_generation_after_resource_prompt_precedence() -> None:
    workspace, _, lesson = _workspace_with_blank_lesson()
    requirements, clarification = _draft_state()

    document_artifact = classify_text_triggered_generation_request(
        lesson=lesson,
        request=ChatRequest(message="请生成一份学习材料"),
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )
    assert document_artifact is not None
    assert document_artifact.trigger == "document_artifact_request"

    generation_control = classify_text_triggered_generation_request(
        lesson=lesson,
        request=ChatRequest(message="看你发挥，直接生成"),
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )
    assert generation_control is not None
    assert generation_control.trigger == "generation_control_request"

    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="开始生成", board_generation_action="start"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=ResourceResolution(matches=[], status="none"),
        )
        is None
    )
    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="按资料生成", resource_reference_action="confirm"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=ResourceResolution(matches=[], status="selected"),
        )
        is None
    )
    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="根据资料生成一份学习材料"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=ResourceResolution(
                matches=[],
                reference_prompt=ResourceReferencePrompt(
                    resource_id="res_demo",
                    chapter_id="chap_demo",
                    resource_name="资料",
                    chapter_title="章节",
                    question="是否使用这份资料？",
                    reason="资料匹配度较高。",
                ),
                status="prompt",
            ),
        )
        is None
    )

    lesson.board_document = build_document(title="已有板书", content_text="# 已有板书\n\n已有内容")
    assert (
        classify_text_triggered_generation_request(
            lesson=lesson,
            request=ChatRequest(message="请生成一份学习材料"),
            requirements=requirements,
            learning_clarification=clarification,
            resource_resolution=ResourceResolution(matches=[], status="none"),
        )
        is None
    )
    assert workspace.packages[0].lessons[0].id == lesson.id


def test_handler_success_freezes_before_generate_commits_consumes_saves_then_assembles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _draft_state()
    requirement_history = _requirement_history(lesson.id)
    requirement_history.record_update(requirements=requirements, clarification=clarification)
    order: list[str] = []
    saved_statuses: list[str | None] = []
    captured: dict[str, Any] = {}
    request = ChatRequest(message="请生成一份学习材料")
    generation_request = classify_text_triggered_generation_request(
        lesson=lesson,
        request=request,
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )
    assert generation_request is not None

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
        response = handle_text_triggered_generation_request(
            generation_request=generation_request,
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=request,
            requirements=requirements,
            learning_clarification=clarification,
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
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [row["change_kind"] for row in versions] == ["created", "forced_frozen"]
    assert [row["event_type"] for row in events] == ["created", "forced_frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["board_generation_action"] == "explicit_board_request"
    assert commit.metadata["generation_request_lane"] == "text_triggered_generation"
    assert commit.metadata["generation_request_trigger"] == "document_artifact_request"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def test_handler_generation_failure_persists_retryable_frozen_run_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _draft_state()
    requirement_history = _requirement_history(lesson.id)
    requirement_history.record_update(requirements=requirements, clarification=clarification)
    initial_commit_count = len(lesson.history_graph.commits)
    order: list[str] = []
    saved_statuses: list[str | None] = []
    request = ChatRequest(message="看你发挥，直接生成")
    generation_request = classify_text_triggered_generation_request(
        lesson=lesson,
        request=request,
        requirements=requirements,
        learning_clarification=clarification,
        resource_resolution=ResourceResolution(matches=[], status="none"),
    )
    assert generation_request is not None

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
        response = handle_text_triggered_generation_request(
            generation_request=generation_request,
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=request,
            requirements=requirements,
            learning_clarification=clarification,
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
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.active_requirement_sheet is not None
    assert response.board_document_operation_status == "failed"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["consumed_commit_id"] is None
    assert [row["change_kind"] for row in versions] == ["created", "forced_frozen"]
    assert [row["event_type"] for row in events] == ["created", "forced_frozen", "generation_failed"]
    failure_metadata = json.loads(events[-1]["metadata_json"])
    assert failure_metadata["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
