import pytest

from app.models import ChatRequest
from app.services import chatbot, workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_intake_orchestrator import LearningIntakeTurnOutcome
from app.services.learning_requirement_refiner import LEARNING_REQUIREMENT_REFINEMENT_FAILURE_REASON
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import openai_course_ai


def _seed_empty_lesson(store: SqliteCourseStore, user_id: str):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("空白学习页")
    lesson.learning_requirements = None
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)
    return lesson


def test_missing_refinement_outcome_returns_audited_failure_without_basic_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_missing_refinement_outcome"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_lesson(store, user_id)

    monkeypatch.setattr(
        chatbot,
        "run_learning_intake_turn",
        lambda **kwargs: LearningIntakeTurnOutcome(
            route="refinement_failed",
            initial_decision=None,
            refinement=None,
            source_discovery=None,
            chatbot_message="",
            assistant_message_source="requirement_refinement_failed",
            evidence_bundle=None,
            candidate_evidence_bundle=None,
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("missing refinement outcome must not invoke ordinary chat"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续整理我的学习需求"),
        user_id=user_id,
    )

    assert response.chatbot_message == ""
    assert response.learning_requirement_operation_status == "failed"
    assert (
        response.learning_requirement_operation_failure_reason
        == LEARNING_REQUIREMENT_REFINEMENT_FAILURE_REASON
    )
    assert response.requirement_cleared is True
    assert store.list_learning_requirement_versions(user_id, lesson.id) == []
    assert store.list_learning_requirement_events(user_id, lesson.id) == []

    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["refinement_route"] == "refinement_failed"
    assert commit.metadata["learning_requirement_operation_status"] == "failed"
    assert (
        commit.metadata["learning_requirement_operation_failure_reason"]
        == LEARNING_REQUIREMENT_REFINEMENT_FAILURE_REASON
    )
    guidance = commit.metadata["guided_requirement_discovery"]
    assert guidance["failure_code"] == "missing_requirement_refinement_outcome"
    assert guidance["requirement_update_skipped"] is True
