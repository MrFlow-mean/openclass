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
from app.services import chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.generation_text_trigger import TextTriggeredGenerationRequest
from app.services.chat.paths.text_triggered_generation import (
    TextTriggeredGenerationDependencies,
    handle_text_triggered_generation,
)
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.resource_resolver import ResourceResolution
from app.services.rich_document import build_document


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
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


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


def _incomplete_state():
    requirements = build_requirements("通用学习主题")
    requirements.learning_goal = "用户希望生成一份通用学习材料。"
    requirements.level = "已有一些背景。"
    requirements.output_preference = "右侧板书"
    clarification = LearningClarificationStatus(
        progress=65,
        label="可强制生成",
        reason="用户提供了足够的通用学习材料方向。",
        missing_items=["细节偏好"],
        can_start=False,
        summary="用户希望生成一份通用学习材料。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="通用学习主题",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="学习内容明确",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        next_question="还需要什么细节偏好？",
        ready_for_board=False,
    )
    return requirements, clarification


def _success_outcome(*, title: str = "生成后的板书") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="板书已生成。",
        new_document=build_document(
            title=title,
            content_text=f"# {title}\n\n## 起点\n\n这是一段根据文本触发请求生成的通用板书。",
        ),
        board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
        assistant_message_source="board_document_editor_ai",
        operation="replace_document",
        summary="已生成板书。",
        section_titles=["起点"],
        changed=True,
        operation_status="succeeded",
    )


def _failed_outcome() -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="板书生成失败。",
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


def _deps(
    *,
    order: list[str],
    generate_from_requirements,
    post_message,
    save_workspace_for_user,
    build_response,
    commit_operations=chatbot_module.commit_operations,
) -> TextTriggeredGenerationDependencies:
    def _checkpoint(**kwargs):
        order.append("checkpoint")
        return chatbot_module._checkpoint_initial_requirement_before_generation(**kwargs)

    def _generate(**kwargs):
        order.append("generate")
        return generate_from_requirements(**kwargs)

    def _post_message(**kwargs):
        order.append("post_message")
        return post_message(**kwargs)

    def _commit_operations(*args, **kwargs):
        order.append("commit")
        return commit_operations(*args, **kwargs)

    def _save(**kwargs):
        order.append("save")
        return save_workspace_for_user(**kwargs)

    def _response(**kwargs):
        order.append("response")
        return build_response(**kwargs)

    return TextTriggeredGenerationDependencies(
        with_task_details=chatbot_module._with_task_details,
        prepare_initial_requirement_for_board_generation=(
            chatbot_module._prepare_initial_requirement_for_board_generation
        ),
        checkpoint_initial_requirement_before_generation=_checkpoint,
        generate_from_requirements=_generate,
        refresh_lesson_runtime=chatbot_module.refresh_lesson_runtime,
        build_board_teaching_guide=chatbot_module.build_board_teaching_guide,
        post_initial_board_generation_message=_post_message,
        commit_operations=_commit_operations,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        board_document_failure_metadata=chatbot_module._board_document_failure_metadata,
        board_document_quality_metadata=chatbot_module._board_document_quality_metadata,
        requirement_history_metadata=chatbot_module._requirement_history_metadata,
        task_metadata=chatbot_module._task_metadata,
        reference_metadata=chatbot_module._reference_metadata,
        save_workspace_for_user=_save,
        build_response=_response,
    )


def _call_handler(
    *,
    workspace,
    package,
    lesson,
    requirement_history,
    requirements,
    learning_clarification,
    deps,
    request: ChatRequest | None = None,
):
    return handle_text_triggered_generation(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=request or ChatRequest(message="请生成一份学习材料"),
        requirements=requirements,
        learning_clarification=learning_clarification,
        trigger=TextTriggeredGenerationRequest(
            trigger="document_artifact_request",
            reason="Blank-board text asks for a document-like learning artifact.",
        ),
        resource_summary_for_turn="暂无已上传资料摘要",
        resource_resolution=ResourceResolution(matches=[], status="none"),
        selected_reference=None,
        requirement_history=requirement_history,
        track_initial_requirement_run=True,
        deps=deps,
    )


def test_handler_success_freezes_generates_commits_consumes_saves_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="text_trigger_success")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []
    captured: dict[str, Any] = {}

    def _generate_from_requirements(**kwargs):
        captured["history_before_generate"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        )
        captured["generate_kwargs"] = kwargs
        return _success_outcome()

    deps = _deps(
        order=order,
        generate_from_requirements=_generate_from_requirements,
        post_message=lambda **kwargs: ("板书已生成，要不要从开头讲？", "chatbot_post_board_generation"),
        save_workspace_for_user=chatbot_module._save_workspace_for_user,
        build_response=chatbot_module._response,
    )

    response = _call_handler(
        workspace=workspace,
        package=package,
        lesson=lesson,
        requirement_history=requirement_history,
        requirements=requirements,
        learning_clarification=clarification,
        deps=deps,
    )

    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson.id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert order == ["checkpoint", "generate", "post_message", "commit", "save", "response"]
    assert captured["history_before_generate"]["status"] == "frozen"
    assert captured["generate_kwargs"]["reference_context"] is None
    assert captured["generate_kwargs"]["requirement_run_id"] == runs[0]["id"]
    assert response.chatbot_message == "板书已生成，要不要从开头讲？"
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.requirement_phase == "consumed"
    assert "根据文本触发请求生成" in response.course_package.lessons[-1].board_document.content_text
    assert runs[0]["status"] == "consumed"
    assert runs[0]["consumed_commit_id"] == commit.id
    assert [version["status"] for version in versions] == ["frozen"]
    assert [version["change_kind"] for version in versions] == ["forced_frozen"]
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "explicit_board_request"
    assert "text_triggered_generation" not in commit.metadata
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"]["action_type"] == "generate_board"
    assert commit.metadata["requirement_cleared"] is True


def test_handler_failure_records_generation_failed_without_success_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    initial_commit_count = len(lesson.history_graph.commits)
    store = _store_with_workspace(tmp_path, workspace, name="text_trigger_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []
    deps = _deps(
        order=order,
        generate_from_requirements=lambda **kwargs: _failed_outcome(),
        post_message=lambda **kwargs: pytest.fail("failed generation must not build a post-generation message"),
        save_workspace_for_user=chatbot_module._save_workspace_for_user,
        build_response=chatbot_module._response,
    )

    response = _call_handler(
        workspace=workspace,
        package=package,
        lesson=lesson,
        requirement_history=requirement_history,
        requirements=requirements,
        learning_clarification=clarification,
        deps=deps,
    )

    runs = _requirement_run_rows(store, lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    assert order == ["checkpoint", "generate", "save", "response"]
    assert len(lesson.history_graph.commits) == initial_commit_count
    assert response.chatbot_message == "板书生成失败。"
    assert response.requirement_phase == "frozen"
    assert response.requirement_cleared is False
    assert response.board_document_operation_status == "failed"
    assert runs[0]["status"] == "frozen"
    assert runs[0]["consumed_commit_id"] is None
    assert events[-1]["event_type"] == "generation_failed"


def test_handler_rejects_api_start_before_state_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, package, lesson = _workspace_with_blank_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="text_trigger_guard")
    monkeypatch.setattr(workspace_state, "STORE", store)
    requirements, clarification = _incomplete_state()
    requirement_history = _requirement_history(lesson.id)
    order: list[str] = []
    deps = _deps(
        order=order,
        generate_from_requirements=lambda **kwargs: pytest.fail("guard should run before generation"),
        post_message=lambda **kwargs: pytest.fail("guard should run before post message"),
        save_workspace_for_user=chatbot_module._save_workspace_for_user,
        build_response=chatbot_module._response,
    )

    with pytest.raises(ValueError, match="board_generation_action=start"):
        _call_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            requirement_history=requirement_history,
            requirements=requirements,
            learning_clarification=clarification,
            deps=deps,
            request=ChatRequest(message="开始生成", board_generation_action="start"),
        )

    assert order == []
    assert _requirement_run_rows(store, lesson.id) == []
