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
from app.services.chat.paths.ready_requirement_generation import (
    ReadyRequirementGenerationDependencies,
    handle_ready_requirement_generation,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import (
    ChatbotReply,
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.resource_library import build_resource_item
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
)


TEST_USER_ID = "user_ready_requirement_generation_handler"


def _workspace_with_blank_lesson():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("空白学习页")
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _workspace_with_resource(tmp_path: Path):
    workspace, package, lesson = _workspace_with_blank_lesson()
    resource_path = tmp_path / "resource.md"
    resource_path.write_text("# 资料章节\n这是一段可用于生成板书的通用资料正文。", encoding="utf-8")
    resource = build_resource_item(resource_path, "resource.md")
    resource.scope_lesson_id = lesson.id
    package.resources.append(resource)
    return workspace, package, lesson


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(
        TEST_USER_ID,
        workspace.__class__.model_validate(workspace.model_dump(mode="json")),
    )
    return store


def _ready_state():
    requirements = build_requirements("通用主题")
    requirements.learning_goal = "用户想学习一个通用主题。"
    requirements.level = "已有一些背景。"
    requirements.output_preference = "右侧板书"
    requirements.action_type = "generate_board"
    requirements.action_instruction = "生成第一版板书"
    clarification = LearningClarificationStatus(
        progress=100,
        label="准备生成知识板书",
        reason="学习需求已经足够生成第一版板书。",
        missing_items=[],
        can_start=True,
        summary="用户想学习一个通用主题。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用主题",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        next_question="",
        ready_for_board=True,
    )
    return requirements, clarification


def _ready_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="用户想学习一个通用主题。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用主题",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        missing_items=[],
        next_question="",
        ready_for_board=True,
        action_type="generate_board",
        action_instruction="生成第一版板书",
    )


def _success_outcome(*, title: str = "第一版板书", summary: str = "已生成第一版板书。") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=summary,
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary=summary,
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
) -> ReadyRequirementGenerationDependencies:
    return ReadyRequirementGenerationDependencies(
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


def _patch_ready_generation_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _ready_requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经就绪，要我按它从开头讲起吗？"),
    )


def test_handler_success_freezes_before_generate_commits_consumes_saves_then_assembles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
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
        response = handle_ready_requirement_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="我已经说明目标、水平和输出形式"),
            requirements=requirements,
            learning_clarification=clarification,
            chatbot_message="需求已经够清楚。",
            resource_summary_for_turn="",
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
            requirement_history=requirement_history,
            requirement_stamp=ready_stamp,
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
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["board_generation_action"] == "ready_requirement_sheet"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_READY.value,
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
    requirements, clarification = _ready_state()
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
        return _failed_outcome()

    def _save_workspace_for_user(**kwargs):
        order.append("save")
        saved_statuses.append(kwargs["requirement_history"].snapshot.status)
        return chatbot_module._save_workspace_for_user(**kwargs)

    def _build_response(**kwargs):
        order.append("response")
        return chatbot_module._response(**kwargs)

    with bind_workflow_trace_collector() as collector:
        response = handle_ready_requirement_generation(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="我已经说明目标、水平和输出形式"),
            requirements=requirements,
            learning_clarification=clarification,
            chatbot_message="需求已经够清楚。",
            resource_summary_for_turn="",
            resource_resolution=ResourceResolution(matches=[], status="none"),
            selected_reference=None,
            requirement_history=requirement_history,
            requirement_stamp=ready_stamp,
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
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    assert json.loads(events[-1]["metadata_json"])["reason"] == "板书文档编辑 AI 没有返回生成结果。"
    assert _node_values(collector) == [
        NodeId.INITIAL_REQUIREMENT_READY.value,
        NodeId.INITIAL_REQUIREMENT_FREEZE.value,
        NodeId.INITIAL_BOARD_GENERATE.value,
        NodeId.INITIAL_GENERATION_FAILED.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def test_handler_response_build_failure_does_not_record_response_assemble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="handler_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _ready_state()
    requirement_history = _requirement_history(lesson.id)
    ready_stamp = requirement_history.record_update(requirements=requirements, clarification=clarification)
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
            handle_ready_requirement_generation(
                workspace=workspace,
                package=package,
                lesson=lesson,
                user_id=TEST_USER_ID,
                request=ChatRequest(message="我已经说明目标、水平和输出形式"),
                requirements=requirements,
                learning_clarification=clarification,
                chatbot_message="需求已经够清楚。",
                resource_summary_for_turn="",
                resource_resolution=ResourceResolution(matches=[], status="none"),
                selected_reference=None,
                requirement_history=requirement_history,
                requirement_stamp=ready_stamp,
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


def test_explicit_board_generation_start_does_not_enter_ready_requirement_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, _, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="explicit_start")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    monkeypatch.setattr(chatbot_module, "generate_from_requirements", lambda **kwargs: _success_outcome())
    monkeypatch.setattr(
        chatbot_module,
        "handle_ready_requirement_generation",
        lambda **kwargs: pytest.fail("explicit board_generation_action=start must not enter ready handler"),
    )

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.board_decision.action == "edit_board"
    assert commit.metadata["board_generation_action"] == "start"


def test_confirmed_resource_generation_does_not_enter_ready_requirement_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, _, lesson = _workspace_with_resource(tmp_path)
    store = _store_with_workspace(tmp_path, workspace, name="confirmed_resource")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_ready_generation_common(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "handle_ready_requirement_generation",
        lambda **kwargs: pytest.fail("confirmed-resource generation must not enter ready handler"),
    )

    first_response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="根据上传资料生成板书"),
        user_id=TEST_USER_ID,
    )
    assert first_response.reference_prompt is not None

    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: _success_outcome(title="确认资料板书", summary="已根据确认资料生成板书。"),
    )
    confirmed_response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="根据上传资料生成板书",
            resource_reference_action="confirm",
            resource_reference_resource_id=first_response.reference_prompt.resource_id,
            resource_reference_chapter_id=first_response.reference_prompt.chapter_id,
        ),
        user_id=TEST_USER_ID,
    )

    commit = confirmed_response.course_package.lessons[-1].history_graph.commits[-1]
    assert confirmed_response.board_decision.action == "edit_board"
    assert confirmed_response.selected_reference is not None
    assert commit.metadata["board_generation_action"] == "resource_reference_confirm"
