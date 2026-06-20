from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    BoardDecision,
    BoardFocusRef,
    BoardPatchValidationResult,
    BoardTaskRequirementSheet,
    ChatRequest,
    DiffPreviewItem,
    LearningClarificationStatus,
)
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.board_task_history import BoardTaskHistoryStamp
from app.services.chat.paths.board_task_edit import (
    BoardTaskEditDependencies,
    handle_board_task_edit_terminal,
)
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import build_initial_workspace_state
from app.services.history import commit_operations
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRouteDecision
from app.services.rich_document import build_document
from app.services.segment_resolver import FocusResolution
from app.services.workflow_trace import NodeId, bind_workflow_trace_collector, record_workflow_step


TEST_USER_ID = "user_board_task_edit_handler"


def _workspace_inputs() -> dict[str, Any]:
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson("测试页面")
    refresh_lesson_runtime(
        lesson,
        document=build_document(
            title="已有板书",
            content_text="# 已有板书\n\n## 目标范围\n原始内容需要被改写。\n",
        ),
    )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    requirements = lesson.learning_requirements.model_copy(
        update={"learning_goal": "围绕现有板书执行局部改写。"}
    )
    lesson.learning_requirements = requirements
    focus = BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        segment_id="seg-target",
        kind="paragraph",
        heading_path=["已有板书", "目标范围"],
        excerpt="原始内容需要被改写。",
        confidence=0.95,
        reason="选区定位到目标段落。",
        display_label="目标范围",
    )
    board_task = BoardTaskRequirementSheet(
        target_hint="目标范围",
        location_status="selected",
        requested_action="edit",
        question_or_topic="改写目标段落",
        progress=100,
        missing_items=[],
    )
    lesson.board_task_requirements = board_task
    return {
        "workspace": workspace,
        "package": package,
        "lesson": lesson,
        "request": ChatRequest(message="请改写这段", selection={"kind": "board", "excerpt": focus.excerpt}),
        "requirements": requirements,
        "learning_clarification": LearningClarificationStatus(
            progress=100,
            label="ready",
            reason="ready",
            can_start=True,
            summary="围绕现有板书执行局部改写。",
        ),
        "requirement_history": LearningRequirementHistoryRecorder.from_store_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
            state=None,
        ),
        "focus": focus,
        "resolution": FocusResolution(focus=focus, candidates=[focus], status="selected", question=""),
        "board_task": board_task,
        "decision": BoardTaskRouteDecision(
            route="edit",
            location_status="found",
            target_focus=focus,
            reason="目标已定位，可以编辑。",
            target_scope="focus",
        ),
    }


def _success_outcome(lesson, *, changed_text: str = "改写后的内容。") -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：已改写目标段落。",
        new_document=build_document(
            title=lesson.board_document.title,
            content_text=f"# 已有板书\n\n## 目标范围\n{changed_text}\n",
            document_id=lesson.board_document.id,
        ),
        board_decision=BoardDecision(action="edit_board", reason="已改写目标段落。"),
        assistant_message_source="board_document_editor_ai",
        operation="board_patch",
        summary="已改写目标段落。",
        section_titles=["目标范围"],
        changed=True,
        operation_status="succeeded",
        patch_validation=BoardPatchValidationResult(status="pass", applied_operations=1),
        diff_preview=[
            DiffPreviewItem(
                op="update_block_content",
                heading_path=["已有板书", "目标范围"],
                before_text="原始内容需要被改写。",
                after_text=changed_text,
                summary="改写目标段落。",
            )
        ],
        patch_risk_level="low",
    )


def _failed_outcome(lesson) -> BoardDocumentEditOutcome:
    return BoardDocumentEditOutcome(
        chatbot_message="AI生成：这次没有安全改写板书。",
        new_document=lesson.board_document,
        board_decision=BoardDecision(action="no_change", reason="没有产生安全改写。"),
        assistant_message_source="board_document_editor_ai",
        operation="board_patch",
        summary="没有产生安全改写。",
        section_titles=[],
        changed=False,
        operation_status="failed",
        failure_reason="没有产生安全改写。",
    )


class _BoardTaskHistoryStub:
    def __init__(self, *, fail_consume: bool = False) -> None:
        self.ready_stamp = BoardTaskHistoryStamp(run_id="run-edit", version_id="ver-ready", phase="ready")
        self.failed_stamp = BoardTaskHistoryStamp(
            run_id="run-edit",
            version_id="ver-failed",
            phase="execution_failed",
        )
        self.consumed_stamp = BoardTaskHistoryStamp(run_id="run-edit", version_id="ver-ready", phase="consumed")
        self.fail_consume = fail_consume
        self.record_update_calls: list[dict[str, Any]] = []
        self.execution_failed_calls: list[dict[str, Any]] = []
        self.consume_commit_ids: list[str] = []

    def record_update(self, *, sheet: BoardTaskRequirementSheet, status: str) -> BoardTaskHistoryStamp:
        self.record_update_calls.append({"sheet": sheet, "status": status})
        return self.ready_stamp

    def execution_failed(self, *, reason: str, metadata: dict[str, object]) -> BoardTaskHistoryStamp:
        self.execution_failed_calls.append({"reason": reason, "metadata": metadata})
        return self.failed_stamp

    def consume(self, *, commit_id: str, change_summary: str | None = None) -> BoardTaskHistoryStamp:
        self.consume_commit_ids.append(commit_id)
        if self.fail_consume:
            raise RuntimeError("consume failed")
        return self.consumed_stamp


def _metadata_from_patch(edit_outcome: BoardDocumentEditOutcome) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if edit_outcome.patch_validation is not None:
        metadata["board_patch_validation"] = edit_outcome.patch_validation.model_dump(mode="json")
    if edit_outcome.diff_preview:
        metadata["board_patch_diff"] = [item.model_dump(mode="json") for item in edit_outcome.diff_preview]
    if edit_outcome.patch_risk_level:
        metadata["board_patch_risk_level"] = edit_outcome.patch_risk_level
    return metadata


def _deps(
    calls: dict[str, Any],
    *,
    edit_outcome: BoardDocumentEditOutcome,
    fail_response: bool = False,
) -> BoardTaskEditDependencies:
    def requirements_from_board_task(**kwargs):
        calls.setdefault("requirements", []).append(kwargs)
        base = kwargs["base"]
        focus = kwargs.get("focus")
        return base.model_copy(
            update={
                "action_type": kwargs["action_type"],
                "action_instruction": kwargs["board_task"].question_or_topic,
                "target_location": focus,
                "location_status": "resolved" if focus else "missing",
            }
        )

    def persist_ready_checkpoint(**kwargs):
        calls.setdefault("ready", []).append(kwargs)
        record_workflow_step(
            NodeId.BOARD_TASK_READY_PERSIST,
            decision=kwargs["stamp"].phase,
            run_id=kwargs["stamp"].run_id,
            version_id=kwargs["stamp"].version_id,
        )

    def edit_existing_document(**kwargs):
        calls.setdefault("edit", []).append(kwargs)
        return edit_outcome

    def refresh_runtime(lesson, *, document, requirements):
        calls.setdefault("refresh", []).append({"lesson": lesson.id, "document": document.id})
        refresh_lesson_runtime(lesson, document=document, requirements=requirements)

    def recent_focus(**kwargs):
        calls.setdefault("recent_focus", []).append(kwargs)
        return kwargs["fallback_focus"]

    def clear_task_requirements(lesson):
        calls.setdefault("clear", []).append(lesson.id)
        lesson.learning_requirements = None

    def normalize_package_state(package):
        calls.setdefault("normalize", []).append(package.id)

    def save_workspace_for_user(**kwargs):
        calls.setdefault("save", []).append(kwargs)

    def build_response(**kwargs):
        calls.setdefault("response", []).append(kwargs)
        if fail_response:
            raise RuntimeError("response failed")
        stamp = kwargs.get("board_task_stamp")
        return SimpleNamespace(
            chatbot_message=kwargs["chatbot_message"],
            board_task_stamp=stamp,
            board_task_phase=stamp.phase if stamp else None,
            active_board_task_sheet=kwargs["lesson"].board_task_requirements,
            requirement_cleared=kwargs.get("requirement_cleared"),
            board_document_operation_status=kwargs.get("board_document_operation_status"),
            board_patch_diff=kwargs.get("board_patch_diff") or [],
        )

    return BoardTaskEditDependencies(
        requirements_from_board_task=requirements_from_board_task,
        persist_ready_checkpoint=persist_ready_checkpoint,
        resource_summary=lambda resources: "暂无已上传资料摘要",
        conversation_summary=lambda conversation: "",
        edit_existing_document=edit_existing_document,
        refresh_lesson_runtime=refresh_runtime,
        build_board_teaching_guide=lambda lesson: {"lesson_id": lesson.id},
        recent_board_edit_focus_for_commit=recent_focus,
        board_patch_metadata=_metadata_from_patch,
        board_search_evidence_metadata=lambda resolution: {"board_search_evidence": {"status": "selected"}},
        implicit_board_search_evidence=lambda **kwargs: {"status": "implicit", **kwargs},
        decision_trace_metadata=lambda **kwargs: {
            "decision_trace": {
                "role_executed": kwargs["role_executed"],
                "document_changed": kwargs["document_changed"],
                "target_scope": kwargs["target_scope"],
            }
        },
        task_metadata=lambda **kwargs: {
            "task_requirement_sheet": kwargs["requirements"].model_dump(mode="json"),
            "target_focus": kwargs["focus"].model_dump(mode="json") if kwargs.get("focus") else None,
            "requirement_cleared": kwargs["requirement_cleared"],
        },
        board_task_metadata=lambda **kwargs: {
            "board_task_run_id": kwargs["stamp"].run_id if kwargs.get("stamp") else None,
            "board_task_version_id": kwargs["stamp"].version_id if kwargs.get("stamp") else None,
            "board_task_phase": kwargs["stamp"].phase if kwargs.get("stamp") else None,
            "board_task_route": kwargs["route"],
            "board_task_decision": kwargs["decision"],
            "board_task_cleared": kwargs["cleared"],
        },
        clear_task_requirements=clear_task_requirements,
        normalize_package_state=normalize_package_state,
        save_workspace_for_user=save_workspace_for_user,
        commit_operations=commit_operations,
        build_response=build_response,
    )


def _node_values(collector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def _call_handler(inputs, calls, history, outcome, *, fail_response: bool = False):
    return handle_board_task_edit_terminal(
        workspace=inputs["workspace"],
        package=inputs["package"],
        lesson=inputs["lesson"],
        user_id=TEST_USER_ID,
        request=inputs["request"],
        requirements=inputs["requirements"],
        learning_clarification=inputs["learning_clarification"],
        resources=[],
        board_task=inputs["board_task"],
        selection_excerpt="原始内容需要被改写。",
        resolution=inputs["resolution"],
        action_type="expand_target",
        requirement_history=inputs["requirement_history"],
        board_task_history=history,
        decision=inputs["decision"],
        source_interaction_metadata={"source": "board_task_edit_handler_test"},
        deps=_deps(calls, edit_outcome=outcome, fail_response=fail_response),
    )


def test_handler_success_persists_ready_commit_consume_and_response() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    history = _BoardTaskHistoryStub()
    initial_commit_count = len(inputs["lesson"].history_graph.commits)
    commit_metadata_at_creation: dict[str, Any] = {}

    def capture_commit_metadata(*args, **kwargs):
        result = commit_operations(*args, **kwargs)
        commit_metadata_at_creation.clear()
        commit_metadata_at_creation.update(
            json.loads(json.dumps(args[0].history_graph.commits[-1].metadata, sort_keys=True))
        )
        return result

    deps = _deps(calls, edit_outcome=_success_outcome(inputs["lesson"]))
    deps = BoardTaskEditDependencies(**{**deps.__dict__, "commit_operations": capture_commit_metadata})

    with bind_workflow_trace_collector() as collector:
        response = handle_board_task_edit_terminal(
            workspace=inputs["workspace"],
            package=inputs["package"],
            lesson=inputs["lesson"],
            user_id=TEST_USER_ID,
            request=inputs["request"],
            requirements=inputs["requirements"],
            learning_clarification=inputs["learning_clarification"],
            resources=[],
            board_task=inputs["board_task"],
            selection_excerpt="原始内容需要被改写。",
            resolution=inputs["resolution"],
            action_type="expand_target",
            requirement_history=inputs["requirement_history"],
            board_task_history=history,
            decision=inputs["decision"],
            source_interaction_metadata={"source": "board_task_edit_handler_test"},
            deps=deps,
        )

    commit = inputs["lesson"].history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_EDIT_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert len(inputs["lesson"].history_graph.commits) == initial_commit_count + 1
    assert "改写后的内容" in inputs["lesson"].board_document.content_text
    assert inputs["lesson"].board_task_requirements is None
    assert inputs["lesson"].learning_requirements is None
    assert history.record_update_calls == [{"sheet": inputs["board_task"], "status": "ready"}]
    assert history.consume_commit_ids == [commit.id]
    assert calls["edit"][0]["selection_excerpt"] == "原始内容需要被改写。"
    assert calls["edit"][0]["target_scope"] == "focus"
    assert calls["edit"][0]["allow_replace_document"] is False
    assert calls["requirements"][0]["action_type"] == "expand_target"
    assert calls["save"][0]["board_task_history"] is history
    assert response.board_task_stamp == history.consumed_stamp
    assert response.board_task_phase == "consumed"
    assert response.active_board_task_sheet is None
    assert commit.label == "Board task edit"
    assert commit.message == "Executed an existing-board edit task"
    assert commit.metadata == commit_metadata_at_creation
    assert commit.metadata["source"] == "board_task_edit_handler_test"
    assert commit.metadata["board_task_route"] == "edit"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_patch_validation"]["status"] == "pass"
    assert commit.metadata["board_patch_diff"][0]["op"] == "update_block_content"
    assert collector.steps[2].run_id == history.consumed_stamp.run_id
    assert collector.steps[2].version_id == history.consumed_stamp.version_id
    assert collector.steps[2].commit_id == commit.id


def test_handler_failure_records_execution_failed_and_keeps_task() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    history = _BoardTaskHistoryStub()
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        response = _call_handler(inputs, calls, history, _failed_outcome(inputs["lesson"]))

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
        NodeId.RESPONSE_ASSEMBLE.value,
    ]
    assert len(inputs["lesson"].history_graph.commits) == initial_commit_count
    assert inputs["lesson"].board_task_requirements == inputs["board_task"]
    assert inputs["lesson"].learning_requirements is not None
    assert history.consume_commit_ids == []
    assert calls.get("clear") is None
    assert response.board_task_stamp == history.failed_stamp
    assert response.active_board_task_sheet == inputs["board_task"]
    assert response.board_document_operation_status == "failed"
    failure = history.execution_failed_calls[-1]
    assert failure["reason"] == "没有产生安全改写。"
    assert failure["metadata"]["board_task_route"] == "edit"
    assert failure["metadata"]["board_task_cleared"] is False
    assert failure["metadata"]["board_search_evidence"] == {"status": "selected"}
    assert NodeId.BOARD_EDIT_EXECUTE.value not in nodes
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes


def test_handler_consume_failure_does_not_clear_save_persist_or_assemble_response() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    history = _BoardTaskHistoryStub(fail_consume=True)
    initial_commit_count = len(inputs["lesson"].history_graph.commits)

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="consume failed"):
            _call_handler(inputs, calls, history, _success_outcome(inputs["lesson"]))

    commit = inputs["lesson"].history_graph.commits[-1]
    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_EDIT_EXECUTE.value,
    ]
    assert len(inputs["lesson"].history_graph.commits) == initial_commit_count + 1
    assert history.consume_commit_ids == [commit.id]
    assert inputs["lesson"].board_task_requirements == inputs["board_task"]
    assert inputs["lesson"].learning_requirements is not None
    assert calls.get("clear") is None
    assert calls.get("save") is None
    assert calls.get("response") is None
    assert commit.metadata["board_task_phase"] == "ready"
    assert commit.metadata["board_task_cleared"] is True
    assert NodeId.PERSIST_BOARD_COMMIT.value not in nodes
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_handler_success_response_failure_keeps_consumed_persist_without_response_node() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    history = _BoardTaskHistoryStub()

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _call_handler(inputs, calls, history, _success_outcome(inputs["lesson"]), fail_response=True)

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_EDIT_EXECUTE.value,
        NodeId.PERSIST_BOARD_COMMIT.value,
    ]
    assert history.consume_commit_ids == [inputs["lesson"].history_graph.commits[-1].id]
    assert inputs["lesson"].board_task_requirements is None
    assert inputs["lesson"].learning_requirements is None
    assert calls["save"][0]["board_task_history"] is history
    assert calls["response"][0]["board_task_stamp"] == history.consumed_stamp
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes


def test_handler_failure_response_failure_keeps_execution_failed_without_response_node() -> None:
    inputs = _workspace_inputs()
    calls: dict[str, Any] = {}
    history = _BoardTaskHistoryStub()

    with bind_workflow_trace_collector() as collector:
        with pytest.raises(RuntimeError, match="response failed"):
            _call_handler(inputs, calls, history, _failed_outcome(inputs["lesson"]), fail_response=True)

    nodes = _node_values(collector)
    assert nodes == [
        NodeId.BOARD_TASK_READY_PERSIST.value,
        NodeId.BOARD_TASK_FAILURE.value,
    ]
    assert history.execution_failed_calls[-1]["metadata"]["board_task_route"] == "edit"
    assert inputs["lesson"].board_task_requirements == inputs["board_task"]
    assert calls["save"][0]["board_task_history"] is history
    assert calls["response"][0]["board_task_stamp"] == history.failed_stamp
    assert NodeId.RESPONSE_ASSEMBLE.value not in nodes
