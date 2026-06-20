from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    InteractionSession,
    InteractionTurnDecision,
    LibraryChapter,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
    ResourceLibraryItem,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.chat.paths.active_interaction_exit import (
    ActiveInteractionExitDependencies,
    handle_active_interaction_exit,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    BoardTaskRouteDecision,
    ChatbotReply,
    InitialLearningWorkModeDecision,
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
    record_workflow_step,
)


TEST_USER_ID = "user_workflow_trace"
RESOURCE_PROMPT_MESSAGE = "根据上传资料回答这个问题"
GENERATION_RESOURCE_PROMPT_MESSAGE = "根据上传资料生成板书"
REQUIREMENT_CHAT_UPDATE_MESSAGE = "我想学一个主题"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_lesson(*, existing_board: bool = False):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    if existing_board:
        refresh_lesson_runtime(
            lesson,
            document=build_document(title="已有板书", content_text="# 已有板书\n\n这一段已有内容。\n"),
        )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def _workspace_with_resource_prompt_candidate(*, active_session: bool = False, existing_board: bool = True):
    workspace, lesson_id = _workspace_with_lesson(existing_board=existing_board)
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    package.resources.append(
        ResourceLibraryItem(
            id="resource-trace",
            name="参考资料",
            mime_type="text/plain",
            resource_type="document",
            size_bytes=128,
            scope_lesson_id=lesson.id,
            outline=[
                LibraryChapter(
                    id="chapter-trace",
                    title="资料章节",
                    level=1,
                    summary="这一章包含上传资料问题的参考内容。",
                    keywords=["上传资料", "回答问题", "参考内容"],
                    path=["资料章节"],
                )
            ],
        )
    )
    if active_session:
        lesson.active_interaction_session = InteractionSession(
            status="active",
            rule_text="按当前规则逐轮互动。",
            interaction_goal="继续当前互动。",
            reference_context="这一段已有内容。",
            compliant_input_rule="用户继续按规则输入。",
            expected_user_behavior="用户继续按规则输入。",
            assistant_behavior="Chatbot 按当前规则回应。",
            turn_count=1,
        )
    return workspace, lesson_id


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _parse_sse(block: str) -> tuple[str, dict[str, Any]]:
    event = "message"
    data_lines: list[str] = []
    for line in block.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    return event, json.loads("\n".join(data_lines))


def _collect_sse_events(stream) -> list[tuple[str, dict[str, Any]]]:
    return [_parse_sse(block) for block in stream]


def _requirement_history_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    return [
        *store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
    ]


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


def _normalize_visible_response(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        is_commit = {"label", "message", "branch_name", "snapshot", "metadata"}.issubset(value)
        for key, item in value.items():
            if key in {"created_at", "updated_at"}:
                normalized[key] = "<timestamp>"
            elif key in {"requirement_run_id", "requirement_version_id"}:
                normalized[key] = "<requirement_id>"
            elif key in {
                "board_task_run_id",
                "board_task_version_id",
                "new_board_task_run_id",
                "new_board_task_version_id",
            }:
                normalized[key] = "<board_task_id>"
            elif is_commit and key == "id":
                normalized[key] = "<commit_id>"
            elif key == "head_commit_id":
                normalized[key] = "<commit_id>"
            else:
                normalized[key] = _normalize_visible_response(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_visible_response(item) for item in value]
    return value


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def _patch_resource_prompt_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        chatbot_module,
        "handle_generation_resource_prompt",
        lambda **kwargs: _fail_if_called("handle_generation_resource_prompt"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _fail_if_called("generate_chatbot_reply"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )


def _collecting_generation_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=70,
        summary="用户已经说明当前学习目标，可以进入后续板书阶段。",
        ready_for_board=False,
        action_type="generate_board",
        action_instruction="根据上传资料生成板书",
    )


def _collecting_requirement_chat_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=55,
        summary="用户正在补充一个通用学习需求，但目标还没有清晰到可以生成板书。",
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
        missing_items=["还需要确认学习目标"],
        next_question="你希望这次学习最后能完成什么？",
        ready_for_board=False,
    )


def _patch_generation_resource_prompt_guards(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"requirement_update": 0}

    def _requirement_update(**kwargs) -> LearningRequirementUpdate:
        calls["requirement_update"] += 1
        return _collecting_generation_requirement_update(**kwargs)

    monkeypatch.setattr(
        chatbot_module,
        "handle_resource_reference_prompt",
        lambda **kwargs: _fail_if_called("handle_resource_reference_prompt"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: _fail_if_called("generate_chatbot_reply"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: _fail_if_called("generate_from_requirements"),
    )
    return calls


def _patch_requirement_chat_update_guards(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"requirement_update": 0}

    def _requirement_update(**kwargs) -> LearningRequirementUpdate:
        calls["requirement_update"] += 1
        return _collecting_requirement_chat_update(**kwargs)

    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_work_mode", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我先继续确认你的学习需求。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: _fail_if_called("generate_from_requirements"),
    )
    return calls


def _interaction_decision(
    route: str,
    *,
    reason: str | None = None,
    progress_note: str | None = None,
) -> InteractionTurnDecision:
    return InteractionTurnDecision(
        route=route,
        reason=reason or f"{route} reason",
        progress_note=progress_note or f"{route} progress",
        user_intent=f"{route} intent",
    )


def _patch_interaction_turn(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    *,
    reason: str | None = None,
    progress_note: str | None = None,
    chatbot_message: str = "AI生成：按当前互动继续。",
) -> InteractionTurnDecision:
    decision = _interaction_decision(route, reason=reason, progress_note=progress_note)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: decision)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=chatbot_message),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )
    return decision


def _interaction_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
        NodeId.INTERACTION_DECIDE.value,
    ]


def _board_task_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def _count_active_interaction_handler_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}
    original_handler = chatbot_module.handle_active_interaction_turn

    def _counting_handler(**kwargs):
        calls["count"] += 1
        return original_handler(**kwargs)

    monkeypatch.setattr(chatbot_module, "handle_active_interaction_turn", _counting_handler)
    return calls


def _count_active_interaction_exit_handler_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}
    original_handler = chatbot_module.handle_active_interaction_exit

    def _counting_handler(**kwargs):
        calls["count"] += 1
        return original_handler(**kwargs)

    monkeypatch.setattr(chatbot_module, "handle_active_interaction_exit", _counting_handler)
    return calls


def _active_interaction_exit_test_deps() -> ActiveInteractionExitDependencies:
    return ActiveInteractionExitDependencies(
        generate_interaction_message=lambda **kwargs: ("", "chatbot_interaction", None),
        task_metadata=lambda **kwargs: {},
        save_workspace_for_user=lambda **kwargs: None,
        build_response=lambda **kwargs: _fail_if_called("build_response"),
    )


def _patch_board_task_terminal_guards(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chatbot_message: str = "AI生成：请先补充板书任务信息。",
) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=chatbot_message),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: _fail_if_called("generate_from_requirements"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_execute_board_task_write",
        lambda **kwargs: _fail_if_called("_execute_board_task_write"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_maybe_start_interaction_session",
        lambda **kwargs: _fail_if_called("_maybe_start_interaction_session"),
    )


def _generic_focus(lesson_id: str, document_id: str, *, label: str, excerpt: str, segment_id: str) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson_id,
        document_id=document_id,
        segment_id=segment_id,
        kind="paragraph",
        heading_path=["已有板书", label],
        excerpt=excerpt,
        confidence=0.8,
        reason=f"{label} 是候选位置。",
        display_label=label,
    )


def test_node_ids_match_latest_workflow_graph_document() -> None:
    doc = Path("docs/architecture/chat-workflow-graph.md").read_text(encoding="utf-8")
    table = doc.split("| NodeId | Type | Current source |", 1)[1].split("Current documented NodeId count", 1)[0]
    documented = re.findall(r"\| `([A-Z_]+)` \|", table)

    assert len(documented) == 59
    assert set(documented) == {node.value for node in NodeId}


def test_record_workflow_step_noops_before_timestamp_when_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import workflow_trace

    monkeypatch.setattr(
        workflow_trace,
        "_utc_now_iso",
        lambda: (_ for _ in ()).throw(AssertionError("unbound trace must not create timestamps")),
    )

    assert current_workflow_trace_collector() is None
    assert record_workflow_step(NodeId.CONTEXT_LOAD, decision="loaded") is None


def test_nested_binding_restores_outer_collector() -> None:
    outer = WorkflowTraceCollector()
    inner = WorkflowTraceCollector()

    with bind_workflow_trace_collector(outer):
        record_workflow_step(NodeId.CONTEXT_LOAD)
        with bind_workflow_trace_collector(inner):
            record_workflow_step(NodeId.BOARD_ACTION_DECIDE)
        record_workflow_step(NodeId.CHAT_TURN_GATE)

    assert isinstance(outer.steps, tuple)
    assert isinstance(inner.steps, tuple)
    assert _node_values(outer) == [NodeId.CONTEXT_LOAD.value, NodeId.CHAT_TURN_GATE.value]
    assert _node_values(inner) == [NodeId.BOARD_ACTION_DECIDE.value]
    assert current_workflow_trace_collector() is None


def test_ordinary_chat_trace_records_current_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="ordinary")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：我们先聊聊。"
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.ORDINARY_CHAT_GENERATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "not_handled"
    assert collector.steps[6].decision == "chatbot"
    assert collector.steps[7].commit_id == commit.id


def test_resource_reference_prompt_trace_records_current_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate()
    store = _store_with_workspace(tmp_path, workspace, name="resource_prompt")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    original_requirements = workspace.packages[0].lessons[-1].learning_requirements.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_resource_prompt_guardrails(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert response.reference_prompt is not None
    assert response.reference_prompt.resource_id == "resource-trace"
    assert response.reference_prompt.chapter_id == "chapter-trace"
    assert response.active_interaction_session is None
    assert response.active_board_task_sheet is None
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.learning_requirement_sheet.model_dump(mode="json") == original_requirements
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "resource_resolver"
    assert commit.metadata["reference_prompt"]["resource_id"] == "resource-trace"
    assert commit.metadata["task_requirement_sheet"] == original_requirements
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.RESOURCE_REFERENCE_PROMPT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "not_handled"
    assert collector.steps[6].decision == "prompted"
    assert collector.steps[7].commit_id == commit.id


def test_active_interaction_session_does_not_record_resource_reference_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="resource_prompt_session")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    handler_calls = _count_active_interaction_handler_calls(monkeypatch)
    decision = _patch_interaction_turn(
        monkeypatch,
        "continue_rule",
        reason="用户输入仍在当前互动规则内。",
        progress_note="继续当前互动。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    assert response.reference_prompt is None
    assert handler_calls["count"] == 1
    assert response.active_interaction_session is not None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "continue_rule"
    assert response.active_interaction_session.status == "active"
    assert response.active_interaction_session.turn_count == 2
    assert response.active_interaction_session.progress_note == decision.progress_note
    assert response.course_package.lessons[-1].board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert commit.metadata["interaction_session_before"]["turn_count"] == 1
    assert commit.metadata["interaction_session_after"]["turn_count"] == 2
    assert commit.metadata["active_interaction_session_after"]["status"] == "active"
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_CONTINUE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "handled"
    assert collector.steps[6].decision == "not_handled"
    assert collector.steps[7].decision == "continue_rule"
    assert collector.steps[7].reason == decision.reason
    assert collector.steps[8].decision == "continue_rule"
    assert collector.steps[8].reason == decision.reason
    assert collector.steps[9].commit_id == commit.id
    assert NodeId.RESOURCE_REFERENCE_PROMPT.value not in _node_values(collector)
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_active_interaction_rule_violation_trace_records_current_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_rule_violation")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    handler_calls = _count_active_interaction_handler_calls(monkeypatch)
    decision = _patch_interaction_turn(
        monkeypatch,
        "rule_violation",
        reason="用户输入不符合当前互动规则。",
        progress_note="请按当前规则继续。",
        chatbot_message="AI生成：请按当前规则来。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="随便说点别的"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert handler_calls["count"] == 1
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.status == "active"
    assert response.active_interaction_session.turn_count == 2
    assert response.active_interaction_session.progress_note == decision.progress_note
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "rule_violation"
    assert response.course_package.lessons[-1].board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert commit.metadata["interaction_session_before"]["turn_count"] == 1
    assert commit.metadata["interaction_session_after"]["turn_count"] == 2
    assert commit.metadata["active_interaction_session_after"]["status"] == "active"
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_RULE_VIOLATION.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "handled"
    assert collector.steps[6].decision == "not_handled"
    assert collector.steps[7].decision == "rule_violation"
    assert collector.steps[7].reason == decision.reason
    assert collector.steps[8].decision == "rule_violation"
    assert collector.steps[8].reason == decision.reason
    assert collector.steps[9].commit_id == commit.id
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_active_interaction_resume_rule_records_interaction_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    lesson = workspace.packages[0].lessons[-1]
    assert lesson.active_interaction_session is not None
    lesson.active_interaction_session = lesson.active_interaction_session.model_copy(
        update={"status": "paused", "pause_reason": "临时讲解已结束。"}
    )
    store = _store_with_workspace(tmp_path, workspace, name="interaction_resume_rule")
    monkeypatch.setattr(workspace_state, "STORE", store)
    handler_calls = _count_active_interaction_handler_calls(monkeypatch)
    decision = _patch_interaction_turn(
        monkeypatch,
        "resume_rule",
        reason="用户回到原互动规则。",
        progress_note="已回到原互动。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续原来的互动"),
            user_id=TEST_USER_ID,
        )

    assert handler_calls["count"] == 1
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.status == "active"
    assert response.active_interaction_session.pause_reason == ""
    assert response.active_interaction_session.turn_count == 2
    assert response.active_interaction_session.progress_note == decision.progress_note
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_CONTINUE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[8].decision == "resume_rule"
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_active_interaction_exit_rule_trace_records_pure_terminal_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_rule")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    handler_calls = _count_active_interaction_exit_handler_calls(monkeypatch)
    decision = _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="用户明确结束当前互动。",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert handler_calls["count"] == 1
    assert response.active_interaction_session is None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert response.course_package.lessons[-1].board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.label == "Interaction session ended"
    assert commit.message == "Exited a rule-based interaction session and found no executable board task in the same turn"
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message"] == "AI生成：好的，我们先结束这个互动。"
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["board_explanation_directive"] is None
    assert "interaction_mode" in commit.metadata
    assert "selection" in commit.metadata
    assert commit.metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert commit.metadata["interaction_session_before"] is not None
    assert commit.metadata["interaction_session_after"] is None
    assert commit.metadata["active_interaction_session_after"] is None
    assert commit.metadata["task_requirement_sheet"] == response.learning_requirement_sheet.model_dump(mode="json")
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_EXIT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "handled"
    assert collector.steps[6].decision == "not_handled"
    assert collector.steps[7].decision == "exit_rule"
    assert collector.steps[7].reason == decision.reason
    assert collector.steps[8].decision == "exit_rule"
    assert collector.steps[8].reason == decision.reason
    assert collector.steps[9].commit_id == commit.id
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_active_interaction_exit_handler_rejects_unsupported_routes() -> None:
    workspace, _lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    session_before = lesson.active_interaction_session
    lesson.active_interaction_session = None

    with pytest.raises(ValueError, match="unsupported active interaction exit route"):
        handle_active_interaction_exit(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="继续互动"),
            requirements=lesson.learning_requirements,
            learning_clarification=chatbot_module._latest_learning_clarification(
                lesson,
                requirements=lesson.learning_requirements,
            ),
            resources=[],
            session_before=session_before,
            decision=_interaction_decision("continue_rule"),
            requirement_history=None,
            board_task_history=None,
            deps=_active_interaction_exit_test_deps(),
        )


def test_active_interaction_exit_handler_requires_previous_session() -> None:
    workspace, _lesson_id = _workspace_with_resource_prompt_candidate(active_session=False)
    package = workspace.packages[0]
    lesson = package.lessons[-1]

    with pytest.raises(ValueError, match="previous interaction session"):
        handle_active_interaction_exit(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="结束互动"),
            requirements=lesson.learning_requirements,
            learning_clarification=chatbot_module._latest_learning_clarification(
                lesson,
                requirements=lesson.learning_requirements,
            ),
            resources=[],
            session_before=None,
            decision=_interaction_decision("exit_rule"),
            requirement_history=None,
            board_task_history=None,
            deps=_active_interaction_exit_test_deps(),
        )


def test_active_interaction_exit_handler_requires_cleared_active_session() -> None:
    workspace, _lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    session_before = lesson.active_interaction_session

    with pytest.raises(ValueError, match="active session to be cleared"):
        handle_active_interaction_exit(
            workspace=workspace,
            package=package,
            lesson=lesson,
            user_id=TEST_USER_ID,
            request=ChatRequest(message="结束互动"),
            requirements=lesson.learning_requirements,
            learning_clarification=chatbot_module._latest_learning_clarification(
                lesson,
                requirements=lesson.learning_requirements,
            ),
            resources=[],
            session_before=session_before,
            decision=_interaction_decision("exit_rule"),
            requirement_history=None,
            board_task_history=None,
            deps=_active_interaction_exit_test_deps(),
        )


def test_interaction_empty_decision_does_not_call_active_interaction_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_empty_decision")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_exit",
        lambda **kwargs: _fail_if_called("handle_active_interaction_exit"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is None
    assert response.active_interaction_session is not None
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_TERMINAL.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[7].decision == "empty"
    assert collector.steps[8].decision == "empty"
    assert NodeId.INTERACTION_CONTINUE.value not in _node_values(collector)
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in _node_values(collector)
    assert NodeId.INTERACTION_EXIT.value not in _node_values(collector)
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_exit_rule_board_task_handoff_remains_legacy_without_exit_terminal_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_board_task_handoff")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_exit",
        lambda **kwargs: _fail_if_called("handle_active_interaction_exit"),
    )
    decision = _patch_interaction_turn(monkeypatch, "exit_rule", reason="用户结束互动后提出板书讲解。")

    def _board_task_response(**kwargs):
        return chatbot_module._response(
            workspace=kwargs["workspace"],
            package=kwargs["package"],
            lesson=kwargs["lesson"],
            chatbot_message="转入板书任务。",
            learning_clarification=chatbot_module._latest_learning_clarification(
                kwargs["lesson"],
                requirements=kwargs["requirements"],
            ),
            requirements=kwargs["requirements"],
            board_decision=BoardDecision(action="no_change", reason=decision.reason),
            requirement_history=kwargs["requirement_history"],
        )

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _board_task_response)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="解释已有内容"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert _node_values(collector) == _interaction_trace_prefix()
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)
    assert NodeId.INTERACTION_EXIT.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_sequence_session_records_sequence_check_without_generic_continue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    lesson = workspace.packages[0].lessons[-1]
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书"],
        excerpt="这一段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解。",
        display_label="已有板书",
    )
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=focus,
        reference_context=focus.excerpt,
        compliant_input_rule="用户确认继续。",
        expected_user_behavior="用户确认继续。",
        assistant_behavior="继续讲解下一个单元。",
        turn_count=1,
        sequence_items=[focus],
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    store = _store_with_workspace(tmp_path, workspace, name="interaction_sequence")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_exit",
        lambda **kwargs: _fail_if_called("handle_active_interaction_exit"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_sequence_end_message",
        lambda **kwargs: ("顺序讲解结束。", "chatbot_interaction"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert collector.steps[5].decision == "handled"
    assert collector.steps[6].node_id == NodeId.INTERACTION_SEQUENCE_CHECK
    assert collector.steps[6].decision == "completed"
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
        NodeId.INTERACTION_EXIT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert NodeId.INTERACTION_DECIDE.value not in _node_values(collector)
    assert NodeId.INTERACTION_CONTINUE.value not in _node_values(collector)
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in _node_values(collector)
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_generation_resource_prompt_trace_records_requirement_collect_before_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    store = _store_with_workspace(tmp_path, workspace, name="legacy_generation_resource_prompt")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_generation_resource_prompt_guards(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    collect_step = collector.steps[6]
    assert response.reference_prompt is not None
    assert response.reference_prompt.resource_id == "resource-trace"
    assert response.board_decision.action == "await_reference_choice"
    assert response.learning_requirement_sheet.action_type == "generate_board"
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.action_type == "generate_board"
    assert len(runs) == 1
    assert len(versions) == 1
    assert response.requirement_run_id == runs[0]["id"]
    assert response.requirement_version_id == versions[0]["id"]
    assert response.requirement_phase == "ready"
    assert runs[0]["status"] == "ready"
    assert runs[0]["active_version_id"] == versions[0]["id"]
    assert runs[0]["frozen_version_id"] is None
    assert runs[0]["consumed_commit_id"] is None
    assert versions[0]["status"] == "ready"
    assert versions[0]["change_kind"] == "completed"
    assert versions[0]["change_summary"] == "Generation requirement persisted while awaiting resource confirmation."
    assert json.loads(versions[0]["sheet_json"]) == response.active_requirement_sheet.model_dump(mode="json")
    assert json.loads(versions[0]["clarification_json"])["ready_for_board"] is True
    assert calls["requirement_update"] == 1
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.active_board_task_sheet is None
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert [event["event_type"] for event in events] == ["created", "completed"]
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "resource_resolver"
    assert commit.metadata["task_requirement_sheet"] == response.learning_requirement_sheet.model_dump(mode="json")
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INITIAL_REQUIREMENT_COLLECT.value,
        NodeId.RESOURCE_REFERENCE_PROMPT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "not_handled"
    assert collect_step.decision == "recorded"
    assert collect_step.run_id == response.requirement_run_id
    assert collect_step.version_id == response.requirement_version_id
    assert collector.steps[7].decision == "prompted_after_requirement_update"
    assert collector.steps[8].commit_id == commit.id


def test_generation_resource_prompt_repeated_turn_does_not_duplicate_requirement_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    store = _store_with_workspace(tmp_path, workspace, name="generation_resource_prompt_idempotency")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_generation_resource_prompt_guards(monkeypatch)

    first_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )
    first_versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert len(first_versions) == 1

    second_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )
    second_versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    runs = _requirement_run_rows(store, lesson_id)

    assert len(runs) == 1
    assert len(second_versions) == 1
    assert second_versions[0]["id"] == first_versions[0]["id"]
    assert first_response.requirement_run_id == second_response.requirement_run_id == runs[0]["id"]
    assert first_response.requirement_version_id == second_response.requirement_version_id == first_versions[0]["id"]
    assert second_response.requirement_phase == "ready"


def test_existing_board_missing_task_fields_trace_records_response_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    store = _store_with_workspace(tmp_path, workspace, name="board_task_missing_fields")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_board_task_terminal_guards(monkeypatch, chatbot_message="AI生成：请告诉我要处理板书里的哪里。")
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(requested_action="explain"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: _fail_if_called("generate_board_task_route_decision"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="解释一下"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert response.chatbot_message == "AI生成：请告诉我要处理板书里的哪里。"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.progress < 100
    assert response.active_board_task_sheet.missing_items == ["目标位置", "问题内容"]
    assert response.board_task_phase == "collecting"
    assert len(runs) == 1
    assert runs[0]["status"] == "collecting"
    assert len(versions) == 1
    assert versions[0]["status"] == "collecting"
    assert [event["event_type"] for event in events] == ["created"]
    assert commit.label == "Board task clarification"
    assert commit.metadata["board_task_route"] == "clarify_location"
    assert commit.metadata["board_task_cleared"] is False
    assert _node_values(collector) == [
        *_board_task_trace_prefix(),
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TASK_CLARIFY_FIELDS.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "collecting"
    assert collector.steps[6].run_id == response.board_task_run_id
    assert collector.steps[6].version_id == response.board_task_version_id
    assert collector.steps[7].decision == "missing_fields"
    assert collector.steps[7].reason == response.active_board_task_sheet.clarification_question
    assert collector.steps[7].commit_id == commit.id


def test_existing_board_missing_task_fields_does_not_record_response_when_response_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    store = _store_with_workspace(tmp_path, workspace, name="board_task_missing_fields_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_board_task_terminal_guards(monkeypatch, chatbot_message="AI生成：请告诉我要处理板书里的哪里。")
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(requested_action="explain"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: _fail_if_called("generate_board_task_route_decision"),
    )

    def _raise_response(**kwargs):
        raise RuntimeError("response build failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response build failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="解释一下"),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_board_task_trace_prefix(),
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TASK_CLARIFY_FIELDS.value,
    ]
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_existing_board_clarify_location_trace_records_response_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    lesson = workspace.packages[0].lessons[-1]
    store = _store_with_workspace(tmp_path, workspace, name="board_task_clarify_location")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_board_task_terminal_guards(monkeypatch, chatbot_message="AI生成：你想让我处理哪一段？")
    first_focus = _generic_focus(
        lesson.id,
        lesson.board_document.id,
        label="第一处",
        excerpt="第一处内容。",
        segment_id="seg_one",
    )
    second_focus = _generic_focus(
        lesson.id,
        lesson.board_document.id,
        label="第二处",
        excerpt="第二处内容。",
        segment_id="seg_two",
    )
    resolution = FocusResolution(
        focus=None,
        candidates=[first_focus, second_focus],
        status="ambiguous",
        question="找到了多个候选位置，请确认其中一个。",
    )
    monkeypatch.setattr(chatbot_module, "resolve_board_focus", lambda **kwargs: resolution)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="多个候选位置",
            requested_action="explain",
            question_or_topic="解释多个候选位置",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route="clarify_location",
            location_status="ambiguous",
            candidate_focuses=[first_focus, second_focus],
            reason="找到了多个候选位置，请确认其中一个。",
        ),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="解释多个候选位置"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert response.chatbot_message == "AI生成：你想让我处理哪一段？"
    assert response.board_decision.action == "await_focus_choice"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.location_status == "ambiguous"
    assert response.board_task_phase == "ready"
    assert len(response.focus_candidates) == 2
    assert len(runs) == 1
    assert runs[0]["status"] == "ready"
    assert len(versions) == 2
    assert [version["status"] for version in versions] == ["ready", "ready"]
    assert [event["event_type"] for event in events] == ["created", "ready", "ready"]
    assert commit.label == "Board task location clarification"
    assert commit.metadata["board_task_route"] == "clarify_location"
    assert commit.metadata["board_task_cleared"] is False
    assert _node_values(collector) == [
        *_board_task_trace_prefix(),
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_TARGET_RESOLVE.value,
        NodeId.BOARD_ROUTE_CLARIFY_LOCATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "ready"
    assert collector.steps[6].run_id == response.board_task_run_id
    assert collector.steps[6].version_id == versions[0]["id"]
    assert collector.steps[7].decision == "ambiguous"
    assert collector.steps[7].reason == resolution.question
    assert collector.steps[8].decision == "ambiguous"
    assert collector.steps[8].reason == "找到了多个候选位置，请确认其中一个。"
    assert collector.steps[8].commit_id == commit.id


def test_existing_board_await_write_confirmation_trace_records_response_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    store = _store_with_workspace(tmp_path, workspace, name="board_task_await_write")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_board_task_terminal_guards(monkeypatch, chatbot_message="AI生成：当前板书没有这部分内容。")
    resolution = FocusResolution(
        focus=None,
        candidates=[],
        status="missing",
        question="没有定位到相关内容。",
    )
    monkeypatch.setattr(chatbot_module, "resolve_board_focus", lambda **kwargs: resolution)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="缺失主题",
            requested_action="explain",
            question_or_topic="讲解缺失主题",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            reason="当前板书没有对应内容，需要确认是否扩写。",
            write_proposal="讲解缺失主题",
        ),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="讲解缺失主题"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    versions = store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert response.chatbot_message == "AI生成：当前板书没有这部分内容。"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.requested_action == "write"
    assert response.active_board_task_sheet.confirmation_status == "awaiting"
    assert response.active_board_task_sheet.location_status == "content_absent"
    assert response.board_task_phase == "awaiting_confirmation"
    assert len(runs) == 1
    assert runs[0]["status"] == "awaiting_confirmation"
    assert len(versions) == 2
    assert [version["status"] for version in versions] == ["ready", "awaiting_confirmation"]
    assert [event["event_type"] for event in events] == ["created", "ready", "awaiting_confirmation"]
    assert commit.label == "Board write confirmation"
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
    assert commit.metadata["board_task_cleared"] is False
    assert _node_values(collector) == [
        *_board_task_trace_prefix(),
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_AWAIT_WRITE_CONFIRMATION.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "ready"
    assert collector.steps[6].run_id == response.board_task_run_id
    assert collector.steps[6].version_id == versions[0]["id"]
    assert collector.steps[7].decision == "awaiting_confirmation"
    assert collector.steps[7].run_id == response.board_task_run_id
    assert collector.steps[7].version_id == response.board_task_version_id
    assert collector.steps[7].commit_id == commit.id


def test_existing_board_write_confirmation_decline_trace_records_not_executed_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    store = _store_with_workspace(tmp_path, workspace, name="board_task_decline")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_board_task_terminal_guards(monkeypatch, chatbot_message="AI生成：当前板书没有这部分内容。")
    resolution = FocusResolution(
        focus=None,
        candidates=[],
        status="missing",
        question="没有定位到相关内容。",
    )
    monkeypatch.setattr(chatbot_module, "resolve_board_focus", lambda **kwargs: resolution)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="缺失主题",
            requested_action="explain",
            question_or_topic="讲解缺失主题",
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route="await_write_confirmation",
            location_status="content_absent",
            reason="当前板书没有对应内容，需要确认是否扩写。",
            write_proposal="讲解缺失主题",
        ),
    )
    first = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="讲解缺失主题"),
        user_id=TEST_USER_ID,
    )
    assert first.active_board_task_sheet is not None
    assert first.active_board_task_sheet.confirmation_status == "awaiting"
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: _fail_if_called("generate_board_task_requirement_sheet"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: _fail_if_called("generate_board_task_route_decision"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="不用"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    runs = _board_task_run_rows(store, lesson_id)
    events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert response.chatbot_message == ""
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet is None
    assert response.board_task_phase == "not_executed"
    assert len(runs) == 1
    assert runs[0]["status"] == "not_executed"
    assert runs[0]["archived_at"] is not None
    assert events[-1]["event_type"] == "not_executed"
    assert json.loads(events[-1]["metadata_json"]) == {"reason": "用户取消了扩写确认。"}
    assert commit.label == "Board task cancelled"
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
    assert commit.metadata["board_task_cleared"] is True
    assert _node_values(collector) == [
        *_board_task_trace_prefix(),
        NodeId.BOARD_TASK_COLLECT.value,
        NodeId.BOARD_WRITE_CONFIRMATION_HANDLE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "awaiting_confirmation"
    assert collector.steps[7].decision == "declined"
    assert collector.steps[7].reason == "用户取消了扩写确认。"
    assert collector.steps[7].run_id == response.board_task_run_id
    assert collector.steps[7].version_id == response.board_task_version_id
    assert collector.steps[7].commit_id == commit.id
    assert NodeId.BOARD_TASK_FAILURE.value not in _node_values(collector)


def test_resource_confirm_does_not_call_generation_resource_prompt_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    store = _store_with_workspace(tmp_path, workspace, name="generation_resource_prompt_confirm_guard")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_generation_resource_prompt_guards(monkeypatch)

    first_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )
    assert first_response.reference_prompt is not None

    monkeypatch.setattr(
        chatbot_module,
        "handle_generation_resource_prompt",
        lambda **kwargs: _fail_if_called("handle_generation_resource_prompt"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "generate_from_requirements",
        lambda **kwargs: BoardDocumentEditOutcome(
            chatbot_message="已根据确认资料生成板书。",
            new_document=build_document(title="确认资料板书", content_text="# 确认资料板书\n\n生成后的内容。"),
            board_decision=BoardDecision(action="edit_board", reason="已生成板书。"),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="已根据确认资料生成板书。",
            section_titles=["确认资料板书"],
            changed=True,
            operation_status="succeeded",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已生成。"),
    )

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

    assert confirmed_response.board_decision.action == "edit_board"
    assert confirmed_response.reference_prompt is None
    assert "生成后的内容" in confirmed_response.course_package.lessons[-1].board_document.content_text


def test_requirement_chat_update_trace_records_after_durable_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update")
    original_board = workspace.packages[0].lessons[-1].board_document.model_dump(mode="json")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_requirement_chat_update_guards(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    nodes = _node_values(collector)
    update_step = collector.steps[6]
    persist_step = collector.steps[7]

    assert response.requirement_run_id == runs[0]["id"]
    assert response.requirement_version_id == versions[0]["id"]
    assert response.requirement_phase == "collecting"
    assert response.requirement_cleared is False
    assert response.active_requirement_sheet is not None
    assert response.learning_clarification.ready_for_board is False
    assert runs[0]["status"] == "collecting"
    assert runs[0]["active_version_id"] == versions[0]["id"]
    assert runs[0]["frozen_version_id"] is None
    assert runs[0]["consumed_commit_id"] is None
    assert versions[0]["status"] == "collecting"
    assert versions[0]["change_kind"] == "created"
    assert [event["event_type"] for event in events] == ["created"]
    assert calls["requirement_update"] == 1
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert commit.metadata["requirement_cleared"] is False
    assert nodes == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.REQUIREMENT_CHAT_UPDATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert update_step.decision == "collecting"
    assert update_step.reason == "not_ready_for_board"
    assert update_step.run_id == response.requirement_run_id
    assert update_step.version_id == response.requirement_version_id
    assert persist_step.commit_id == commit.id
    assert NodeId.INITIAL_REQUIREMENT_FREEZE.value not in nodes
    assert NodeId.INITIAL_BOARD_GENERATE.value not in nodes


def test_requirement_chat_update_does_not_trace_without_requirement_stamp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_no_stamp")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_requirement_chat_update_guards(monkeypatch)
    monkeypatch.setattr(chatbot_module, "_maybe_record_initial_requirement_update", lambda *args, **kwargs: None)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
            user_id=TEST_USER_ID,
        )

    assert calls["requirement_update"] == 1
    assert response.requirement_run_id is None
    assert response.requirement_version_id is None
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]
    assert NodeId.REQUIREMENT_CHAT_UPDATE.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_non_ordinary_path_never_records_ordinary_chat_generate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="initial")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="",
            reason="学习方向仍然过宽。",
            next_question="你想先聚焦到哪个具体问题？",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("non-ordinary path must not generate ordinary chat")),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="我想学点东西但没想好"),
            user_id=TEST_USER_ID,
        )

    assert response.chatbot_message == "你想先聚焦到哪个具体问题？"
    assert NodeId.ORDINARY_CHAT_GENERATE.value not in _node_values(collector)
    assert _node_values(collector)[:6] == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def test_traced_and_untraced_ordinary_chat_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="traced")
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="最近有点累，想随便聊聊"),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_resource_prompt_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="resource_prompt_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="resource_prompt_traced")
    _patch_resource_prompt_guardrails(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_generation_resource_prompt_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    untraced_store = _store_with_workspace(tmp_path, workspace, name="generation_resource_prompt_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="generation_resource_prompt_traced")
    _patch_generation_resource_prompt_guards(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_requirement_chat_update_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_traced")
    _patch_requirement_chat_update_guards(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_interaction_continue_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    untraced_store = _store_with_workspace(tmp_path, workspace, name="interaction_continue_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="interaction_continue_traced")
    _patch_interaction_turn(
        monkeypatch,
        "continue_rule",
        reason="用户输入仍在当前互动规则内。",
        progress_note="继续当前互动。",
    )

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="继续互动"),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_interaction_exit_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    untraced_store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_traced")
    _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="用户明确结束当前互动。",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="结束互动"),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_interaction_continue_does_not_record_persist_or_response_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(
        monkeypatch,
        "continue_rule",
        reason="用户输入仍在当前互动规则内。",
        progress_note="继续当前互动。",
    )

    def _raise_on_save(**kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="继续互动"),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_CONTINUE.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_interaction_exit_does_not_record_persist_or_response_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "handle_active_interaction_turn",
        lambda **kwargs: _fail_if_called("handle_active_interaction_turn"),
    )
    _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="用户明确结束当前互动。",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    def _raise_on_save(**kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="结束互动"),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_EXIT.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_requirement_chat_update_does_not_record_update_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_commit_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_requirement_chat_update_guards(monkeypatch)

    def _raise_on_commit(*args, **kwargs):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(chatbot_module, "commit_operations", _raise_on_commit)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="commit failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.REQUIREMENT_CHAT_UPDATE.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_requirement_chat_update_does_not_record_update_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_requirement_chat_update_guards(monkeypatch)

    def _raise_on_save(**kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", _raise_on_save)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.REQUIREMENT_CHAT_UPDATE.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_requirement_chat_update_records_durable_update_before_response_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_requirement_chat_update_guards(monkeypatch)

    def _raise_on_response(**kwargs):
        raise RuntimeError("response failed")

    monkeypatch.setattr(chatbot_module, "_response", _raise_on_response)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
                user_id=TEST_USER_ID,
            )

    runs = _requirement_run_rows(store, lesson_id)
    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert len(runs) == 1
    assert len(versions) == 1
    assert [event["event_type"] for event in events] == ["created"]
    assert runs[0]["status"] == "collecting"
    assert runs[0]["active_version_id"] == versions[0]["id"]
    assert runs[0]["frozen_version_id"] is None
    assert runs[0]["consumed_commit_id"] is None
    assert versions[0]["status"] == "collecting"
    assert versions[0]["change_kind"] == "created"
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert _node_values(collector) == [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.REQUIREMENT_CHAT_UPDATE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
    ]
    assert collector.steps[6].decision == "collecting"
    assert collector.steps[6].reason == "not_ready_for_board"
    assert collector.steps[6].run_id == runs[0]["id"]
    assert collector.steps[6].version_id == versions[0]["id"]
    assert collector.steps[7].decision == "committed"
    assert collector.steps[7].commit_id is not None
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_workflow_trace_does_not_leak_to_response_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="AI生成：我们先聊聊。"),
    )

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="最近有点累，想随便聊聊"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_resource_prompt_trace_does_not_leak_to_response_sse_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate()
    store = _store_with_workspace(tmp_path, workspace, name="resource_prompt_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_resource_prompt_guardrails(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))

    workspace_for_stream, stream_lesson_id = _workspace_with_resource_prompt_candidate()
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="resource_prompt_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


def test_interaction_continue_trace_does_not_leak_to_response_sse_session_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_continue_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(
        monkeypatch,
        "continue_rule",
        reason="用户输入仍在当前互动规则内。",
        progress_note="继续当前互动。",
    )

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_after"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_requirement_history_rows(store, lesson_id)))
    assert TRACE_KEYS.isdisjoint(
        _all_keys(store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id))
    )

    workspace_for_stream, stream_lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="interaction_continue_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


def test_interaction_exit_trace_does_not_leak_to_response_sse_session_history_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="用户明确结束当前互动。",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_before"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_requirement_history_rows(store, lesson_id)))
    assert TRACE_KEYS.isdisjoint(
        _all_keys(store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id))
    )

    workspace_for_stream, stream_lesson_id = _workspace_with_resource_prompt_candidate(active_session=True)
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="interaction_exit_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


def test_generation_resource_prompt_trace_does_not_leak_to_response_sse_history_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    store = _store_with_workspace(tmp_path, workspace, name="generation_resource_prompt_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_generation_resource_prompt_guards(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(_requirement_history_rows(store, lesson_id)))

    workspace_for_stream, stream_lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="generation_resource_prompt_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message=GENERATION_RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


def test_requirement_chat_update_trace_does_not_leak_to_response_history_or_commit_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_lesson()
    store = _store_with_workspace(tmp_path, workspace, name="requirement_chat_update_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_requirement_chat_update_guards(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(_requirement_history_rows(store, lesson_id)))

    workspace_for_stream, stream_lesson_id = _workspace_with_lesson()
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="requirement_chat_update_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message=REQUIREMENT_CHAT_UPDATE_MESSAGE),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))
