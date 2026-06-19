from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import BoardFocusRef, BoardTaskRequirementSheet, ChatRequest, InteractionSession, InteractionTurnDecision
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths import active_interaction_empty as active_interaction_empty_module
from app.services.chat.paths import interaction_sequence_continue as interaction_sequence_continue_module
from app.services.chat.paths import interaction_sequence_end as interaction_sequence_end_module
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.explanation_atoms import ATOMIC_EXPLANATION_SEQUENCE_MODE
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import focus_context
from app.services.sequence_planner import SequencePlan
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_sequence_trace"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_sequence_session(*, final_item: bool = False):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 第一段\n第一段已有内容。\n\n## 第二段\n第二段已有内容。\n",
        ),
    )
    first_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书", "第一段"],
        excerpt="第一段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解当前单元。",
        display_label="第一段",
    )
    second_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["已有板书", "第二段"],
        excerpt="第二段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解下一单元。",
        display_label="第二段",
    )
    sequence_items = [first_focus] if final_item else [first_focus, second_focus]
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="按顺序讲解。",
        interaction_goal="顺序讲解板书。",
        target_focus=first_focus,
        reference_context=first_focus.excerpt,
        compliant_input_rule="用户确认继续。",
        expected_user_behavior="用户确认继续。",
        assistant_behavior="继续讲解下一个单元。",
        progress_note="准备讲解第 1 个单元。",
        pause_reason="等待用户确认。",
        turn_count=2,
        sequence_items=sequence_items,
        sequence_index=0,
        sequence_mode="section_explanation",
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id, first_focus, second_focus


def _workspace_with_active_session():
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    lesson = workspace.packages[0].lessons[-1]
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="按当前规则互动。",
        interaction_goal="继续当前互动。",
        reference_context="第一段已有内容。",
        compliant_input_rule="用户继续按规则输入。",
        expected_user_behavior="用户继续按规则输入。",
        assistant_behavior="Chatbot 按当前规则回应。",
        progress_note="当前普通互动进度。",
        turn_count=2,
    )
    return workspace, lesson_id


def _sequence_start_inputs(tmp_path: Path, *, name: str):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n第一段已有内容。第二段已有内容。\n",
        ),
    )
    first_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="第一段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解第一个单元。",
        display_label="第一个单元",
    )
    second_focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="第二段已有内容。",
        confidence=1.0,
        reason="测试顺序讲解第二个单元。",
        display_label="第二个单元",
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="目标范围",
        target_location=first_focus,
        location_status="resolved",
        requested_action="explain",
        question_or_topic="按顺序讲解目标范围。",
        missing_items=[],
        progress=100,
    )
    lesson.board_task_requirements = board_task
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store = _store_with_workspace(tmp_path, workspace, name=name)
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    board_task_stamp = board_task_history.record_update(
        sheet=board_task,
        status="ready",
        change_summary="Ready to start a sequential board explanation.",
    )
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    requirements = lesson.learning_requirements
    learning_clarification = chatbot_module._latest_learning_clarification(
        lesson,
        requirements=requirements,
    )
    decision = BoardTaskRouteDecision(
        route="explain",
        location_status="found",
        target_focus=first_focus,
        candidate_focuses=[first_focus, second_focus],
        reason="已定位可顺序讲解的内容。",
    )
    sequence_plan = SequencePlan(
        mode=ATOMIC_EXPLANATION_SEQUENCE_MODE,
        items=[first_focus, second_focus],
        start_index=0,
        scope_label="已有板书 / 目标范围",
        reason="按板书范围生成最小可讲单元顺序讲解计划。",
        planner_name="sequence_planner",
    )
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "lesson_id": lesson.id,
        "store": store,
        "request": ChatRequest(message="按顺序讲解目标范围"),
        "requirements": requirements,
        "learning_clarification": learning_clarification,
        "resources": package.resources,
        "board_task": board_task,
        "board_task_history": board_task_history,
        "board_task_stamp": board_task_stamp,
        "decision": decision,
        "resolution": None,
        "sequence_plan": sequence_plan,
        "requirement_history": requirement_history,
        "first_focus": first_focus,
        "second_focus": second_focus,
    }


def _store_with_workspace(tmp_path: Path, workspace, *, name: str) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(TEST_USER_ID, workspace.__class__.model_validate(workspace.model_dump(mode="json")))
    return store


def _clone_workspace(workspace):
    return workspace.__class__.model_validate(workspace.model_dump(mode="json"))


def _request(message: str) -> ChatRequest:
    return ChatRequest(message=message, interaction_mode="ask")


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
            elif key in {"requirement_run_id", "requirement_version_id"}:
                normalized[key] = "<requirement_id>"
            elif key in {"board_task_run_id", "board_task_version_id"}:
                normalized[key] = "<board_task_id>"
            elif key == "id" and isinstance(item, str) and item.startswith("interaction_"):
                normalized[key] = "<interaction_id>"
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


def _history_rows(store: SqliteCourseStore, lesson_id: str) -> list[dict[str, Any]]:
    return [
        *store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
        *store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id),
    ]


def _sequence_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
    ]


def _sequence_exit_trace() -> list[str]:
    return [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_EXIT.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _sequence_continue_trace() -> list[str]:
    return [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_CONTINUE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def _patch_sequence_reply_generators(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {"end": [], "explain": []}

    def _sequence_end(**kwargs):
        calls["end"].append(kwargs)
        return "AI生成：顺序讲解结束。", "chatbot_interaction"

    def _directed_explanation(**kwargs):
        calls["explain"].append(kwargs)
        return "AI生成：继续顺序讲解。", "chatbot_interaction", {"directive": "sequence"}

    monkeypatch.setattr(chatbot_module, "_generate_sequence_end_message", _sequence_end)
    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _directed_explanation)
    return calls


def _patch_sequence_start_reply(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _directed_explanation(**kwargs):
        calls.append(kwargs)
        return "AI生成：开始顺序讲解。", "chatbot_interaction", {"directive": "sequence_start"}

    monkeypatch.setattr(chatbot_module, "_generate_board_directed_explanation_message", _directed_explanation)
    return calls


def _start_sequence_with_inputs(inputs: dict[str, Any]):
    return chatbot_module._start_section_explanation_sequence(
        workspace=inputs["workspace"],
        package=inputs["package"],
        lesson=inputs["lesson"],
        user_id=TEST_USER_ID,
        request=inputs["request"],
        requirements=inputs["requirements"],
        learning_clarification=inputs["learning_clarification"],
        resources=inputs["resources"],
        board_task=inputs["board_task"],
        board_task_history=inputs["board_task_history"],
        board_task_stamp=inputs["board_task_stamp"],
        action_decision=None,
        decision=inputs["decision"],
        resolution=inputs["resolution"],
        sequence_plan=inputs["sequence_plan"],
        requirement_history=inputs["requirement_history"],
        interaction_metadata={"source": "sequence_start_test"},
    )


def test_sequence_start_records_trace_and_consumes_board_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    inputs = _sequence_start_inputs(tmp_path, name="sequence_start_current")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    calls = _patch_sequence_start_reply(monkeypatch)
    original_board = inputs["lesson"].board_document.model_dump(mode="json")

    with bind_workflow_trace_collector() as collector:
        response = _start_sequence_with_inputs(inputs)

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    session = response.active_interaction_session
    assert _node_values(collector) == [
        NodeId.BOARD_SEQUENCE_START.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "started"
    assert collector.steps[0].reason == inputs["sequence_plan"].reason
    assert collector.steps[0].run_id == inputs["board_task_stamp"].run_id
    assert collector.steps[0].version_id == inputs["board_task_stamp"].version_id
    assert collector.steps[0].commit_id == commit.id
    assert collector.steps[1].decision == "committed"
    assert collector.steps[1].run_id == inputs["board_task_stamp"].run_id
    assert collector.steps[1].version_id == inputs["board_task_stamp"].version_id
    assert collector.steps[1].commit_id == commit.id
    assert collector.steps[2].decision == "assembled"
    assert session is not None
    assert session.status == "active"
    assert session.sequence_mode == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert session.sequence_index == 0
    assert session.sequence_items == inputs["sequence_plan"].items
    assert session.target_focus == inputs["first_focus"]
    assert session.reference_context == focus_context(inputs["first_focus"])
    assert session.source_board_task_run_id == inputs["board_task_stamp"].run_id
    assert session.source_board_task_version_id == inputs["board_task_stamp"].version_id
    assert session.source_board_task_route == "explain"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert response.chatbot_message == "AI生成：开始顺序讲解。"
    assert response.resolved_focus == inputs["first_focus"]
    assert response.focus_candidates == inputs["sequence_plan"].items
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.active_board_task_sheet is None
    assert response.board_task_sheet == inputs["board_task"]
    assert response.board_task_run_id == inputs["board_task_stamp"].run_id
    assert response.board_task_version_id == inputs["board_task_stamp"].version_id
    assert response.board_task_phase == "consumed"
    assert commit.label == "Section explanation session start"
    assert commit.message == "Started a sequential section explanation session"
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["source"] == "sequence_start_test"
    assert commit.metadata["assistant_message"] == response.chatbot_message
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["board_explanation_directive"] == {"directive": "sequence_start"}
    assert commit.metadata["board_task_run_id"] == inputs["board_task_stamp"].run_id
    assert commit.metadata["board_task_version_id"] == inputs["board_task_stamp"].version_id
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["section_explanation_sequence"] == [
        item.model_dump(mode="json") for item in inputs["sequence_plan"].items
    ]
    assert commit.metadata["explanation_sequence_mode"] == ATOMIC_EXPLANATION_SEQUENCE_MODE
    assert commit.metadata["interaction_session_before"] is None
    assert commit.metadata["interaction_session_after"] == session.model_dump(mode="json")
    assert commit.metadata["active_interaction_session_after"] == session.model_dump(mode="json")
    assert len(calls) == 1
    assert calls[0]["target_excerpt"] == focus_context(inputs["first_focus"])
    assert calls[0]["interaction_context"]["sequence_index"] == 0
    assert calls[0]["interaction_context"]["sequence_total"] == 2
    assert "第 1/2" in calls[0]["request"].message
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    board_task_events = inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"])
    assert [event["event_type"] for event in board_task_events] == ["created", "ready", "consumed"]
    assert json.loads(board_task_events[-1]["metadata_json"]) == {"commit_id": commit.id}
    board_task_versions = inputs["store"].list_board_task_versions(
        owner_user_id=TEST_USER_ID,
        lesson_id=inputs["lesson_id"],
    )
    assert len(board_task_versions) == 1
    assert board_task_versions[0]["status"] == "ready"


@pytest.mark.parametrize("failure_point", ["reply", "commit", "consume", "save", "response"])
def test_sequence_start_failure_ordering_records_trace_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    inputs = _sequence_start_inputs(tmp_path, name=f"sequence_start_{failure_point}_failure")
    monkeypatch.setattr(workspace_state, "STORE", inputs["store"])
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    if failure_point == "reply":
        monkeypatch.setattr(
            chatbot_module,
            "_generate_board_directed_explanation_message",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
        )
        expected_error = "reply failed"
    else:
        _patch_sequence_start_reply(monkeypatch)
        if failure_point == "commit":
            monkeypatch.setattr(
                chatbot_module,
                "commit_operations",
                lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
            )
            expected_error = "commit failed"
        elif failure_point == "consume":
            monkeypatch.setattr(
                inputs["board_task_history"],
                "consume",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("consume failed")),
            )
            expected_error = "consume failed"
        elif failure_point == "save":
            monkeypatch.setattr(
                chatbot_module,
                "_save_workspace_for_user",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
            )
            expected_error = "save failed"
        else:
            monkeypatch.setattr(
                chatbot_module,
                "_response",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
            )
            expected_error = "response failed"

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match=expected_error):
            _start_sequence_with_inputs(inputs)

    nodes = _node_values(collector)
    if failure_point in {"reply", "commit"}:
        assert nodes == []
    elif failure_point in {"consume", "save"}:
        assert nodes == [NodeId.BOARD_SEQUENCE_START.value]
    else:
        assert nodes == [NodeId.BOARD_SEQUENCE_START.value, NodeId.PERSIST_CHAT_COMMIT.value]
        assert collector.steps[1].commit_id == inputs["lesson"].history_graph.commits[-1].id
    if nodes:
        assert collector.steps[0].decision == "started"
        assert collector.steps[0].commit_id == inputs["lesson"].history_graph.commits[-1].id
        assert collector.steps[0].run_id == inputs["board_task_stamp"].run_id
        assert collector.steps[0].version_id == inputs["board_task_stamp"].version_id
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    board_task_events = inputs["store"].list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=inputs["lesson_id"])
    if failure_point == "response":
        assert [event["event_type"] for event in board_task_events] == ["created", "ready", "consumed"]
    else:
        assert board_task_events == []
    if failure_point in {"reply", "commit"}:
        assert len(inputs["lesson"].history_graph.commits) == initial_commit_count
    else:
        assert len(inputs["lesson"].history_graph.commits) == initial_commit_count + 1


def _patch_handled_sequence_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: _fail_if_called("generate_interaction_turn_decision"),
    )
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
        "handle_active_interaction_empty_decision",
        lambda **kwargs: _fail_if_called("handle_active_interaction_empty_decision"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_handoff_fallback",
        lambda **kwargs: _fail_if_called("handle_interaction_handoff_fallback"),
    )


@pytest.mark.parametrize(
    ("case", "message", "final_item", "trace", "sequence_decision", "outcome_node", "commit_label", "commit_message"),
    [
        (
            "explicit_exit",
            "结束",
            False,
            _sequence_exit_trace(),
            "exit_requested",
            NodeId.INTERACTION_EXIT,
            "Section explanation session ended",
            "Ended a sequential section explanation session",
        ),
        (
            "follow_up",
            "为什么这里这样？",
            False,
            _sequence_continue_trace(),
            "follow_up_current",
            NodeId.INTERACTION_CONTINUE,
            "Section explanation follow-up",
            "Answered a follow-up within the current sequential section",
        ),
        (
            "completion",
            "继续",
            True,
            _sequence_exit_trace(),
            "completed",
            NodeId.INTERACTION_EXIT,
            "Section explanation session completed",
            "Completed a sequential section explanation session",
        ),
        (
            "advance",
            "继续",
            False,
            _sequence_continue_trace(),
            "advance",
            NodeId.INTERACTION_CONTINUE,
            "Section explanation turn",
            "Continued a sequential section explanation session",
        ),
    ],
)
def test_sequence_turn_records_exact_trace_and_preserves_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
    trace: list[str],
    sequence_decision: str,
    outcome_node: NodeId,
    commit_label: str,
    commit_message: str,
) -> None:
    workspace, lesson_id, first_focus, second_focus = _workspace_with_sequence_session(final_item=final_item)
    lesson_before = workspace.packages[0].lessons[-1]
    session_before = lesson_before.active_interaction_session
    assert session_before is not None
    session_before_json = session_before.model_dump(mode="json")
    original_board = lesson_before.board_document.model_dump(mode="json")
    store = _store_with_workspace(tmp_path, workspace, name=f"sequence_{case}")
    monkeypatch.setattr(workspace_state, "STORE", store)
    calls = _patch_sequence_reply_generators(monkeypatch)
    _patch_handled_sequence_guardrails(monkeypatch)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(message),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    metadata = commit.metadata
    nodes = _node_values(collector)
    assert nodes == trace
    assert nodes.count(NodeId.INTERACTION_SEQUENCE_CHECK.value) == 1
    assert NodeId.INTERACTION_DECIDE.value not in nodes
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in nodes
    assert NodeId.INTERACTION_NEW_TASK.value not in nodes
    assert NodeId.INTERACTION_TERMINAL.value not in nodes
    assert collector.steps[6].decision == sequence_decision
    assert collector.steps[6].reason is None
    assert collector.steps[7].node_id == outcome_node
    assert collector.steps[7].decision == ("exit_rule" if outcome_node == NodeId.INTERACTION_EXIT else "continue_rule")
    assert collector.steps[8].decision == "committed"
    assert collector.steps[8].commit_id == commit.id
    assert collector.steps[9].decision == "assembled"
    assert lesson.board_document.model_dump(mode="json") == original_board
    assert store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id) == []
    assert commit.label == commit_label
    assert commit.message == commit_message
    assert metadata["kind"] == "interaction_flow"
    assert metadata["assistant_message"] == response.chatbot_message
    assert metadata["assistant_message_source"] == "chatbot_interaction"
    assert metadata["interaction_session_before"] == session_before_json
    assert response.interaction_decision is not None

    if case == "explicit_exit":
        assert response.active_interaction_session is None
        assert response.interaction_decision.route == "exit_rule"
        assert response.interaction_decision.reason == "用户结束当前顺序讲解。"
        assert metadata["interaction_session_after"] is None
        assert len(calls["end"]) == 1
        assert len(calls["explain"]) == 0
    elif case == "completion":
        assert response.active_interaction_session is None
        assert response.interaction_decision.route == "exit_rule"
        assert response.interaction_decision.reason == "顺序讲解已经完成。"
        assert metadata["interaction_session_after"] is None
        assert len(calls["end"]) == 1
        assert len(calls["explain"]) == 0
    elif case == "follow_up":
        assert response.active_interaction_session is not None
        session_after = response.active_interaction_session
        assert session_after.sequence_index == session_before.sequence_index
        assert session_after.turn_count == session_before.turn_count + 1
        assert session_after.target_focus == first_focus
        assert session_after.reference_context == focus_context(first_focus)
        assert session_after.status == "active"
        assert session_after.pause_reason == ""
        assert response.resolved_focus == first_focus
        assert response.interaction_decision.route == "continue_rule"
        assert metadata["interaction_session_after"] == session_after.model_dump(mode="json")
        assert len(calls["end"]) == 0
        assert len(calls["explain"]) == 1
        explain_request = calls["explain"][0]["request"]
        assert "请只围绕当前" in explain_request.message
        assert "不要推进到下一个" in explain_request.message
        assert calls["explain"][0]["target_excerpt"] == focus_context(first_focus)
    else:
        assert response.active_interaction_session is not None
        session_after = response.active_interaction_session
        assert session_after.sequence_index == session_before.sequence_index + 1
        assert session_after.turn_count == session_before.turn_count + 1
        assert session_after.target_focus == second_focus
        assert session_after.reference_context == focus_context(second_focus)
        assert session_after.status == "active"
        assert session_after.pause_reason == ""
        assert response.resolved_focus == second_focus
        assert response.interaction_decision.route == "continue_rule"
        assert metadata["interaction_session_after"] == session_after.model_dump(mode="json")
        assert len(calls["end"]) == 0
        assert len(calls["explain"]) == 1
        assert calls["explain"][0]["target_excerpt"] == focus_context(second_focus)
        assert "第二段" in calls["explain"][0]["request"].message


def test_unrecognized_sequence_input_records_not_handled_before_generic_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_unrecognized")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_sequence_end",
        lambda **kwargs: _fail_if_called("handle_interaction_sequence_end"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request("苹果香蕉"),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    assert nodes[:8] == [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_DECIDE.value,
    ]
    assert collector.steps[6].decision == "not_handled"
    assert collector.steps[7].decision == "empty"
    assert response.interaction_decision is None


def test_non_sequence_active_session_records_one_not_handled_sequence_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="non_sequence_not_handled")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_sequence_end",
        lambda **kwargs: _fail_if_called("handle_interaction_sequence_end"),
    )

    with bind_workflow_trace_collector() as collector:
        chat_service.process_chat_on_lesson(
            lesson_id,
            _request("继续"),
            user_id=TEST_USER_ID,
        )

    nodes = _node_values(collector)
    assert nodes.count(NodeId.INTERACTION_SEQUENCE_CHECK.value) == 1
    assert nodes[:8] == [
        *_sequence_trace_prefix(),
        NodeId.INTERACTION_DECIDE.value,
    ]
    assert collector.steps[6].decision == "not_handled"


@pytest.mark.parametrize(
    ("case", "message", "final_item", "sequence_decision", "outcome"),
    [
        ("explicit_exit", "结束", False, "exit_requested", "exit_requested"),
        ("completion", "继续", True, "completed", "completed"),
    ],
)
def test_sequence_exit_and_completion_call_end_handler_after_sequence_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
    sequence_decision: str,
    outcome: str,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    store = _store_with_workspace(tmp_path, workspace, name=f"{case}_handler_boundary")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_sequence_reply_generators(monkeypatch)
    calls: list[dict[str, Any]] = []

    def _wrapped_end_handler(**kwargs):
        calls.append(kwargs)
        assert kwargs["outcome"] == outcome
        assert _node_values(collector) == _sequence_trace_prefix()
        assert collector.steps[-1].decision == sequence_decision
        return interaction_sequence_end_module.handle_interaction_sequence_end(**kwargs)

    monkeypatch.setattr(chatbot_module, "handle_interaction_sequence_end", _wrapped_end_handler)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(message),
            user_id=TEST_USER_ID,
        )

    assert len(calls) == 1
    assert response.active_interaction_session is None
    nodes = _node_values(collector)
    assert nodes.count(NodeId.INTERACTION_SEQUENCE_CHECK.value) == 1
    assert nodes == _sequence_exit_trace()


@pytest.mark.parametrize(
    ("case", "message", "final_item"),
    [
        ("explicit_exit", "结束", False),
        ("follow_up", "为什么这里这样？", False),
        ("completion", "继续", True),
        ("advance", "继续", False),
    ],
)
def test_sequence_traced_and_untraced_responses_and_metadata_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    untraced_store = _store_with_workspace(tmp_path, _clone_workspace(workspace), name=f"{case}_untraced")
    traced_store = _store_with_workspace(tmp_path, _clone_workspace(workspace), name=f"{case}_traced")
    _patch_sequence_reply_generators(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        _request(message),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request(message),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


@pytest.mark.parametrize(
    ("case", "message", "final_item"),
    [
        ("follow_up", "为什么这里这样？", False),
        ("advance", "继续", False),
    ],
)
def test_sequence_follow_up_and_advance_do_not_call_end_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    store = _store_with_workspace(tmp_path, workspace, name=f"{case}_no_end_handler")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_sequence_reply_generators(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_sequence_end",
        lambda **kwargs: _fail_if_called("handle_interaction_sequence_end"),
    )

    response = chat_service.process_chat_on_lesson(
        lesson_id,
        _request(message),
        user_id=TEST_USER_ID,
    )

    assert response.active_interaction_session is not None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "continue_rule"


def test_sequence_trace_does_not_leak_to_response_sse_history_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="sequence_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_sequence_reply_generators(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            _request("继续"),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_before"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata["interaction_session_after"]))
    assert TRACE_KEYS.isdisjoint(_all_keys(_history_rows(store, lesson_id)))

    sse_workspace, sse_lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session()
    sse_store = _store_with_workspace(tmp_path, sse_workspace, name="sequence_sse_no_trace_leak")
    monkeypatch.setattr(workspace_state, "STORE", sse_store)
    _patch_sequence_reply_generators(monkeypatch)
    events = _collect_sse_events(chat_router._chat_stream_events(sse_lesson_id, _request("继续"), user_id=TEST_USER_ID))
    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))


@pytest.mark.parametrize(
    ("case", "message", "final_item", "sequence_decision", "outcome_node"),
    [
        ("explicit_exit", "结束", False, "exit_requested", NodeId.INTERACTION_EXIT),
        ("follow_up", "为什么这里这样？", False, "follow_up_current", NodeId.INTERACTION_CONTINUE),
        ("completion", "继续", True, "completed", NodeId.INTERACTION_EXIT),
        ("advance", "继续", False, "advance", NodeId.INTERACTION_CONTINUE),
    ],
)
@pytest.mark.parametrize("failure_point", ["reply", "commit", "save", "response"])
def test_sequence_failure_ordering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
    final_item: bool,
    sequence_decision: str,
    outcome_node: NodeId,
    failure_point: str,
) -> None:
    workspace, lesson_id, _first_focus, _second_focus = _workspace_with_sequence_session(final_item=final_item)
    store = _store_with_workspace(tmp_path, workspace, name=f"{case}_{failure_point}_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)

    if failure_point == "reply":
        if outcome_node == NodeId.INTERACTION_EXIT:
            monkeypatch.setattr(
                chatbot_module,
                "_generate_sequence_end_message",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
            )
        else:
            monkeypatch.setattr(
                chatbot_module,
                "_generate_board_directed_explanation_message",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
            )
        expected_error = "reply failed"
    else:
        _patch_sequence_reply_generators(monkeypatch)
        if failure_point == "commit":
            target_module = (
                interaction_sequence_end_module
                if outcome_node == NodeId.INTERACTION_EXIT
                else interaction_sequence_continue_module
            )
            monkeypatch.setattr(
                target_module,
                "commit_operations",
                lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
            )
            expected_error = "commit failed"
        elif failure_point == "save":
            monkeypatch.setattr(
                chatbot_module,
                "_save_workspace_for_user",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
            )
            expected_error = "save failed"
        else:
            monkeypatch.setattr(
                chatbot_module,
                "_response",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
            )
            expected_error = "response failed"

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match=expected_error):
            chat_service.process_chat_on_lesson(
                lesson_id,
                _request(message),
                user_id=TEST_USER_ID,
            )

    nodes = _node_values(collector)
    assert nodes[:7] == _sequence_trace_prefix()
    assert collector.steps[6].decision == sequence_decision
    if failure_point == "reply":
        assert outcome_node.value not in nodes
        assert NodeId.PERSIST_CHAT_COMMIT.value not in nodes
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    elif failure_point in {"commit", "save"}:
        assert nodes == [*_sequence_trace_prefix(), outcome_node.value]
        assert NodeId.PERSIST_CHAT_COMMIT.value not in nodes
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    else:
        assert nodes == [*_sequence_trace_prefix(), outcome_node.value, NodeId.PERSIST_CHAT_COMMIT.value]
        assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
