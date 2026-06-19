from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    ChatRequest,
    InteractionRuleDraft,
)
from app.routers import chat as chat_router
from app.services import chat_service, chatbot as chatbot_module, workspace_state
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.interaction_rules import InteractionStartResolution
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_interaction_start_trace"
TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}


def _workspace_with_existing_board():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 原文\n\n## 第一段\n目标原文内容\n\n## 第二段\n其他内容\n",
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


def _clone_workspace(workspace):
    return workspace.__class__.model_validate(workspace.model_dump(mode="json"))


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
                "source_board_task_run_id",
                "source_board_task_version_id",
            }:
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


def _start_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def _interaction_draft(*, target_hint: str = "") -> InteractionRuleDraft:
    return InteractionRuleDraft(
        should_start=True,
        rule_text="按用户指定的规则参考原文逐轮互动。",
        interaction_goal="围绕选中原文进行规则互动。",
        target_hint=target_hint,
        expected_user_behavior="用户每轮按规则给出输入。",
        assistant_behavior="Chatbot 每轮参考规则和原文回应。",
        reference_instruction="优先参考选中原文。",
    )


def _requirements_for_start(lesson, *, target_hint: str = ""):
    requirements = lesson.learning_requirements.model_copy(
        update={
            "learning_goal": "围绕现有板书启动规则互动。",
            "interaction_rule_draft": _interaction_draft(target_hint=target_hint),
        }
    )
    lesson.learning_requirements = requirements
    return requirements


def _direct_start_inputs(workspace, lesson_id: str, *, target_hint: str = "") -> dict[str, Any]:
    package = workspace.packages[0]
    lesson = package.lessons[-1]
    requirements = _requirements_for_start(lesson, target_hint=target_hint)
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "user_id": TEST_USER_ID,
        "request": ChatRequest(message="开始规则互动"),
        "requirements": requirements,
        "learning_clarification": chatbot_module._latest_learning_clarification(
            lesson,
            requirements=requirements,
        ),
        "resources": package.resources,
        "selection_text": None,
        "action_type": "explain_target",
        "requirement_history": LearningRequirementHistoryRecorder.from_store_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson_id,
            state=None,
        ),
    }


def _focus_resolution(lesson) -> FocusResolution:
    candidate = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=["原文", "第一段"],
        excerpt="目标原文内容",
        confidence=0.55,
        reason="测试候选位置。",
        display_label="第一段",
    )
    return FocusResolution(
        focus=None,
        candidates=[candidate],
        status="ambiguous",
        question="请选择要用于互动的板书位置。",
    )


def _patch_reply(monkeypatch: pytest.MonkeyPatch, message: str = "AI生成：已按你的规则开始互动。") -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=message),
    )


def _fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def _patch_start_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_course_ai,
        "generate_interaction_turn_decision",
        lambda **kwargs: _fail_if_called("generate_interaction_turn_decision"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _fail_if_called("generate_learning_requirement_update"),
    )


def test_service_interaction_start_records_exact_trace_and_preserves_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="service_start")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_start_guardrails(monkeypatch)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_start_focus_clarification",
        lambda **kwargs: _fail_if_called("handle_interaction_start_focus_clarification"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="选中内容",
            location_status="selected",
            requested_action="chat",
            question_or_topic="围绕选中原文进行规则互动。",
            interaction_rule_draft=_interaction_draft(target_hint="选中内容"),
            progress=100,
            missing_items=[],
        ),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(
                message="我们按这个规则和选中内容互动",
                selection={
                    "kind": "board",
                    "excerpt": "目标原文内容",
                    "lesson_id": lesson_id,
                },
            ),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    assert response.chatbot_message == "AI生成：已按你的规则开始互动。"
    assert response.active_interaction_session is not None
    assert response.active_interaction_session.status == "active"
    assert response.active_interaction_session.source_board_task_run_id is not None
    assert response.active_interaction_session.source_board_task_version_id is not None
    assert response.requirement_cleared is True
    assert response.active_requirement_sheet is None
    assert response.active_board_task_sheet is None
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot_interaction"
    assert commit.metadata["board_task_route"] == "chat"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["active_interaction_session_after"]["status"] == "active"
    assert [event["event_type"] for event in store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)][-1] == "consumed"
    assert _node_values(collector) == [
        *_start_trace_prefix(),
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.INTERACTION_START_PERSIST.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[5].decision == "not_handled"
    assert collector.steps[6].decision == "resolved"
    assert collector.steps[6].reason == "围绕选中原文进行规则互动。"
    assert collector.steps[7].decision == "started"
    assert collector.steps[7].reason == "围绕选中原文进行规则互动。"
    assert collector.steps[7].run_id == response.active_interaction_session.source_board_task_run_id
    assert collector.steps[7].version_id == response.active_interaction_session.source_board_task_version_id
    assert collector.steps[7].commit_id == commit.id
    assert collector.steps[8].decision == "assembled"
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_focus_clarification_records_start_resolve_without_start_persist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="focus_clarification")
    monkeypatch.setattr(workspace_state, "STORE", store)
    inputs = _direct_start_inputs(workspace, lesson_id, target_hint="不明确的位置")
    resolution = _focus_resolution(inputs["lesson"])
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: InteractionStartResolution(session=None, focus_resolution=resolution),
    )
    _patch_reply(monkeypatch, message="AI生成：你想用哪一段开始互动？")

    with bind_workflow_trace_collector() as collector:
        response = chatbot_module._maybe_start_interaction_session(**inputs)

    assert response is not None
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.board_decision.action == "await_focus_choice"
    assert response.focus_candidates == resolution.candidates
    assert response.active_interaction_session is None
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert commit.metadata["interaction_session_before"] is None
    assert commit.metadata["interaction_session_after"] is None
    assert _node_values(collector) == [
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "clarify_focus"
    assert collector.steps[0].reason == resolution.question
    assert collector.steps[1].commit_id == commit.id
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_focus_clarification_allows_empty_candidates_without_changing_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="focus_clarification_empty_candidates")
    monkeypatch.setattr(workspace_state, "STORE", store)
    inputs = _direct_start_inputs(workspace, lesson_id, target_hint="不存在的位置")
    resolution = FocusResolution(
        focus=None,
        candidates=[],
        status="missing",
        question="我没有找到可用于互动的板书位置，请你再指定一下。",
    )
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: InteractionStartResolution(session=None, focus_resolution=resolution),
    )
    _patch_reply(monkeypatch, message="AI生成：我没有找到可用于互动的位置。")

    with bind_workflow_trace_collector() as collector:
        response = chatbot_module._maybe_start_interaction_session(**inputs)

    assert response is not None
    commit = response.course_package.lessons[-1].history_graph.commits[-1]
    assert response.board_decision.action == "await_focus_choice"
    assert response.focus_candidates == []
    assert response.active_interaction_session is None
    assert commit.metadata["focus_candidates"] == []
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert _node_values(collector) == [
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[0].decision == "clarify_focus"
    assert collector.steps[0].reason == resolution.question
    assert collector.steps[1].commit_id == commit.id
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_board_task_focus_clarification_preserves_active_task_without_consuming(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="board_task_focus_clarification")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_start_guardrails(monkeypatch)
    _patch_reply(monkeypatch, message="AI生成：你想用哪一段开始互动？")
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="选中内容",
            location_status="selected",
            requested_action="chat",
            question_or_topic="围绕选中内容进行规则互动。",
            interaction_rule_draft=_interaction_draft(target_hint="选中内容"),
            progress=100,
            missing_items=[],
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: InteractionStartResolution(
            session=None,
            focus_resolution=_focus_resolution(kwargs["lesson"]),
        ),
    )

    with bind_workflow_trace_collector() as collector:
        response = chat_service.process_chat_on_lesson(
            lesson_id,
            ChatRequest(
                message="按这个位置开始规则互动",
                selection={
                    "kind": "board",
                    "excerpt": "目标原文内容",
                    "lesson_id": lesson_id,
                },
            ),
            user_id=TEST_USER_ID,
        )

    lesson = response.course_package.lessons[-1]
    commit = lesson.history_graph.commits[-1]
    board_task_events = store.list_board_task_events(owner_user_id=TEST_USER_ID, lesson_id=lesson_id)
    assert response.board_decision.action == "await_focus_choice"
    assert response.active_interaction_session is None
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.requested_action == "chat"
    assert commit.metadata["kind"] == "interaction_flow"
    assert commit.metadata["assistant_message_source"] == "chatbot"
    assert commit.metadata["board_task_route"] == "chat"
    assert commit.metadata["board_task_cleared"] is False
    assert commit.metadata["board_task_run_id"] is not None
    assert commit.metadata["board_task_version_id"] is not None
    assert not any(event["event_type"] == "consumed" for event in board_task_events)
    assert _node_values(collector) == [
        *_start_trace_prefix(),
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.PERSIST_CHAT_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert collector.steps[6].decision == "clarify_focus"
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert TRACE_KEYS.isdisjoint(_all_keys(response.model_dump(mode="json")))
    assert TRACE_KEYS.isdisjoint(_all_keys(commit.metadata))


def test_start_resolution_no_session_no_focus_records_not_started_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    inputs = _direct_start_inputs(workspace, lesson_id)
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: InteractionStartResolution(session=None, focus_resolution=None),
    )

    with bind_workflow_trace_collector() as collector:
        response = chatbot_module._maybe_start_interaction_session(**inputs)

    assert response is None
    assert inputs["lesson"].active_interaction_session is None
    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert collector.steps[0].decision == "not_started"


@pytest.mark.parametrize(
    ("request_update", "requirements_update"),
    [
        ({"interaction_mode": "direct_edit"}, {}),
        ({}, {"interaction_rule_draft": None}),
    ],
)
def test_start_early_guards_return_without_trace_or_building_session(
    monkeypatch: pytest.MonkeyPatch,
    request_update: dict[str, Any],
    requirements_update: dict[str, Any],
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    inputs = _direct_start_inputs(workspace, lesson_id)
    if request_update:
        inputs["request"] = inputs["request"].model_copy(update=request_update)
    if requirements_update:
        inputs["requirements"] = inputs["requirements"].model_copy(update=requirements_update)
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: _fail_if_called("build_interaction_start"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "handle_interaction_start_focus_clarification",
        lambda **kwargs: _fail_if_called("handle_interaction_start_focus_clarification"),
    )

    with bind_workflow_trace_collector() as collector:
        response = chatbot_module._maybe_start_interaction_session(**inputs)

    assert response is None
    assert collector.steps == ()


def test_start_reply_failure_keeps_resolve_without_start_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    inputs = _direct_start_inputs(workspace, lesson_id)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("reply failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="reply failed"):
            chatbot_module._maybe_start_interaction_session(**inputs)

    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert collector.steps[0].decision == "resolved"
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_start_commit_failure_keeps_resolve_without_persist_or_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    inputs = _direct_start_inputs(workspace, lesson_id)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "commit_operations",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="commit failed"):
            chatbot_module._maybe_start_interaction_session(**inputs)

    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_start_workspace_save_failure_does_not_record_commit_or_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="save_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    inputs = _direct_start_inputs(workspace, lesson_id)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "_save_workspace_for_user",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="save failed"):
            chatbot_module._maybe_start_interaction_session(**inputs)

    assert _node_values(collector) == [NodeId.INTERACTION_START_RESOLVE.value]
    assert NodeId.INTERACTION_START_PERSIST.value not in _node_values(collector)
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_start_response_failure_keeps_commit_but_not_response_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="response_failure")
    monkeypatch.setattr(workspace_state, "STORE", store)
    inputs = _direct_start_inputs(workspace, lesson_id)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        chatbot_module,
        "_response",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("response failed")),
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            chatbot_module._maybe_start_interaction_session(**inputs)

    assert _node_values(collector) == [
        NodeId.INTERACTION_START_RESOLVE.value,
        NodeId.INTERACTION_START_PERSIST.value,
    ]
    assert NodeId.PERSIST_CHAT_COMMIT.value not in _node_values(collector)
    assert NodeId.RESPONSE_ASSEMBLE.value not in _node_values(collector)


def test_traced_and_untraced_interaction_start_responses_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    untraced_store = _store_with_workspace(tmp_path, workspace, name="untraced")
    traced_store = _store_with_workspace(tmp_path, workspace, name="traced")
    _patch_start_guardrails(monkeypatch)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="选中内容",
            location_status="selected",
            requested_action="chat",
            question_or_topic="围绕选中原文进行规则互动。",
            interaction_rule_draft=_interaction_draft(target_hint="选中内容"),
            progress=100,
            missing_items=[],
        ),
    )
    request = ChatRequest(
        message="我们按这个规则和选中内容互动",
        selection={"kind": "board", "excerpt": "目标原文内容", "lesson_id": lesson_id},
    )

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chat_service.process_chat_on_lesson(lesson_id, request, user_id=TEST_USER_ID)

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chat_service.process_chat_on_lesson(lesson_id, request, user_id=TEST_USER_ID)

    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert _normalize_visible_response(traced_commit.metadata) == _normalize_visible_response(untraced_commit.metadata)
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_traced_and_untraced_focus_clarification_responses_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_workspace, lesson_id = _workspace_with_existing_board()
    untraced_workspace = _clone_workspace(base_workspace)
    traced_workspace = _clone_workspace(base_workspace)
    untraced_store = _store_with_workspace(tmp_path, untraced_workspace, name="focus_untraced")
    traced_store = _store_with_workspace(tmp_path, traced_workspace, name="focus_traced")
    monkeypatch.setattr(
        chatbot_module,
        "build_interaction_start",
        lambda **kwargs: InteractionStartResolution(
            session=None,
            focus_resolution=_focus_resolution(kwargs["lesson"]),
        ),
    )
    _patch_reply(monkeypatch, message="AI生成：你想用哪一段开始互动？")

    monkeypatch.setattr(workspace_state, "STORE", untraced_store)
    untraced_response = chatbot_module._maybe_start_interaction_session(
        **_direct_start_inputs(untraced_workspace, lesson_id, target_hint="不明确的位置")
    )

    monkeypatch.setattr(workspace_state, "STORE", traced_store)
    with bind_workflow_trace_collector():
        traced_response = chatbot_module._maybe_start_interaction_session(
            **_direct_start_inputs(traced_workspace, lesson_id, target_hint="不明确的位置")
        )

    assert traced_response is not None
    assert untraced_response is not None
    untraced_commit = untraced_response.course_package.lessons[-1].history_graph.commits[-1]
    traced_commit = traced_response.course_package.lessons[-1].history_graph.commits[-1]
    assert _normalize_visible_response(traced_commit.metadata) == _normalize_visible_response(untraced_commit.metadata)
    assert _normalize_visible_response(traced_response.model_dump(mode="json")) == _normalize_visible_response(
        untraced_response.model_dump(mode="json")
    )


def test_interaction_start_trace_does_not_leak_to_sse_final_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace, lesson_id = _workspace_with_existing_board()
    store = _store_with_workspace(tmp_path, workspace, name="sse")
    monkeypatch.setattr(workspace_state, "STORE", store)
    _patch_start_guardrails(monkeypatch)
    _patch_reply(monkeypatch)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_sheet",
        lambda **kwargs: BoardTaskRequirementSheet(
            target_hint="选中内容",
            location_status="selected",
            requested_action="chat",
            question_or_topic="围绕选中原文进行规则互动。",
            interaction_rule_draft=_interaction_draft(target_hint="选中内容"),
            progress=100,
            missing_items=[],
        ),
    )

    events = _collect_sse_events(
        chat_router._chat_stream_events(
            lesson_id,
            ChatRequest(
                message="我们按这个规则和选中内容互动",
                selection={"kind": "board", "excerpt": "目标原文内容", "lesson_id": lesson_id},
            ),
            user_id=TEST_USER_ID,
        )
    )

    final_payload = events[-1][1]
    assert events[-1][0] == "final"
    assert final_payload["chatbot_message"] == "AI生成：已按你的规则开始互动。"
    assert TRACE_KEYS.isdisjoint(_all_keys(final_payload))
