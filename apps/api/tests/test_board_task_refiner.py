import json

import pytest

from app.models import BoardDecision, BoardTaskRequirementSheet, ChatRequest, ConversationTurn, SelectionRef
from app.services import workspace_state
from app.services import board_task_executor
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.board_explanation_gate import BoardDirectedExplanationResult
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import BoardTaskRequirementRefinement, openai_course_ai
from app.services.board_task_refiner import _board_summary
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
    assert "本阶段只允许 explain、write 或 edit" in system_prompt
    payload = json.loads(str(captured["user_prompt"]))
    assert payload["response_contract"]["board_task_sheet"]["location_kind"] == "target_range、insertion_anchor 或 unspecified。"
    assert payload["response_contract"]["board_task_sheet"]["requested_action"] == "只允许 explain、write、edit 或 null。"


def _seed_existing_board_workspace(store: SqliteCourseStore, user_id: str, *, content_text: str | None = None):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("已有板书页")
    lesson.board_document = build_document(
        title="已有板书页",
        content_text=content_text or "# 已有板书\n\n## 第一节\n\n这里已经有一段学习内容。",
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


def test_board_summary_includes_outline_titles_beyond_prefix_limit() -> None:
    lesson = create_empty_lesson("长板书")
    long_prefix = "\n\n".join(f"普通段落 {index} " + ("内容" * 30) for index in range(40))
    lesson.board_document = build_document(
        title="长板书",
        content_text=f"# 长板书\n\n## 1.1 开始\n\n{long_prefix}\n\n## 1.4 极限的运算法则\n\n后半段内容。",
        document_id=lesson.board_document.id,
        page_settings=lesson.board_document.page_settings,
    )

    summary = _board_summary(lesson, limit=2200)

    assert "板书结构目录" in summary
    assert "1.4 极限的运算法则" in summary


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


def test_existing_board_explain_ready_executes_and_consumes_board_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_task_explain"
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
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_board_task_route_decision", lambda **kwargs: None)

    def _fake_directed_explanation(**kwargs):
        assert "这里已经有一段学习内容" in kwargs["target_excerpt"]
        return BoardDirectedExplanationResult(
            chatbot_message="这是第一节的讲解。",
            assistant_message_source="chatbot_board_directed",
            directive_payload={
                "status": "approved",
                "target_summary": "第一节",
                "target_excerpt": kwargs["target_excerpt"],
                "teaching_instruction": "只讲目标片段。",
            },
        )

    monkeypatch.setattr(
        board_task_executor,
        "generate_board_directed_explanation_message",
        _fake_directed_explanation,
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="讲第一节，按初学者角度讲清楚。"),
        user_id=user_id,
    )

    assert response.board_decision.action == "no_change"
    assert response.chatbot_message == "这是第一节的讲解。"
    assert response.active_board_task_sheet is None
    assert response.resolved_focus is not None
    assert "第一节" in response.resolved_focus.excerpt
    assert response.board_task_run_id
    assert response.board_task_version_id
    assert response.board_task_phase == "consumed"
    versions = store.list_board_task_versions(user_id, lesson.id)
    assert len(versions) == 1
    stored_sheet = json.loads(versions[0]["sheet_json"])
    assert stored_sheet["board_workflow"] == "act_on_existing_board"
    assert stored_sheet["location_kind"] == "target_range"
    assert stored_sheet["requested_action"] == "explain"
    assert versions[0]["status"] == "ready"
    events = store.list_board_task_events(user_id, lesson.id)
    assert events[-1]["event_type"] == "consumed"
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    assert saved_lesson.board_task_requirements is None
    assert saved_lesson.learning_requirements is None
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["board_task_route"] == "explain"
    assert commit.metadata["board_task_cleared"] is True
    assert commit.metadata["assistant_message_source"] == "chatbot_board_directed"
    assert commit.metadata["board_explanation_directive"]["status"] == "approved"
    assert commit.metadata["document_changed"] is False


def test_existing_board_task_with_clarification_does_not_execute_same_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_task_clarify"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="你想让我从哪个角度讲第一节？",
            board_task_sheet=BoardTaskRequirementSheet(
                location_kind="target_range",
                target_hint="第一节",
                requested_action="explain",
                question_or_topic="讲解主题待定",
                missing_items=["具体讲解主题"],
                clarification_question="你想让我从哪个角度讲第一节？",
                progress=100,
            ),
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(
        board_task_executor,
        "execute_ready_board_task",
        lambda **kwargs: pytest.fail("Unresolved clarification must not execute."),
        raising=False,
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="讲第一节。"),
        user_id=user_id,
    )

    assert response.chatbot_message == "你想让我从哪个角度讲第一节？"
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.progress < 100
    assert "澄清问题" in response.active_board_task_sheet.missing_items
    assert "具体讲解主题" in response.active_board_task_sheet.missing_items
    assert response.board_task_phase == "collecting"
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "board_task_requirement_refinement"
    assert commit.metadata["board_task_sheet"]["location_kind"] == "target_range"
    assert commit.metadata["board_task_history_changed"] is True
    assert commit.metadata["document_changed"] is False


def test_existing_board_write_ready_generates_patch_and_consumes_board_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_task_write"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="我会在第一节附近补写说明。",
            board_task_sheet=BoardTaskRequirementSheet(
                location_kind="insertion_anchor",
                target_hint="第一节",
                requested_action="write",
                question_or_topic="补写一个小结",
                progress=100,
            ),
        )

    def _fake_edit_existing_document(**kwargs):
        assert kwargs["focus"] is not None
        assert kwargs["target_scope"] == "focus"
        new_document = build_document(
            title=kwargs["lesson"].board_document.title,
            content_text=kwargs["lesson"].board_document.content_text + "\n\n补写的小结。",
            document_id=kwargs["lesson"].board_document.id,
            page_settings=kwargs["lesson"].board_document.page_settings,
        )
        return BoardDocumentEditOutcome(
            chatbot_message="已在第一节附近补写小结。",
            new_document=new_document,
            board_decision=BoardDecision(action="edit_board", reason="补写完成。"),
            assistant_message_source="board_document_editor_ai",
            operation="board_patch",
            summary="补写小结。",
            section_titles=["第一节"],
            changed=True,
            operation_status="succeeded",
            operations=[],
            diff_preview=[],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_board_task_route_decision", lambda **kwargs: None)
    monkeypatch.setattr(board_task_executor, "edit_existing_document", _fake_edit_existing_document)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="在第一节后面补写一个小结。"),
        user_id=user_id,
    )

    assert response.chatbot_message == "已在第一节附近补写小结。"
    assert response.board_decision.action == "edit_board"
    assert response.board_document_operation_status == "succeeded"
    assert response.active_board_task_sheet is None
    assert response.board_task_phase == "consumed"
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    assert "补写的小结" in saved_lesson.board_document.content_text
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "board_document_edit"
    assert commit.metadata["board_task_route"] == "write"
    assert commit.metadata["board_task_cleared"] is True


def test_existing_board_absent_explain_asks_write_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_task_absent"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="我先定位板书中有没有这部分。",
            board_task_sheet=BoardTaskRequirementSheet(
                location_kind="target_range",
                target_hint="全新缺失主题",
                requested_action="explain",
                question_or_topic="全新缺失主题",
                progress=100,
            ),
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)
    monkeypatch.setattr(openai_course_ai, "generate_board_search_rerank", lambda **kwargs: None)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_route_decision",
        lambda **kwargs: pytest.fail("content_absent explain should be gated before model route decision."),
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="讲讲全新缺失主题。"),
        user_id=user_id,
    )

    assert "还没有定位到对应内容" in response.chatbot_message
    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.confirmation_status == "awaiting"
    assert response.active_board_task_sheet.location_status == "content_absent"
    assert response.active_board_task_sheet.requested_action == "write"
    assert response.board_task_phase == "awaiting_confirmation"
    saved_lesson = store.load_for_user(user_id).packages[0].lessons[0]
    assert "全新缺失主题" not in saved_lesson.board_document.content_text
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["board_task_route"] == "await_write_confirmation"
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
                clarification_question="你希望我从哪个角度讲这段？",
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
    assert response.active_board_task_sheet.progress < 100


def test_board_caret_quote_marks_insertion_anchor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = "user_existing_board_insertion_anchor"
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_existing_board_workspace(store, user_id)

    def _fake_refinement(**kwargs):
        assert "第一节" in kwargs["selection_excerpt"]
        return BoardTaskRequirementRefinement(
            route="board_task_refining",
            chatbot_message="我已经把光标位置标为插入锚点。",
            board_task_sheet=BoardTaskRequirementSheet(
                requested_action="write",
                question_or_topic="在这里补充一个说明",
                clarification_question="你希望补充成小结还是示例？",
                progress=66,
            ),
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_task_requirement_refinement", _fake_refinement)

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(
            message="在这里补充一个说明。",
            selection=SelectionRef(
                kind="board",
                excerpt="第一节｜这里已经有一段学习内容。",
                location_kind="insertion_anchor",
                lesson_id=lesson.id,
                document_id=lesson.board_document.id,
                before_text="第一节",
                after_text="这里已经有一段学习内容。",
            ),
        ),
        user_id=user_id,
    )

    assert response.active_board_task_sheet is not None
    assert response.active_board_task_sheet.location_kind == "insertion_anchor"
    assert response.active_board_task_sheet.target_hint == "第一节｜这里已经有一段学习内容。"
    assert response.active_board_task_sheet.target_location is not None
    assert response.active_board_task_sheet.target_location.display_label == "InsertionAnchor"
    assert response.active_board_task_sheet.progress < 100
