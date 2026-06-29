import json

import pytest

from app.models import ChatRequest, ConversationTurn
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, OpenAICourseAI, openai_course_ai


TEST_USER_ID = "user_basic_chat"


def _seed_workspace(store: SqliteCourseStore, *, content_text: str = "这段右侧文档不应该被聊天修改。"):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("基础聊天页")
    lesson.board_document.content_text = content_text
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_basic_chat_prompt_gets_board_sensor_without_board_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return ChatbotReply(chatbot_message="可以，我们就正常聊天。")

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    result = ai.generate_basic_chat_reply(
        conversation_summary="user: 你好",
        board_document_state={
            "status": "empty",
            "is_empty": True,
            "chatbot_context": "当前右侧板书/文档框为空。",
            "content_visibility": "status_only",
        },
        learning_purpose_detection={
            "has_learning_purpose": False,
            "needs_guidance": False,
            "need_kind": "none",
            "guidance_direction": "none",
            "known_purpose": "",
            "specific_knowledge_point": "",
            "specific_practice_content": "",
            "current_level": "",
            "missing_piece": "",
            "reason": "用户只是寒暄。",
        },
        minimal_learning_requirement={
            "has_learning_purpose": False,
            "need_kind": "none",
            "known_purpose": "",
            "specific_knowledge_point": "",
            "specific_practice_content": "",
            "current_level": "",
            "missing_items": [],
            "next_question_focus": "none",
            "core_factors_recorded": False,
            "board_work_allowed": False,
        },
        learning_requirement_refinement={
            "learning_mode": "unknown",
            "raw_user_input": "",
            "domain": "",
            "new_learning": {
                "learning_purpose": "",
                "learning_context": "",
                "motivation_trigger": "",
                "desired_output": "",
                "current_background": "",
                "target_knowledge_point": "",
                "candidate_entry_points": [],
                "selected_entry_point": "",
                "reason_for_recommendation": "",
            },
            "practice_old_skill": {
                "practice_content": "",
                "practice_scenario": "",
                "current_level": "",
                "weak_points": [],
                "practice_goal": "",
                "diagnostic_results": [],
                "diagnostic_questions": [],
            },
            "teaching_preferences": {
                "difficulty_level": "",
                "teaching_style": "",
                "session_time": "",
            },
            "status": "collecting_mode",
            "next_question": "",
            "ready_to_teach": False,
            "teaching_contract": "",
        },
        user_message="帮我解释一下这个概念",
    )

    assert result == ChatbotReply(chatbot_message="可以，我们就正常聊天。")
    assert captured["role"] == "chatbot"
    assert "像 ChatGPT" in captured["system_prompt"]
    assert "directive" not in captured["system_prompt"]
    payload = json.loads(captured["user_prompt"])
    assert payload["recent_conversation"] == "user: 你好"
    assert payload["board_document_sensor"] == {
        "status": "empty",
        "is_empty": True,
        "chatbot_context": "当前右侧板书/文档框为空。",
        "content_visibility": "status_only",
    }
    assert payload["learning_purpose_detection"] == {
        "has_learning_purpose": False,
        "needs_guidance": False,
        "need_kind": "none",
        "guidance_direction": "none",
        "known_purpose": "",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_piece": "",
        "reason": "用户只是寒暄。",
    }
    assert payload["minimal_learning_requirement"] == {
        "has_learning_purpose": False,
        "need_kind": "none",
        "known_purpose": "",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": [],
        "next_question_focus": "none",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }
    assert payload["learning_requirement_refinement"]["learning_mode"] == "unknown"
    assert payload["learning_requirement_refinement"]["ready_to_teach"] is False
    assert payload["user_message"] == "帮我解释一下这个概念"
    assert "lesson_title" not in payload
    assert "resource_summary" not in payload
    assert "board_summary" not in payload


def test_process_chat_on_lesson_records_basic_chat_without_document_change(
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
            has_learning_purpose=False,
            reason="用户没有表达学习目的。",
        ),
    )

    def _fake_basic_reply(**kwargs):
        captured.update(kwargs)
        return ChatbotReply(chatbot_message="这是一个普通聊天回答。")

    monkeypatch.setattr(openai_course_ai, "generate_basic_chat_reply", _fake_basic_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="你现在能正常问答吗？",
            conversation=[ConversationTurn(role="user", content="你好")],
        ),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "这是一个普通聊天回答。"
    assert response.board_decision.action == "no_change"
    assert response.active_requirement_sheet is None
    assert response.active_board_task_sheet is None
    assert captured == {
        "conversation_summary": "user: 你好",
        "board_document_state": {
            "status": "non_empty",
            "is_empty": False,
            "chatbot_context": "当前右侧板书/文档框不是空的，里面已有内容。",
            "content_visibility": "status_only",
        },
        "learning_purpose_detection": {
            "has_learning_purpose": False,
            "needs_guidance": False,
            "need_kind": "none",
            "guidance_direction": "none",
            "known_purpose": "",
            "specific_knowledge_point": "",
            "specific_practice_content": "",
            "current_level": "",
            "missing_piece": "",
            "reason": "用户没有表达学习目的。",
        },
        "minimal_learning_requirement": {
            "has_learning_purpose": False,
            "need_kind": "none",
            "known_purpose": "",
            "specific_knowledge_point": "",
            "specific_practice_content": "",
            "current_level": "",
            "missing_items": [],
            "next_question_focus": "none",
            "core_factors_recorded": False,
            "board_work_allowed": False,
        },
        "learning_requirement_refinement": {
            "learning_mode": "unknown",
            "raw_user_input": "你现在能正常问答吗？",
            "domain": "",
            "new_learning": {
                "learning_purpose": "",
                "learning_context": "",
                "motivation_trigger": "",
                "desired_output": "",
                "current_background": "",
                "target_knowledge_point": "",
                "candidate_entry_points": [],
                "selected_entry_point": "",
                "reason_for_recommendation": "",
            },
            "practice_old_skill": {
                "practice_content": "",
                "practice_scenario": "",
                "current_level": "",
                "weak_points": [],
                "practice_goal": "",
                "diagnostic_results": [],
                "diagnostic_questions": [],
            },
            "teaching_preferences": {
                "difficulty_level": "",
                "teaching_style": "",
                "session_time": "",
            },
            "status": "collecting_mode",
            "next_question": "",
            "ready_to_teach": False,
            "teaching_contract": "",
        },
        "user_message": "你现在能正常问答吗？",
    }
    saved = store.load_for_user(TEST_USER_ID)
    saved_lesson = saved.packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "这段右侧文档不应该被聊天修改。"
    assert saved_lesson.learning_requirements is None
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.label == "Basic chat"
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["board_document_sensor"] == {
        "status": "non_empty",
        "is_empty": False,
        "chatbot_context": "当前右侧板书/文档框不是空的，里面已有内容。",
        "content_visibility": "status_only",
    }
    assert commit.metadata["learning_purpose_detection"] == {
        "has_learning_purpose": False,
        "needs_guidance": False,
        "need_kind": "none",
        "guidance_direction": "none",
        "known_purpose": "",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_piece": "",
        "reason": "用户没有表达学习目的。",
    }
    assert commit.metadata["minimal_learning_requirement"] == {
        "has_learning_purpose": False,
        "need_kind": "none",
        "known_purpose": "",
        "specific_knowledge_point": "",
        "specific_practice_content": "",
        "current_level": "",
        "missing_items": [],
        "next_question_focus": "none",
        "core_factors_recorded": False,
        "board_work_allowed": False,
    }
    assert commit.metadata["learning_requirement_refinement"]["ready_to_teach"] is False
    assert commit.metadata["basic_chat_only"] is True
    assert commit.metadata["document_changed"] is False


def test_process_chat_reuses_previous_learning_requirement_refinement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store)
    detections = iter(
        [
            LearningPurposeDetection(
                has_learning_purpose=True,
                needs_guidance=True,
                need_kind="new_knowledge",
                known_purpose="想学习一个笼统领域",
            ),
            LearningPurposeDetection(
                has_learning_purpose=True,
                needs_guidance=True,
                need_kind="new_knowledge",
                known_purpose="为了以后学机器学习",
            ),
        ]
    )
    captured_refinements: list[dict[str, object]] = []

    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_purpose_detection",
        lambda **kwargs: next(detections),
    )

    def _fake_basic_reply(**kwargs):
        captured_refinements.append(kwargs["learning_requirement_refinement"])
        return ChatbotReply(chatbot_message="继续收敛。")

    monkeypatch.setattr(openai_course_ai, "generate_basic_chat_reply", _fake_basic_reply)

    process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学高等数学"),
        user_id=TEST_USER_ID,
    )
    process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="为了以后学机器学习"),
        user_id=TEST_USER_ID,
    )

    assert captured_refinements[0]["domain"] == "高等数学"
    assert captured_refinements[1]["domain"] == "高等数学"
    assert captured_refinements[1]["new_learning"]["learning_purpose"] == "为了以后学机器学习"
    assert captured_refinements[1]["ready_to_teach"] is False

    saved = store.load_for_user(TEST_USER_ID)
    saved_lesson = saved.packages[0].lessons[0]
    latest_metadata = saved_lesson.history_graph.commits[-1].metadata
    assert latest_metadata["learning_requirement_refinement"]["domain"] == "高等数学"
    assert latest_metadata["document_changed"] is False


def test_basic_chat_detects_latest_board_document_state_each_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store, content_text="")
    captured_states: list[dict[str, object]] = []

    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_purpose_detection",
        lambda **kwargs: LearningPurposeDetection(has_learning_purpose=False),
    )

    def _fake_basic_reply(**kwargs):
        captured_states.append(kwargs["board_document_state"])
        return ChatbotReply(chatbot_message="收到当前板书状态。")

    monkeypatch.setattr(openai_course_ai, "generate_basic_chat_reply", _fake_basic_reply)

    process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="现在板书为空吗？"),
        user_id=TEST_USER_ID,
    )
    workspace = store.load_for_user(TEST_USER_ID)
    workspace.packages[0].lessons[0].board_document.content_text = "第二轮前写入的板书内容"
    store.save_for_user(TEST_USER_ID, workspace)

    process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="现在板书有内容了吗？"),
        user_id=TEST_USER_ID,
    )

    assert [state["status"] for state in captured_states] == ["empty", "non_empty"]
