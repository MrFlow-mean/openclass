import json

import pytest

from app.models import ChatRequest, ConversationTurn, SelectionRef, SourceIngestionRecord
from app.services import workspace_state
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import BoardTaskRequirementRefinement, ChatbotReply, OpenAICourseAI, openai_course_ai
from app.services.resource_resolver import resource_resolver
from app.services.source_evidence_store import SourceEvidenceStore
from app.services.source_structure_indexer import SourceStructureIndexer
from app.services.source_structure_store import SourceStructureStore


TEST_USER_ID = "user_basic_chat"


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("基础聊天页")
    lesson.board_document.content_text = "这段右侧文档不应该被聊天修改。"
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


def test_basic_chat_prompt_is_chatgpt_like_without_board_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
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
        board_document_state={"status": "empty", "has_content": False},
        conversation_summary="user: 你好",
        user_message="帮我解释一下这个概念",
    )

    assert result == ChatbotReply(chatbot_message="可以，我们就正常聊天。")
    assert captured["role"] == "chatbot"
    assert "像 ChatGPT" in captured["system_prompt"]
    assert "板书正文" in captured["system_prompt"]
    assert "directive" not in captured["system_prompt"]
    payload = json.loads(captured["user_prompt"])
    assert payload["board_document_state"] == {"status": "empty", "has_content": False}
    assert payload["recent_conversation"] == "user: 你好"
    assert payload["user_message"] == "帮我解释一下这个概念"
    assert "lesson_title" not in payload
    assert payload["resource_summary"] == "无"


def test_process_chat_on_lesson_records_basic_chat_without_document_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store)
    captured: dict[str, object] = {}

    def _fake_refinement(**kwargs):
        captured.update(kwargs)
        return BoardTaskRequirementRefinement(route="ordinary_chat", chatbot_message="这是一个普通聊天回答。")

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_basic_chat_reply",
        lambda **kwargs: pytest.fail("non-empty board should be classified by board task refinement first"),
    )

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
        "lesson_title": "基础聊天页",
        "existing_task": None,
        "board_document_state": {
            "status": "non_empty",
            "has_content": True,
            "reason": "当前右侧板书文档已有可见内容。",
        },
        "board_summary": "基础聊天页\n\n这段右侧文档不应该被聊天修改。",
        "conversation_summary": "user: 你好",
        "user_message": "你现在能正常问答吗？",
        "selection_excerpt": None,
        "include_stream_result": True,
    }
    saved = store.load_for_user(TEST_USER_ID)
    saved_lesson = saved.packages[0].lessons[0]
    assert saved_lesson.board_document.content_text == "这段右侧文档不应该被聊天修改。"
    assert saved_lesson.learning_requirements is None
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.label == "Basic chat"
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["basic_chat_only"] is True
    assert commit.metadata["board_task_refinement_route"] == "ordinary_chat"
    assert commit.metadata["board_document_state"] == {
        "status": "non_empty",
        "has_content": True,
        "reason": "当前右侧板书文档已有可见内容。",
    }
    assert response.agent_turn_decision is not None
    assert response.agent_turn_decision.route == "ordinary_chat"
    assert [event.stage for event in response.agent_activity] == [
        "turn_decision",
        "build_context",
        "execute_role",
        "verify",
        "persist_history",
        "final",
    ]
    assert commit.metadata["agent_turn_decision"]["route"] == "ordinary_chat"
    assert [event["stage"] for event in commit.metadata["agent_activity"]] == [
        "turn_decision",
        "build_context",
        "execute_role",
        "verify",
        "persist_history",
        "final",
    ]
    assert commit.metadata["document_changed"] is False


def test_structured_source_reference_grounds_basic_chat_without_visible_locator_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "openclass.sqlite3"
    store = SqliteCourseStore(database_path, legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store)
    package_id = store.load_for_user(TEST_USER_ID).packages[0].id
    markdown_path = tmp_path / "reference.md"
    markdown_path.write_text("# 2.1 Core Section\n\nExact grounded source body.", encoding="utf-8")
    source_store = SourceEvidenceStore(database_path)
    structure_store = SourceStructureStore(database_path)
    source = SourceIngestionRecord(
        id="source_structured_reference",
        owner_user_id=TEST_USER_ID,
        package_id=package_id,
        title="Reference",
        source_type="local_file",
        file_name="reference.md",
        mime_type="text/markdown",
        size_bytes=markdown_path.stat().st_size,
        status="ready",
        metadata={"local_source_path": str(markdown_path)},
    )
    source_store.save_source(source)
    SourceStructureIndexer(store=structure_store).rebuild_structure(source)
    structure_view = structure_store.get_structure_view(source=source)
    assert structure_view is not None
    chapter = structure_view.chapters[0]
    monkeypatch.setattr(resource_resolver, "store", source_store)
    monkeypatch.setattr(resource_resolver, "structure_store", structure_store)
    captured: dict[str, object] = {}

    def _fake_refinement(**kwargs):
        captured["refinement"] = kwargs
        return BoardTaskRequirementRefinement(route="ordinary_chat", chatbot_message="不应直接使用这段未取证回复。")

    def _fake_basic_reply(**kwargs):
        captured["basic_reply"] = kwargs
        return ChatbotReply(chatbot_message="这是基于指定章节的回答。")

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_basic_chat_reply", _fake_basic_reply)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="请讲解这一章。",
            selection=SelectionRef(
                kind="source",
                excerpt="《Reference》 · 2.1 Core Section",
                heading_path=chapter.path,
                source_ingestion_id=source.id,
                source_title=source.title,
                source_chapter_id=chapter.id,
                source_chapter_number=chapter.number,
                source_chapter_title=chapter.title,
            ),
        ),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "这是基于指定章节的回答。"
    assert response.candidate_evidence_bundle is not None
    assert response.candidate_evidence_bundle.evidence_items[0].chapter_id == chapter.id
    assert "Exact grounded source body" in captured["basic_reply"]["resource_summary"]
    assert captured["basic_reply"]["user_message"] == "请讲解这一章。"
    assert captured["refinement"]["selection_excerpt"] is None
    assert "《Reference》" in captured["refinement"]["user_message"]
    commit = store.load_for_user(TEST_USER_ID).packages[0].lessons[0].history_graph.commits[-1]
    assert commit.metadata["user_message"] == "请讲解这一章。"
    assert commit.metadata["selection"]["kind"] == "source"
    assert commit.metadata["selection"]["source_chapter_id"] == chapter.id


def test_non_empty_basic_chat_clears_legacy_learning_requirement_sheet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_workspace(store)
    workspace = store.load_for_user(TEST_USER_ID)
    saved_lesson = workspace.packages[0].lessons[0]
    requirements = build_requirements(saved_lesson.title)
    requirements.learning_goal = "以太坊开发由哪几部分组成"
    requirements.work_mode = "knowledge_board"
    requirements.granularity = "single_knowledge_point"
    saved_lesson.learning_requirements = requirements
    store.save_for_user(TEST_USER_ID, workspace)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_refinement",
        lambda **kwargs: BoardTaskRequirementRefinement(route="ordinary_chat", chatbot_message="继续正常聊天。"),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="这页先聊一下。"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "继续正常聊天。"
    assert response.active_requirement_sheet is None
    assert response.requirement_cleared is True
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert saved_lesson.learning_requirements is None
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "basic_chat"
    assert commit.metadata["active_requirement_sheet_after"] is None
