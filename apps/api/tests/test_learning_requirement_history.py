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
from app.services.openai_course_ai import (
    BoardDocumentEditResult,
    ChatbotReply,
    LearningRequirementUpdate,
    emit_ai_stream_event,
    openai_course_ai,
)
from app.services.rich_document import build_document


TEST_USER_ID = "user_requirement_history"


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
    assert commit.metadata["requirement_run_id"] == response.requirement_run_id
    assert commit.metadata["frozen_requirement_version_id"] is not None
    assert commit.metadata["task_requirement_sheet"] == json.loads(versions[-1]["sheet_json"])
    assert "起点" not in commit.metadata["task_requirement_sheet"]["board_scope"]


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
    monkeypatch.setattr(openai_course_ai, "generate_board_document_edit", lambda **kwargs: None)
    _, lesson = _seed_workspace(store)

    response = chat_service.process_chat_on_lesson(
        lesson.id,
        ChatRequest(message="目标已经完整"),
        user_id=TEST_USER_ID,
    )

    versions = store.list_learning_requirement_versions(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    events = store.list_learning_requirement_events(owner_user_id=TEST_USER_ID, lesson_id=lesson.id)
    reloaded = store.load_for_user(TEST_USER_ID).packages[0].lessons[0]
    assert response.requirement_phase == "frozen"
    assert [row["change_kind"] for row in versions] == ["completed", "frozen"]
    assert [row["event_type"] for row in events] == ["created", "completed", "frozen", "generation_failed"]
    assert reloaded.board_document.content_text == ""
    assert reloaded.history_graph.commits[-1].metadata.get("kind") != "board_document_generation"
    frozen_sheet = json.loads(versions[-1]["sheet_json"])
    assert frozen_sheet["action_type"] == "generate_board"


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
