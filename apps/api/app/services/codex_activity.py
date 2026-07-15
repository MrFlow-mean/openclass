from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.models import AgentActivityEvent


def _activity_status(value: object, *, default: str = "completed") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"inprogress", "in_progress", "pending", "running"}:
        return "running"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized in {"declined", "blocked"}:
        return "blocked"
    if normalized in {"skipped", "cancelled", "canceled"}:
        return "skipped"
    return default


def _activity_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


class CodexActivityRecorder:
    """Normalize public app-server progress into durable learner-visible events."""

    def __init__(
        self,
        callback: Callable[[AgentActivityEvent], None] | None = None,
    ) -> None:
        self.callback = callback
        self._events: dict[str, AgentActivityEvent] = {}
        self._order: list[str] = []
        self._item_phases: dict[str, str] = {}

    @property
    def events(self) -> list[AgentActivityEvent]:
        return [self._events[event_id] for event_id in self._order]

    def _publish(
        self,
        *,
        item_id: str,
        turn_id: str,
        stage: str,
        label: str,
        status: str,
        role: str,
        metadata: dict[str, Any],
    ) -> None:
        if not item_id:
            return
        existing = self._events.get(item_id)
        event = AgentActivityEvent(
            id=item_id,
            turn_id=turn_id or (existing.turn_id if existing else ""),
            stage=stage,
            label=label,
            status=status,
            role=role,
            metadata=metadata,
            created_at=(
                existing.created_at
                if existing is not None
                else datetime.now(timezone.utc).isoformat()
            ),
        )
        if existing is None:
            self._order.append(item_id)
        self._events[item_id] = event
        if self.callback is not None:
            self.callback(event)

    def _append_detail(
        self,
        *,
        item_id: str,
        turn_id: str,
        delta: str,
        stage: str,
        label: str,
        role: str,
        kind: str,
    ) -> None:
        if not delta:
            return
        existing = self._events.get(item_id)
        metadata = dict(existing.metadata) if existing is not None else {"kind": kind}
        metadata["detail"] = f"{_activity_text(metadata.get('detail'))}{delta}"
        self._publish(
            item_id=item_id,
            turn_id=turn_id,
            stage=stage,
            label=label,
            status="running",
            role=role,
            metadata=metadata,
        )

    def is_commentary_agent_message(self, item_id: str) -> bool:
        return self._item_phases.get(item_id) == "commentary"

    def start_item(self, params: dict[str, Any]) -> None:
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_id = str(item.get("id") or "")
        turn_id = str(params.get("turnId") or "")
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            phase = str(item.get("phase") or "")
            if item_id:
                self._item_phases[item_id] = phase
            if phase != "commentary":
                return
            self._publish(
                item_id=item_id,
                turn_id=turn_id,
                stage="execute_role",
                label="Codex 工作进展",
                status="running",
                role="Codex",
                metadata={"kind": "commentary", "detail": str(item.get("text") or "")},
            )
            return
        descriptors: dict[str, tuple[str, str, str]] = {
            "reasoning": ("build_context", "Codex 正在思考", "Codex"),
            "plan": ("build_context", "制定工作计划", "Codex"),
            "commandExecution": ("execute_role", "运行命令", "Codex tool"),
            "fileChange": ("execute_role", "更新板书文档", "Codex tool"),
            "mcpToolCall": ("execute_role", "调用工具", "Codex tool"),
            "dynamicToolCall": ("execute_role", "调用工具", "Codex tool"),
            "collabAgentToolCall": ("execute_role", "协同处理", "Codex"),
            "subAgentActivity": ("execute_role", "协同处理", "Codex"),
            "webSearch": ("build_context", "搜索网络", "Codex tool"),
            "imageView": ("build_context", "查看图片", "Codex tool"),
            "imageGeneration": ("execute_role", "生成图片", "Codex tool"),
            "contextCompaction": ("build_context", "整理上下文", "Codex"),
        }
        descriptor = descriptors.get(item_type)
        if descriptor is None:
            return
        stage, label, role = descriptor
        metadata: dict[str, Any] = {"kind": item_type}
        if item_type == "commandExecution":
            metadata.update(
                {
                    "command": str(item.get("command") or ""),
                    "cwd": str(item.get("cwd") or ""),
                    "detail": str(item.get("aggregatedOutput") or ""),
                }
            )
        elif item_type == "webSearch":
            metadata.update({"query": str(item.get("query") or ""), "detail": str(item.get("query") or "")})
        elif item_type in {"mcpToolCall", "dynamicToolCall"}:
            metadata.update(
                {
                    "tool": str(item.get("tool") or ""),
                    "server": str(item.get("server") or item.get("namespace") or ""),
                    "arguments": item.get("arguments"),
                }
            )
        self._publish(
            item_id=item_id,
            turn_id=turn_id,
            stage=stage,
            label=label,
            status="running",
            role=role,
            metadata=metadata,
        )

    def append_notification_delta(self, method: str, params: dict[str, Any]) -> bool:
        item_id = str(params.get("itemId") or "")
        turn_id = str(params.get("turnId") or "")
        delta = str(params.get("delta") or params.get("message") or "")
        if method == "item/agentMessage/delta" and self.is_commentary_agent_message(item_id):
            self._append_detail(
                item_id=item_id,
                turn_id=turn_id,
                delta=delta,
                stage="execute_role",
                label="Codex 工作进展",
                role="Codex",
                kind="commentary",
            )
            return True
        mappings: dict[str, tuple[str, str, str, str]] = {
            "item/reasoning/summaryTextDelta": ("build_context", "Codex 正在思考", "Codex", "reasoning"),
            "item/plan/delta": ("build_context", "制定工作计划", "Codex", "plan"),
            "item/commandExecution/outputDelta": ("execute_role", "运行命令", "Codex tool", "commandExecution"),
            "item/fileChange/outputDelta": ("execute_role", "更新板书文档", "Codex tool", "fileChange"),
            "item/mcpToolCall/progress": ("execute_role", "调用工具", "Codex tool", "mcpToolCall"),
        }
        mapping = mappings.get(method)
        if mapping is None:
            return False
        stage, label, role, kind = mapping
        self._append_detail(
            item_id=item_id,
            turn_id=turn_id,
            delta=delta,
            stage=stage,
            label=label,
            role=role,
            kind=kind,
        )
        return True

    def complete_item(self, params: dict[str, Any]) -> None:
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        item_id = str(item.get("id") or "")
        turn_id = str(params.get("turnId") or "")
        item_type = str(item.get("type") or "")
        if item_type == "agentMessage":
            phase = str(item.get("phase") or self._item_phases.get(item_id) or "")
            if item_id:
                self._item_phases[item_id] = phase
            if phase != "commentary":
                return
            existing = self._events.get(item_id)
            metadata = dict(existing.metadata) if existing is not None else {"kind": "commentary"}
            text = str(item.get("text") or "")
            if text:
                metadata["detail"] = text
            self._publish(
                item_id=item_id,
                turn_id=turn_id,
                stage="execute_role",
                label="Codex 工作进展",
                status="completed",
                role="Codex",
                metadata=metadata,
            )
            return
        if item_type == "reasoning":
            summary = item.get("summary") if isinstance(item.get("summary"), list) else []
            detail = "\n\n".join(str(part) for part in summary if str(part).strip())
            existing = self._events.get(item_id)
            metadata = dict(existing.metadata) if existing is not None else {"kind": "reasoning"}
            if detail:
                metadata["detail"] = detail
            self._publish(
                item_id=item_id,
                turn_id=turn_id,
                stage="build_context",
                label="Codex 已完成思考",
                status="completed",
                role="Codex",
                metadata=metadata,
            )
            return
        descriptors: dict[str, tuple[str, str, str]] = {
            "plan": ("build_context", "工作计划已更新", "Codex"),
            "commandExecution": ("execute_role", "命令执行完成", "Codex tool"),
            "fileChange": ("execute_role", "板书文档已更新", "Codex tool"),
            "mcpToolCall": ("execute_role", "工具调用完成", "Codex tool"),
            "dynamicToolCall": ("execute_role", "工具调用完成", "Codex tool"),
            "collabAgentToolCall": ("execute_role", "协同处理完成", "Codex"),
            "subAgentActivity": ("execute_role", "协同处理完成", "Codex"),
            "webSearch": ("build_context", "网络搜索完成", "Codex tool"),
            "imageView": ("build_context", "图片查看完成", "Codex tool"),
            "imageGeneration": ("execute_role", "图片生成完成", "Codex tool"),
            "contextCompaction": ("build_context", "上下文整理完成", "Codex"),
        }
        descriptor = descriptors.get(item_type)
        if descriptor is None:
            return
        stage, label, role = descriptor
        existing = self._events.get(item_id)
        metadata = dict(existing.metadata) if existing is not None else {"kind": item_type}
        if item_type == "plan":
            metadata["detail"] = str(item.get("text") or metadata.get("detail") or "")
        elif item_type == "commandExecution":
            metadata.update(
                {
                    "command": str(item.get("command") or metadata.get("command") or ""),
                    "cwd": str(item.get("cwd") or metadata.get("cwd") or ""),
                    "detail": str(item.get("aggregatedOutput") or metadata.get("detail") or ""),
                    "exit_code": item.get("exitCode"),
                    "duration_ms": item.get("durationMs"),
                }
            )
        elif item_type == "fileChange":
            metadata["changes"] = item.get("changes") if isinstance(item.get("changes"), list) else []
        elif item_type in {"mcpToolCall", "dynamicToolCall"}:
            metadata.update(
                {
                    "tool": str(item.get("tool") or metadata.get("tool") or ""),
                    "server": str(item.get("server") or item.get("namespace") or metadata.get("server") or ""),
                    "arguments": item.get("arguments", metadata.get("arguments")),
                    "result": item.get("result") or item.get("contentItems"),
                    "error": item.get("error"),
                    "duration_ms": item.get("durationMs"),
                }
            )
        elif item_type == "webSearch":
            metadata.update({"query": str(item.get("query") or metadata.get("query") or ""), "action": item.get("action")})
            metadata["detail"] = str(metadata.get("query") or "")
        self._publish(
            item_id=item_id,
            turn_id=turn_id,
            stage=stage,
            label=label,
            status=_activity_status(item.get("status")),
            role=role,
            metadata=metadata,
        )
