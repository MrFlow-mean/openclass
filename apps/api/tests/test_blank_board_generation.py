import pytest

from app.models import BoardDecision, ChatRequest, LearningClarificationStatus
from app.services import blank_board_generation, board_teaching, workspace_state
from app.services.board_document_editor import BoardDocumentEditOutcome
from app.services.board_explanation_gate import BoardDirectedExplanationResult
from app.services.chat_service import process_chat_on_lesson
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.openai_course_ai import ChatbotReply, openai_course_ai
from app.services.rich_document import build_document


TEST_USER_ID = "user_blank_board_generation"


def _seed_ready_blank_board(store: SqliteCourseStore) -> str:
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("极限与连续")
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id

    requirements = build_requirements(lesson.title)
    requirements.learning_goal = "从零理解极限与连续的第一课"
    requirements.level = "高中毕业，基础扎实"
    requirements.known_background = "已经学过基本函数图像"
    requirements.target_depth = "能解释极限直觉并识别连续的基本条件"
    requirements.work_mode = "knowledge_board"
    requirements.granularity = "single_knowledge_point"
    lesson.learning_requirements = requirements
    clarification = LearningClarificationStatus(
        progress=100,
        label="ready",
        reason="学习需求已清晰。",
        missing_items=[],
        can_start=True,
        forced_start=False,
        summary="高中毕业生从极限与连续开始学。",
        next_question="",
        ready_for_board=True,
        work_mode="knowledge_board",
        granularity="single_knowledge_point",
    )
    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=None,
    )
    recorder.record_update(requirements=requirements, clarification=clarification)
    store.save_for_user_with_learning_requirement_history(
        TEST_USER_ID,
        workspace,
        learning_requirement_history_operations=recorder.operations,
    )
    return lesson.id


def test_board_generation_action_generates_blank_board_and_invites_teaching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_ready_blank_board(store)
    captured: dict[str, object] = {}

    def _fake_generate_from_requirements(**kwargs):
        captured.update(kwargs)
        lesson = kwargs["lesson"]
        new_document = build_document(
            title="极限与连续第一课",
            content_text=(
                "# 极限与连续第一课\n\n"
                "## 1. 极限要解决什么问题\n\n"
                "极限先描述输入逼近时输出的趋势。\n\n"
                "### 1.1 观察入口\n\n"
                "用函数图像观察靠近某个点时的变化。\n\n"
                "## 2. 连续的直觉\n\n"
                "连续可以先理解为图像不断开。"
            ),
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        return BoardDocumentEditOutcome(
            chatbot_message="模型给出的承接话不应进入聊天框。",
            new_document=new_document,
            board_decision=BoardDecision(action="edit_board", reason="已生成空白板书。"),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="生成极限与连续第一课板书。",
            section_titles=["1. 极限要解决什么问题", "2. 连续的直觉"],
            changed=True,
            operation_status="succeeded",
        )

    monkeypatch.setattr(blank_board_generation, "generate_from_requirements", _fake_generate_from_requirements)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经生成好了。要不要从开头开始讲？"),
    )

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "板书已经生成好了。要不要从开头开始讲？"
    assert response.board_document_operation_status == "succeeded"
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.teaching_progress is None
    assert captured["requirement_run_id"] is not None
    assert captured["frozen_requirement_version_id"] is not None
    assert captured["resource_summary"] == ""

    saved = store.load_for_user(TEST_USER_ID)
    saved_lesson = saved.packages[0].lessons[-1]
    assert saved_lesson.learning_requirements is None
    assert saved_lesson.board_teaching_guide is not None
    assert saved_lesson.board_teaching_guide.teaching_flow == [
        "1. 极限要解决什么问题",
        "2. 连续的直觉",
    ]
    assert saved_lesson.board_teaching_progress is None
    assert "# 极限与连续第一课" in saved_lesson.board_document.content_text
    assert "## 1. 极限要解决什么问题" in saved_lesson.board_document.content_text
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.label == "Board document generation"
    assert commit.metadata["kind"] == "board_document_generation"
    assert commit.metadata["board_generation_action"] == "start"
    assert commit.metadata["assistant_message"] == "板书已经生成好了。要不要从开头开始讲？"
    assert commit.metadata["assistant_message_source"] == "chatbot_post_board_generation"
    assert commit.metadata["document_changed"] is True
    assert commit.metadata["active_requirement_sheet_after"] is None
    assert commit.metadata["board_teaching_flow"] == [
        "1. 极限要解决什么问题",
        "2. 连续的直觉",
    ]

    history_state = store.load_learning_requirement_history_state(TEST_USER_ID, lesson_id)
    assert history_state is None


def test_confirm_after_board_generation_teaches_from_first_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_ready_blank_board(store)

    def _fake_generate_from_requirements(**kwargs):
        lesson = kwargs["lesson"]
        new_document = build_document(
            title="极限与连续第一课",
            content_text=(
                "# 极限与连续第一课\n\n"
                "## 1. 极限要解决什么问题\n\n"
                "极限先描述输入逼近时输出的趋势。\n\n"
                "## 2. 连续的直觉\n\n"
                "连续可以先理解为图像不断开。"
            ),
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        return BoardDocumentEditOutcome(
            chatbot_message="",
            new_document=new_document,
            board_decision=BoardDecision(action="edit_board", reason="已生成空白板书。"),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="生成板书。",
            section_titles=["1. 极限要解决什么问题", "2. 连续的直觉"],
            changed=True,
            operation_status="succeeded",
        )

    monkeypatch.setattr(blank_board_generation, "generate_from_requirements", _fake_generate_from_requirements)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经生成好了。要不要从开头开始讲？"),
    )

    generation_response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )
    assert generation_response.chatbot_message

    def _fake_directed_explanation(**kwargs):
        assert kwargs["action_type"] == "teach_section"
        assert "极限要解决什么问题" in kwargs["target_excerpt"]
        return BoardDirectedExplanationResult(
            chatbot_message="第一节讲解内容。",
            assistant_message_source="chatbot_board_directed",
            directive_payload={
                "status": "approved",
                "target_summary": "第一节",
                "target_excerpt": kwargs["target_excerpt"],
            },
        )

    monkeypatch.setattr(board_teaching, "generate_board_directed_explanation_message", _fake_directed_explanation)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_task_requirement_refinement",
        lambda **kwargs: pytest.fail("Teaching confirmation should not enter board task refinement."),
    )

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="好"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "第一节讲解内容。"
    assert response.teaching_progress is not None
    assert response.teaching_progress.section_index == 0
    assert response.teaching_progress.current_section_title == "1. 极限要解决什么问题"
    assert response.teaching_progress.has_next_section is True
    assert response.teaching_progress.waiting_for_continue is True
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    assert saved_lesson.board_teaching_progress is not None
    assert saved_lesson.board_teaching_progress.current_section_index == 0
    commit = saved_lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "board_section_teaching"
    assert commit.metadata["teaching_action"] == "restart"
    assert commit.metadata["document_changed"] is False


def test_continue_after_first_section_teaches_next_section(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_ready_blank_board(store)

    def _fake_generate_from_requirements(**kwargs):
        lesson = kwargs["lesson"]
        new_document = build_document(
            title="极限与连续第一课",
            content_text=(
                "# 极限与连续第一课\n\n"
                "## 1. 极限要解决什么问题\n\n"
                "极限先描述输入逼近时输出的趋势。\n\n"
                "## 2. 连续的直觉\n\n"
                "连续可以先理解为图像不断开。"
            ),
            document_id=lesson.board_document.id,
            page_settings=lesson.board_document.page_settings,
        )
        return BoardDocumentEditOutcome(
            chatbot_message="",
            new_document=new_document,
            board_decision=BoardDecision(action="edit_board", reason="已生成空白板书。"),
            assistant_message_source="board_document_editor_ai",
            operation="replace_document",
            summary="生成板书。",
            section_titles=["1. 极限要解决什么问题", "2. 连续的直觉"],
            changed=True,
            operation_status="succeeded",
        )

    monkeypatch.setattr(blank_board_generation, "generate_from_requirements", _fake_generate_from_requirements)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经生成好了。要不要从开头开始讲？"),
    )
    process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    def _fake_directed_explanation(**kwargs):
        if "连续的直觉" in kwargs["target_excerpt"]:
            message = "第二节讲解内容。"
        else:
            message = "第一节讲解内容。"
        return BoardDirectedExplanationResult(
            chatbot_message=message,
            assistant_message_source="chatbot_board_directed",
            directive_payload={"status": "approved", "target_excerpt": kwargs["target_excerpt"]},
        )

    monkeypatch.setattr(board_teaching, "generate_board_directed_explanation_message", _fake_directed_explanation)
    process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="好"),
        user_id=TEST_USER_ID,
    )

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="继续"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "第二节讲解内容。"
    assert response.teaching_progress is not None
    assert response.teaching_progress.section_index == 1
    assert response.teaching_progress.current_section_title == "2. 连续的直觉"
    assert response.teaching_progress.has_next_section is False
    assert response.teaching_progress.waiting_for_continue is False


def test_board_generation_action_requires_ready_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("未清晰需求")
    workspace.packages[0].lessons.append(lesson)
    workspace.packages[0].open_lesson_ids.append(lesson.id)
    workspace.packages[0].workspace_tab_order.append(lesson.id)
    workspace.packages[0].active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)

    def _unexpected_generate_from_requirements(**kwargs):
        raise AssertionError("BoardEditor should not run before requirements are ready")

    monkeypatch.setattr(
        blank_board_generation,
        "generate_from_requirements",
        _unexpected_generate_from_requirements,
    )

    response = process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == ""
    assert response.board_document_operation_status == "failed"
    assert "学习需求尚未清晰" in (response.board_document_operation_failure_reason or "")
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    assert saved_lesson.board_document.content_text == ""


def test_board_generation_action_does_not_overwrite_non_empty_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson_id = _seed_ready_blank_board(store)
    workspace = store.load_for_user(TEST_USER_ID)
    lesson = workspace.packages[0].lessons[-1]
    lesson.board_document.content_text = "# 已有板书\n\n不要覆盖。"
    store.save_for_user(TEST_USER_ID, workspace)

    def _unexpected_generate_from_requirements(**kwargs):
        raise AssertionError("BoardEditor should not overwrite a non-empty board")

    monkeypatch.setattr(
        blank_board_generation,
        "generate_from_requirements",
        _unexpected_generate_from_requirements,
    )

    response = process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="开始生成板书", board_generation_action="start"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == ""
    assert response.board_document_operation_status == "failed"
    assert "不是空白文档" in (response.board_document_operation_failure_reason or "")
    saved_lesson = store.load_for_user(TEST_USER_ID).packages[0].lessons[-1]
    assert saved_lesson.board_document.content_text == "# 已有板书\n\n不要覆盖。"
