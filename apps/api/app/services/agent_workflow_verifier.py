from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import AgentTurnDecision, ChatResponse, Lesson
from app.services.history import current_head_commit


@dataclass(frozen=True)
class AgentVerificationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def verify_agent_response(
    *,
    lesson: Lesson,
    response: ChatResponse,
    decision: AgentTurnDecision,
) -> AgentVerificationResult:
    try:
        commit = current_head_commit(lesson)
        metadata = commit.metadata if isinstance(commit.metadata, dict) else {}
    except Exception:
        metadata = {}

    issues: list[str] = []
    warnings: list[str] = []
    kind = str(metadata.get("kind") or "")
    board_task_route = str(metadata.get("board_task_route") or "")

    if kind == "board_document_generation":
        _require(metadata, "requirement_run_id", issues)
        _require(metadata, "frozen_requirement_version_id", issues)
        if metadata.get("document_changed") is not True:
            issues.append("board_generation_without_document_change")

    if kind == "board_section_teaching":
        _require(metadata, "board_explanation_directive", issues)
        _require(metadata, "teaching_progress", issues)
        if response.teaching_progress is None:
            issues.append("teaching_response_without_progress")

    if board_task_route == "explain":
        _require(metadata, "board_explanation_directive", issues)
        _require(metadata, "resolved_focus", issues)
        _require(metadata, "board_task_run_id", issues)
        _require(metadata, "board_task_version_id", issues)

    if board_task_route in {"write", "edit"}:
        _require(metadata, "board_task_run_id", issues)
        _require(metadata, "board_task_version_id", issues)
        if not metadata.get("resolved_focus") and metadata.get("target_scope") != "document":
            issues.append("board_write_or_edit_without_focus")
        if metadata.get("document_changed") is not True:
            issues.append("board_write_or_edit_without_document_change")

    if board_task_route == "chat":
        _require(metadata, "resolved_focus", issues)
        _require(metadata, "board_task_run_id", issues)
        _require(metadata, "board_task_version_id", issues)
        _require(metadata, "active_interaction_session_after", issues)
        if metadata.get("document_changed") is True:
            issues.append("board_chat_route_changed_document")

    if kind == "interaction_session_turn":
        _require(metadata, "interaction_decision", issues)
        if metadata.get("document_changed") is True:
            issues.append("interaction_session_turn_changed_document")

    if decision.route == "blank_board_generate" and response.board_document_operation_status == "succeeded":
        if not response.requirement_run_id or not response.requirement_version_id:
            issues.append("generation_response_without_requirement_stamp")

    if decision.route in {"post_generation_teaching_start", "board_teaching_continue"}:
        if response.teaching_progress is None:
            issues.append("teaching_route_without_progress")

    if decision.route == "board_task_refine_or_execute" and response.board_document_operation_status == "succeeded":
        if not response.board_task_run_id or not response.board_task_version_id:
            warnings.append("board_task_mutation_response_without_task_stamp")

    return AgentVerificationResult(
        ok=not issues,
        issues=issues,
        warnings=warnings,
        metadata={
            "commit_kind": kind,
            "board_task_route": board_task_route or None,
            "issue_count": len(issues),
            "warning_count": len(warnings),
        },
    )


def _require(metadata: dict[str, Any], key: str, issues: list[str]) -> None:
    if metadata.get(key) in (None, "", [], {}):
        issues.append(f"missing_{key}")
