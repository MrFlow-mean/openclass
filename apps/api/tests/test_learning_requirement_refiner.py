import json

import pytest

from app.models import ChatRequest, ConversationTurn
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import (
    BlankBoardRequirementRefinement,
    ChatbotReply,
    OpenAICourseAI,
    bind_ai_output_stream,
    openai_course_ai,
)


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

    def _fake_parse(role, system_prompt, user_prompt, schema, **kwargs):
        captured["role"] = role
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["schema"] = schema
        return BlankBoardRequirementRefinement(route="ordinary_chat", chatbot_message="收到。")

    monkeypatch.setattr(ai, "_parse", _fake_parse)

    ai.generate_blank_board_requirement_refinement(
        board_document_state={"status": "empty"},
        conversation_summary="",
        user_message="我想学一个领域",
    )

    assert captured["role"] == "pm"
    assert captured["schema"] is BlankBoardRequirementRefinement
    system_prompt = str(captured["system_prompt"])
    assert "开场承接 + 简短学习地图 + 2-5 个入口选项 + 一个推荐入口 + 推荐理由 + 一个绑定推荐入口的主问题" in system_prompt
    assert "学习地图和入口选项必须真的写进 chatbot_message" in system_prompt
    assert "knowledge_board + broad_topic" in system_prompt
    assert "纯新手、零基础、入门、先了解一下" in system_prompt
    assert "不要强制追问考试、面试、工作、赚钱、项目或现实产出场景" in system_prompt
    payload = json.loads(str(captured["user_prompt"]))
    assert "开场承接" in payload["response_contract"]["chatbot_message"]
    assert "推荐理由" in payload["response_contract"]["chatbot_message"]
    assert "当前水平" in payload["response_contract"]["next_question"]
    assert "纯新手入门" in payload["response_contract"]["next_question"]
    assert "已会/未会" in system_prompt


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
    assert call_count == 1
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


def test_non_empty_board_keeps_basic_chat_path(
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
        "generate_basic_chat_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我会按已有板书继续正常聊天。"),
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
    assert commit.metadata["board_document_state"]["status"] == "non_empty"


def test_empty_board_broad_learning_need_repairs_short_guidance_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_broad_repair"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_empty_workspace(store, user_id)
    calls: list[dict[str, object]] = []

    def _fake_refinement(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BlankBoardRequirementRefinement(
                route="requirement_refining",
                chatbot_message="数学很广，你想学哪部分？",
                progress=10,
                summary="用户想学一个宽泛领域。",
                work_mode="unknown",
                granularity="broad_topic",
                learning_goal="数学",
                missing_items=["current_level", "target_scenario"],
                next_question="你想学哪部分？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "好，数学是个很大的世界，我们先把地图打开看一眼。"
                "\n\n数学可以先从几个入口切入："
                "\n1. **基础对象层**：先弄清数字、式子、函数这些对象在讨论什么。"
                "\n2. **规则方法层**：学习变形、证明、计算和建模这些通用方法。"
                "\n3. **典型应用层**：把知识放到题目、项目或真实问题里使用。"
                "\n\n如果你现在只是想入门，我建议先从**基础对象层**开始，因为它最容易收敛到第一个可学习的知识点。"
                "你现在更想先了解整体地图，还是直接从一个最基础的概念开始？"
            ),
            progress=45,
            summary="用户想学数学，需要收敛到具体知识点。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="数学",
            guidance_strategy="domain_map",
            learning_map_summary="数学可以先看基础对象、规则方法和典型应用。",
            entry_point_options=[
                {
                    "label": "基础对象层",
                    "why_it_matters": "先知道数学讨论的对象。",
                    "best_for": "刚开始入门的人。",
                },
                {
                    "label": "规则方法层",
                    "why_it_matters": "掌握计算和推理的方法。",
                    "best_for": "想开始做题的人。",
                },
                {
                    "label": "典型应用层",
                    "why_it_matters": "理解知识能解决什么问题。",
                    "best_for": "有明确应用目标的人。",
                },
            ],
            recommended_entry_point="基础对象层",
            reason_for_recommendation="它最基础，最容易落到第一个知识点。",
            next_question="你现在更想先了解整体地图，还是直接从一个最基础的概念开始？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学数学"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert calls[1]["quality_repair_context"] is not None
    assert "基础对象层" in response.chatbot_message
    assert "规则方法层" in response.chatbot_message
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.work_mode == "knowledge_board"
    assert response.active_requirement_sheet.granularity == "broad_topic"
    assert response.learning_clarification.missing_items == ["用户想学的内容需要收敛到具体知识点"]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert discovery["recommended_entry_point"] == "基础对象层"


def test_recommended_entry_without_level_question_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_entry_then_level"
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
                    "这个领域可以先打开一张地图："
                    "\n1. **基础对象层**：先理解最基本的对象。"
                    "\n2. **规则方法层**：再学习通用规则和方法。"
                    "\n3. **应用迁移层**：最后放到任务里使用。"
                    "\n\n我推荐你先从**基础对象层**开始，因为它最容易落到第一个知识点。"
                    "我们就从这里开始，可以吗？"
                ),
                progress=45,
                summary="用户想学一个宽泛领域。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛领域",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看基础对象、规则方法和应用迁移。",
                entry_point_options=[
                    {
                        "label": "基础对象层",
                        "why_it_matters": "最容易形成第一个知识点。",
                        "best_for": "新手。",
                    },
                    {
                        "label": "规则方法层",
                        "why_it_matters": "帮助理解通用方法。",
                        "best_for": "有一点基础的人。",
                    },
                    {
                        "label": "应用迁移层",
                        "why_it_matters": "帮助连接任务。",
                        "best_for": "有明确目标的人。",
                    },
                ],
                recommended_entry_point="基础对象层",
                reason_for_recommendation="它最基础，最容易落到第一个知识点。",
                next_question="我们就从这里开始，可以吗？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "这个领域可以先打开一张地图："
                "\n1. **基础对象层**：先理解最基本的对象。"
                "\n2. **规则方法层**：再学习通用规则和方法。"
                "\n3. **应用迁移层**：最后放到任务里使用。"
                "\n\n我推荐你先从**基础对象层**开始，因为它最容易落到第一个知识点。"
                "不过在真正开始前，我想先确认你的当前水平：你之前接触过这个领域吗？已经会哪些，哪些还没学过？"
            ),
            progress=45,
            summary="用户想学一个宽泛领域。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个宽泛领域",
            guidance_strategy="domain_map",
            learning_map_summary="可以先看基础对象、规则方法和应用迁移。",
            entry_point_options=[
                {
                    "label": "基础对象层",
                    "why_it_matters": "最容易形成第一个知识点。",
                    "best_for": "新手。",
                },
                {
                    "label": "规则方法层",
                    "why_it_matters": "帮助理解通用方法。",
                    "best_for": "有一点基础的人。",
                },
                {
                    "label": "应用迁移层",
                    "why_it_matters": "帮助连接任务。",
                    "best_for": "有明确目标的人。",
                },
            ],
            recommended_entry_point="基础对象层",
            reason_for_recommendation="它最基础，最容易落到第一个知识点。",
            next_question="你之前接触过这个领域吗？已经会哪些，哪些还没学过？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想入门一个领域"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert calls[1]["quality_repair_context"] is not None
    assert "当前水平" in response.chatbot_message
    assert "已经会哪些" in response.chatbot_message
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.current_questions == [
        "你之前接触过这个领域吗？已经会哪些，哪些还没学过？"
    ]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["guided_requirement_discovery"]["quality_repaired"] is True


def test_form_like_internal_field_guidance_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_form_like_repair"
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
                    "这个方向可以先看一张地图："
                    "\n1. **整体组成**：先知道这个方向由哪些部分构成。"
                    "\n2. **基础概念**：挑一个最小概念开始理解。"
                    "\n3. **应用迁移**：把理解放到任务里使用。"
                    "\n\n我推荐先从**基础概念**开始，因为它最容易落到第一块可学习内容。"
                    "请填写 learning_goal、current_level、target_scenario。学习内容：当前水平：面向场景："
                ),
                progress=45,
                summary="用户想学一个宽泛主题。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛主题",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成、基础概念和应用迁移。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助用户建立方向感。",
                        "best_for": "完全不了解的人。",
                    },
                    {
                        "label": "基础概念",
                        "why_it_matters": "最容易落到第一块知识点。",
                        "best_for": "想开始学习的人。",
                    },
                ],
                recommended_entry_point="基础概念",
                reason_for_recommendation="它最容易变成第一块可生成板书的内容。",
                next_question="请填写学习内容、当前水平、面向场景。",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "好，我们先把这个方向打开成一张小地图。"
                "\n1. **整体组成**：先看它大概由哪些部分构成，避免一上来迷路。"
                "\n2. **基础概念**：挑一个最小、最容易讲清楚的概念作为第一步。"
                "\n3. **应用迁移**：等概念站稳后，再看它能放到哪些任务里使用。"
                "\n\n我建议先从**基础概念**开始，因为它最容易收敛成第一块板书。"
                "你之前接触过这个方向吗，还是更接近完全新手？"
            ),
            progress=45,
            summary="用户想学一个宽泛主题。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个宽泛主题",
            guidance_strategy="domain_map",
            learning_map_summary="可以先看整体组成、基础概念和应用迁移。",
            entry_point_options=[
                {
                    "label": "整体组成",
                    "why_it_matters": "帮助用户建立方向感。",
                    "best_for": "完全不了解的人。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "最容易落到第一块知识点。",
                    "best_for": "想开始学习的人。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最容易变成第一块可生成板书的内容。",
            next_question="你之前接触过这个方向吗，还是更接近完全新手？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    stream_events: list[dict[str, object]] = []
    with bind_ai_output_stream(lambda payload: stream_events.append(payload)):
        response = process_chat_on_lesson(
            lesson.id,
            ChatRequest(message="我想学一个方向"),
            user_id=user_id,
        )

    assert len(calls) == 2
    repair_context = calls[1]["quality_repair_context"]
    assert repair_context is not None
    assert "字段" in repair_context["repair_reason"]
    assert "请填写" not in response.chatbot_message
    assert "learning_goal" not in response.chatbot_message
    streamed_message = "".join(
        str(event.get("delta") or "")
        for event in stream_events
        if event.get("type") == "field_delta"
        and event.get("role") == "pm"
        and event.get("field") == "chatbot_message"
    )
    assert streamed_message == response.chatbot_message
    assert "请填写" not in streamed_message
    assert "learning_goal" not in streamed_message
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert any("泄露内部字段" in issue for issue in discovery["quality_issues"])
    assert any("填表" in issue for issue in discovery["quality_issues"])


def test_multi_question_guidance_is_repaired_to_one_main_question(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_multi_question_repair"
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
                    "这个方向可以先看三层："
                    "\n1. **整体组成**：知道它有哪些部分。"
                    "\n2. **基础概念**：挑一个最小概念开始。"
                    "\n3. **应用迁移**：看它怎么进入真实任务。"
                    "\n\n我推荐先从**基础概念**开始，因为它最容易变成第一块板书。"
                    "你目前接触到什么程度？你学完更想做到什么？"
                ),
                progress=50,
                summary="用户想学一个宽泛主题。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个宽泛主题",
                guidance_strategy="domain_map",
                learning_map_summary="可以先看整体组成、基础概念和应用迁移。",
                entry_point_options=[
                    {
                        "label": "整体组成",
                        "why_it_matters": "帮助建立整体视野。",
                        "best_for": "不知道从哪开始的人。",
                    },
                    {
                        "label": "基础概念",
                        "why_it_matters": "帮助落到第一块知识点。",
                        "best_for": "想直接开始的人。",
                    },
                ],
                recommended_entry_point="基础概念",
                reason_for_recommendation="它最容易变成第一块板书。",
                next_question="你目前接触到什么程度？你学完更想做到什么？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "这个方向可以先看三层："
                "\n1. **整体组成**：知道它有哪些部分。"
                "\n2. **基础概念**：挑一个最小概念开始。"
                "\n3. **应用迁移**：看它怎么进入真实任务。"
                "\n\n我推荐先从**基础概念**开始，因为它最容易变成第一块板书。"
                "你目前大概接触到什么程度？"
            ),
            progress=50,
            summary="用户想学一个宽泛主题。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个宽泛主题",
            guidance_strategy="domain_map",
            learning_map_summary="可以先看整体组成、基础概念和应用迁移。",
            entry_point_options=[
                {
                    "label": "整体组成",
                    "why_it_matters": "帮助建立整体视野。",
                    "best_for": "不知道从哪开始的人。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "帮助落到第一块知识点。",
                    "best_for": "想直接开始的人。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最容易变成第一块板书。",
            next_question="你目前大概接触到什么程度？",
            ready_for_board=False,
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个宽泛主题"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert response.chatbot_message.count("？") == 1
    repair_context = calls[1]["quality_repair_context"]
    assert repair_context is not None
    assert "多个独立问题" in repair_context["repair_reason"]
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert any("多个独立问题" in issue for issue in discovery["quality_issues"])


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
    ordinary_response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="先不说学习，聊点别的。"),
        user_id=user_id,
    )
    assert ordinary_response.active_requirement_sheet is not None
    assert ordinary_response.requirement_run_id == response.requirement_run_id
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 1


def test_pure_novice_intro_keeps_intro_goal_without_requiring_external_scenario(
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
                "明白，你是纯新手入门，我先用外行人能看懂的方式把地图打开："
                "\n1. **这个领域是什么**：先知道它研究什么、解决什么类型的问题。"
                "\n2. **核心组成**：看它通常由哪些部分拼起来。"
                "\n3. **第一个入口**：选一个最基础、最容易获得正反馈的概念开始。"
                "\n\n我推荐先从**基础概念**开始，因为它最适合零基础建立第一块理解。"
                "我们先从这个入口开始，可以吗？"
            ),
            progress=70,
            summary="用户零基础纯新手，想先入门了解一个领域。",
            work_mode="knowledge_board",
            granularity="broad_topic",
            learning_goal="一个领域入门",
            current_level="零基础纯新手",
            known_background="用户明确表示纯新手入门。",
            guidance_strategy="recommended_entry",
            learning_map_summary="纯新手先看领域是什么、核心组成和第一个入口。",
            entry_point_options=[
                {
                    "label": "这个领域是什么",
                    "why_it_matters": "帮助外行先建立方向感。",
                    "best_for": "完全不了解的人。",
                },
                {
                    "label": "基础概念",
                    "why_it_matters": "最容易形成第一块可学习内容。",
                    "best_for": "零基础纯新手。",
                },
            ],
            recommended_entry_point="基础概念",
            reason_for_recommendation="它最基础，最适合零基础建立第一块理解。",
            learning_need_checklist=[
                "需要选择具体子方向",
                "需要了解学习目的或应用场景",
                "用户想学的内容",
            ],
            checklist=[
                {
                    "title": "当前水平已知",
                    "is_clear": True,
                    "evidence": "用户说纯新手入门。",
                },
                {
                    "title": "学习目的或场景已知",
                    "is_clear": False,
                    "evidence": "",
                },
            ],
            next_question="我们先从这个入口开始，可以吗？",
            ready_for_board=False,
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

    assert second_response.requirement_run_id == first_response.requirement_run_id
    assert second_response.active_requirement_sheet is not None
    assert second_response.active_requirement_sheet.level == "零基础纯新手"
    assert second_response.active_requirement_sheet.target_depth == "入门了解 / 建立领域地图"
    assert second_response.active_requirement_sheet.success_criteria == "理解领域组成，并确定第一个可学习入口"
    assert "需要了解学习目的或应用场景" not in second_response.active_requirement_sheet.learning_need_checklist
    assert "应用场景" not in second_response.learning_clarification.missing_items
    assert all("场景" not in item.title for item in second_response.learning_clarification.checklist)
    assert second_response.active_requirement_sheet.current_questions == ["我们先从这个入口开始，可以吗？"]
    assert "为了什么" not in second_response.chatbot_message
    assert "应用场景" not in second_response.chatbot_message
    assert "我们先从这个入口开始，可以吗？" in second_response.chatbot_message
    assert len(store.list_learning_requirement_versions(user_id, lesson.id)) == 2


def test_pure_novice_intro_external_goal_question_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_blank_pure_novice_intro_repair"
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
                    "你是纯新手入门，我们先把地图铺开："
                    "\n1. **整体认识**：先知道这个领域大概在研究什么。"
                    "\n2. **核心组成**：再看它通常由哪些部分组成。"
                    "\n3. **基础入口**：最后选一个最小入口开始。"
                    "\n\n我推荐先从**基础入口**开始，因为它最适合零基础建立第一块理解。"
                    "你学这个是为了考试、工作还是赚钱？"
                ),
                progress=65,
                summary="用户零基础纯新手，想入门一个领域。",
                work_mode="knowledge_board",
                granularity="broad_topic",
                learning_goal="一个领域入门",
                current_level="零基础纯新手",
                known_background="用户明确表示纯新手入门。",
                guidance_strategy="domain_map",
                learning_map_summary="纯新手先看整体认识、核心组成和基础入口。",
                entry_point_options=[
                    {
                        "label": "整体认识",
                        "why_it_matters": "帮助用户建立方向感。",
                        "best_for": "完全不了解的人。",
                    },
                    {
                        "label": "基础入口",
                        "why_it_matters": "最容易形成第一块理解。",
                        "best_for": "零基础纯新手。",
                    },
                ],
                recommended_entry_point="基础入口",
                reason_for_recommendation="它最适合零基础建立第一块理解。",
                next_question="你学这个是为了考试、工作还是赚钱？",
                ready_for_board=False,
            )
        return BlankBoardRequirementRefinement(
            route="requirement_refining",
            chatbot_message=(
                "你是纯新手入门，我们先把地图铺开："
                "\n1. **整体认识**：先知道这个领域大概在研究什么。"
                "\n2. **核心组成**：再看它通常由哪些部分组成。"
                "\n3. **基础入口**：最后选一个最小入口开始。"
                "\n\n我推荐先从**基础入口**开始，因为它最适合零基础建立第一块理解。"
                "我们先从这个入口开始，可以吗？"
            ),
            guidance_strategy="domain_map",
            learning_map_summary="纯新手先看整体认识、核心组成和基础入口。",
            entry_point_options=[
                {
                    "label": "整体认识",
                    "why_it_matters": "帮助用户建立方向感。",
                    "best_for": "完全不了解的人。",
                },
                {
                    "label": "基础入口",
                    "why_it_matters": "最容易形成第一块理解。",
                    "best_for": "零基础纯新手。",
                },
            ],
            recommended_entry_point="基础入口",
            reason_for_recommendation="它最适合零基础建立第一块理解。",
            next_question="我们先从这个入口开始，可以吗？",
        )

    monkeypatch.setattr(openai_course_ai, "generate_blank_board_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我纯新手入门，先了解一下。"),
        user_id=user_id,
    )

    assert len(calls) == 2
    assert calls[1]["quality_repair_context"] is not None
    assert "纯新手入门场景" in calls[1]["quality_repair_context"]["repair_reason"]
    assert "考试" not in response.chatbot_message
    assert "工作" not in response.chatbot_message
    assert "赚钱" not in response.chatbot_message
    assert response.active_requirement_sheet is not None
    assert response.active_requirement_sheet.level == "零基础纯新手"
    commit = store.load_for_user(user_id).packages[0].lessons[0].history_graph.commits[-1]
    discovery = commit.metadata["guided_requirement_discovery"]
    assert discovery["quality_repaired"] is True
    assert any("纯新手入门场景" in issue for issue in discovery["quality_issues"])


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
    assert discovery["quality_repaired"] is False
    assert discovery["quality_issues"] == []


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
