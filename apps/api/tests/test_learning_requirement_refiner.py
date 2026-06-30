import json

import pytest

from app.models import ChatRequest, ConversationTurn
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BlankBoardRequirementRefinement, openai_course_ai


def _seed_empty_workspace(store: SqliteCourseStore, user_id: str, title: str = "空白学习页"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson(title)
    lesson.learning_requirements = None
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)
    return lesson


def test_empty_board_ordinary_chat_does_not_create_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_ordinary"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="可以，我们就正常聊这个。",
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("empty board ordinary chat should be decided by the refiner"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="今天有点累，随便聊聊。",
            conversation=[ConversationTurn(role="user", content="你好")],
        ),
        user_id=user_id,
    )

    assert response.chatbot_message == "可以，我们就正常聊这个。"
    assert response.active_requirement_sheet is None
    assert response.requirement_run_id is None
    assert store.list_learning_requirement_versions(user_id, lesson.id) == []
    saved = store.load_for_user(user_id)
    saved_lesson = saved.packages[0].lessons[0]
    assert saved_lesson.learning_requirements is None
    assert saved_lesson.board_document.content_text == ""
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["refinement_route"] == "ordinary_chat"
    assert commit.metadata["board_document_state"]["status"] == "empty"


def test_empty_board_broad_learning_need_collects_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_broad"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="这个范围还比较大，我先帮你画一张入口地图，再选一个起点。",
            progress=45,
            summary="用户想从一个较宽的领域入门，需要缩小到具体知识点。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="某个领域入门",
            board_scope=["领域是什么", "常见组成部分", "推荐入门入口"],
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="你更想先理解整体组成，还是先挑一个最基础的概念开始？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个领域，但不知道从哪开始。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "knowledge_board"
    assert response.active_requirement_sheet.granularity == "broad_topic"
    assert response.learning_clarification.ready_for_board is False
    assert response.requirement_phase == "collecting"
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    events = store.list_learning_requirement_events(user_id, lesson.id)
    assert len(versions) == 1
    assert versions[0]["status"] == "collecting"
    assert events[0]["event_type"] == "created"
    sheet_json = json.loads(versions[0]["sheet_json"])
    assert sheet_json["work_mode"] == "knowledge_board"
    assert sheet_json["granularity"] == "broad_topic"

    second_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="还是刚才那个方向。"),
        user_id=user_id,
    )
    assert second_response.requirement_run_id == response.requirement_run_id
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 1

    def _fake_ordinary_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="可以，先聊这个也没问题。",
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_ordinary_refinement)
    ordinary_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="先不说学习，聊点别的。"),
        user_id=user_id,
    )
    assert ordinary_response.active_requirement_sheet is not None
    assert ordinary_response.requirement_run_id == response.requirement_run_id
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 1


def test_empty_board_specific_knowledge_point_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_knowledge_ready"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="这个目标已经足够聚焦，下一步可以为它准备板书。",
            progress=100,
            summary="用户想学习一个明确知识点。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="一个明确知识点",
            ready_for_board=True,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个明确知识点。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.learning_goal == "一个明确知识点"
    assert response.learning_clarification.ready_for_board is True
    assert response.learning_clarification.missing_items == []
    assert response.requirement_phase == "ready"
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    assert versions[0]["status"] == "ready"
    assert versions[0]["change_kind"] == "completed"


def test_empty_board_practice_need_missing_level_stays_collecting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_practice_missing"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="可以，我们先把练习目标定下来；还需要知道你现在大概到什么水平。",
            progress=65,
            summary="用户想练习一项内容，但当前水平缺失。",
            work_mode="practice_artifact",
            granularity="practice_artifact",
            learning_goal="一项旧知识或技能",
            target_scenario="一个实际使用场景",
            missing_items=["当前水平"],
            next_question="你现在大概能独立完成到什么程度？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想练这个技能，之后要用在某个场景里。"),
        user_id=user_id,
    )

    assert response.learning_clarification.ready_for_board is False
    assert "当前水平" in response.learning_clarification.missing_items
    assert response.requirement_phase == "collecting"
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "practice_artifact"


def test_empty_board_practice_need_with_three_core_factors_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_practice_ready"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="这三个核心因素已经齐了，下一步可以准备练习型板书。",
            progress=100,
            summary="用户想练习一项旧知识或技能，并给出了水平和场景。",
            work_mode="practice_artifact",
            granularity="practice_artifact",
            learning_goal="一项旧知识或技能",
            current_level="已经有基础但不稳定",
            target_scenario="无明确应用场景",
            ready_for_board=True,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想练一个技能，我有基础，但先不限定具体场景。"),
        user_id=user_id,
    )

    assert response.learning_clarification.ready_for_board is True
    assert response.requirement_phase == "ready"
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.learning_goal == "一项旧知识或技能"
    assert response.active_requirement_sheet.level == "已经有基础但不稳定"
    fact_labels = {fact.label for fact in response.learning_clarification.key_facts}
    assert {"用户想学的内容", "当前水平", "面向场景"} <= fact_labels
