from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    ChatRequest,
    InteractionSession,
    InteractionTurnDecision,
    LibraryChapter,
    ResourceLibraryItem,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    ChatbotReply,
    InitialLearningWorkModeDecision,
    LearningRequirementUpdate,
    openai_course_ai,
)
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
    record_workflow_step,
)


TEST_USER_ID = "user_workflow_trace"
RESOURCE_PROMPT_MESSAGE = "根据上传资料回答这个问题"
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


def _ready_learning_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="用户已经说明当前学习目标，可以进入后续板书阶段。",
        ready_for_board=True,
        action_type="generate_board",
        action_instruction="根据上传资料生成板书",
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
    monkeypatch.setattr(workspace_state, "STORE", store)
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

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=RESOURCE_PROMPT_MESSAGE),
            user_id=TEST_USER_ID,
        )

    assert response.reference_prompt is None
    assert response.active_interaction_session is not None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "continue_rule"
    assert collector.steps[5].decision == "handled"
    assert NodeId.RESOURCE_REFERENCE_PROMPT.value not in _node_values(collector)


def test_explicit_generation_resource_prompt_does_not_call_general_prompt_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_resource_prompt_candidate(existing_board=False)
    store = _store_with_workspace(tmp_path, workspace, name="legacy_generation_resource_prompt")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "handle_resource_reference_prompt",
        lambda **kwargs: _fail_if_called("handle_resource_reference_prompt"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _ready_learning_requirement_update)
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

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="根据上传资料生成板书"),
            user_id=TEST_USER_ID,
        )

    assert response.reference_prompt is not None
    assert response.reference_prompt.resource_id == "resource-trace"
    assert response.board_decision.action == "await_reference_choice"
    assert response.learning_requirement_sheet.action_type == "generate_board"
    assert NodeId.RESOURCE_REFERENCE_PROMPT.value not in _node_values(collector)


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
