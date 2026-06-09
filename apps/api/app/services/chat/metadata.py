from __future__ import annotations

from app.models import (
    BoardFocusRef,
    BoardTaskRequirementSheet,
    LearningClarificationStatus,
    LearningRequirementSheet,
)
from app.services.ai_logging import current_ai_log_context
from app.services.board_task_history import BoardTaskHistoryStamp
from app.services.learning_requirement_history import RequirementHistoryStamp
from app.services.resource_resolver import ResourceResolution
from app.services.segment_resolver import FocusResolution


def task_metadata(
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
    focus: BoardFocusRef | None = None,
    focus_candidates: list[BoardFocusRef] | None = None,
    requirement_cleared: bool = False,
) -> dict[str, object]:
    return {
        "task_requirement_sheet": requirements.model_dump(mode="json"),
        "learning_clarification": learning_clarification.model_dump(mode="json"),
        "resolved_focus": focus.model_dump(mode="json") if focus else None,
        "focus_candidates": [candidate.model_dump(mode="json") for candidate in (focus_candidates or [])],
        "requirement_cleared": requirement_cleared,
        "active_requirement_sheet_after": None if requirement_cleared else requirements.model_dump(mode="json"),
    }


def requirement_history_metadata(
    stamp: RequirementHistoryStamp | None,
    *,
    run_status_after_commit: str | None = None,
) -> dict[str, object]:
    if stamp is None:
        return {
            "requirement_run_id": None,
            "frozen_requirement_version_id": None,
        }
    metadata = {
        "requirement_run_id": stamp.run_id,
        "frozen_requirement_version_id": stamp.version_id,
        "requirement_phase": stamp.phase,
        "frozen_requirement_phase": stamp.phase,
    }
    if run_status_after_commit is not None:
        metadata["requirement_run_status_after_commit"] = run_status_after_commit
    return metadata


def reference_metadata(
    *,
    resolution: ResourceResolution,
) -> dict[str, object]:
    return {
        "resource_matches": [match.model_dump(mode="json") for match in resolution.matches],
        "reference_prompt": (
            resolution.reference_prompt.model_dump(mode="json") if resolution.reference_prompt else None
        ),
        "selected_reference": (
            {
                "resource_id": resolution.selected_reference.resource_id,
                "chapter_id": resolution.selected_reference.chapter_id,
                "resource_name": resolution.selected_reference.resource_name,
                "chapter_title": resolution.selected_reference.chapter_title,
                "summary": resolution.selected_reference.summary,
            }
            if resolution.selected_reference
            else None
        ),
        "resource_resolution_status": resolution.status,
    }


def board_task_metadata(
    *,
    board_task: BoardTaskRequirementSheet | None,
    stamp: BoardTaskHistoryStamp | None,
    route: str | None = None,
    decision: dict[str, object] | None = None,
    cleared: bool = False,
) -> dict[str, object]:
    return {
        "board_task_sheet": board_task.model_dump(mode="json") if board_task else None,
        "board_task_run_id": stamp.run_id if stamp else None,
        "board_task_version_id": stamp.version_id if stamp else None,
        "board_task_phase": stamp.phase if stamp else None,
        "board_task_route": route,
        "board_task_decision": decision,
        "board_task_cleared": cleared,
        "requirement_cleared": True,
        "active_requirement_sheet_after": None,
    }


def board_search_evidence_metadata(resolution: FocusResolution | None) -> dict[str, object]:
    return {
        "board_search_evidence": resolution.evidence.model_dump(mode="json") if resolution and resolution.evidence else None,
    }


def board_document_quality_metadata(edit_outcome) -> dict[str, object]:
    return {
        "quality_repair_attempts": edit_outcome.quality_repair_attempts,
        "quality_review_status": edit_outcome.quality_review_status,
    }


def board_document_failure_metadata(edit_outcome) -> dict[str, object]:
    context = current_ai_log_context()
    metadata: dict[str, object] = {
        "assistant_message_source": edit_outcome.assistant_message_source,
        "board_edit_operation": edit_outcome.operation,
        "board_edit_summary": edit_outcome.summary,
        "board_document_operation_status": edit_outcome.operation_status,
        **board_document_quality_metadata(edit_outcome),
    }
    trace_id = context.get("trace_id")
    if trace_id:
        metadata["trace_id"] = trace_id
    return metadata
