from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    ChatRequest,
    InteractionSession,
    InteractionTurnDecision,
    LibraryChapter,
    ResourceLibraryItem,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.interaction_board_task_handoff import (
    InteractionBoardTaskHandoffDependencies,
    attempt_interaction_board_task_handoff,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.history import commit_operations
from app.services.interaction_rules import interaction_session_metadata
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.workflow_trace import (
    NodeId,
    WorkflowTraceCollector,
    bind_workflow_trace_collector,
    current_workflow_trace_collector,
)


TEST_USER_ID = "user_interaction_handoff"
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


def _workspace_with_active_session():
    workspace, lesson_id = _workspace_with_lesson(existing_board=True)
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    package.resources.append(
        ResourceLibraryItem(
            id="resource-handoff",
            name="参考资料",
            mime_type="text/plain",
            resource_type="document",
            size_bytes=128,
            scope_lesson_id=lesson.id,
            outline=[
                LibraryChapter(
                    id="chapter-handoff",
                    title="资料章节",
                    level=1,
                    summary="这一章包含参考内容。",
                    keywords=["参考内容"],
                    path=["资料章节"],
                )
            ],
        )
    )
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


def _workspace_with_sequence_session():
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
            elif key in {"requirement_run_id", "requirement_version_id"}:
                normalized[key] = "<requirement_id>"
            elif key in {"board_task_run_id", "board_task_version_id"}:
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


def _requirement_history(lesson_id: str) -> LearningRequirementHistoryRecorder:
    return LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=None,
    )


def _board_task_history(lesson_id: str) -> BoardTaskHistoryRecorder:
    return BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
        state=None,
    )


def _handoff_deps(handler) -> InteractionBoardTaskHandoffDependencies:
    return InteractionBoardTaskHandoffDependencies(
        handle_existing_board_task_flow=handler,
        build_interaction_session_metadata=interaction_session_metadata,
    )


def _count_handoff_coordinator_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"count": 0}
    original_handler = chatbot_module.attempt_interaction_board_task_handoff

    def _counting_handler(**kwargs):
        calls["count"] += 1
        calls["kwargs"] = kwargs
        return original_handler(**kwargs)

    monkeypatch.setattr(chatbot_module, "attempt_interaction_board_task_handoff", _counting_handler)
    return calls


def _patch_board_task_handoff_response(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"count": 0}

    def _board_task_response(**kwargs):
        calls["count"] += 1
        calls["kwargs"] = kwargs
        collector = current_workflow_trace_collector()
        calls["nodes_at_entry"] = _node_values(collector) if collector is not None else []
        calls["last_step_at_entry"] = collector.steps[-1] if collector is not None else None
        calls["commit_count_before"] = len(kwargs["lesson"].history_graph.commits)
        commit_operations(
            kwargs["lesson"],
            [],
            label="Board task handoff",
            message="Handled board task handoff",
            new_document=kwargs["lesson"].board_document,
            metadata={
                "kind": "board_task_flow",
                "user_message": kwargs["request"].message,
                "assistant_message": "转入板书任务。",
                "assistant_message_source": "board_task_flow",
                **kwargs["source_interaction_metadata"],
            },
        )
        calls["commit_count_after"] = len(kwargs["lesson"].history_graph.commits)
        response = chatbot_module._response(
            workspace=kwargs["workspace"],
            package=kwargs["package"],
            lesson=kwargs["lesson"],
            chatbot_message="转入板书任务。",
            learning_clarification=chatbot_module._latest_learning_clarification(
                kwargs["lesson"],
                requirements=kwargs["requirements"],
            ),
            requirements=kwargs["requirements"],
            board_decision=BoardDecision(action="no_change", reason="board task handled"),
            requirement_history=kwargs["requirement_history"],
            board_task_history=kwargs["board_task_history"],
        )
        calls["response"] = response
        return response

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _board_task_response)
    return calls


def _direct_handoff_inputs(route: str = "new_task") -> dict[str, Any]:
    workspace, lesson_id = _workspace_with_active_session()
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "user_id": TEST_USER_ID,
        "request": ChatRequest(message=f"{route} message"),
        "requirements": lesson.learning_requirements,
        "resources": package.resources,
        "selection_excerpt": None,
        "selection_text": None,
        "requirement_history": _requirement_history(lesson_id),
        "board_task_history": _board_task_history(lesson_id),
        "session_before": lesson.active_interaction_session,
        "decision": _interaction_decision(route),
    }


@pytest.mark.parametrize("route", ["continue_rule", "resume_rule", "rule_violation", "exit_rule"])
def test_coordinator_rejects_unsupported_routes(route: str) -> None:
    inputs = _direct_handoff_inputs(route=route)
    lesson = inputs["lesson"]
    session_before = lesson.active_interaction_session

    with pytest.raises(ValueError, match="unsupported interaction board-task handoff route"):
        attempt_interaction_board_task_handoff(
            **inputs,
            deps=_handoff_deps(lambda **kwargs: _fail_if_called("handle_existing_board_task_flow")),
        )

    assert lesson.active_interaction_session == session_before


def test_coordinator_rejects_missing_session_before() -> None:
    inputs = _direct_handoff_inputs()
    inputs["session_before"] = None
    lesson = inputs["lesson"]
    session_before = lesson.active_interaction_session

    with pytest.raises(ValueError, match="requires a previous interaction session"):
        attempt_interaction_board_task_handoff(
            **inputs,
            deps=_handoff_deps(lambda **kwargs: _fail_if_called("handle_existing_board_task_flow")),
        )

    assert lesson.active_interaction_session == session_before


def test_coordinator_rejects_missing_active_session() -> None:
    inputs = _direct_handoff_inputs()
    lesson = inputs["lesson"]
    lesson.active_interaction_session = None

    with pytest.raises(ValueError, match="requires an active session"):
        attempt_interaction_board_task_handoff(
            **inputs,
            deps=_handoff_deps(lambda **kwargs: _fail_if_called("handle_existing_board_task_flow")),
        )

    assert lesson.active_interaction_session is None


def test_coordinator_rejects_mismatched_active_session() -> None:
    inputs = _direct_handoff_inputs()
    lesson = inputs["lesson"]
    session_before = inputs["session_before"]
    lesson.active_interaction_session = InteractionSession(
        status="active",
        rule_text="另一个规则。",
        interaction_goal="另一个互动。",
        reference_context="另一个目标。",
        compliant_input_rule="用户继续。",
        expected_user_behavior="用户继续。",
        assistant_behavior="继续回应。",
    )

    with pytest.raises(ValueError, match="session mismatch"):
        attempt_interaction_board_task_handoff(
            **inputs,
            deps=_handoff_deps(lambda **kwargs: _fail_if_called("handle_existing_board_task_flow")),
        )

    assert lesson.active_interaction_session != session_before


def test_coordinator_clears_session_before_board_task_and_returns_same_response() -> None:
    inputs = _direct_handoff_inputs()
    lesson = inputs["lesson"]
    decision = inputs["decision"]
    calls: dict[str, Any] = {"count": 0}

    def _board_task_response(**kwargs):
        calls["count"] += 1
        calls["active_session_at_entry"] = kwargs["lesson"].active_interaction_session
        response = chatbot_module._response(
            workspace=kwargs["workspace"],
            package=kwargs["package"],
            lesson=kwargs["lesson"],
            chatbot_message="转入板书任务。",
            learning_clarification=chatbot_module._latest_learning_clarification(
                kwargs["lesson"],
                requirements=kwargs["requirements"],
            ),
            requirements=kwargs["requirements"],
            board_decision=BoardDecision(action="no_change", reason="board task handled"),
            requirement_history=kwargs["requirement_history"],
            board_task_history=kwargs["board_task_history"],
        )
        calls["response"] = response
        return response

    with bind_workflow_trace_collector() as collector:
        result = attempt_interaction_board_task_handoff(
            **inputs,
            deps=_handoff_deps(_board_task_response),
        )

    assert calls["count"] == 1
    assert calls["active_session_at_entry"] is None
    assert lesson.active_interaction_session is None
    assert result.response is calls["response"]
    assert result.response is not None
    assert result.response.interaction_decision == decision
    assert result.source_interaction_metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert result.source_interaction_metadata["interaction_session_before"] is not None
    assert result.source_interaction_metadata["interaction_session_after"] is None
    assert result.source_interaction_metadata["active_interaction_session_after"] is None
    assert _node_values(collector) == [NodeId.INTERACTION_NEW_TASK.value]


@pytest.mark.parametrize("route", ["new_task", "side_learning_request"])
def test_supported_routes_call_coordinator_once_and_return_board_task_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name=f"interaction_handoff_{route}")
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
    coordinator_calls = _count_handoff_coordinator_calls(monkeypatch)
    decision = _patch_interaction_turn(monkeypatch, route, reason=f"{route} handoff reason")
    handoff_calls = _patch_board_task_handoff_response(monkeypatch)
    user_request = ChatRequest(message=f"{route} message")

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            user_request,
            user_id=TEST_USER_ID,
        )

    kwargs = handoff_calls["kwargs"]
    source_metadata = kwargs["source_interaction_metadata"]
    assert coordinator_calls["count"] == 1
    assert handoff_calls["count"] == 1
    assert handoff_calls["response"] is response
    assert handoff_calls["commit_count_after"] == handoff_calls["commit_count_before"] + 1
    assert handoff_calls["nodes_at_entry"] == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert handoff_calls["last_step_at_entry"].decision == route
    assert handoff_calls["last_step_at_entry"].reason == decision.reason
    assert kwargs["lesson"].active_interaction_session is None
    assert kwargs["force_task_attempt"] is True
    assert kwargs["request"] == user_request
    assert kwargs["requirements"] is not None
    assert kwargs["resources"][0].id == "resource-handoff"
    assert kwargs["requirement_history"].lesson_id == lesson_id
    assert kwargs["board_task_history"].lesson_id == lesson_id
    assert source_metadata["interaction_decision"] == decision.model_dump(mode="json")
    assert source_metadata["interaction_session_before"] is not None
    assert source_metadata["interaction_session_after"] is None
    assert source_metadata["active_interaction_session_after"] is None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == route
    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert collector.steps[7].decision == route
    assert collector.steps[8].decision == route
    assert collector.steps[8].reason == decision.reason
    assert NodeId.INTERACTION_CONTINUE.value not in _node_values(collector)
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in _node_values(collector)
    assert NodeId.INTERACTION_EXIT.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


@pytest.mark.parametrize("route", ["new_task", "side_learning_request"])
def test_handoff_none_fallback_stays_in_chatbot_with_attempt_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name=f"interaction_{route}_fallback")
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
    if route == "side_learning_request":
        monkeypatch.setattr(
            chatbot_module,
            "_generate_board_directed_explanation_message",
            lambda **kwargs: ("AI生成：暂时没有可执行的板书任务。", "chatbot_interaction", None),
        )
    decision = _patch_interaction_turn(
        monkeypatch,
        route,
        reason=f"{route} fallback reason",
        chatbot_message="AI生成：暂时没有可执行的板书任务。",
    )
    handoff_calls: dict[str, Any] = {"count": 0}

    def _board_task_none(**kwargs):
        handoff_calls["count"] += 1
        handoff_calls["active_session_at_entry"] = kwargs["lesson"].active_interaction_session
        return None

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _board_task_none)

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=f"{route} fallback"),
            user_id=TEST_USER_ID,
        )

    assert handoff_calls["count"] == 1
    assert handoff_calls["active_session_at_entry"] is None
    assert response.active_interaction_session is None
    assert response.interaction_decision is not None
    assert response.interaction_decision.route == route
    assert response.chatbot_message == "AI生成：暂时没有可执行的板书任务。"
    nodes = _node_values(collector)
    assert nodes[:9] == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert collector.steps[8].decision == route
    assert collector.steps[8].reason == decision.reason
    assert NodeId.INTERACTION_CONTINUE.value not in nodes
    assert NodeId.INTERACTION_RULE_VIOLATION.value not in nodes
    assert NodeId.INTERACTION_EXIT.value not in nodes


def test_handoff_exception_propagates_with_attempt_trace_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="interaction_handoff_exception")
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
    decision = _patch_interaction_turn(
        monkeypatch,
        "new_task",
        reason="new task raises reason",
    )

    def _raise_board_task(**kwargs):
        assert kwargs["lesson"].active_interaction_session is None
        raise RuntimeError("board task failed")

    monkeypatch.setattr(chatbot_module, "_handle_existing_board_task_flow", _raise_board_task)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="board task failed"):
            chat_service.process_chat_on_lesson(
                lesson_id,
                ChatRequest(message="新的板书任务"),
                user_id=TEST_USER_ID,
            )

    assert _node_values(collector) == [
        *_interaction_trace_prefix(),
        NodeId.INTERACTION_NEW_TASK.value,
    ]
    assert collector.steps[8].decision == "new_task"
    assert collector.steps[8].reason == decision.reason
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_exit_rule_does_not_call_handoff_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="interaction_exit_no_handoff")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )
    _patch_interaction_turn(
        monkeypatch,
        "exit_rule",
        reason="用户结束互动。",
        chatbot_message="AI生成：好的，我们先结束这个互动。",
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="结束互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == "exit_rule"
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


@pytest.mark.parametrize("route", ["continue_rule", "resume_rule", "rule_violation"])
def test_continue_resume_and_violation_do_not_call_handoff_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name=f"interaction_{route}_no_handoff")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )
    _patch_interaction_turn(monkeypatch, route, reason=f"{route} reason")

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message=f"{route} message"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is not None
    assert response.interaction_decision.route == route
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_empty_decision_does_not_call_handoff_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="interaction_empty_no_handoff")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(openai_course_ai, "generate_interaction_turn_decision", lambda **kwargs: None)
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="继续互动"),
            user_id=TEST_USER_ID,
        )

    assert response.interaction_decision is None
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_sequence_session_does_not_call_handoff_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_sequence_session()
    store = _store_with_workspace(tmp_path, workspace, name="interaction_sequence_no_handoff")
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        chatbot_module,
        "attempt_interaction_board_task_handoff",
        lambda **kwargs: _fail_if_called("attempt_interaction_board_task_handoff"),
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
    assert NodeId.INTERACTION_NEW_TASK.value not in _node_values(collector)


def test_traced_and_untraced_handoff_have_same_visible_response_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="interaction_handoff_untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="interaction_handoff_traced")
    _patch_interaction_turn(
        monkeypatch,
        "new_task",
        reason="用户提出新的板书任务。",
    )
    _patch_board_task_handoff_response(monkeypatch)

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="新的板书任务"),
        user_id=TEST_USER_ID,
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="新的板书任务"),
            user_id=TEST_USER_ID,
        )

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert traced_commit.metadata == untraced_commit.metadata
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_handoff_trace_does_not_leak_to_response_sse_metadata_or_histories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_active_session()
    store = _store_with_workspace(tmp_path, workspace, name="interaction_handoff_leak")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_interaction_turn(
        monkeypatch,
        "new_task",
        reason="用户提出新的板书任务。",
    )
    handoff_calls = _patch_board_task_handoff_response(monkeypatch)

    with bind_workflow_trace_collector():
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(message="新的板书任务"),
            user_id=TEST_USER_ID,
        )

    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    source_metadata = handoff_calls["kwargs"]["source_interaction_metadata"]
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))
    assert TRACE_KEYS.isdisjoint(_all_keys(source_metadata))
    assert TRACE_KEYS.isdisjoint(
        _all_keys(store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id))
    )
    assert TRACE_KEYS.isdisjoint(
        _all_keys(store.list_board_task_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson_id))
    )

    workspace_for_stream, stream_lesson_id = _workspace_with_active_session()
    stream_store = _store_with_workspace(tmp_path, workspace_for_stream, name="interaction_handoff_stream")
    monkeypatch.setattr(workspace_state, "STORE", stream_store)
    events = _collect_sse_events(
        chat_router._chat_stream_events(
            stream_lesson_id,
            ChatRequest(message="新的板书任务"),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = next(payload for event, payload in events if event == "final")
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))
