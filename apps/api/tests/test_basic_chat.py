import json

import pytest
from pydantic import BaseModel

from app.models import ChatRequest, ConversationTurn
from app.services import openai_course_ai as openai_course_ai_module
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_purpose_detector import LearningPurposeDetection
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    AIOutputParseError,
    ChatbotReply,
    OpenAICourseAI,
    ParsedAIResponse,
    openai_course_ai,
)


TEST_USER_ID = "user_basic_chat"


class GenericStructuredReply(BaseModel):
    message: str


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


def _enable_parse_test_provider(monkeypatch: pytest.MonkeyPatch, ai: OpenAICourseAI, logged_events: list[dict]) -> None:
    monkeypatch.setattr(ai, "_model_for", lambda role: ("openai", "test-model"))
    monkeypatch.setattr(ai, "_provider_available", lambda provider: True)
    monkeypatch.setattr(ai, "_fallback_model_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai, "_should_retry_provider_fallback", lambda exc: False)
    monkeypatch.setattr(
        openai_course_ai_module.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )


def test_chatbot_parse_recovers_streamed_reply_before_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = OpenAICourseAI()
    logged_events: list[dict] = []
    calls: list[dict] = []
    _enable_parse_test_provider(monkeypatch, ai, logged_events)

    def _fake_call_parse(**kwargs):
        calls.append(kwargs)
        raise AIOutputParseError(
            "Model response did not contain a JSON object",
            output_text='{"chatbot_message":"长内容先流出来了，但 JSON 外壳还没有闭合',
        )

    monkeypatch.setattr(ai, "_call_parse", _fake_call_parse)

    result = ai._parse("chatbot", "system", "user", ChatbotReply)

    assert result == ChatbotReply(chatbot_message="长内容先流出来了，但 JSON 外壳还没有闭合")
    assert len(calls) == 1
    assert logged_events[-1]["event_type"] == "openai_text_call_recovered"
    assert logged_events[-1]["recovered_before_retry"] is True
    assert logged_events[-1]["retry_skipped"] is True
    assert logged_events[-1]["retry_reason"] == "chatbot_reply_parse"


def test_chatbot_parse_still_retries_when_streamed_reply_is_not_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    logged_events: list[dict] = []
    calls: list[dict] = []
    _enable_parse_test_provider(monkeypatch, ai, logged_events)

    def _fake_call_parse(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise AIOutputParseError(
                "Model response did not contain a JSON object",
                output_text='{"other_field":"没有可恢复的聊天正文',
            )
        return ParsedAIResponse(
            output_parsed=ChatbotReply(chatbot_message="重试后的正式回复。"),
            output_text='{"chatbot_message":"重试后的正式回复。"}',
        )

    monkeypatch.setattr(ai, "_call_parse", _fake_call_parse)

    result = ai._parse("chatbot", "system", "user", ChatbotReply)

    assert result == ChatbotReply(chatbot_message="重试后的正式回复。")
    assert len(calls) == 2
    assert any(event["event_type"] == "openai_text_call_retry" for event in logged_events)


def test_parse_does_not_recover_non_chatbot_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = OpenAICourseAI()
    logged_events: list[dict] = []
    calls: list[dict] = []
    _enable_parse_test_provider(monkeypatch, ai, logged_events)

    def _fake_call_parse(**kwargs):
        calls.append(kwargs)
        raise AIOutputParseError(
            "Model response did not contain a JSON object",
            output_text='{"chatbot_message":"这个字段不应该放松其他 schema"',
        )

    monkeypatch.setattr(ai, "_call_parse", _fake_call_parse)

    result = ai._parse("board", "system", "user", GenericStructuredReply)

    assert result is None
    assert len(calls) == 1
    assert not any(event["event_type"].endswith("_recovered") for event in logged_events)


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
                "current_level": "",
                "application_scenario": "",
                "problem_to_solve": "",
                "target_knowledge_point": "",
                "candidate_entry_points": [],
                "domain_map": [],
                "learning_plan_options": [],
                "guidance_prompts": [],
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
    assert "domain_map" in captured["system_prompt"]
    assert "learning_plan_options" in captured["system_prompt"]
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
                "current_level": "",
                "application_scenario": "",
                "problem_to_solve": "",
                "target_knowledge_point": "",
                "candidate_entry_points": [],
                "domain_map": [],
                "learning_plan_options": [],
                "guidance_prompts": [],
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
