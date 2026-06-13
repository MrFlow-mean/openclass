import json

import pytest

from app.models import (
    ChatRequest,
    LearningClarificationStatus,
    LearningRequirementChecklistItem,
    LearningRequirementKeyFact,
)
from app.routers.chat import _chat_stream_events
from app.services import chat_service, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.learning_requirement_history import LearningRequirementHistoryRecorder
from app.services.lesson_factory import build_requirements, create_empty_lesson
from app.services.initial_learning_intent import InitialLearningIntentDecision
from app.services.openai_course_ai import (
    BoardDocumentEditResult,
    ChatbotReply,
    LearningRequirementUpdate,
    emit_ai_stream_event,
    openai_course_ai,
)
from app.services.rich_document import build_document


TEST_USER_ID = "user_requirement_history"


@pytest.fixture(autouse=True)
def disable_default_post_board_generation_reply(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(openai_course_ai, "generate_post_board_generation_reply", lambda **kwargs: None)


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("空白学习页")
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return workspace, lesson


def _clarification(*, ready: bool, forced: bool = False) -> LearningClarificationStatus:
    return LearningClarificationStatus(
        progress=100 if ready else 45,
        label="需求已清晰" if ready else "继续澄清",
        reason="已记录用户当前学习需求。",
        missing_items=[] if ready else ["学习目标还不完整"],
        can_start=ready,
        forced_start=forced,
        summary="已记录用户当前学习需求。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="用户想学习一个通用主题。",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        next_question="" if ready else "你希望达到什么程度？",
        ready_for_board=ready,
    )


def _requirement_update(*, ready: bool, action_type: str | None = None) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100 if ready else 45,
        summary="用户想学习一个通用主题。",
        key_facts=[
            LearningRequirementKeyFact(
                label="学习内容",
                value="一个通用主题",
                evidence="来自用户输入。",
                category="learning",
            )
        ],
        checklist=[
            LearningRequirementChecklistItem(
                title="明确学习内容",
                is_clear=True,
                evidence="来自用户输入。",
            )
        ],
        missing_items=[] if ready else ["学习目标还不完整"],
        next_question="" if ready else "你希望达到什么程度？",
        ready_for_board=ready,
        action_type=action_type,
        action_instruction="生成第一版板书" if action_type == "generate_board" else "",
    )


def _history_kinds(store: SqliteCourseStore, lesson_id: str) -> tuple[list[str], list[str]]:
    versions = store.list_learning_requirement_versions(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
    )
    events = store.list_learning_requirement_events(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson_id,
    )
    return [row["change_kind"] for row in versions], [row["event_type"] for row in events]


def _sse_event_name(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("event:"):
            return line.split(":", 1)[1].strip()
    return ""


def _sse_payload(block: str) -> dict[str, object]:
    lines = [line.split(":", 1)[1].lstrip() for line in block.splitlines() if line.startswith("data:")]
    return json.loads("\n".join(lines))


def test_requirement_history_records_changed_versions_and_events(tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    workspace, lesson = _seed_workspace(store)
    requirements = build_requirements("通用主题")
    collecting = _clarification(ready=False)

    recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        ),
    )
    first_stamp = recorder.record_update(requirements=requirements, clarification=collecting)
    store.save_for_user_with_requirement_history(TEST_USER_ID, workspace, recorder.operations)

    same_recorder = LearningRequirementHistoryRecorder.from_store_state(
        owner_user_id=TEST_USER_ID,
        lesson_id=lesson.id,
        state=store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        ),
    )
    same_recorder.record_update(requirements=requirements, clarification=collecting)
    assert same_recorder.operations == []

    requirements.level = "已有基础"
    updated = _clarification(ready=False)
    ready = _clarification(ready=True)
    same_recorder.record_update(requirements=requirements, clarification=updated)
    same_recorder.record_update(requirements=requirements, clarification=ready)
    frozen_stamp = same_recorder.freeze(
        requirements=requirements,
        clarification=ready,
        forced=False,
    )
    consumed_stamp = same_recorder.consume(commit_id="commit_test")
    store.save_for_user_with_requirement_history(TEST_USER_ID, workspace, same_recorder.operations)

    version_kinds, event_kinds = _history_kinds(store, lesson.id)
    assert first_stamp.phase == "collecting"
    assert frozen_stamp.phase == "frozen"
    assert consumed_stamp.phase == "consumed"
    assert version_kinds == ["created", "updated", "completed", "frozen"]
    assert event_kinds == ["created", "updated", "completed", "frozen", "consumed"]


def test_blank_board_chat_collects_requirement_version_without_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我先确认你的目标和基础。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=False),
    )

    def _unexpected_board_edit(**kwargs):
        raise AssertionError("collecting requirement turns must not generate a board")

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _unexpected_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个主题"),
        user_id=TEST_USER_ID,
    )

    assert response.requirement_run_id is not None
    assert response.requirement_version_id is not None
    assert response.requirement_phase == "collecting"
    assert response.requirement_cleared is False
    assert response.course_package.lessons[0].board_document.content_text == ""
    assert _history_kinds(store, lesson.id) == (["created"], ["created"])


def test_existing_board_content_does_not_enter_initial_requirement_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace, lesson = _seed_workspace(store)
    lesson.board_document = build_document(title="已有板书", content_text="已有板书内容")
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    store.save_for_user(TEST_USER_ID, workspace)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我会围绕已有内容回答。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )

    def _unexpected_board_edit(**kwargs):
        raise AssertionError("existing board content is outside the initial blank-board chain")

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _unexpected_board_edit)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想继续聊这个内容"),
        user_id=TEST_USER_ID,
    )

    assert response.requirement_run_id is None
    assert response.course_package.lessons[0].board_document.content_text == "已有板书内容"
    assert _history_kinds(store, lesson.id) == ([], [])


def test_blank_board_specific_knowledge_goal_generates_from_minimal_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: InitialLearningIntentDecision(
            learning_mode="learn_concept",
            target_granularity="specific_concept",
            next_action="freeze_minimal_and_generate_board",
            trace_reason="用户给出了明确知识目标。",
        ),
    )

    def _unexpected_requirement_update(**kwargs):
        raise AssertionError("specific knowledge goals should not enter full requirement probing first")

    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _unexpected_requirement_update)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_document_edit",
        lambda **kwargs: BoardDocumentEditResult(
            operation="replace_document",
            title="第一版板书",
            content_text="# 第一版板书\n\n## 明确目标\n\n围绕用户目标组织第一版内容。",
            summary="已生成第一版板书。",
            chatbot_message="已生成第一版板书。",
            section_titles=["明确目标"],
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经就绪，要我按它从开头讲起吗？"),
    )
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="请解释一个明确概念"),
        user_id=TEST_USER_ID,
    )

    version_kinds, event_kinds = _history_kinds(store, lesson.id)
    commit = response.course_package.lessons[0].history_graph.commits[-1]
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert "第一版板书" in response.course_package.lessons[0].board_document.content_text
    assert version_kinds == ["completed", "frozen"]
    assert event_kinds == ["created", "completed", "frozen", "consumed"]
    assert commit.metadata["board_generation_action"] == "initial_learning_intent_gate"
    assert commit.metadata["initial_learning_intent"]["next_action"] == "freeze_minimal_and_generate_board"
    assert commit.metadata["initial_learning_intent"]["minimal_frozen_requirement"] is True
    assert commit.metadata["initial_learning_intent"]["board_editor_called"] is True


def test_blank_board_broad_learning_goal_asks_for_specific_concept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: InitialLearningIntentDecision(
            learning_mode="learn_concept",
            target_granularity="broad_domain",
            next_action="ask_specific_concept",
            trace_reason="用户给出了宽泛学习方向。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="你想先弄懂其中哪一个具体问题？"),
    )

    def _unexpected_requirement_update(**kwargs):
        raise AssertionError("broad knowledge goals should ask for a concrete target first")

    def _unexpected_board_edit(**kwargs):
        raise AssertionError("broad knowledge goals must not generate a default board")

    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _unexpected_requirement_update)
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _unexpected_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我想学一个宽泛方向"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[0].history_graph.commits[-1]
    assert response.chatbot_message == "你想先弄懂其中哪一个具体问题？"
    assert response.requirement_phase == "collecting"
    assert response.course_package.lessons[0].board_document.content_text == ""
    assert commit.metadata["initial_learning_intent"]["next_action"] == "ask_specific_concept"
    assert commit.metadata["initial_learning_intent"]["board_editor_called"] is False


def test_blank_board_practice_activity_uses_existing_requirement_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: InitialLearningIntentDecision(
            learning_mode="practice_activity",
            target_granularity="ambiguous",
            next_action="collect_practice_requirements",
            trace_reason="用户请求练习型教学。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我先了解你的练习目标和当前基础。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=False),
    )

    def _unexpected_board_edit(**kwargs):
        raise AssertionError("practice collection should not generate a normal lecture board")

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _unexpected_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="帮我做一组练习来巩固这部分内容"),
        user_id=TEST_USER_ID,
    )

    commit = response.course_package.lessons[0].history_graph.commits[-1]
    assert response.requirement_phase == "collecting"
    assert response.course_package.lessons[0].board_document.content_text == ""
    assert commit.metadata["initial_learning_intent"]["next_action"] == "collect_practice_requirements"
    assert commit.metadata["initial_learning_intent"]["board_editor_called"] is False


def test_blank_board_undecided_learning_mode_asks_mode_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_initial_learning_intent_decision",
        lambda **kwargs: InitialLearningIntentDecision(
            learning_mode="undecided",
            target_granularity="ambiguous",
            next_action="ask_learning_mode",
            trace_reason="用户尚未说明学习形态。",
        ),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="你是想学习一个知识内容，还是做练习型教学？"),
    )

    def _unexpected_requirement_update(**kwargs):
        raise AssertionError("undecided learning mode should be clarified before requirement collection")

    def _unexpected_board_edit(**kwargs):
        raise AssertionError("undecided learning mode must not generate a board")

    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _unexpected_requirement_update)
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _unexpected_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="先看看"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message == "你是想学习一个知识内容，还是做练习型教学？"
    assert response.requirement_phase == "collecting"
    assert response.course_package.lessons[0].board_document.content_text == ""


def test_existing_board_does_not_call_initial_learning_intent_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    workspace, lesson = _seed_workspace(store)
    lesson.board_document = build_document(title="已有板书", content_text="已有板书内容")
    lesson.history_graph.commits[-1].snapshot = lesson.board_document
    store.save_for_user(TEST_USER_ID, workspace)

    def _unexpected_gate(**kwargs):
        raise AssertionError("existing board turns must not enter the initial learning intent gate")

    monkeypatch.setattr(openai_course_ai, "generate_initial_learning_intent_decision", _unexpected_gate)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="我会围绕已有内容继续。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=False),
    )

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="继续聊聊"),
        user_id=TEST_USER_ID,
    )

    assert response.chatbot_message
    assert response.course_package.lessons[0].board_document.content_text == "已有板书内容"


def test_ready_blank_board_freezes_then_generates_and_consumes_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )

    def _fake_board_edit(**kwargs):
        captured["learning_requirement_context"] = kwargs["learning_requirement_context"]
        captured["user_instruction_present"] = "user_instruction" in kwargs
        captured["conversation_summary_present"] = "conversation_summary" in kwargs
        captured["state_before_board"] = store.load_learning_requirement_history_state(
            owner_user_id=TEST_USER_ID,
            lesson_id=lesson.id,
        )
        captured["history_before_board"] = _history_kinds(store, lesson.id)
        return BoardDocumentEditResult(
            operation="replace_document",
            title="第一版板书",
            content_text="# 第一版板书\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用板书。",
            summary="已生成第一版板书。",
            chatbot_message="已生成第一版板书。",
            section_titles=["起点"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_post_board_generation_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="板书已经就绪，要我按它从开头讲起吗？"),
    )
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="我已经说明目标、水平和输出形式"),
        user_id=TEST_USER_ID,
    )

    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    version_kinds, event_kinds = _history_kinds(store, lesson.id)
    commit = response.course_package.lessons[0].history_graph.commits[-1]
    assert response.requirement_phase == "consumed"
    assert response.requirement_cleared is True
    assert response.chatbot_message == "板书已经就绪，要我按它从开头讲起吗？"
    assert response.active_requirement_sheet is None
    assert "第一版板书" in response.course_package.lessons[0].board_document.content_text
    assert captured["learning_requirement_context"]["summary"] == "用户想学习一个通用主题。"
    assert captured["learning_requirement_context"]["requirement_run_id"] is not None
    assert captured["learning_requirement_context"]["frozen_requirement_version_id"] is not None
    assert captured["user_instruction_present"] is False
    assert captured["conversation_summary_present"] is False
    assert captured["state_before_board"]["status"] == "frozen"
    assert captured["history_before_board"] == (["completed", "frozen"], ["created", "completed", "frozen"])
    assert version_kinds == ["completed", "frozen"]
    assert event_kinds == ["created", "completed", "frozen", "consumed"]
    assert commit.metadata["board_generation_action"] == "ready_requirement_sheet"
    assert commit.metadata["assistant_message"] == response.chatbot_message
    assert commit.metadata["assistant_message_source"] == "chatbot_post_board_generation"
    assert commit.metadata["board_editor_message"] == "已生成第一版板书。"
    assert commit.metadata["requirement_run_id"] == response.requirement_run_id
    assert commit.metadata["frozen_requirement_version_id"] is not None
    assert commit.metadata["requirement_phase"] == "frozen"
    assert commit.metadata["frozen_requirement_phase"] == "frozen"
    assert commit.metadata["requirement_run_status_after_commit"] == "consumed"
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert "起点" not in commit.metadata["task_requirement_sheet"]["board_scope"]
    assert commit.metadata["task_requirement_sheet"]["action_instruction"].startswith("生成第一版板书；学习目标：")
    assert commit.metadata["task_requirement_sheet"]["action_instruction"] != "我已经说明目标、水平和输出形式"


def test_forced_generation_writes_forced_frozen_before_board_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=False),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_board_document_edit",
        lambda **kwargs: BoardDocumentEditResult(
            operation="replace_document",
            title="强制生成板书",
            content_text="# 强制生成板书\n\n## 当前已知\n\n基于当前已有信息生成。",
            summary="已按当前信息生成。",
            chatbot_message="已按当前信息生成。",
            section_titles=["当前已知"],
        ),
    )
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="别问了，直接生成板书"),
        user_id=TEST_USER_ID,
    )

    version_kinds, event_kinds = _history_kinds(store, lesson.id)
    assert response.requirement_phase == "consumed"
    assert "completed" not in version_kinds
    assert "forced_frozen" in version_kinds
    assert "forced_frozen" in event_kinds
    assert event_kinds[-1] == "consumed"


def test_generation_failure_keeps_frozen_requirement_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )
    board_calls = []

    def _failed_board_edit(**kwargs):
        board_calls.append(kwargs)
        return None

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _failed_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="目标已经完整"),
        user_id=TEST_USER_ID,
    )

    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    reloaded = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert len(board_calls) == 2
    assert response.requirement_phase == "frozen"
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "板书文档编辑 AI 没有返回生成结果。"
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    assert reloaded.board_document.content_text == ""
    assert reloaded.history_graph.commits[-1].metadata.get("kind") != "board_document_generation"
    frozen_sheet = json.loads(versions[-1]["sheet_json"])
    assert frozen_sheet["action_type"] == "generate_board"


def test_generation_retry_success_consumes_frozen_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )
    board_calls = []

    def _retrying_board_edit(**kwargs):
        board_calls.append(kwargs)
        if len(board_calls) == 1:
            return None
        return BoardDocumentEditResult(
            operation="replace_document",
            title="第一版板书",
            content_text="# 第一版板书\n\n## 核心内容\n\n这是重试后生成的板书。",
            summary="生成了第一版板书。",
            chatbot_message="板书已经生成。",
            section_titles=["核心内容"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _retrying_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="目标已经完整"),
        user_id=TEST_USER_ID,
    )

    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    reloaded = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert len(board_calls) == 2
    assert response.requirement_phase == "consumed"
    assert response.board_document_operation_status == "succeeded"
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert "重试后生成的板书" in reloaded.board_document.content_text


def test_initial_generation_retries_flat_long_document_before_consuming_requirement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )
    flat_content = "\n\n".join(
        f"第 {index} 段：这是一段没有标题层级的长篇板书内容，用来模拟模型把整份文档压成普通段落的情况。"
        for index in range(1, 32)
    )
    board_calls = []

    def _retrying_board_edit(**kwargs):
        board_calls.append(kwargs)
        if len(board_calls) == 1:
            return BoardDocumentEditResult(
                operation="replace_document",
                title="无结构板书",
                content_text=flat_content,
                summary="返回了一份无结构长文。",
                chatbot_message="返回了一份无结构长文。",
                section_titles=[],
            )
        return BoardDocumentEditResult(
            operation="replace_document",
            title="结构化板书",
            content_text="# 结构化板书\n\n## 第一部分\n\n这是一段有标题层级的内容。\n\n## 第二部分\n\n这是一段继续展开的内容。",
            summary="重试后生成了结构化板书。",
            chatbot_message="板书已经生成。",
            section_titles=["第一部分", "第二部分"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _retrying_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="目标已经完整"),
        user_id=TEST_USER_ID,
    )

    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    reloaded = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert len(board_calls) == 2
    assert response.requirement_phase == "consumed"
    assert response.board_document_operation_status == "succeeded"
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "consumed"]
    assert "# 结构化板书" in reloaded.board_document.content_text
    assert "无结构长文" not in reloaded.board_document.content_text


def test_initial_generation_rejects_flat_long_document_without_headings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )
    flat_content = "\n\n".join(
        f"第 {index} 段：这是一段没有标题层级的长篇板书内容，用来模拟模型把整份文档压成普通段落的情况。"
        for index in range(1, 32)
    )
    board_calls = []

    def _flat_board_edit(**kwargs):
        board_calls.append(kwargs)
        return BoardDocumentEditResult(
            operation="replace_document",
            title="无结构板书",
            content_text=flat_content,
            summary="返回了一份无结构长文。",
            chatbot_message="返回了一份无结构长文。",
            section_titles=[],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _flat_board_edit)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="目标已经完整"),
        user_id=TEST_USER_ID,
    )

    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    reloaded = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert len(board_calls) == 3
    assert response.requirement_phase == "frozen"
    assert response.board_document_operation_status == "failed"
    assert response.board_document_operation_failure_reason == "首次板书生成结果缺少标题层级，已阻止写入。"
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    assert reloaded.board_document.content_text == ""


def test_stream_emits_requirement_update_before_document_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_chatbot_reply",
        lambda **kwargs: ChatbotReply(chatbot_message="需求已经够清楚。"),
    )
    monkeypatch.setattr(
        openai_course_ai,
        "generate_learning_requirement_update",
        lambda **kwargs: _requirement_update(ready=True, action_type="generate_board"),
    )

    def _fake_board_edit(**kwargs):
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "board",
                "field": "content_text",
                "delta": "#",
                "value": "#",
            }
        )
        return BoardDocumentEditResult(
            operation="replace_document",
            title="第一版板书",
            content_text="# 第一版板书\n\n## 起点\n\n这是一段根据冻结需求清单生成的通用板书。",
            summary="已生成第一版板书。",
            chatbot_message="已生成第一版板书。",
            section_titles=["起点"],
        )

    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", _fake_board_edit)
    _, lesson = _seed_workspace(store)

    events = list(
        _chat_stream_events(
            lesson.id,
            ChatRequest(message="我已经说明目标、水平和输出形式"),
            user_id=TEST_USER_ID,
        )
    )
    names = [_sse_event_name(block) for block in events]
    requirement_index = names.index("requirement_update")
    document_index = names.index("document_delta")
    payload = _sse_payload(events[requirement_index])

    assert requirement_index < document_index
    assert payload["requirement_phase"] == "frozen"
    assert payload["learning_clarification"]["progress"] == 100
    assert payload["learning_requirement_sheet"]["current_questions"] == []
