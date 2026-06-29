import pytest

from app.models import ChatRequest
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai


TEST_USER_ID = "user_chat_minimal_requirement"


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("最小需求清单页")
    lesson.board_document.content_text = ""
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_chat_records_minimal_requirement_for_skill_practice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_purpose_detection",
        lambda **kwargs: LearningPurposeDetection(
            has_learning_purpose=True,
            needs_guidance=True,
            need_kind="skill_practice",
            guidance_direction="skill_practice",
            known_purpose="想基于已有基础练习一项技能",
            current_level="有一点基础",
            missing_piece="还不清楚具体练习内容。",
            reason="用户表达了练习意图，但目标仍需收束。",
        ),
    )

    def _fake_basic_reply(**kwargs):
        captured.update(kwargs)
        return ChatbotReply(chatbot_message="可以，我们先明确你具体想练哪一项。")

    monkeypatch.setattr(openai_course_ai, "generate_basic_chat_reply", _fake_basic_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我有一点基础，想练一下，但不知道怎么练"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "可以，我们先明确你具体想练哪一项。"
    assert captured["minimal_learning_requirement"] == {
        "has_learning_purpose": True,
        "need_kind": "skill_practice",
        "known_purpose": "想基于已有基础练习一项技能",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "有一点基础",
        "missing_items": ["specific_practice_content"],
        "next_question_focus": "specific_practice_content",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }
    saved = store.load_for_user(TEST_USER_ID)
    commit = saved.packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["minimal_learning_requirement"] == captured["minimal_learning_requirement"]
    assert commit.metadata["document_changed"] is False
