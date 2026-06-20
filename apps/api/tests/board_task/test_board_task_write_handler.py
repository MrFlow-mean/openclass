from __future__ import annotations

import json
from typing import Any, NoReturn

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardPatchValidationResult,
    BoardTaskRequirementSheet,
    ChatRequest,
    DiffPreviewItem,
    LearningClarificationStatus,
    LearningRequirementSheet,
    SelectionRef,
)
from app.services import chatbot as chatbot_module
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.board_task_history import BoardTaskHistoryRecorder
from app.services.chat.paths.board_task_write import BoardTaskWriteDependencies, handle_board_task_write_terminal
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision, openai_course_ai
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TEST_USER_ID = "user_board_task_write_handler"


def _workspace_context():
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("处理器测试")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n这一段已有内容。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, package, lesson


def _requirements() -> LearningRequirementSheet:
    return LearningRequirementSheet(
        theme="已有板书任务",
        learning_goal="围绕已有板书完成用户指定动作",
        level="",
        known_background="",
        current_questions=[],
        target_depth="",
        output_preference="",
        boundary="",
        board_scope=[],
        success_criteria="",
    )


def _clarification() -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100,
        label="可执行",
        reason="已有板书任务已经完整。",
        ready_for_board=False,
        summary="已有板书任务已经完整。",
    )


def _board_task(*, action: str = "write", confirmation_status: str = "none") -> BoardTaskRequirementSheet:
    return BoardTaskRequirementSheet(
        target_hint="目标范围",
        location_status="selected",
        requested_action=action,
        question_or_topic="补充通用说明",
        confirmation_status=confirmation_status,
        progress=100,
        missing_items=[],
    )


def _focus(lesson) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-target",
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="这一段已有内容。",
        confidence=0.95,
        reason="选区已经定位到目标范围。",
        display_label="目标范围",
    )


def _selection() -> SelectionRef:
    return SelectionRef(
        kind="board",
        excerpt="这一段已有内容。",
        heading_path=["已有板书", "目标范围"],
    )


def _route_decision(lesson) -> BoardTaskRouteDecision:
    return BoardTaskRouteDecision(
        route="write",
        location_status="found",
        target_focus=_focus(lesson),
        reason="已定位可扩写的板书内容。",
        write_proposal="补充通用说明",
        target_scope="focus",
    )


def _histories(lesson):
    requirement_history = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    board_task_history = BoardTaskHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    return requirement_history, board_task_history


def _success_outcome(lesson, *, chatbot_message: str = "AI生成：已补充通用说明。") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message=chatbot_message,
        new_document=build_document(
            title=lesson.board_document.title,
            content_text="# 已有板书\n\n## 目标范围\n这一段已有内容。\n\n补充后的通用说明。\n",
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已补充内容。"),
        assistant_message_source="board_document_editor_ai",
        operation="append_section",
        summary="已补充通用说明。",
        section_titles=["目标范围"],
        changed=True,
        operation_status="succeeded",
        patch_validation=BoardPatchValidationResult(status="pass", applied_operations=1),
        diff_preview=[
            DiffPreviewItem(
                op="insert_block",
                heading_path=["已有板书", "目标范围"],
                before_text="这一段已有内容。",
                after_text="补充后的通用说明。",
                summary="补充目标范围。",
            )
        ],
        patch_risk_level="low",
    )


def _failure_outcome(
    lesson,
    *,
    operation_status: str = "failed",
    summary: str = "Board task write did not produce a safe document change.",
) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：这次没有安全写入。",
        new_document=lesson.board_document,
        board_decision=BoardDecision(action="no_change", reason="没有安全变更。"),
        assistant_message_source="board_document_editor_ai",
        operation="append_section",
        summary=summary,
        section_titles=[],
        changed=False,
        operation_status=operation_status,
        failure_reason="no_safe_change" if operation_status == "failed" else None,
    )


def _node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _make_deps(
    *,
    lesson,
    edit_outcome: BoardDocumentEditOutcome,
    response_builder=None,
) -> tuple[BoardTaskWriteDependencies, dict[str, Any]]:
    calls: list[str] = []
    commit_metadata_at_creation: dict[str, Any] = {}
    save_snapshots: list[dict[str, Any]] = []

    def _edit(**kwargs):
        calls.append("edit")
        return edit_outcome

    def _refresh(target_lesson, *, document, requirements):
        calls.append("refresh")
        refresh_lesson_runtime(target_lesson, document=document, requirements=requirements)

    def _directive(**kwargs):
        calls.append("directive")
        return (
            "AI生成：已写入，并开始围绕新内容讲解。",
            "chatbot_board_directed",
            {"status": "approved", "target_excerpt": kwargs["target_excerpt"]},
        )

    def _commit(*args, **kwargs):
        calls.append("commit")
        result = commit_operations(*args, **kwargs)
        commit_metadata_at_creation.clear()
        commit_metadata_at_creation.update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    def _normalize(package):
        calls.append("normalize")
        chatbot_module.workspace_state.normalize_package_state(package)

    def _save(**kwargs):
        calls.append("save")
        board_task_history = kwargs["board_task_history"]
        save_snapshots.append(
            {
                "latest_commit_id": lesson.history_graph.commits[-1].id,
                "latest_label": lesson.history_graph.commits[-1].label,
                "operations": [dict(operation) for operation in board_task_history.operations],
            }
        )

    def _response(**kwargs):
        calls.append("response")
        if response_builder is not None:
            return response_builder(**kwargs)
        return chatbot_module._response(**kwargs)

    deps = BoardTaskWriteDependencies(
        requirements_from_board_task=chatbot_module._requirements_from_board_task,
        resource_summary=lambda resources: "资料摘要",
        conversation_summary=lambda conversation: "对话摘要",
        edit_existing_document=_edit,
        refresh_lesson_runtime=_refresh,
        build_board_teaching_guide=chatbot_module.build_board_teaching_guide,
        recent_board_edit_focus_for_commit=chatbot_module._recent_board_edit_focus_for_commit,
        generate_board_directed_explanation_message=_directive,
        board_patch_metadata=chatbot_module._board_patch_metadata,
        decision_trace_metadata=chatbot_module.decision_trace_metadata,
        task_metadata=chatbot_module._task_metadata,
        board_task_metadata=chatbot_module._board_task_metadata,
        implicit_board_search_evidence=chatbot_module._implicit_board_search_evidence,
        clear_task_requirements=chatbot_module._clear_task_requirements,
        normalize_package_state=_normalize,
        save_workspace_for_user=_save,
        commit_operations=_commit,
        build_response=_response,
    )
    return deps, {
        "calls": calls,
        "commit_metadata_at_creation": commit_metadata_at_creation,
        "save_snapshots": save_snapshots,
    }


def _run_handler(
    *,
    workspace,
    package,
    lesson,
    board_task: BoardTaskRequirementSheet,
    deps: BoardTaskWriteDependencies,
    route_decision: BoardTaskRouteDecision | None,
    board_task_history: BoardTaskHistoryRecorder,
    search_evidence: dict[str, object] | None = None,
):
    requirement_history, _ = _histories(lesson)
    lesson.board_task_requirements = board_task
    return handle_board_task_write_terminal(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="请在这段后面补充一段通用说明", selection=_selection()),
        requirements=_requirements(),
        learning_clarification=_clarification(),
        resources=[],
        board_task=board_task,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        route_decision=route_decision,
        search_evidence=search_evidence,
        source_interaction_metadata={"source_marker": "unit"},
        deps=deps,
    )


def test_direct_write_success_preserves_commit_patch_consume_save_and_response_order() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, edit_outcome=_success_outcome(lesson))
    search_evidence = {"status": "found", "source": "unit"}

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            deps=deps,
            route_decision=_route_decision(lesson),
            board_task_history=board_task_history,
            search_evidence=search_evidence,
        )

    commit = lesson.history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert captured["calls"] == ["edit", "refresh", "commit", "normalize", "save", "response"]
    assert commit.metadata == captured["commit_metadata_at_creation"]
    assert commit.metadata["board_task_route"] == "write"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_patch_validation"]["status"] == "pass"
    assert commit.metadata["board_patch_diff"][0]["op"] == "insert_block"
    assert commit.metadata["board_patch_risk_level"] == "low"
    assert commit.metadata["board_search_evidence"] == search_evidence
    assert commit.metadata["decision_trace"]["role_executed"] == "board_editor"
    assert commit.metadata["source_marker"] == "unit"
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    assert response.board_patch_diff[0].op == "insert_block"
    saved_operations = captured["save_snapshots"][0]["operations"]
    assert captured["save_snapshots"][0]["latest_label"] == "Board task write"
    assert any(
        operation.get("type") == "update_board_task_run"
        and operation.get("status") == "consumed"
        and operation.get("consumed_commit_id") == commit.id
        for operation in saved_operations
    )
    assert collector.steps[-2].commit_id == commit.id
    assert collector.steps[-1].node_id == NodeId.RESPONSE_ASSEMBLE


def test_accepted_write_confirmation_success_uses_board_directive() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, edit_outcome=_success_outcome(lesson, chatbot_message=""))

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(confirmation_status="confirmed"),
            deps=deps,
            route_decision=None,
            board_task_history=board_task_history,
        )

    commit = lesson.history_graph.commits[-1]
    assert _node_values(collector) == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert "directive" in captured["calls"]
    assert response.chatbot_message == "AI生成：已写入，并开始围绕新内容讲解。"
    assert response.board_task_phase == "consumed"
    assert commit.metadata["assistant_message_source"] == "chatbot_board_directed"
    assert commit.metadata["board_explanation_directive"]["status"] == "approved"
    assert commit.metadata["board_task_decision"] is None
    assert commit.metadata["target_scope"] == "append"


def test_unchanged_document_failure_records_failure_without_commit() -> None:
    workspace, package, lesson = _workspace_context()
    initial_commit_id = lesson.history_graph.commits[-1].id
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(
        lesson=lesson,
        edit_outcome=_failure_outcome(lesson, operation_status="succeeded", summary="没有产生文档变更。"),
    )

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            deps=deps,
            route_decision=_route_decision(lesson),
            board_task_history=board_task_history,
        )

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert lesson.history_graph.commits[-1].id == initial_commit_id
    assert captured["calls"] == ["edit", "normalize", "save", "response"]
    assert response.active_board_task_sheet is not None
    assert response.board_task_phase == "ready"
    assert response.board_document_operation_status == "succeeded"
    saved_operations = captured["save_snapshots"][0]["operations"]
    failure_events = [
        operation
        for operation in saved_operations
        if operation.get("type") == "insert_board_task_event" and operation.get("event_type") == "execution_failed"
    ]
    assert failure_events
    assert json.loads(failure_events[-1]["metadata_json"])["board_task_cleared"] is False
    assert NodeId.BOARD_WRITE_EXECUTE.value not in nodes
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes


def test_execution_failure_metadata_keeps_active_task_and_operation_failure() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)
    deps, captured = _make_deps(lesson=lesson, edit_outcome=_failure_outcome(lesson))

    with bind_workflow_trace_collector() as collector:
        response = _run_handler(
            workspace=workspace,
            package=package,
            lesson=lesson,
            board_task=_board_task(),
            deps=deps,
            route_decision=_route_decision(lesson),
            board_task_history=board_task_history,
        )

    assert _node_values(collector) == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "no_safe_change"
    saved_operations = captured["save_snapshots"][0]["operations"]
    failure_metadata = [
        json.loads(operation["metadata_json"])
        for operation in saved_operations
        if operation.get("type") == "insert_board_task_event" and operation.get("event_type") == "execution_failed"
    ][-1]
    assert failure_metadata["assistant_message_source"] == "board_document_editor_ai"
    assert failure_metadata["board_edit_operation"] == "append_section"
    assert failure_metadata["board_task_route"] == "write"
    assert failure_metadata["board_task_cleared"] is False
    assert failure_metadata["board_search_evidence"]["query_plan"]["route"] == "write"


def test_response_failure_does_not_record_response_assemble_after_durable_success() -> None:
    workspace, package, lesson = _workspace_context()
    _, board_task_history = _histories(lesson)

    def _raise_response(**kwargs):
        raise RuntimeError("response failed")

    deps, captured = _make_deps(
        lesson=lesson,
        edit_outcome=_success_outcome(lesson),
        response_builder=_raise_response,
    )

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _run_handler(
                workspace=workspace,
                package=package,
                lesson=lesson,
                board_task=_board_task(),
                deps=deps,
                route_decision=_route_decision(lesson),
                board_task_history=board_task_history,
            )

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_WRITE_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
    ]
    assert captured["calls"] == ["edit", "refresh", "commit", "normalize", "save", "response"]
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
    assert any(
        operation.get("type") == "update_board_task_run" and operation.get("status") == "consumed"
        for operation in captured["save_snapshots"][0]["operations"]
    )


def _unexpected_call(name: str) -> NoReturn:
    raise AssertionError(f"{name} should not be called")


@pytest.mark.parametrize(
    ("route", "requested_action", "resolution_status"),
    [
        ("clarify_location", "edit", "ambiguous"),
        ("edit", "edit", "selected"),
        ("explain", "explain", "selected"),
        ("chat", "chat", "selected"),
    ],
)
def test_non_write_routes_do_not_dispatch_to_write_handler(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    requested_action: str,
    resolution_status: str,
) -> None:
    workspace, package, lesson = _workspace_context()
    requirements = _requirements()
    requirement_history, board_task_history = _histories(lesson)
    focus = _focus(lesson)
    board_task = _board_task(action=requested_action)
    if resolution_status == "ambiguous":
        board_task.location_status = "ambiguous"

    monkeypatch.setattr(chatbot_module, "_execute_board_task_write", lambda **kwargs: _unexpected_call("write handler"))
    monkeypatch.setattr(chatbot_module, "_save_workspace_for_user", lambda **kwargs: None)
    monkeypatch.setattr(chatbot_module, "_emit_board_task_update", lambda **kwargs: None)
    monkeypatch.setattr(chatbot_module, "update_board_task_from_chat", lambda **kwargs: board_task)
    monkeypatch.setattr(chatbot_module, "plan_explanation_sequence", lambda **kwargs: None)
    monkeypatch.setattr(
        chatbot_module,
        "resolve_board_focus",
        lambda **kwargs: FocusResolution(
            focus=None if resolution_status == "ambiguous" else focus,
            candidates=[focus],
            status=resolution_status,
            question="请确认目标位置。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: BoardTaskRouteDecision(
            route=route,
            location_status="ambiguous" if route == "clarify_location" else "found",
            target_focus=None if route == "clarify_location" else focus,
            candidate_focuses=[focus],
            reason="非 write 路线测试。",
        ),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_focus_candidate_message",
        lambda **kwargs: ("AI生成：请确认位置。", "chatbot_board_task_clarification"),
    )
    monkeypatch.setattr(
        chatbot_module,
        "_generate_board_directed_explanation_message",
        lambda **kwargs: (
            "AI生成：按板书侧 directive 讲解。",
            "chatbot_board_directed",
            {"status": "approved"},
        ),
    )
    monkeypatch.setattr(chatbot_module, "edit_existing_document", lambda **kwargs: _success_outcome(lesson))
    monkeypatch.setattr(
        chatbot_module,
        "_maybe_start_interaction_session",
        lambda **kwargs: chatbot_module._response(
            workspace=kwargs["workspace"],
            package=kwargs["package"],
            lesson=kwargs["lesson"],
            chatbot_message="AI生成：进入互动。",
            requirements=kwargs["requirements"],
            learning_clarification=kwargs["learning_clarification"],
            board_decision=BoardDecision(action="no_change", reason="chat route"),
        ),
    )

    response = chatbot_module._handle_existing_board_task_flow(
        workspace=workspace,
        package=package,
        lesson=lesson,
        user_id=TEST_USER_ID,
        request=ChatRequest(message="请处理目标范围", selection=_selection()),
        requirements=requirements,
        resources=[],
        selection_excerpt=_selection().excerpt,
        selection_text=_selection().excerpt,
        requirement_history=requirement_history,
        board_task_history=board_task_history,
        force_task_attempt=True,
    )

    assert response is not None
