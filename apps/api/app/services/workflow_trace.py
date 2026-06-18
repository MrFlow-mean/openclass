from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class NodeId(StrEnum):
    INGRESS_HTTP = "INGRESS_HTTP"
    INGRESS_SSE = "INGRESS_SSE"
    CHAT_SERVICE_FACADE = "CHAT_SERVICE_FACADE"
    CONTEXT_LOAD = "CONTEXT_LOAD"
    TURN_CONTEXT_BUILD = "TURN_CONTEXT_BUILD"
    BOARD_ACTION_DECIDE = "BOARD_ACTION_DECIDE"
    CHAT_TURN_GATE = "CHAT_TURN_GATE"
    RESOURCE_PREFLIGHT = "RESOURCE_PREFLIGHT"
    ACTIVE_INTERACTION_CHECK = "ACTIVE_INTERACTION_CHECK"
    LEGACY_COMPATIBILITY_DISPATCH = "LEGACY_COMPATIBILITY_DISPATCH"
    RESPONSE_ASSEMBLE = "RESPONSE_ASSEMBLE"
    STREAM_EVENT_EMIT = "STREAM_EVENT_EMIT"
    INTERACTION_SEQUENCE_CHECK = "INTERACTION_SEQUENCE_CHECK"
    INTERACTION_DECIDE = "INTERACTION_DECIDE"
    INTERACTION_CONTINUE = "INTERACTION_CONTINUE"
    INTERACTION_RULE_VIOLATION = "INTERACTION_RULE_VIOLATION"
    INTERACTION_EXIT = "INTERACTION_EXIT"
    INTERACTION_NEW_TASK = "INTERACTION_NEW_TASK"
    INTERACTION_START_RESOLVE = "INTERACTION_START_RESOLVE"
    INTERACTION_START_PERSIST = "INTERACTION_START_PERSIST"
    INTERACTION_TERMINAL = "INTERACTION_TERMINAL"
    INITIAL_MODE_DECIDE = "INITIAL_MODE_DECIDE"
    INITIAL_UNKNOWN_GUIDANCE = "INITIAL_UNKNOWN_GUIDANCE"
    INITIAL_NARROW_TOPIC = "INITIAL_NARROW_TOPIC"
    INITIAL_REQUIREMENT_COLLECT = "INITIAL_REQUIREMENT_COLLECT"
    INITIAL_REQUIREMENT_READY = "INITIAL_REQUIREMENT_READY"
    INITIAL_REQUIREMENT_FREEZE = "INITIAL_REQUIREMENT_FREEZE"
    INITIAL_BOARD_GENERATE = "INITIAL_BOARD_GENERATE"
    INITIAL_GENERATION_FAILED = "INITIAL_GENERATION_FAILED"
    INITIAL_BOARD_COMMIT = "INITIAL_BOARD_COMMIT"
    RESOURCE_REFERENCE_PROMPT = "RESOURCE_REFERENCE_PROMPT"
    RESOURCE_CONFIRMED_GENERATE = "RESOURCE_CONFIRMED_GENERATE"
    BOARD_TASK_COLLECT = "BOARD_TASK_COLLECT"
    BOARD_TASK_CLARIFY_FIELDS = "BOARD_TASK_CLARIFY_FIELDS"
    BOARD_TASK_READY_PERSIST = "BOARD_TASK_READY_PERSIST"
    BOARD_TARGET_RESOLVE = "BOARD_TARGET_RESOLVE"
    BOARD_ROUTE_DECIDE = "BOARD_ROUTE_DECIDE"
    BOARD_ROUTE_CLARIFY_LOCATION = "BOARD_ROUTE_CLARIFY_LOCATION"
    BOARD_AWAIT_WRITE_CONFIRMATION = "BOARD_AWAIT_WRITE_CONFIRMATION"
    BOARD_WRITE_CONFIRMATION_HANDLE = "BOARD_WRITE_CONFIRMATION_HANDLE"
    BOARD_WRITE_EXECUTE = "BOARD_WRITE_EXECUTE"
    BOARD_EDIT_EXECUTE = "BOARD_EDIT_EXECUTE"
    BOARD_EXPLAIN_DIRECTIVE = "BOARD_EXPLAIN_DIRECTIVE"
    BOARD_EXPLAIN_COMMIT = "BOARD_EXPLAIN_COMMIT"
    BOARD_CHAT_ROUTE = "BOARD_CHAT_ROUTE"
    BOARD_SEQUENCE_PLAN = "BOARD_SEQUENCE_PLAN"
    BOARD_SEQUENCE_START = "BOARD_SEQUENCE_START"
    BOARD_TASK_FAILURE = "BOARD_TASK_FAILURE"
    ORDINARY_CHAT_GENERATE = "ORDINARY_CHAT_GENERATE"
    REQUIREMENT_CHAT_UPDATE = "REQUIREMENT_CHAT_UPDATE"
    LEGACY_TEACHING_ACTION = "LEGACY_TEACHING_ACTION"
    LEGACY_DIRECT_EDIT_ACTION = "LEGACY_DIRECT_EDIT_ACTION"
    LEGACY_DOCUMENT_ACTION = "LEGACY_DOCUMENT_ACTION"
    LEGACY_FALLBACK_EXPLAIN = "LEGACY_FALLBACK_EXPLAIN"
    PERSIST_CHAT_COMMIT = "PERSIST_CHAT_COMMIT"
    PERSIST_BOARD_COMMIT = "PERSIST_BOARD_COMMIT"
    TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
    TERMINAL_CLARIFY = "TERMINAL_CLARIFY"
    TERMINAL_ERROR = "TERMINAL_ERROR"


@dataclass(frozen=True)
class WorkflowStepTrace:
    node_id: NodeId
    entered_at: str
    decision: str | None = None
    reason: str | None = None
    run_id: str | None = None
    version_id: str | None = None
    commit_id: str | None = None


@dataclass
class WorkflowTraceCollector:
    _steps: list[WorkflowStepTrace] = field(default_factory=list)

    @property
    def steps(self) -> tuple[WorkflowStepTrace, ...]:
        return tuple(self._steps)

    def record(self, step: WorkflowStepTrace) -> None:
        self._steps.append(step)

    def snapshot(self) -> tuple[WorkflowStepTrace, ...]:
        return self.steps


_workflow_trace_collector: ContextVar[WorkflowTraceCollector | None] = ContextVar(
    "openclass_workflow_trace_collector",
    default=None,
)


@contextmanager
def bind_workflow_trace_collector(
    collector: WorkflowTraceCollector | None = None,
) -> Iterator[WorkflowTraceCollector]:
    bound_collector = collector or WorkflowTraceCollector()
    token = _workflow_trace_collector.set(bound_collector)
    try:
        yield bound_collector
    finally:
        _workflow_trace_collector.reset(token)


def current_workflow_trace_collector() -> WorkflowTraceCollector | None:
    return _workflow_trace_collector.get()


def record_workflow_step(
    node_id: NodeId,
    *,
    decision: str | None = None,
    reason: str | None = None,
    run_id: str | None = None,
    version_id: str | None = None,
    commit_id: str | None = None,
) -> WorkflowStepTrace | None:
    collector = _workflow_trace_collector.get()
    if collector is None:
        return None

    step = WorkflowStepTrace(
        node_id=node_id,
        entered_at=_utc_now_iso(),
        decision=decision,
        reason=reason,
        run_id=run_id,
        version_id=version_id,
        commit_id=commit_id,
    )
    collector.record(step)
    return step


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
