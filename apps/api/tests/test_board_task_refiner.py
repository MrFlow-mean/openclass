import json

import pytest

from app.models import BoardTaskRequirementSheet, ChatRequest, ConversationTurn, SelectionRef
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRequirementRefinement, openai_course_ai
from app.services.rich_document import build_document


def test_existing_board_task_refinement_prompt_uses_three_factor_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_parse_response(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return None

    monkeypatch.setattr(openai_course_ai, "_parse_response", _fake_parse_response)

    result = openai_course_ai.generate_board_task_requirement_refinement(
        lesson_title="已有板书",
        existing_task=None,
        board_document_state={"status": "non_empty"},
        board_summary="## 第一节\n已有内容",
        conversation_summary="",
        user_message="我想处理一下这里",
        include_stream_result=True,
    )

    assert result is not None
    assert captured["role"] == "pm"
    assert captured["schema"] is BoardTaskRequirementRefinement
    system_prompt = str(captured["system_prompt"])
    assert "清单只收敛三个核心因素" in system_prompt
    assert "location：位置" in system_prompt
    assert "本阶段只允许 explain 或 write" in system_prompt
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["response_contract"]["board_task_sheet"]["location_kind"] == "target_range、insertion_anchor 或 unspecified。"
    assert payload["response_contract"]["board_task_sheet"]["requested_action"] == "只允许 explain、write 或 null。"


def _seed_existing_board_workspace(store: SqliteCourseStore, user_id: str):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("已有板书页")
    lesson.board_document = build_document(
        title="已有板书页",
        content_text="# 已有板书\n\n## 第一节\n\n这里已经有一段学习内容。",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )
    lesson.learning_requirements = None
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(user_id, workspace)
    return lesson


def test_existing_board_ordinary_chat_does_not_create_board_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_ordinary"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        assert kwargs["board_document_state"]["status"] == "non_empty"
        return BoardTaskRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="可以，我们先随便聊聊。",
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("non-empty board should be classified by board task refinement first"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="今天先不学习，聊两句。",
            conversation=[ConversationTurn(role="user", content="你好")],
        ),
        user_id=user_id,
    )

    assert response.chatbot_message == "可以，我们先随便聊聊。"
    assert response.active_board_task_sheet is None
    assert response.board_task_run_id is None
    assert store.list_board_task_versions(user_id, lesson.id) == []
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    assert saved_lesson.board_task_requirements is None
    assert saved_lesson.board_document.content_text == lesson.board_document.content_text
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["board_task_refinement_route"] == "ordinary_chat"
    assert commit.metadata["basic_chat_only"] is True
    assert commit.metadata["document_changed"] is False


def test_existing_board_action_need_records_board_task_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_task"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        assert "第一节" in kwargs["board_summary"]
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="我先把需求记录为：讲第一节，并按你要的角度讲清楚。",
            board_task_sheet=BoardTaskRequirementSheet(
                location_kind="target_range",
                target_hint="第一节",
                requested_action="explain",
                question_or_topic="按初学者角度讲清楚",
                progress=100,
            ),
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="讲第一节，按初学者角度讲清楚。"),
        user_id=user_id,
    )

    assert response.board_decision.action == "no_change"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.location_kind == "target_range"
    assert response.active_board_task_sheet.requested_action == "explain"
    assert response.active_board_task_sheet.progress == 100
    assert response.board_task_run_id
    assert response.board_task_version_id
    assert response.board_task_phase == "ready"
    versions = store.list_board_task_versions(user_id, lesson.id)
    assert len(versions) == 1
    stored_sheet = json.loads(versions[0]["sheet_json"])
    assert stored_sheet["board_workflow"] == "act_on_existing_board"
    assert stored_sheet["location_kind"] == "target_range"
    assert stored_sheet["requested_action"] == "explain"
    assert versions[0]["status"] == "ready"
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    assert saved_lesson.board_task_requirements is not None
    assert saved_lesson.learning_requirements is None
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "board_task_requirement_refinement"
    assert commit.metadata["board_task_sheet"]["location_kind"] == "target_range"
    assert commit.metadata["board_task_history_changed"] is True
    assert commit.metadata["document_changed"] is False


def test_board_selection_quote_marks_target_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_selection_target"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        assert kwargs["selection_excerpt"] == "这里已经有一段学习内容。"
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="我已经把你选中的文字标为目标范围。",
            board_task_sheet=BoardTaskRequirementSheet(
                requested_action="explain",
                question_or_topic="讲清楚选中内容",
                progress=66,
            ),
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="讲清楚这段。",
            selection=SelectionRef(
                kind="board",
                excerpt="这里已经有一段学习内容。",
                location_kind="target_range",
                lesson_id=lesson.id,
                document_id=lesson.board_document.id,
            ),
        ),
        user_id=user_id,
    )

    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.location_kind == "target_range"
    assert response.active_board_task_sheet.target_hint == "这里已经有一段学习内容。"
    assert response.active_board_task_sheet.target_location is not None
    assert response.active_board_task_sheet.target_location.display_label == "TargetRange"
    assert response.active_board_task_sheet.progress == 100
