from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardDecision, BoardFocusRef, BoardTaskRequirementSheet, ChatRequest
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat_turn_gate import ChatTurnGateDecision
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_manager import LearningRequirementUpdate
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision, ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution


TEST_USER_ID = "user_legacy_append_fallback_parity"


def _workspace_with_board():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("追加写入兼容测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 第一节\n第一节已有内容。\n\n## 第二节\n第二节已有内容。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _board_task_run_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM board_task_runs
            WHERE owner_user_id = ? AND lesson_id = ?
            ORDER BY created_at, id
            """,
            (TEST_USER_ID, lesson_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _focus(
    lesson,
    *,
    label: str,
    heading_path: list[str],
    excerpt: str,
    order: int,
) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id=f"seg-{order}",
        kind="paragraph",
        heading_path=heading_path,
        excerpt=excerpt,
        confidence=0.82,
        reason=f"候选位置：{label}",
        display_label=label,
        order_start=order,
        order_end=order,
    )


def _success_outcome(
    lesson,
    *,
    content: str = "补充后的通用说明。",
    chatbot_message: str = "AI生成：已写入右侧板书。",
) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=chatbot_message,
        new_document=build_document(
            title=lesson.board_document.title,
            content_text=f"{lesson.board_document.content_text.rstrip()}\n\n## 新增内容\n{content}\n",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已追加内容。"),
        assistant_message_source="board_document_editor_ai",
        operation="append_section",
        summary="已追加内容。",
        section_titles=["新增内容"],
        changed=True,
        operation_status="succeeded",
    )


def _requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        requirements=kwargs["current"].model_dump(mode="json"),
        clarification={
            "progress": 100,
            "label": "ready",
            "reason": "测试中保持当前需求。",
            "ready_for_board": False,
            "summary": "测试中保持当前需求。",
        },
        chatbot_message="",
    )


def _patch_common_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：请确认是否先扩写板书。"),
    )


def test_legacy_active_append_requirement_still_writes_without_board_task_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_board()
    lesson = workspace.packages[0].lessons[-1]
    requirements = build_requirements(lesson.title)
    requirements.action_type = "append_section"
    requirements.action_instruction = "按已有需求追加一段通用内容。"
    requirements.learning_goal = "用户已经确认要追加通用内容。"
    lesson.learning_requirements = requirements
    store = _store_with_workspace(tmp_path, workspace, name="legacy_active_append")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "decide_chat_turn",
        lambda **kwargs: ChatTurnGateDecision(
            route="ordinary_chat",
            reason="测试旧 append fallback 可达性。",
            should_update_learning_requirement=False,
            should_try_board_task=False,
            requires_resource_reference=False,
            matched_rules=["legacy_append_test"],
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: pytest.fail("legacy active append fallback must not enter BoardTask collection"),
    )
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _success_outcome(lesson))

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="写啊"),
        user_id=TEST_USER_ID,
    )

    updated_lesson = response.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：已写入右侧板书。"
    assert response.active_board_task_sheet is None
    assert response.requirement_cleared is True
    assert "补充后的通用说明" in updated_lesson.board_document.content_text
    assert commit.label == "Board document edit"
    assert commit.metadata["board_edit_operation"] == "append_section"
    assert commit.metadata["task_requirement_sheet"]["action_type"] == "append_section"
    assert "board_task_run_id" not in commit.metadata
    assert _board_task_run_rows(store, lesson_id) == []


def test_absent_append_topic_waits_for_confirmation_then_confirmed_write_consumes_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="append_absent_confirm")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="缺失内容",
            location_status="missing",
            requested_action="explain",
            question_or_topic="补充缺失内容",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: FocusResolution(focus=None, candidates=[], status="missing", question="没有对应内容。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            reason="当前板书没有对应内容，需要先确认是否扩写。",
            write_proposal="补充缺失内容",
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "edit_existing_document",
        lambda **kwargs: _success_outcome(lesson, content="确认后补充的缺失内容。", chatbot_message=""),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_board_directed_explanation_message",
        lambda **kwargs: (
            "AI生成：已补充，并可以围绕新内容继续讲解。",
            "chatbot_board_directed",
            {"status": "approved", "target_excerpt": kwargs["target_excerpt"]},
        ),
    )

    first = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="讲解缺失内容"),
        user_id=TEST_USER_ID,
    )
    second = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="可以"),
        user_id=TEST_USER_ID,
    )

    updated_lesson = second.course_package.lessons[-1]
    commit = updated_lesson.history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert first.active_board_task_sheet is not None
    assert first.active_board_task_sheet.confirmation_status == "awaiting"
    assert "确认后补充的缺失内容" not in first.course_package.lessons[-1].board_document.content_text
    assert second.active_board_task_sheet is None
    assert second.board_task_phase == "consumed"
    assert "确认后补充的缺失内容" in updated_lesson.board_document.content_text
    assert runs[0]["status"] == "consumed"
    assert events[-1]["event_type"] == "consumed"
    assert json.loads(events[-1]["metadata_json"]) == {"commit_id": commit.id}
    assert commit.metadata["board_task_route"] == "write"
    assert commit.metadata["board_task_cleared"] is True


def test_absent_append_topic_decline_keeps_document_unchanged_and_archives_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_board()
    lesson = workspace.packages[0].lessons[-1]
    original_text = lesson.board_document.content_text
    store = _store_with_workspace(tmp_path, workspace, name="append_absent_decline")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="缺失内容",
            location_status="missing",
            requested_action="explain",
            question_or_topic="补充缺失内容",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: FocusResolution(focus=None, candidates=[], status="missing", question="没有对应内容。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            reason="当前板书没有对应内容，需要先确认是否扩写。",
            write_proposal="补充缺失内容",
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "edit_existing_document",
        lambda **kwargs: pytest.fail("declined append confirmation must not write the board"),
    )

    first = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="讲解缺失内容"),
        user_id=TEST_USER_ID,
    )
    second = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="不用"),
        user_id=TEST_USER_ID,
    )

    runs = _board_task_run_rows(store, lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert first.active_board_task_sheet is not None
    assert second.active_board_task_sheet is None
    assert second.board_task_phase == "not_executed"
    assert second.course_package.lessons[-1].board_document.content_text == original_text
    assert runs[0]["status"] == "not_executed"
    assert events[-1]["event_type"] == "not_executed"


def test_autonomous_append_uses_same_heading_tail_and_cross_section_still_clarifies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_board()
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="append_autonomous_location")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_common_ai(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="第一节",
            location_status="ambiguous",
            requested_action="write",
            question_or_topic="补充第一节的通用说明",
            progress=100,
            missing_items=[],
        ),
    )
    same_section_candidates = [
        _focus(lesson, label="第一节前段", heading_path=["已有板书", "第一节"], excerpt="第一节已有内容。", order=1),
        _focus(lesson, label="第一节后段", heading_path=["已有板书", "第一节"], excerpt="第一节后段内容。", order=2),
    ]
    cross_section_candidates = [
        _focus(lesson, label="第一节", heading_path=["已有板书", "第一节"], excerpt="第一节已有内容。", order=1),
        _focus(lesson, label="第二节", heading_path=["已有板书", "第二节"], excerpt="第二节已有内容。", order=4),
    ]
    candidates_by_call = [same_section_candidates, cross_section_candidates]

    def _resolve(**kwargs):
        candidates = candidates_by_call.pop(0)
        return FocusResolution(focus=None, candidates=candidates, status="ambiguous", question="有多个候选位置。")

    def _route(**kwargs):
        return BoardTaskRouteDecision(
            route="clarify_location",
            location_status="ambiguous",
            reason="有多个候选位置。",
            candidate_focuses=kwargs["location_evidence"]["candidates"],
            write_proposal="补充第一节的通用说明",
        )

    monkeypatch.setattr(chatbot_module, "resolve_board_focus", _resolve)
    monkeypatch.setattr(openai_course_ai, "generate_board_task_route_decision", _route)
    monkeypatch.setattr(
        chatbot_module,
        "edit_existing_document",
        lambda **kwargs: _success_outcome(lesson, content="系统自行选择同一小节末尾后补充的内容。"),
    )

    same_section = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="给第一节续写，你自己定合适的位置直接写"),
        user_id=TEST_USER_ID,
    )
    cross_section = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="给第一节续写，你自己定合适的位置直接写"),
        user_id=TEST_USER_ID,
    )

    first_commit = same_section.course_package.lessons[-1].history_graph.commits[-1]
    second_commit = cross_section.course_package.lessons[-1].history_graph.commits[-1]
    assert same_section.active_board_task_sheet is None
    assert "系统自行选择同一小节末尾后补充的内容" in same_section.course_package.lessons[-1].board_document.content_text
    assert first_commit.metadata["board_task_route"] == "write"
    assert first_commit.metadata["target_scope"] == "focus"
    assert cross_section.active_board_task_sheet is not None
    assert cross_section.board_decision.action == "await_focus_choice"
    assert second_commit.label == "Board task location clarification"
    assert second_commit.metadata["board_task_route"] == "clarify_location"
