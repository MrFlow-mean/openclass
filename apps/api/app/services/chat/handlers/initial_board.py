from __future__ import annotations

import json

from app.models import LearningClarificationStatus, LearningRequirementSheet, Lesson
from app.services.learning_requirement_history import (
    LearningRequirementHistoryRecorder,
    RequirementHistoryStamp,
)
from app.services.rich_document import is_document_empty


def should_track_initial_requirement_run(lesson: Lesson) -> bool:
    return is_document_empty(lesson.board_document)


def freeze_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> RequirementHistoryStamp:
    return requirement_history.freeze(
        requirements=requirements,
        clarification=learning_clarification,
        forced=learning_clarification.forced_start or not learning_clarification.ready_for_board,
    )


def frozen_requirement_snapshot(
    requirement_history: LearningRequirementHistoryRecorder,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus] | None:
    snapshot = requirement_history.snapshot
    if snapshot.status != "frozen" or not snapshot.latest_sheet_json or not snapshot.latest_clarification_json:
        return None
    try:
        requirements = LearningRequirementSheet.model_validate(json.loads(snapshot.latest_sheet_json))
        clarification = LearningClarificationStatus.model_validate(json.loads(snapshot.latest_clarification_json))
    except Exception:
        return None
    return requirements, clarification


def normalize_requirement_for_board_generation(
    *,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus]:
    frozen_requirements = LearningRequirementSheet.model_validate(requirements.model_dump(mode="json"))
    frozen_clarification = LearningClarificationStatus.model_validate(
        learning_clarification.model_dump(mode="json")
    )
    frozen_requirements.current_questions = []
    frozen_requirements.risk_notes = []
    frozen_requirements.location_clarification_question = ""
    frozen_clarification.progress = 100
    frozen_clarification.missing_items = []
    frozen_clarification.can_start = True
    frozen_clarification.next_question = ""
    if not frozen_clarification.ready_for_board:
        frozen_clarification.forced_start = True
    frozen_clarification.ready_for_board = True
    return frozen_requirements, frozen_clarification


def prepare_initial_requirement_for_board_generation(
    requirement_history: LearningRequirementHistoryRecorder,
    *,
    enabled: bool,
    requirements: LearningRequirementSheet,
    learning_clarification: LearningClarificationStatus,
) -> tuple[LearningRequirementSheet, LearningClarificationStatus, RequirementHistoryStamp | None]:
    if not enabled:
        return requirements, learning_clarification, None
    existing_frozen = frozen_requirement_snapshot(requirement_history)
    if existing_frozen is not None:
        frozen_requirements, frozen_clarification = existing_frozen
        return frozen_requirements, frozen_clarification, requirement_history.current_stamp()
    frozen_requirements, frozen_clarification = normalize_requirement_for_board_generation(
        requirements=requirements,
        learning_clarification=learning_clarification,
    )
    frozen_stamp = freeze_requirement_for_board_generation(
        requirement_history,
        requirements=frozen_requirements,
        learning_clarification=frozen_clarification,
    )
    return frozen_requirements, frozen_clarification, frozen_stamp
