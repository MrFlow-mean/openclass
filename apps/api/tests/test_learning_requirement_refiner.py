import json
from types import SimpleNamespace

import pytest

from app.models import BoardTaskRequirementSheet, ChatRequest, ConversationTurn
from app.services import learning_intake_policy
from app.services import openai_course_ai as openai_course_ai_module
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.blank_board_requirement_mapping import build_blank_board_requirement_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    BlankBoardRequirementRefinement,
    BlankBoardRequirementRefinementResult,
    BlankBoardRequirementTurn,
    BoardDocumentEditResult,
    BoardTaskRequirementRefinement,
    ChatbotReply,
    InitialLearningWorkModeDecision,
    OpenAICourseAI,
    bind_ai_output_stream,
    emit_ai_stream_event,
    openai_course_ai,
)
from app.services.resource_resolver import ResourceResolutionOutcome, resource_resolver


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


def test_blank_board_refinement_prompt_requires_rich_broad_topic_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    def _fake_parse_response(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        captured["kwargs"] = kwargs
        return openai_course_ai_module.ParsedAIResponse(
            output_parsed=BlankBoardRequirementTurn(
                route="requirement_refining",
                chatbot_message="先确认你的起点。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个领域",
            )
        )

    monkeypatch.setattr(ai, "_parse_response", _fake_parse_response)

    ai.generate_blank_board_requirement_refinement(
        board_document_state={"status": "empty"},
        conversation_summary="",
        user_message="我想学一个领域",
    )

    assert captured["role"] == "pm"
    assert captured["schema"] is BlankBoardRequirementTurn
    system_prompt = str(captured["system_prompt"])
    assert "通用 learning intake 策略" in system_prompt
    assert "用户新增信息正在收敛哪一类不确定项" in system_prompt
    assert "背景 + 宽泛学习方向" in system_prompt
    assert "3-5 个 A/B/C/D 当前水平画像" in system_prompt
    assert "entry_point_options 同步记录这些水平卡片" in system_prompt
    assert "优先归为 practice_artifact" in system_prompt
    assert "不把候选资料当成用户已确认资料" in system_prompt
    assert "不得追问章节内子主题" in system_prompt
    payload = json.loads(str(captured["user_prompt"]))
    assert "response_contract" not in payload
    assert payload["existing_requirement_state"] is None
    assert payload["existing_clarification_state"] is None
    assert payload["source_reference_summary"] == "无"
    assert captured["kwargs"] == {
        "visible_stream_field": "chatbot_message",
        "disable_stream_repair": True,
    }


def test_compact_second_turn_keeps_existing_practice_goal_and_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    lesson = create_empty_lesson("练习页")
    assert lesson.learning_requirements is not None
    existing = lesson.learning_requirements.model_copy(
        update={
            "learning_goal": "练习一个已学技能",
            "success_criteria": "用于一个已确认场景",
            "work_mode": "practice_artifact",
            "granularity": "practice_artifact",
        }
    )
    monkeypatch.setattr(
        ai,
        "_parse_response",
        lambda *args, **kwargs: openai_course_ai_module.ParsedAIResponse(
            output_parsed=BlankBoardRequirementTurn(
                route="requirement_refining",
                work_mode="practice_artifact",
                granularity="practice_artifact",
                current_level="高中毕业",
            )
        ),
    )

    result = ai.generate_blank_board_requirement_refinement(
        board_document_state={"status": "empty"},
        conversation_summary="",
        user_message="我高中毕业",
        existing_requirement_sheet=existing.model_dump(mode="json"),
    )

    assert isinstance(result, BlankBoardRequirementRefinement)
    assert result.learning_goal == "练习一个已学技能"
    assert result.current_level == "高中毕业"
    assert result.target_scenario == "用于一个已确认场景"
    state = build_blank_board_requirement_state(
        lesson=lesson,
        base_requirement=existing,
        result=result,
    )
    assert state.requirement.success_criteria == "用于一个已确认场景"


def test_resolved_source_chapter_is_ready_without_a_subtopic_question() -> None:
    lesson = create_empty_lesson("资料学习页")
    assert lesson.learning_requirements is not None
    result = BlankBoardRequirementRefinement(
        route="requirement_refining",
        chatbot_message="请先确认候选资料。",
        work_mode="knowledge_board",
        granularity="source_chapter",
        learning_goal="已解析章节",
        boundary="已解析章节",
        next_question="你想先从本章哪个内容开始？",
        ready_for_board=False,
    )

    resolved = build_blank_board_requirement_state(
        lesson=lesson,
        base_requirement=lesson.learning_requirements,
        result=result,
        resolved_source_chapter=True,
    )
    unresolved = build_blank_board_requirement_state(
        lesson=lesson,
        base_requirement=lesson.learning_requirements,
        result=result,
    )

    assert resolved.granularity == "source_chapter"
    assert resolved.ready_for_board is True
    assert resolved.missing_items == []
    assert resolved.requirement.current_questions == []
    assert resolved.clarification.next_question == ""
    assert resolved.clarification.checklist[0].is_clear is True
    assert unresolved.granularity == "broad_topic"
    assert unresolved.ready_for_board is False
    assert "用户想学的内容需要收敛到具体知识点" in unresolved.missing_items


def test_learning_intake_policy_is_generic_and_covers_strategy_matrix() -> None:
    policy_text = " ".join(
        [
            learning_intake_policy.BLANK_BOARD_LEARNING_INTAKE_POLICY,
            *learning_intake_policy.BLANK_BOARD_LEARNING_INTAKE_RESPONSE_CONTRACT.values(),
        ]
    )

    for concrete_term in ["高数", "导数", "极限", "积分", "法语", "英语", "CSAPP", "统计学习理论", "高考", "旅游"]:
        assert concrete_term not in policy_text

    assert {
        "starting_point",
        "light_self_report",
        "recent_experience",
        "known_unknown",
        "mode_split",
        "scenario",
        "goal_output",
        "stuck_point",
        "choice_cards",
        "domain_map",
        "recommended_entry",
        "implicit_observation",
    } <= set(learning_intake_policy.LEARNING_INTAKE_STRATEGIES)
    assert "背景 + 宽泛学习方向" in learning_intake_policy.BLANK_BOARD_LEARNING_INTAKE_POLICY
    assert "当前水平画像" in learning_intake_policy.BLANK_BOARD_LEARNING_INTAKE_POLICY


def test_board_document_edit_prompt_scopes_initial_board_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return BoardDocumentEditResult(
            operation="replace_document",
            title="入口课",
            content_text="# 入口课\n\n## 1.1 概念引入\n\n正文",
            summary="已生成。",
            chatbot_message="已生成。",
            section_titles=["1.1 概念引入"],
        )

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    ai.generate_board_document_edit(
        intent="generate_from_requirements",
        lesson_title="学习页",
        learning_requirement_context={"learning_goal": "学习一个新领域"},
        current_document_title="",
        current_document_text="",
        resource_summary="",
    )

    assert captured["role"] == "board"
    assert captured["schema"] is BoardDocumentEditResult
    system_prompt = str(captured["system_prompt"])
    assert "一次可讲完的聚焦板书" in system_prompt
    assert "不要在正文中完整讲解后续模块" in system_prompt
    assert "board_generation_quality_pipeline" not in system_prompt
    assert "概念引入、正式定义、性质或结论" in system_prompt
    assert "典型例题、解答过程、注释、习题" in system_prompt
    assert "后续学习路线" in system_prompt
    assert "绝不能用 ASCII、Unicode 线框字符" in system_prompt
    assert "只有真实代码、命令输出或算法伪代码可以使用代码块" in system_prompt
    assert "不写任何固定主题模板" in system_prompt
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["generation_source"] == "frozen_learning_requirement"
    assert "一次可讲完的聚焦小课" in payload["response_contract"]["content_text"]
    assert "教材体/大学讲义体" in payload["response_contract"]["content_text"]
    assert "不要在代码块中用 ASCII、Unicode 线框字符" in payload["response_contract"]["content_text"]
    assert "后续学习路线/下一步" in payload["response_contract"]["content_text"]


def test_board_task_requirement_prompt_records_existing_board_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = OpenAICourseAI()
    captured: dict[str, object] = {}

    monkeypatch.setattr(type(ai), "enabled", property(lambda _instance: True))

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return BoardTaskRequirementSheet(
            target_hint="第一节",
            requested_action="explain",
            question_or_topic="讲解这里",
            progress=100,
        )

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    result = ai.generate_board_task_requirement_sheet(
        lesson_title="已有板书",
        existing_task=None,
        board_summary="第一节已有内容。",
        resource_summary="",
        conversation_summary="",
        user_message="讲第一节",
    )

    assert result is not None
    assert result.board_workflow == "act_on_existing_board"
    assert captured["role"] == "pm"
    assert captured["schema"] is BoardTaskRequirementSheet
    assert "board_workflow 必须记录为 act_on_existing_board" in str(captured["system_prompt"])
    assert "核心三因素：位置、动作、怎么做" in str(captured["system_prompt"])
    assert "location_kind=target_range" in str(captured["system_prompt"])
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["response_contract"]["board_workflow"] == "固定写 act_on_existing_board，表示对已有板书内容做动作。"
    assert payload["response_contract"]["location_kind"] == "target_range、insertion_anchor 或 unspecified。"


def test_parse_response_logs_model_call_started(monkeypatch: pytest.MonkeyPatch) -> None:
    ai = OpenAICourseAI()
    logged_events: list[dict[str, object]] = []

    monkeypatch.setattr(
        openai_course_ai_module.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )
    monkeypatch.setattr(ai, "_model_for", lambda role: ("deepseek", "timing-model"))
    monkeypatch.setattr(ai, "_provider_available", lambda provider: True)
    monkeypatch.setattr(
        ai,
        "_call_parse",
        lambda **kwargs: openai_course_ai_module.ParsedAIResponse(
            output_parsed=BlankBoardRequirementRefinement(route="ordinary_chat", chatbot_message="收到。"),
            output_text='{"route":"ordinary_chat","chatbot_message":"收到。"}',
        ),
    )

    response = ai._parse_response(
        "pm",
        system_prompt="system",
        user_prompt="user",
        schema=BlankBoardRequirementRefinement,
        visible_stream_field="chatbot_message",
    )

    assert response is not None
    started_event = next(event for event in logged_events if event["event_type"] == "ai_model_call_started")
    assert started_event["provider"] == "deepseek"
    assert started_event["role"] == "pm"
    assert started_event["model"] == "timing-model"
    assert started_event["schema"] == "BlankBoardRequirementRefinement"
    assert started_event["prompt_chars"] == len("system") + len("user")


def test_blank_board_stream_parser_emits_chatbot_message_before_json_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return iter(
                [
                    {"choices": [{"delta": {"content": '{"chatbot_message":"你好'}}]},
                    {"choices": [{"delta": {"content": '世界","route":"ordinary_chat"}'}}]},
                ]
            )

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": _FakeCompletions()})()

    ai = OpenAICourseAI()
    client = _FakeClient()
    stream_events: list[dict[str, object]] = []
    logged_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        openai_course_ai_module.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )
    with bind_ai_output_stream(lambda payload: stream_events.append(payload)):
        result = ai._stream_openai_chat_completion(
            client=client,
            model="test-model",
            messages=[],
            schema=BlankBoardRequirementRefinement,
            schema_payload=BlankBoardRequirementRefinement.model_json_schema(),
            role="pm",
            field_name="chatbot_message",
            use_response_format=True,
        )

    assert result.output_text == '{"chatbot_message":"你好世界","route":"ordinary_chat"}'
    assert result.visible_field_value == "你好世界"
    assert result.visible_field_was_streamed is True
    assert [
        event["delta"]
        for event in stream_events
        if event.get("type") == "field_delta" and event.get("field") == "chatbot_message"
    ] == ["你好", "世界"]
    timing_events = [event for event in logged_events if event["event_type"] == "ai_stream_timing"]
    assert [event["stream_event"] for event in timing_events] == [
        "first_model_chunk",
        "first_visible_field_delta",
    ]
    assert all(event["role"] == "pm" for event in timing_events)
    assert all(event["model"] == "test-model" for event in timing_events)
    assert all(event["schema"] == "BlankBoardRequirementRefinement" for event in timing_events)
    assert all(event["field"] == "chatbot_message" for event in timing_events)
    assert all(isinstance(event["elapsed_ms"], int) for event in timing_events)


def test_non_stream_parser_retries_json_repair_without_response_schema() -> None:
    invalid_output = (
        '{"route":"requirement_refining","chatbot_message":"已确认",'
        '"learning_goal":"某主题的第四章","key_facts":['
        '{"label":"学习范围","value":"第四章","evidence":"用户说"第四章"",'
        '"category":"learning"}]}'
    )
    repaired_output = json.dumps(
        {
            "route": "requirement_refining",
            "chatbot_message": "已确认学习主题和章节范围。",
            "work_mode": "knowledge_board",
            "granularity": "broad_topic",
            "learning_goal": "某主题的第四章",
            "boundary": "第四章",
            "ready_for_board": False,
        },
        ensure_ascii=False,
    )

    class _FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=invalid_output))],
                    id="initial",
                    usage=None,
                )
            if len(self.calls) == 2:
                raise RuntimeError("schema repair request unavailable")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=repaired_output))],
                id="repaired",
                usage=None,
            )

    completions = _FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    ai = OpenAICourseAI()

    response = ai._call_openai_chat_parse(
        role="pm",
        model="test-model",
        system_prompt="system",
        user_prompt="user",
        schema=BlankBoardRequirementRefinement,
        client=client,
    )

    assert isinstance(response.output_parsed, BlankBoardRequirementRefinement)
    assert response.output_parsed.route == "requirement_refining"
    assert response.output_parsed.learning_goal == "某主题的第四章"
    assert response.output_parsed.boundary == "第四章"
    assert len(completions.calls) == 3
    assert "response_format" in completions.calls[1]
    assert "response_format" not in completions.calls[2]


def test_requirement_payload_cannot_be_downgraded_to_ordinary_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_structural_scope_requirement"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: BlankBoardRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="已记录你要学习的主题和章节范围。",
            summary="学习某主题的第四章。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="某主题的第四章",
            boundary="第四章",
            ready_for_board=False,
        ),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学习某主题的第四章"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.learning_goal == "某主题的第四章"
    assert response.active_requirement_sheet.boundary == "第四章"
    assert response.learning_clarification.label == "collecting"
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    assert len(versions) == 1


def test_empty_board_ordinary_chat_does_not_create_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_ordinary"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    call_count = 0

    def _fake_refinement(**kwargs):
        nonlocal call_count
        call_count += 1
        return BlankBoardRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="可以，我们就正常聊这个。",
        )

    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(route="ordinary_chat"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: pytest.fail("ordinary chat must skip requirement manager"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="可以，我们就正常聊这个。"),
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
    assert call_count == 0
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


def test_explicit_source_intent_is_persisted_in_learning_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_source_intent"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            route="learning_intake",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            topic="目标知识点",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="已经记录资料学习范围。",
            progress=100,
            summary="用户要求依据上传资料学习目标知识点。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="目标知识点",
            ready_for_board=True,
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_intake_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我会依据资料继续确认学习起点。"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="请根据我上传的资料讲目标知识点。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.source_grounding.requested_by_user is True
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    persisted_sheet = json.loads(versions[-1]["sheet_json"])
    assert persisted_sheet["source_grounding"]["requested_by_user"] is True


@pytest.mark.parametrize(
    ("resolution_status", "resolution_metadata"),
    [
        ("ambiguous_source", {"reason": "多个资料节点仍无法唯一确定。"}),
        ("content_unavailable", {"reason": "资料位置已确定，但正文当前不可读取。"}),
        (
            "no_match",
            {
                "reason": "显式引用的旧章节已经不存在。",
                "requested_chapter_id": "sourcechapter_stale",
                "intent_signals": ["explicit_source_chapter_id"],
            },
        ),
    ],
)
def test_unusable_explicit_source_reference_is_persisted_as_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    resolution_status: str,
    resolution_metadata: dict[str, object],
) -> None:
    user_id = f"user_unusable_source_reference_{resolution_status}"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    monkeypatch.setattr(resource_resolver, "has_ready_sources", lambda **kwargs: True)
    monkeypatch.setattr(
        resource_resolver,
        "preview_for_learning_requirement",
        lambda **kwargs: ResourceResolutionOutcome(
            status=resolution_status,
            metadata=resolution_metadata,
        ),
    )
    refinement_calls = 0

    def _fake_refinement(**kwargs):
        nonlocal refinement_calls
        refinement_calls += 1
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="需要继续确认资料位置。",
            progress=100,
            summary="学习目标已明确，但资料位置当前不可用。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="目标知识点",
            ready_for_board=True,
        )

    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        _fake_refinement,
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_intake_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="请确认具体资料位置。"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="请根据上传资料讲解目标知识点。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert refinement_calls == 2
    assert response.active_requirement_sheet.source_grounding.requested_by_user is True
    assert response.active_requirement_sheet.source_grounding.confirmation_status == "stale"
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    persisted_sheet = json.loads(versions[-1]["sheet_json"])
    assert persisted_sheet["source_grounding"]["confirmation_status"] == "stale"


def test_empty_board_refinement_keeps_pm_draft_internal_and_persists_chatbot_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_streamed_visible_reply"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    pm_draft = "这段是需求管理器的内部草稿。"
    final_reply = "这段才是 Chatbot 的最终回复。"

    def _fake_refinement(**kwargs):
        assert kwargs["include_stream_result"] is True
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "pm",
                "field": "chatbot_message",
                "delta": pm_draft,
                "value": pm_draft,
            }
        )
        return BlankBoardRequirementRefinementResult(
            result=BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=pm_draft,
                progress=50,
                summary="用户想学一个宽泛主题。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛主题",
                ready_for_board=False,
            ),
            visible_chat_buffer=pm_draft,
            visible_chat_was_streamed=True,
        )

    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            route="learning_intake",
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="一个宽泛主题",
        ),
    )
    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_intake_reply",
        lambda **kwargs: ChatbotReply(chatbot_message=final_reply),
    )

    stream_events: list[dict[str, object]] = []
    with bind_ai_output_stream(lambda payload: stream_events.append(payload)):
        response = process_chat_on_lesson(
            lesson.id,
            ChatRequest(message="我想学一个宽泛主题"),
            user_id=user_id,
        )

    streamed_message = "".join(
        str(event.get("delta") or "")
        for event in stream_events
        if event.get("type") == "field_delta"
        and event.get("role") == "pm"
        and event.get("field") == "chatbot_message"
    )
    assert streamed_message == pm_draft
    assert response.chatbot_message == final_reply
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["assistant_message"] == final_reply
    assert commit.metadata["assistant_message_source"] == "chatbot_learning_intake"
    assert commit.metadata["visible_reply_owner"] == "chatbot"
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["visible_chat_source"] == "streamed_buffer"
    assert discovery["visible_chat_was_streamed"] is True


def test_empty_board_refinement_parse_failure_discards_pm_draft_without_requirement_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_streamed_parse_failed"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    pm_draft = "结构化清单失败前产生的内部草稿。"

    def _fake_refinement(**kwargs):
        assert kwargs["include_stream_result"] is True
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "pm",
                "field": "chatbot_message",
                "delta": pm_draft,
                "value": pm_draft,
            }
        )
        return BlankBoardRequirementRefinementResult(
            result=None,
            visible_chat_buffer=pm_draft,
            visible_chat_was_streamed=True,
            structured_parse_failed=True,
        )

    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            route="learning_intake",
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="一个宽泛主题",
        ),
    )
    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("structured refinement failure must not fall back to ordinary chat"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个宽泛主题"),
        user_id=user_id,
    )

    assert response.chatbot_message == ""
    assert response.active_requirement_sheet is None
    assert response.learning_requirement_operation_status == "failed"
    assert response.learning_requirement_operation_failure_reason == (
        "本轮学习需求没有成功更新，请重试刚才的输入。"
    )
    assert store.list_learning_requirement_versions(user_id, lesson.id) == []
    assert store.list_learning_requirement_events(user_id, lesson.id) == []
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["refinement_route"] == "refinement_failed"
    assert commit.metadata["assistant_message"] == ""
    assert commit.metadata["assistant_message_source"] == "chatbot_empty"
    assert commit.metadata["visible_reply_owner"] == "chatbot"
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["structured_parse_failed"] is True
    assert discovery["requirement_update_skipped"] is True
    assert discovery["discarded_unvalidated_pm_draft"] is True

    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: BlankBoardRequirementRefinementResult(
            result=None,
            failure_kind="deadline_exceeded",
            failure_reason="AI call deadline exceeded",
        ),
    )
    timeout_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续记录我的学习背景"),
        user_id=user_id,
    )
    assert timeout_response.learning_requirement_operation_status == "failed"
    timeout_commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    timeout_discovery = timeout_commit.metadata["guided_requirement_discovery"]
    assert timeout_discovery["failure_code"] == "requirement_refinement_deadline_exceeded"
    assert timeout_discovery["failure_detail"] == "AI call deadline exceeded"
    assert timeout_discovery["structured_parse_failed"] is False


def test_empty_board_refinement_failure_keeps_active_requirement_version_and_records_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_active_refinement_failed"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_work_mode",
        lambda **kwargs: InitialLearningWorkModeDecision(
            route="learning_intake",
            work_mode="narrow_topic",
            granularity="broad_topic",
            topic="一个宽泛主题",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="先确认学习起点。",
            progress=40,
            summary="用户想学习一个宽泛主题。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个宽泛主题",
            missing_items=["具体学习入口"],
            next_question="你想从哪个具体概念开始？",
            ready_for_board=False,
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_intake_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="你想从哪个具体概念开始？"),
    )

    first_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个宽泛主题"),
        user_id=user_id,
    )
    versions_before = store.list_learning_requirement_versions(user_id, lesson.id)
    events_before = store.list_learning_requirement_events(user_id, lesson.id)
    assert len(versions_before) == 1

    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: BlankBoardRequirementRefinementResult(
            result=None,
            visible_chat_buffer="未校验的需求管理器草稿。",
            visible_chat_was_streamed=True,
            structured_parse_failed=True,
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("active refinement failure must not invoke ordinary chat"),
    )

    failed_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我高中毕业"),
        user_id=user_id,
    )

    versions_after = store.list_learning_requirement_versions(user_id, lesson.id)
    events_after = store.list_learning_requirement_events(user_id, lesson.id)
    assert versions_after == versions_before
    assert len(events_after) == len(events_before) + 1
    assert events_after[-1]["event_type"] == "refinement_failed"
    failure_metadata = json.loads(events_after[-1]["metadata_json"])
    assert failure_metadata["discarded_unvalidated_pm_draft"] is True
    assert failed_response.requirement_run_id == first_response.requirement_run_id
    assert failed_response.requirement_version_id == first_response.requirement_version_id
    assert failed_response.requirement_phase == first_response.requirement_phase
    assert failed_response.active_requirement_sheet == first_response.active_requirement_sheet
    assert failed_response.learning_clarification == first_response.learning_clarification
    assert failed_response.chatbot_message == ""
    assert failed_response.learning_requirement_operation_status == "failed"


def test_non_empty_board_uses_existing_board_task_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_non_empty_basic"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    workspace = store.load_for_user(user_id)
    saved_lesson = workspace.packages[0].lessons[0]
    saved_lesson.board_document.content_text = "# 已有板书\n\n这里已经有一段学习内容。"
    store.save_for_user(user_id, workspace)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_blank_board_requirement_refinement",
        lambda **kwargs: pytest.fail("non-empty board should not enter blank-board requirement refinement"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_refinement",
        lambda **kwargs: BoardTaskRequirementRefinement(
            route="ordinary_chat",
            chatbot_message="我会按已有板书继续正常聊天。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("non-empty board should be classified by board task refinement first"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="先聊一下这个页面。"),
        user_id=user_id,
    )

    assert response.chatbot_message == "我会按已有板书继续正常聊天。"
    assert response.requirement_run_id is None
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["board_task_refinement_route"] == "ordinary_chat"
    assert commit.metadata["board_document_state"]["status"] == "non_empty"


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
            guidance_strategy="domain_map",
            learning_map_summary="这个领域可以先看整体对象、核心规则和典型应用。",
            entry_point_options=[
                {
                    "label": "整体组成",
                    "why_it_matters": "先知道领域由哪些部分构成。",
                    "best_for": "完全不知道从哪开始的学习者。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "最容易形成第一个可讲解知识点。",
                    "best_for": "想马上开始学习的人。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最基础，也最容易从宽泛方向收敛到单一知识点。",
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
    assert response.active_requirement_sheet.board_workflow == "generate_from_scratch"
    assert response.active_requirement_sheet.board_scope == []
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
    assert sheet_json["board_workflow"] == "generate_from_scratch"
    assert sheet_json["current_questions"] == ["你更想先理解整体组成，还是先挑一个最基础的概念开始？"]
    event_metadata = json.loads(events[0]["metadata_json"])
    assert event_metadata["guidance_strategy"] == "domain_map"
    assert event_metadata["recommended_entry_point"] == "基础概念"
    assert event_metadata["entry_point_options"][0]["label"] == "整体组成"
    saved = store.load_for_user(user_id)
    commit_metadata = saved.packages[0].lessons[0].history_graph.commits[-1].metadata
    assert commit_metadata["guided_requirement_discovery"]["guidance_strategy"] == "domain_map"
    assert commit_metadata["guided_requirement_discovery"]["recommended_entry_point"] == "基础概念"

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
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="可以，先聊这个也没问题。"),
    )
    ordinary_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="先不说学习，聊点别的。"),
        user_id=user_id,
    )
    assert ordinary_response.active_requirement_sheet is not None
    assert ordinary_response.requirement_run_id == response.requirement_run_id
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 1


def test_pure_novice_intro_lands_foundation_entry_without_requiring_external_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_pure_novice_intro"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个领域可以先看一张地图："
                    "\n1. **整体组成**：先知道它大概由哪些部分构成。"
                    "\n2. **基础概念**：挑一个最小、最容易讲清楚的入口。"
                    "\n3. **应用迁移**：等基础站稳后再看它怎么解决真实任务。"
                    "\n\n我推荐先从**基础概念**开始，因为它最容易形成第一块可学习内容。"
                    "如果你已经有一点基础，我会把入口往更具体的概念收；如果你完全没接触过，"
                    "我会先用整体地图帮你建立方向感，再落到第一课。"
                    "你之前接触过这个领域吗，还是更接近完全新手？"
                ),
                progress=45,
                summary="用户想学一个宽泛领域。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛领域",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成、基础概念和应用迁移。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助用户建立方向感。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础概念",
                        "why_it_matters": "最容易落到第一块知识点。",
                        "best_for": "想马上开始的人。",
                    },
                ],
                recommended_entry_point="基础概念",
                reason_for_recommendation="它最容易形成第一块可学习内容。",
                next_question="你之前接触过这个领域吗，还是更接近完全新手？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "明白，你是纯新手入门。那第一课我直接定为**这个领域的基础概念与整体组成**。"
                "选它的原因是：零基础先看清这个领域研究什么、由哪些部分组成、核心对象如何协作，"
                "后面再进入规则、工具或实操会更稳。学完这一课，你应该能说清这个领域的基本组成和第一个后续入口。"
            ),
            progress=100,
            summary="用户零基础纯新手，适合先学领域基础概念与整体组成。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="这个领域的基础概念与整体组成",
            current_level="零基础纯新手",
            known_background="用户明确表示纯新手入门。",
            guidance_strategy="recommended_entry",
            learning_map_summary="纯新手先理解领域基础概念与整体组成。",
            entry_point_options=[
                {
                    "label": "这个领域的基础概念与整体组成",
                    "why_it_matters": "帮助零基础先建立整体结构感。",
                    "best_for": "完全不了解的人。",
                },
            ],
            recommended_entry_point="这个领域的基础概念与整体组成",
            reason_for_recommendation="它最基础，能避免新手过早进入后续工具或实操路线。",
            learning_need_checklist=[
                "用户想学的内容",
            ],
            checklist=[
                {
                    "title": "当前水平已知",
                    "is_clear": True,
                    "evidence": "用户说纯新手入门。",
                },
                {
                    "title": "用户想学的内容",
                    "is_clear": True,
                    "evidence": "这个领域的基础概念与整体组成。",
                },
            ],
            target_depth="入门了解 / 建立领域地图",
            success_criteria="理解领域组成，并确定后续学习入口",
            next_question="",
            ready_for_board=True,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    first_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个领域"),
        user_id=user_id,
    )
    second_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我纯新手入门"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert second_response.requirement_run_id == first_response.requirement_run_id
    assert second_response.active_requirement_sheet is not None
    assert second_response.active_requirement_sheet.granularity == "single_knowledge_point"
    assert second_response.active_requirement_sheet.learning_goal == "这个领域的基础概念与整体组成"
    assert second_response.active_requirement_sheet.level == "零基础纯新手"
    assert second_response.active_requirement_sheet.target_depth == "入门了解 / 建立领域地图"
    assert second_response.active_requirement_sheet.success_criteria == ""
    assert second_response.active_requirement_sheet.learning_need_checklist == []
    assert "应用场景" not in second_response.learning_clarification.missing_items
    assert all("场景" not in item.title for item in second_response.learning_clarification.checklist)
    assert second_response.active_requirement_sheet.current_questions == []
    assert second_response.learning_clarification.ready_for_board is True
    assert second_response.requirement_phase == "ready"
    assert "为了什么" not in second_response.chatbot_message
    assert "应用场景" not in second_response.chatbot_message
    assert "可以吗" not in second_response.chatbot_message
    assert "这个领域的基础概念与整体组成" in second_response.chatbot_message
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["recommended_entry_point"] == "这个领域的基础概念与整体组成"
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 2


def test_delegated_pure_novice_intro_lands_first_lesson_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_delegated_pure_novice_ready"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message=(
                    "这个复合领域可以先看一张地图："
                    "\n1. **整体结构**：先知道它由哪些部分组成。"
                    "\n2. **基础规则**：再理解各部分如何协作。"
                    "\n3. **实践入口**：最后进入一个小任务。"
                    "\n\n如果你现在还不确定自己的基础，我先把**整体结构**作为暂定入口，"
                    "因为它能帮你先建立方向感；如果你已经有一点背景，**基础规则**也可以作为下一步。"
                    "你之前接触过这个领域吗，还是更接近完全新手？"
                ),
                progress=45,
                summary="用户想学一个宽泛复合领域。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个复合领域",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体结构、基础规则和实践入口。",
                entry_point_options=[
                    {
                        "label": "整体结构",
                        "why_it_matters": "帮助用户先建立方向感。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础规则",
                        "why_it_matters": "帮助理解各部分如何协作。",
                        "best_for": "已有一点背景的人。",
                    },
                ],
                recommended_entry_point="整体结构",
                reason_for_recommendation="它最适合作为第一步。",
                next_question="你之前接触过这个领域吗，还是更接近完全新手？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "明白，你是纯入门新手，也希望我来安排入口。"
                "那第一课先定为**这个领域由哪几部分组成**。"
                "选它的原因是：你先把整体结构看清楚，后面进入规则、工具或练习时就不会迷路。"
                "学完这一课，你应该能说清这个领域的几个核心组成部分分别负责什么。"
            ),
            progress=100,
            summary="用户是纯入门新手，并委托 AI 安排入门入口。",
            work_mode="knowledge_board",
            granularity="single_knowledge_point",
            learning_goal="这个领域由哪几部分组成",
            current_level="零基础纯新手",
            known_background="用户明确表示纯入门新手，并要求系统指导。",
            target_depth="入门了解 / 建立领域地图",
            success_criteria="理解领域组成，并确定后续学习入口",
            guidance_strategy="recommended_entry",
            learning_map_summary="第一课先理解这个领域由哪些部分组成。",
            recommended_entry_point="这个领域由哪几部分组成",
            reason_for_recommendation="它最适合纯新手先建立整体结构感。",
            learner_profile_inference="用户是纯入门新手，且委托系统安排入口。",
            missing_items=[],
            next_question="",
            ready_for_board=True,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    first_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个复合领域"),
        user_id=user_id,
    )
    second_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我是纯入门新手，为我指导"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert second_response.requirement_run_id == first_response.requirement_run_id
    assert second_response.active_requirement_sheet is not None
    assert second_response.active_requirement_sheet.granularity == "single_knowledge_point"
    assert second_response.active_requirement_sheet.learning_goal == "这个领域由哪几部分组成"
    assert second_response.active_requirement_sheet.level == "零基础纯新手"
    assert second_response.active_requirement_sheet.target_depth == "入门了解 / 建立领域地图"
    assert second_response.active_requirement_sheet.success_criteria == ""
    assert second_response.active_requirement_sheet.current_questions == []
    assert second_response.learning_clarification.ready_for_board is True
    assert second_response.learning_clarification.missing_items == []
    assert second_response.requirement_phase == "ready"
    assert "愿意从" not in second_response.chatbot_message
    assert "这个领域由哪几部分组成" in second_response.chatbot_message
    versions = store.list_learning_requirement_versions(user_id, lesson.id)
    assert [version["status"] for version in versions] == ["collecting", "ready"]


def test_empty_board_unknown_start_uses_recommended_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_recommended_entry"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "我先给你安排一个最容易进入的起点：从整体组成看一眼，再落到一个基础概念。"
                "如果你没有特别偏好，我们就从这个基础概念开始。"
            ),
            progress=35,
            summary="用户希望系统安排学习入口。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="待推荐入口的宽泛主题",
            guidance_strategy="recommended_entry",
            learning_map_summary="可以先看整体组成、基础概念和典型应用。",
            entry_point_options=[
                {
                    "label": "整体组成",
                    "why_it_matters": "降低完全不知道从哪开始的压力。",
                    "best_for": "希望系统安排路线的人。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "更容易变成第一块可生成板书的内容。",
                    "best_for": "想直接开始的人。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最基础，适合作为第一块板书入口。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="如果你没有特别偏好，我们就从这个基础概念开始，可以吗？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="不知道，你安排。"),
        user_id=user_id,
    )

    assert response.learning_clarification.ready_for_board is False
    assert "基础概念" in response.chatbot_message
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.current_questions == [
        "如果你没有特别偏好，我们就从这个基础概念开始，可以吗？"
    ]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["guided_requirement_discovery"]["guidance_strategy"] == "recommended_entry"
    assert commit.metadata["guided_requirement_discovery"]["recommended_entry_point"] == "基础概念"


def test_background_plus_broad_goal_uses_level_choice_cards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_background_broad_goal"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "这个方向已经明确了：你是在一个阶段转换点上，想提前进入新领域。"
                "为了不把第一课定得太浅或太跳，我先帮你定位起点："
                "\nA. 几乎没接触过前置概念"
                "\nB. 有一些相关基础，但还没系统学过"
                "\nC. 能做基础任务，但不清楚严格定义和应用边界"
                "\nD. 已经预习过一部分，想查漏补缺"
                "\n如果不确定，先选 B 就行。哪个最像你现在的状态？"
            ),
            progress=45,
            summary="用户给出了学习背景和宽泛预习方向，但当前水平待确认。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个新领域的预习",
            known_background="用户处在阶段转换点，想提前预习一个新领域。",
            guidance_strategy="choice_cards",
            learning_map_summary="先确认起点，再把宽泛预习方向落到第一课入口。",
            entry_point_options=[
                {
                    "label": "A. 几乎没接触过前置概念",
                    "why_it_matters": "适合从整体地图和最小术语开始。",
                    "best_for": "只知道方向、还没有相关基础的人。",
                },
                {
                    "label": "B. 有一些相关基础，但还没系统学过",
                    "why_it_matters": "适合从前置能力到新领域入口的过渡开始。",
                    "best_for": "有基础但没有系统进入新领域的人。",
                },
                {
                    "label": "C. 能做基础任务，但不清楚严格定义和应用边界",
                    "why_it_matters": "适合补定义、边界和应用连接。",
                    "best_for": "会做一点但理解不稳的人。",
                },
                {
                    "label": "D. 已经预习过一部分，想查漏补缺",
                    "why_it_matters": "适合先做结构梳理和缺口定位。",
                    "best_for": "已经自学过一轮的人。",
                },
            ],
            recommended_entry_point="B. 有一些相关基础，但还没系统学过",
            reason_for_recommendation="用户已经给出阶段背景，B 是较稳妥的默认过渡起点。",
            learner_profile_inference="用户有明确背景和预习方向，但当前水平仍待确认。",
            missing_items=["当前水平"],
            next_question="哪个最像你现在的状态？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我刚结束上一阶段学习，想预习一个新领域。"),
        user_id=user_id,
    )

    assert response.learning_clarification.ready_for_board is False
    assert "A. 几乎没接触过前置概念" in response.chatbot_message
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.current_questions == ["哪个最像你现在的状态？"]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["guidance_strategy"] == "choice_cards"
    assert len(discovery["entry_point_options"]) == 4
    assert discovery["entry_point_options"][1]["label"] == "B. 有一些相关基础，但还没系统学过"


def test_choice_card_selection_updates_level_and_recommends_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_choice_card_selected"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "好，那我先按“有一些相关基础，但还没系统学过”来安排。"
                "我建议第一步从基础入口的直观含义开始，再慢慢补严格定义。"
                "我们就从这个基础入口开始，可以吗？"
            ),
            progress=70,
            summary="用户选择了水平卡片，适合从基础入口过渡到严格定义。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="基础入口的直观含义",
            current_level="有一些相关基础，但还没系统学过",
            known_background="用户选择了当前水平卡片 B。",
            guidance_strategy="starting_point",
            learning_map_summary="先用直观含义连接已有基础，再进入严格定义和应用边界。",
            recommended_entry_point="基础入口的直观含义",
            reason_for_recommendation="它能承接用户已有基础，又不会一开始跳到过深内容。",
            learner_profile_inference="用户不是纯零基础，但还没有系统进入新领域。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="我们就从这个基础入口开始，可以吗？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="选 B。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level == "有一些相关基础，但还没系统学过"
    assert response.active_requirement_sheet.known_background == "用户选择了当前水平卡片 B。"
    assert response.active_requirement_sheet.current_questions == ["我们就从这个基础入口开始，可以吗？"]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["guidance_strategy"] == "starting_point"
    assert discovery["recommended_entry_point"] == "基础入口的直观含义"
    assert "不是纯零基础" in discovery["learner_profile_inference"]


def test_empty_board_known_unknown_self_report_updates_background(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_known_unknown"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message="你已经有一部分基础了，我会避开重复内容，优先从还没学过的部分切入。",
            progress=70,
            summary="用户说明了已会和未会内容。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="未会部分的入门",
            current_level="已经学过一部分基础内容",
            known_background="已会：前置概念；未会：后续概念。",
            guidance_strategy="known_unknown",
            learner_profile_inference="用户已学过前置概念，还没学后续概念，适合从后续概念的直观含义开始。",
            recommended_entry_point="后续概念的直观含义",
            reason_for_recommendation="它连接用户已会内容，同时避开重复讲解。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="我们就从后续概念的直观含义开始，可以吗？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="前面的基础学过，后面的还没学。"),
        user_id=user_id,
    )

    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level == "已经学过一部分基础内容"
    assert response.active_requirement_sheet.known_background == "已会：前置概念；未会：后续概念。"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["guidance_strategy"] == "known_unknown"
    assert "前置概念" in discovery["learner_profile_inference"]


def test_recent_experience_and_stuck_point_records_background_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_recent_stuck"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    call_count = 0

    def _fake_refinement(**kwargs):
        nonlocal call_count
        call_count += 1
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "你已经给了一个很有用的起点：最近做过一个任务，但卡在把知识迁移到新问题上。"
                "我先按这个线索把地图缩小："
                "\n1. **卡点复盘**：先找出是概念没站稳，还是方法不会迁移。"
                "\n2. **基础概念**：如果概念不稳，就回到最小概念补齐。"
                "\n3. **应用迁移**：如果概念能听懂但不会用，就用小任务练迁移。"
                "\n\n我建议先从**卡点复盘**开始，因为它最贴近你刚才说的最近经历。"
                "你最近卡住的那一步，更像概念没懂，还是会看例子但自己做不出来？"
            ),
            progress=65,
            summary="用户最近做过任务，但卡在知识迁移。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="最近卡住的学习内容",
            current_level="最近做过相关任务，但迁移应用不稳定",
            known_background="最近经历：做过相关任务；卡点：迁移到新问题时不稳定。",
            guidance_strategy="stuck_point",
            learning_map_summary="可从卡点复盘、基础概念和应用迁移三个入口缩小。",
            entry_point_options=[
                {
                    "label": "卡点复盘",
                    "why_it_matters": "直接对应用户最近卡住的位置。",
                    "best_for": "已经尝试过但不稳定的人。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "帮助补齐不稳的底层理解。",
                    "best_for": "概念还没听懂的人。",
                },
                {
                    "label": "应用迁移",
                    "why_it_matters": "帮助把会看的内容变成会用的能力。",
                    "best_for": "能看例子但不会独立做的人。",
                },
            ],
            recommended_entry_point="卡点复盘",
            reason_for_recommendation="它最贴近用户刚透露的最近经历。",
            learner_profile_inference="用户有近期尝试经历，主要卡在迁移应用。",
            missing_items=["用户想学的内容需要收敛到具体知识点"],
            next_question="你最近卡住的那一步，更像概念没懂，还是会看例子但自己做不出来？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我最近做过一个任务，但换个问题就不会了。"),
        user_id=user_id,
    )

    assert call_count == 1
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level == "最近做过相关任务，但迁移应用不稳定"
    assert "最近经历" in response.active_requirement_sheet.known_background
    assert response.active_requirement_sheet.current_questions == [
        "你最近卡住的那一步，更像概念没懂，还是会看例子但自己做不出来？"
    ]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["guidance_strategy"] == "stuck_point"


def test_empty_board_specific_knowledge_point_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_knowledge_ready"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    call_count = 0

    def _fake_refinement(**kwargs):
        nonlocal call_count
        call_count += 1
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
    assert call_count == 1
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
    call_count = 0

    def _fake_refinement(**kwargs):
        nonlocal call_count
        call_count += 1
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
    assert call_count == 1
    assert response.requirement_phase == "ready"
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.learning_goal == "一项旧知识或技能"
    assert response.active_requirement_sheet.level == "已经有基础但不稳定"
    fact_labels = {fact.label for fact in response.learning_clarification.key_facts}
    assert {"用户想学的内容", "当前水平", "面向场景"} <= fact_labels
