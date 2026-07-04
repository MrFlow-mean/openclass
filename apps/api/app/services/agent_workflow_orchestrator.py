from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import (
    AgentActivityEvent,
    AgentActivityStage,
    AgentActivityStatus,
    AgentTurnDecision,
    ChatResponse,
    new_id,
)
from app.services.agent_workflow_verifier import AgentVerificationResult


@dataclass
class AgentWorkflowOrchestrator:
    decision: AgentTurnDecision
    turn_id: str = field(default_factory=lambda: new_id("agentturn"))
    events: list[AgentActivityEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._decision_metadata: dict[str, Any] = self.decision.model_dump(mode="json")
        self._activity_metadata: list[dict[str, Any]] = []
        self.commit_metadata: dict[str, Any] = {
            "agent_turn_id": self.turn_id,
            "agent_turn_decision": self._decision_metadata,
            "agent_activity": self._activity_metadata,
        }
        self.add(
            stage="turn_decision",
            label="判断任务类型",
            role="AgentTurnDecision",
            metadata={"route": self.decision.route, "next_step": self.decision.next_step},
        )

    def add(
        self,
        *,
        stage: AgentActivityStage,
        label: str,
        role: str,
        status: AgentActivityStatus = "completed",
        metadata: dict[str, Any] | None = None,
    ) -> AgentActivityEvent:
        event = AgentActivityEvent(
            turn_id=self.turn_id,
            stage=stage,
            label=label,
            status=status,
            role=role,
            metadata=metadata or {},
        )
        self.events.append(event)
        self._activity_metadata.append(event.model_dump(mode="json"))
        return event

    def update_decision(self, decision: AgentTurnDecision) -> None:
        self.decision = decision
        self._decision_metadata.clear()
        self._decision_metadata.update(decision.model_dump(mode="json"))
        for index, event in enumerate(self.events):
            if event.stage != "turn_decision":
                continue
            event.metadata = {"route": decision.route, "next_step": decision.next_step}
            self._activity_metadata[index] = event.model_dump(mode="json")
            break

    def record_context_ready(self, *, label: str, role: str, metadata: dict[str, Any] | None = None) -> None:
        self.add(stage="build_context", label=label, role=role, metadata=metadata)

    def record_target_resolution(self, response: ChatResponse) -> None:
        if response.resolved_focus is not None:
            self.add(
                stage="resolve_target",
                label="定位板书目标位置",
                role="FocusResolver",
                metadata={"status": "resolved", "focus": response.resolved_focus.model_dump(mode="json")},
            )
            return
        if response.focus_candidates:
            self.add(
                stage="resolve_target",
                label="定位到多个候选位置",
                role="FocusResolver",
                status="blocked",
                metadata={"candidate_count": len(response.focus_candidates)},
            )

    def record_execution(self, *, label: str, role: str, response: ChatResponse) -> None:
        status: AgentActivityStatus = "completed"
        if response.board_document_operation_status == "failed":
            status = "failed"
        elif response.needs_clarification or response.clarification_questions or response.board_task_questions:
            status = "blocked"
        self.add(
            stage="execute_role",
            label=label,
            role=role,
            status=status,
            metadata={
                "board_document_operation_status": response.board_document_operation_status,
                "board_decision": response.board_decision.model_dump(mode="json"),
            },
        )

    def record_verification(self, verification: AgentVerificationResult) -> None:
        self.add(
            stage="verify",
            label="验证本轮结果",
            role="AgentVerifier",
            status="completed" if verification.ok else "failed",
            metadata={
                **verification.metadata,
                "issues": verification.issues,
                "warnings": verification.warnings,
            },
        )

    def record_persisted(self, response: ChatResponse) -> None:
        self.add(
            stage="persist_history",
            label="写入历史记录",
            role="History",
            metadata={
                "requirement_run_id": response.requirement_run_id,
                "board_task_run_id": response.board_task_run_id,
                "document_changed": response.board_document_operation_status == "succeeded",
            },
        )

    def finalize_response(self, response: ChatResponse) -> ChatResponse:
        self.add(
            stage="final",
            label="返回用户回复",
            role="Chatbot",
            metadata={"has_chatbot_message": bool(response.chatbot_message.strip())},
        )
        response.agent_turn_decision = self.decision
        response.agent_activity = list(self.events)
        return response
