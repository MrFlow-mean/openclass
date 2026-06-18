from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from app.models import (
    ChatRequest,
    InteractionSession,
    InteractionTurnDecision,
    LibraryChapter,
    ResourceLibraryItem,
)
from app.services import chat_service, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document


TEST_USER_ID = "user_chat_workflow_dispatch"
EXISTING_BOARD_TEXT = "# 已有板书\n\n## 目标段落\n这里是当前互动使用的板书内容。\n"


def _unexpected_call(name: str) -> NoReturn:
    raise AssertionError(f"{name} should not be called for this dispatch path")


def _seed_workspace(
    store: SqliteCourseStore,
    *,
    active_session: bool = False,
    with_resource: bool = False,
):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("调度测试")
    refresh_lesson_runtime(lesson, document=build_document(title="已有板书", content_text=EXISTING_BOARD_TEXT))
    if active_session:
        lesson.active_interaction_session = InteractionSession(
            status="active",
            rule_text="按当前规则逐轮互动。",
            interaction_goal="继续当前互动。",
            reference_context="这里是当前互动使用的板书内容。",
            compliant_input_rule="用户继续按规则输入。",
            expected_user_behavior="用户继续按规则输入。",
            assistant_behavior="Chatbot 按当前规则回应。",
            turn_count=1,
        )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    if with_resource:
        package.resources.append(
            ResourceLibraryItem(
                id="resource-dispatch",
                name="参考资料",
                mime_type="text/plain",
                resource_type="document",
                size_bytes=128,
                scope_lesson_id=lesson.id,
                outline=[
                    LibraryChapter(
                        id="chapter-dispatch",
                        title="第一章",
                        level=1,
                        summary="这一章包含可被引用的参考内容。",
                        keywords=["第一章", "参考内容"],
                        path=["第一章"],
                    )
                ],
            )
        )
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def _patch_non_resource_mutation_paths_to_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _unexpected_call("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _unexpected_call("generate_board_task_requirement_sheet"),
    )


def test_resource_reference_prompt_wins_when_no_active_interaction_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_non_resource_mutation_paths_to_fail(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _unexpected_call("generate_chatbot_reply"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: _unexpected_call("generate_interaction_turn_decision"),
    )

    lesson = _seed_workspace(store, with_resource=True)
    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="根据上传资料回答这个问题"),
        user_id=TEST_USER_ID,
    )

    assert response.reference_prompt is not None
    assert response.reference_prompt.resource_id == "resource-dispatch"
    assert response.reference_prompt.chapter_id == "chapter-dispatch"
    assert response.active_interaction_session is None
    assert response.active_board_task_sheet is None
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "resource_resolver"
    assert commit.metadata["reference_prompt"]["resource_id"] == "resource-dispatch"


def test_active_interaction_session_takes_precedence_over_resource_prompt_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_non_resource_mutation_paths_to_fail(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: InteractionTurnDecision(
            route="continue_rule",
            reason="用户输入仍在当前互动规则内。",
            progress_note="继续当前互动。",
            user_intent="继续互动",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：按当前互动继续。"),
    )

    lesson = _seed_workspace(store, active_session=True, with_resource=True)
    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="根据上传资料回答这个问题"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "AI生成：按当前互动继续。"
    assert response.reference_prompt is None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "continue_rule"
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.turn_count == 2
    assert response.active_board_task_sheet is None
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["interaction_decision"]["route"] == "continue_rule"
    assert commit.metadata["active_interaction_session_after"]["turn_count"] == 2
