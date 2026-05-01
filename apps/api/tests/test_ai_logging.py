import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import (
    ChatRequest,
    CreateBranchRequest,
    DocumentSaveRequest,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers import documents as documents_router
from app.routers import realtime as realtime_router
from app.routers.auth import current_user
from app.services import chat_service, workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.openai_course_ai import openai_course_ai
from app.services.resource_library import build_resource_item


TEST_USER = UserView(
    id="user_test",
    email="test@example.com",
    role="user",
    created_at="2026-01-01T00:00:00+00:00",
)


def _read_log_entries(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seed_test_user_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    workspace.packages[0].title = "测试课程工作台"
    store.save_for_user(TEST_USER.id, workspace)
    return workspace


def _disable_course_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_course_ai, "assess_learning_requirements", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_document_edit", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_board_teaching_guide", lambda **kwargs: None)
    monkeypatch.setattr(openai_course_ai, "generate_teaching_guide", lambda **kwargs: None)


@pytest.fixture
def isolated_ai_log(monkeypatch: pytest.MonkeyPatch, tmp_path):
    log_path = tmp_path / "logs" / "ai-usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ai_usage_logger, "path", log_path)
    return log_path


def test_chat_route_logs_pm_workflow_request_and_response(monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path) -> None:
    _disable_course_ai(monkeypatch)
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    lesson_id = _seed_test_user_workspace(store).packages[0].lessons[0].id
    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="请解释一下勾股定理的核心公式"),
        user_id=TEST_USER.id,
    )

    assert response.board_decision.action == "edit_board"
    assert "开始生成板书" in response.board_decision.reason
    assert response.board_edit_prompt is None
    assert response.resource_matches == []
    assert response.selected_reference is None

    entries = _read_log_entries(isolated_ai_log)
    event_types = [entry["event_type"] for entry in entries]
    assert "chat_request" in event_types
    assert "chat_response" in event_types
    assert "ai_interaction_message" in event_types

    chat_request = next(entry for entry in entries if entry["event_type"] == "chat_request")
    chat_response = next(entry for entry in entries if entry["event_type"] == "chat_response")
    interaction_messages = [
        entry for entry in entries if entry["event_type"] == "ai_interaction_message"
    ]
    updated_lesson = next(lesson for lesson in response.course_package.lessons if lesson.id == lesson_id)
    flow_commit = updated_lesson.history_graph.commits[-1]
    assert chat_request["payload"]["message"] == "请解释一下勾股定理的核心公式"
    assert chat_response["payload"]["teacher_message"] == response.teacher_message
    assert chat_request["context"]["trace_id"] == chat_response["context"]["trace_id"]
    assert len(interaction_messages) == 2
    assert interaction_messages[0]["payload"]["channel"] == "text"
    assert interaction_messages[0]["payload"]["direction"] == "input"
    assert interaction_messages[1]["payload"]["channel"] == "text"
    assert interaction_messages[1]["payload"]["direction"] == "output"
    assert flow_commit.metadata["kind"] == "chat_flow"
    assert flow_commit.metadata["board_action"] == "edit_board"
    assert flow_commit.metadata["board_teaching_guide"] is not None

    branched_package = documents_router.create_lesson_branch(
        lesson_id,
        CreateBranchRequest(name="flow-branch", from_commit_id=flow_commit.id),
        user=TEST_USER,
    )
    branched_lesson = next(lesson for lesson in branched_package.lessons if lesson.id == lesson_id)
    assert branched_lesson.history_graph.branches["flow-branch"].base_commit_id == flow_commit.id


def test_document_save_route_keeps_autosave_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    workspace = _seed_test_user_workspace(store)
    lesson = workspace.packages[0].lessons[0]
    document = lesson.board_document.model_copy(deep=True)
    document.content_html = "<p>自动保存后的内容</p>"
    document.content_text = "自动保存后的内容"

    package = documents_router.save_document(
        lesson.id,
        DocumentSaveRequest(
            document=document,
            label="Auto Save",
            message="Auto-saved Word-like rich document changes from the editor",
            metadata={
                "kind": "auto_document_save",
                "autosave": True,
                "autosave_reason": "pagehide",
                "source": "word_board_editor",
            },
        ),
        user=TEST_USER,
    )

    updated_lesson = next(current for current in package.lessons if current.id == lesson.id)
    commit = updated_lesson.history_graph.commits[-1]
    assert commit.snapshot.content_text == "自动保存后的内容"
    assert commit.metadata["kind"] == "auto_document_save"
    assert commit.metadata["autosave"] is True
    assert commit.metadata["autosave_reason"] == "pagehide"


def test_document_save_beacon_accepts_plain_text_json(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    workspace = _seed_test_user_workspace(store)
    lesson = workspace.packages[0].lessons[0]
    document = lesson.board_document.model_copy(deep=True)
    document.content_html = "<p>关闭页面前保存</p>"
    document.content_text = "关闭页面前保存"
    save_request = DocumentSaveRequest(
        document=document,
        label="Auto Save",
        message="Auto-saved Word-like rich document changes from the editor",
        metadata={
            "kind": "auto_document_save",
            "autosave": True,
            "autosave_reason": "pagehide",
        },
    )

    main_module.app.dependency_overrides[current_user] = lambda: TEST_USER
    try:
        response = TestClient(main_module.app).post(
            f"/api/lessons/{lesson.id}/document/save-beacon",
            content=save_request.model_dump_json(),
            headers={"content-type": "text/plain;charset=UTF-8"},
        )
    finally:
        main_module.app.dependency_overrides.pop(current_user, None)

    assert response.status_code == 200
    updated_lesson = next(current for current in response.json()["lessons"] if current["id"] == lesson.id)
    commit = updated_lesson["history_graph"]["commits"][-1]
    assert commit["snapshot"]["content_text"] == "关闭页面前保存"
    assert commit["metadata"]["autosave"] is True
    assert commit["metadata"]["autosave_reason"] == "pagehide"


def test_chat_route_does_not_match_references_after_reset(
    monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path
) -> None:
    _disable_course_ai(monkeypatch)
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)

    workspace = _seed_test_user_workspace(store)
    package = workspace.packages[0]
    resource_path = tmp_path / "pythagorean.md"
    resource_path.write_text(
        "# 勾股定理\n勾股定理说明直角三角形两条直角边的平方和等于斜边的平方。\n\n## 应用\n可以用来计算距离。",
        encoding="utf-8",
    )
    resource = build_resource_item(resource_path, "勾股定理笔记.md")
    resource.scope_lesson_id = package.lessons[0].id
    package.resources.append(resource)
    store.save_for_user(TEST_USER.id, workspace)

    lesson_id = store.load_for_user(TEST_USER.id).packages[0].lessons[0].id
    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="请解释一下勾股定理的核心公式"),
        user_id=TEST_USER.id,
    )

    assert response.board_decision.action == "edit_board"
    assert response.resource_matches == []
    assert response.selected_reference is None


def test_realtime_transcript_route_logs_voice_event(monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_test_user_workspace(store).packages[0].lessons[0]

    response = realtime_router.log_realtime_event(
        lesson.id,
        RealtimeTranscriptLogRequest(
            client_session_id="realtime_session_1",
            lesson_title="勾股定理",
            role="assistant",
            transport_event_type="response.audio_transcript.done",
            transcript="我们先从直角三角形开始。",
        ),
        user=TEST_USER,
    )

    assert response == {"status": "ok"}
    entries = _read_log_entries(isolated_ai_log)
    event_types = [entry["event_type"] for entry in entries]
    assert "realtime_transcript" in event_types
    assert "ai_interaction_message" in event_types
