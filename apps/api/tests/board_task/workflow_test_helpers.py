from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import BoardFocusRef, InteractionSession, LibraryChapter, ResourceLibraryItem
from app.services.course_runtime import refresh_lesson_runtime
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.rich_document import build_document
from app.services.workflow_trace import NodeId, WorkflowTraceCollector, bind_workflow_trace_collector


TRACE_KEYS = {
    "workflow_trace",
    "workflow_steps",
    "workflow_node_id",
    "workflow_step_trace",
}

DEFAULT_BOARD_CONTENT = "# 已有板书\n\n这一段已有内容。\n"

collect_workflow_trace = bind_workflow_trace_collector


def workspace_with_lesson(
    *,
    existing_board: bool = False,
    lesson_title: str = "测试页面",
    board_title: str = "已有板书",
    content_text: str = DEFAULT_BOARD_CONTENT,
):
    workspace = build_initial_workspace_state()
    package = workspace.packages[0]
    lesson = create_empty_lesson(lesson_title)
    if existing_board:
        refresh_lesson_runtime(
            lesson,
            document=build_document(title=board_title, content_text=content_text),
        )
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    return workspace, lesson.id


def save_workspace_to_store(
    tmp_path: Path,
    workspace,
    *,
    user_id: str,
    name: str,
) -> SqliteCourseStore:
    store = SqliteCourseStore(tmp_path / name / "openclass.sqlite3", legacy_json_path=None)
    store.save_for_user(user_id, clone_workspace(workspace))
    return store


def clone_workspace(workspace):
    return workspace.__class__.model_validate(workspace.model_dump(mode="json"))


def active_interaction_session(**overrides: Any) -> InteractionSession:
    defaults: dict[str, Any] = {
        "status": "active",
        "rule_text": "按当前规则逐轮互动。",
        "interaction_goal": "继续当前互动。",
        "reference_context": "这一段已有内容。",
        "compliant_input_rule": "用户继续按规则输入。",
        "expected_user_behavior": "用户继续按规则输入。",
        "assistant_behavior": "Chatbot 按当前规则回应。",
        "turn_count": 1,
    }
    defaults.update(overrides)
    return InteractionSession(**defaults)


def board_focus(
    lesson,
    *,
    heading_path: list[str] | None = None,
    excerpt: str = "这一段已有内容。",
    confidence: float = 1.0,
    reason: str = "测试顺序讲解。",
    display_label: str = "已有板书",
) -> BoardFocusRef:
    return BoardFocusRef(
        source="board",
        lesson_id=lesson.id,
        document_id=lesson.board_document.id,
        heading_path=heading_path or ["已有板书"],
        excerpt=excerpt,
        confidence=confidence,
        reason=reason,
        display_label=display_label,
    )


def append_resource(
    package,
    lesson,
    *,
    resource_id: str,
    chapter_id: str,
    name: str = "参考资料",
    chapter_title: str = "资料章节",
    summary: str = "这一章包含参考内容。",
    keywords: list[str] | None = None,
) -> None:
    package.resources.append(
        ResourceLibraryItem(
            id=resource_id,
            name=name,
            mime_type="text/plain",
            resource_type="document",
            size_bytes=128,
            scope_lesson_id=lesson.id,
            outline=[
                LibraryChapter(
                    id=chapter_id,
                    title=chapter_title,
                    level=1,
                    summary=summary,
                    keywords=keywords or ["参考内容"],
                    path=[chapter_title],
                )
            ],
        )
    )


def parse_sse(block: str) -> tuple[str, dict[str, Any]]:
    event = "message"
    data_lines: list[str] = []
    for line in block.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    return event, json.loads("\n".join(data_lines))


def collect_sse_events(stream) -> list[tuple[str, dict[str, Any]]]:
    return [parse_sse(block) for block in stream]


def node_values(collector: WorkflowTraceCollector) -> list[str]:
    return [step.node_id.value for step in collector.steps]


def chat_trace_prefix() -> list[str]:
    return [
        NodeId.CONTEXT_LOAD.value,
        NodeId.TURN_CONTEXT_BUILD.value,
        NodeId.BOARD_ACTION_DECIDE.value,
        NodeId.CHAT_TURN_GATE.value,
        NodeId.RESOURCE_PREFLIGHT.value,
        NodeId.ACTIVE_INTERACTION_CHECK.value,
    ]


def active_interaction_trace_prefix() -> list[str]:
    return [
        *chat_trace_prefix(),
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
        NodeId.INTERACTION_DECIDE.value,
    ]


def sequence_interaction_trace_prefix() -> list[str]:
    return [
        *chat_trace_prefix(),
        NodeId.INTERACTION_SEQUENCE_CHECK.value,
    ]


def all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(all_keys(item))
        return keys
    return set()


def normalize_visible_response(
    value: Any,
    *,
    normalize_board_task_ids: bool = False,
    normalize_source_board_task_ids: bool = False,
    normalize_interaction_ids: bool = False,
) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        is_commit = {"label", "message", "branch_name", "snapshot", "metadata"}.issubset(value)
        for key, item in value.items():
            if key in {"created_at", "updated_at"}:
                normalized[key] = "<timestamp>"
            elif key in {"requirement_run_id", "requirement_version_id"}:
                normalized[key] = "<requirement_id>"
            elif normalize_board_task_ids and key in {"board_task_run_id", "board_task_version_id"}:
                normalized[key] = "<board_task_id>"
            elif normalize_source_board_task_ids and key in {
                "source_board_task_run_id",
                "source_board_task_version_id",
            }:
                normalized[key] = "<board_task_id>"
            elif normalize_interaction_ids and key == "id" and isinstance(item, str) and item.startswith("interaction_"):
                normalized[key] = "<interaction_id>"
            elif is_commit and key == "id":
                normalized[key] = "<commit_id>"
            elif key == "head_commit_id":
                normalized[key] = "<commit_id>"
            else:
                normalized[key] = normalize_visible_response(
                    item,
                    normalize_board_task_ids=normalize_board_task_ids,
                    normalize_source_board_task_ids=normalize_source_board_task_ids,
                    normalize_interaction_ids=normalize_interaction_ids,
                )
        return normalized
    if isinstance(value, list):
        return [
            normalize_visible_response(
                item,
                normalize_board_task_ids=normalize_board_task_ids,
                normalize_source_board_task_ids=normalize_source_board_task_ids,
                normalize_interaction_ids=normalize_interaction_ids,
            )
            for item in value
        ]
    return value


def fail_if_called(name: str):
    raise AssertionError(f"{name} should not be called for this workflow path")


def patch_commit_operations_failure(monkeypatch, target_module, *, message: str = "commit failed") -> None:
    monkeypatch.setattr(
        target_module,
        "commit_operations",
        lambda *args, **kwargs: _raise_runtime_error(message),
    )


def patch_chatbot_save_failure(monkeypatch, chatbot_module, *, message: str = "save failed") -> None:
    monkeypatch.setattr(
        chatbot_module,
        "_save_workspace_for_user",
        lambda **kwargs: _raise_runtime_error(message),
    )


def patch_chatbot_response_failure(monkeypatch, chatbot_module, *, message: str = "response failed") -> None:
    monkeypatch.setattr(
        chatbot_module,
        "_response",
        lambda **kwargs: _raise_runtime_error(message),
    )


def _raise_runtime_error(message: str):
    raise RuntimeError(message)
