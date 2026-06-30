import json
from types import SimpleNamespace

import pytest

from app.models import RealtimeConnectRequest
from app.services import chat_service, openai_realtime, workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import ComplexProblemSolution, OpenAICourseAI
from app.services.realtime_tool_bridge import RealtimeToolSession, _tool_call_from_event, execute_realtime_tool


TEST_USER_ID = "user_realtime_test"


def _seed_workspace(store: SqliteCourseStore):
    workspace = build_initial_workspace_state()
    lesson = create_empty_lesson("Realtime 测试页")
    lesson.board_document.content_text = "当前板书有一个待讲解片段。"
    package = workspace.packages[0]
    package.lessons.append(lesson)
    package.open_lesson_ids.append(lesson.id)
    package.workspace_tab_order.append(lesson.id)
    package.active_lesson_id = lesson.id
    store.save_for_user(TEST_USER_ID, workspace)
    return lesson


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    return store


@pytest.fixture
def isolated_ai_log(monkeypatch: pytest.MonkeyPatch, tmp_path):
    log_path = tmp_path / "logs" / "ai-usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ai_usage_logger, "path", log_path)
    return log_path


def test_realtime_connect_reports_disabled_by_default(monkeypatch, isolated_store) -> None:
    monkeypatch.delenv("OPENCLASS_REALTIME_ENABLED", raising=False)
    lesson = _seed_workspace(isolated_store)

    with pytest.raises(openai_realtime.RealtimeServiceError) as exc_info:
        openai_realtime.connect_openai_realtime_session(
            lesson.id,
            RealtimeConnectRequest(offer_sdp="v=0", client_session_id="realtime_test"),
            user_id=TEST_USER_ID,
        )

    assert exc_info.value.status_code == 410


def test_realtime_connect_posts_sdp_with_chatbot_tools(monkeypatch, isolated_store, isolated_ai_log) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_REALTIME_TOOLS_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
    monkeypatch.setenv("OPENAI_REALTIME_VOICE", "verse")
    lesson = _seed_workspace(isolated_store)
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 201
        text = "answer-sdp"
        headers = {"Location": "/v1/realtime/calls/rtc_test_call"}

    class _FakeClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, files):
            captured["url"] = url
            captured["headers"] = headers
            captured["files"] = files
            return _FakeResponse()

    sideband_sessions = []
    monkeypatch.setattr(openai_realtime.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        openai_realtime,
        "start_sideband_session",
        lambda session, *, api_key: sideband_sessions.append((session, api_key)),
    )

    response = openai_realtime.connect_openai_realtime_session(
        lesson.id,
        RealtimeConnectRequest(offer_sdp="v=0", client_session_id="realtime_test"),
        user_id=TEST_USER_ID,
    )

    assert response.answer_sdp == "answer-sdp"
    assert response.call_id == "rtc_test_call"
    assert response.tools_enabled is True
    assert response.client_session_id == "realtime_test"
    assert captured["url"] == "https://api.openai.com/v1/realtime/calls"
    assert "test-openai-key" in captured["headers"]["Authorization"]
    session_payload = json.loads(captured["files"]["session"][1])
    assert session_payload["model"] == "gpt-realtime-2"
    assert session_payload["audio"]["output"]["voice"] == "verse"
    tool_names = {tool["name"] for tool in session_payload["tools"]}
    assert {"run_chatbot_workflow", "solve_complex_problem"} <= tool_names
    assert "同一个角色" in session_payload["instructions"]
    assert "当前板书有一个待讲解片段" not in session_payload["instructions"]
    assert "实时 Chatbot 不能直接读取" in session_payload["instructions"]
    assert sideband_sessions[0][0].call_id == "rtc_test_call"
    assert sideband_sessions[0][1] == "test-openai-key"


def test_realtime_tool_run_chatbot_workflow_reuses_chat_service(monkeypatch, isolated_store, isolated_ai_log) -> None:
    lesson = _seed_workspace(isolated_store)
    captured: dict[str, object] = {}

    def _fake_chat(lesson_id, request, *, user_id):
        captured["lesson_id"] = lesson_id
        captured["message"] = request.message
        captured["user_id"] = user_id
        return SimpleNamespace(
            chatbot_message="Chatbot 已处理语音问题。",
            board_decision=SimpleNamespace(action="no_change"),
            needs_clarification=False,
            requirement_cleared=False,
            resolved_focus=None,
        )

    monkeypatch.setattr(chat_service, "process_chat_on_lesson", _fake_chat)
    session = RealtimeToolSession(
        call_id="rtc_tool",
        lesson_id=lesson.id,
        user_id=TEST_USER_ID,
        client_session_id="realtime_tool_session",
    )

    payload = execute_realtime_tool(
        session,
        "run_chatbot_workflow",
        {
            "lesson_id": lesson.id,
            "client_session_id": "realtime_tool_session",
            "message": "请按当前板书讲一下。",
        },
    )

    assert payload["status"] == "ok"
    assert payload["chatbot_message"] == "Chatbot 已处理语音问题。"
    assert captured == {
        "lesson_id": lesson.id,
        "message": "请按当前板书讲一下。",
        "user_id": TEST_USER_ID,
    }


def test_realtime_tool_call_can_be_detected_from_response_done() -> None:
    event = {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "status": "completed",
                    "name": "run_chatbot_workflow",
                    "call_id": "call_realtime",
                    "arguments": json.dumps(
                        {
                            "lesson_id": "lesson_1",
                            "client_session_id": "session_1",
                            "message": "请继续讲。",
                        }
                    ),
                }
            ]
        },
    }

    assert _tool_call_from_event(event) == (
        "call_realtime",
        "run_chatbot_workflow",
        {
            "lesson_id": "lesson_1",
            "client_session_id": "session_1",
            "message": "请继续讲。",
        },
    )


def test_strong_reasoning_solver_uses_configured_model_and_effort(monkeypatch, isolated_ai_log) -> None:
    monkeypatch.setenv("OPENAI_STRONG_REASONING_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_STRONG_REASONING_EFFORT", "xhigh")
    captured: dict[str, object] = {}
    ai = OpenAICourseAI()

    class _FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="resp_reasoning",
                usage={"input_tokens": 10, "output_tokens": 20},
                output_parsed=ComplexProblemSolution(
                    summary="需要分步分析。",
                    answer="这是给 Chatbot 转述的答案。",
                    confidence="high",
                ),
            )

    ai.client = SimpleNamespace(responses=_FakeResponses())

    result = ai.solve_complex_problem(
        lesson_title="测试页",
        question="请严谨分析这个复杂问题。",
        board_summary="当前板书摘要",
    )

    assert result is not None
    assert result.model == "gpt-5.5"
    assert result.reasoning_effort == "xhigh"
    assert captured["model"] == "gpt-5.5"
    assert captured["reasoning"] == {"effort": "xhigh"}
    assert captured["text_format"] is ComplexProblemSolution
    prompt_payload = json.loads(captured["input"][1]["content"])
    assert prompt_payload["board_summary"] != "当前板书摘要"
    assert "没有直接读取右侧板书文档的权限" in prompt_payload["board_summary"]
