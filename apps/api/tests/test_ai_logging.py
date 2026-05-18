import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.main as main_module
from app.models import (
    AIModelSelection,
    ChatRequest,
    CreateBranchRequest,
    DocumentSaveRequest,
    LearningRequirementChecklistItem,
    RealtimeTranscriptLogRequest,
    UserView,
)
from app.routers.auth import current_user
from app.routers import documents as documents_router
from app.routers import realtime as realtime_router
from app.services.ai_logging import ai_log_context, ai_usage_logger
from app.services import chat_service, workspace_state
from app.services.course_store import SqliteCourseStore, build_initial_workspace_state
from app.services.openai_course_ai import (
    CourseChatReply,
    GeneratedResourceCatalog,
    LearningRequirementUpdate,
    OpenAICourseAI,
    bind_text_model_selection,
    openai_course_ai,
)
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


def _fake_teacher_message(**kwargs) -> str:
    return "AI生成：这是一段测试讲解。"


def _fake_requirement_update(**kwargs) -> LearningRequirementUpdate:
    return LearningRequirementUpdate(
        progress=100,
        summary="用户已经说明当前学习目标，可以进入后续板书阶段。",
        checklist=[
            LearningRequirementChecklistItem(
                title="用户已经说明当前学习目标",
                is_clear=True,
                evidence="用户提出了当前要解决的学习问题。",
            ),
            LearningRequirementChecklistItem(
                title="后续板书可以围绕该目标组织",
                is_clear=True,
                evidence="对话已经给出可继续展开的学习方向。",
            ),
        ],
        missing_items=[],
        next_question="",
        ready_for_board=True,
    )


@pytest.fixture
def isolated_ai_log(monkeypatch: pytest.MonkeyPatch, tmp_path):
    log_path = tmp_path / "logs" / "ai-usage.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ai_usage_logger, "path", log_path)
    return log_path


def test_openai_parse_logs_prompt_and_output(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _LessonOutput:
        id = "resp_123"
        output_text = '{"title":"工作台标题"}'
        usage = {"total_tokens": 42}

        def __init__(self) -> None:
            self.output_parsed = _Output(title="工作台标题")

    class _FakeResponses:
        def __init__(self) -> None:
            self.payload = None

        def parse(self, **kwargs):
            self.payload = kwargs
            return _LessonOutput()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    ai = OpenAICourseAI()
    ai.client = _FakeClient()
    ai.config.default_model = "gpt-5.3"
    ai.config.lesson_model = "gpt-5.3"
    ai.config.compat_api = "responses"

    with ai_log_context(trace_id="trace_unit", route="unit_test"):
        generated = ai._parse(
            "lesson",
            system_prompt="Return a structured title.",
            user_prompt='{"request":"unit"}',
            schema=_Output,
        )

    assert generated is not None
    entries = _read_log_entries(isolated_ai_log)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event_type"] == "openai_text_call"
    assert entry["context"]["trace_id"] == "trace_unit"
    assert entry["payload"]["model"] == "gpt-5.3"
    assert entry["payload"]["user_prompt"]
    assert entry["payload"]["parsed_output"]["title"] == "工作台标题"


def test_openai_parse_retries_model_not_found_with_fallback(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _Response:
        id = "resp_retry"
        output_text = '{"title":"勾股定理"}'
        usage = {"total_tokens": 21}

        def __init__(self) -> None:
            self.output_parsed = _Output(title="勾股定理")

    class _FakeResponses:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def parse(self, **kwargs):
            model = kwargs["model"]
            self.calls.append(model)
            if model == "gpt-5.3":
                raise Exception(
                    "Error code: 400 - {'error': {'message': \"The requested model 'gpt-5.3' does not exist.\", "
                    "'type': 'invalid_request_error', 'param': 'model', 'code': 'model_not_found'}}"
                )
            return _Response()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    ai = OpenAICourseAI()
    ai.client = _FakeClient()
    ai.config.default_model = "gpt-5.3"
    ai.config.pm_model = "gpt-5.3"
    ai.config.fallback_model = "gpt-5.4"
    ai.config.compat_api = "responses"

    with ai_log_context(trace_id="trace_retry", route="unit_test"):
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "勾股定理"
    assert ai.client.responses.calls == ["gpt-5.3", "gpt-5.4"]

    entries = _read_log_entries(isolated_ai_log)
    assert [entry["event_type"] for entry in entries] == ["openai_text_call_retry", "openai_text_call"]
    assert entries[0]["payload"]["model"] == "gpt-5.3"
    assert entries[0]["payload"]["retry_model"] == "gpt-5.4"
    assert entries[1]["payload"]["model"] == "gpt-5.4"
    assert entries[1]["payload"]["fallback_from_model"] == "gpt-5.3"


def test_openai_parse_falls_back_to_google_on_provider_auth_error(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _GoogleResponse:
        id = "google_123"
        output_text = '{"title":"勾股定理"}'
        usage = {"totalTokenCount": 12}
        output_parsed = _Output(title="勾股定理")

    class _FakeOpenAIResponses:
        def parse(self, **kwargs):
            raise Exception(
                "Error code: 401 - {'error': {'message': 'Incorrect API key provided', "
                "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}"
            )

    class _FakeOpenAIClient:
        def __init__(self) -> None:
            self.responses = _FakeOpenAIResponses()

    class _FakeGoogleClient:
        def __init__(self) -> None:
            self.payload = None

        def parse(self, **kwargs):
            self.payload = kwargs
            return _GoogleResponse()

    ai = OpenAICourseAI()
    ai.client = _FakeOpenAIClient()
    ai.google_client = _FakeGoogleClient()
    ai.google_config.default_model = "gemini-good"
    ai.config.compat_api = "responses"

    with bind_text_model_selection(AIModelSelection(provider="openai", model="gpt-bad")):
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "勾股定理"
    assert ai.google_client.payload["model"] == "gemini-good"

    entries = _read_log_entries(isolated_ai_log)
    assert [entry["event_type"] for entry in entries] == ["openai_text_call_provider_retry", "google_text_call"]
    assert entries[0]["payload"]["retry_provider"] == "google"
    assert entries[0]["payload"]["retry_model"] == "gemini-good"
    assert entries[1]["payload"]["fallback_from_provider"] == "openai"
    assert entries[1]["payload"]["fallback_from_model"] == "gpt-bad"


def test_unavailable_provider_falls_back_to_google(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _GoogleResponse:
        id = "google_456"
        output_text = '{"title":"函数"}'
        usage = {"totalTokenCount": 10}
        output_parsed = _Output(title="函数")

    class _FakeGoogleClient:
        def __init__(self) -> None:
            self.payload = None

        def parse(self, **kwargs):
            self.payload = kwargs
            return _GoogleResponse()

    ai = OpenAICourseAI()
    ai.client = None
    ai.google_client = _FakeGoogleClient()
    ai.google_config.default_model = "gemini-ready"

    with bind_text_model_selection(AIModelSelection(provider="openai", model="gpt-missing")):
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "函数"
    assert ai.google_client.payload["model"] == "gemini-ready"

    entries = _read_log_entries(isolated_ai_log)
    assert [entry["event_type"] for entry in entries] == [
        "openai_text_call_skipped",
        "openai_text_call_provider_retry",
        "google_text_call",
    ]
    assert entries[0]["payload"]["reason"] == "client_disabled"
    assert entries[1]["payload"]["retry_provider"] == "google"
    assert entries[1]["payload"]["retry_model"] == "gemini-ready"
    assert entries[2]["payload"]["fallback_from_provider"] == "openai"
    assert entries[2]["payload"]["fallback_from_model"] == "gpt-missing"


def test_openai_compat_chat_completions_mode_parses_json(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _Message:
        content = '{"title":"勾股定理"}'

    class _Choice:
        message = _Message()

    class _Response:
        id = "chatcmpl_123"
        choices = [_Choice()]
        usage = {"total_tokens": 12}

    class _FakeChatCompletions:
        def __init__(self) -> None:
            self.payload = None

        def create(self, **kwargs):
            self.payload = kwargs
            return _Response()

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    ai = OpenAICourseAI()
    ai.client = _FakeClient()
    ai.config.compat_api = "chat_completions"
    ai.config.default_model = "gpt-5.4"
    ai.config.pm_model = "gpt-5.4"

    with ai_log_context(trace_id="trace_chat_compat", route="unit_test"):
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "勾股定理"
    assert ai.client.chat.completions.payload["model"] == "gpt-5.4"
    assert ai.client.chat.completions.payload["response_format"]["type"] == "json_schema"
    entries = _read_log_entries(isolated_ai_log)
    assert entries[0]["event_type"] == "openai_text_call"
    assert entries[0]["payload"]["output_text"] == '{"title":"勾股定理"}'


def test_openai_defaults_to_official_openai_and_gpt_image_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)

    ai = OpenAICourseAI()

    assert ai.config.base_url == "https://api.openai.com/v1"
    assert ai.config.compat_api == "chat_completions"
    assert ai.config.image_model == "gpt-image-2"


@pytest.mark.parametrize(
    ("provider", "client_attr", "model"),
    [
        ("deepseek", "deepseek_client", "deepseek-v4-pro"),
        ("kimi", "kimi_client", "kimi-k2.6"),
        ("minimax", "minimax_client", "MiniMax-M2.7-highspeed"),
        ("openai_compatible", "openai_compatible_client", "router-model"),
    ],
)
def test_openai_compatible_style_providers_route_to_selected_client(
    provider: str, client_attr: str, model: str, isolated_ai_log
) -> None:
    class _Output(BaseModel):
        title: str

    class _Message:
        content = '{"title":"统一接口"}'

    class _Choice:
        message = _Message()

    class _Response:
        id = f"{provider}_123"
        choices = [_Choice()]
        usage = {"total_tokens": 12}

    class _FakeChatCompletions:
        def __init__(self) -> None:
            self.payload = None

        def create(self, **kwargs):
            self.payload = kwargs
            return _Response()

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeChatCompletions()

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    ai = OpenAICourseAI()
    fake_client = _FakeClient()
    setattr(ai, client_attr, fake_client)

    with bind_text_model_selection(AIModelSelection(provider=provider, model=model)):  # type: ignore[arg-type]
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "统一接口"
    assert fake_client.chat.completions.payload["model"] == model
    entries = _read_log_entries(isolated_ai_log)
    assert entries[0]["event_type"] == f"{provider}_text_call"
    assert entries[0]["payload"]["provider"] == provider
    assert entries[0]["payload"]["model"] == model


def test_anthropic_compatible_provider_routes_to_selected_client(isolated_ai_log) -> None:
    class _Output(BaseModel):
        title: str

    class _FakeAnthropicCompatibleClient:
        def __init__(self) -> None:
            self.payload = None

        def parse(self, **kwargs):
            self.payload = kwargs

            class _Response:
                id = "anthropic_compatible_123"
                output_text = '{"title":"统一接口"}'
                usage = {"input_tokens": 3, "output_tokens": 5}
                output_parsed = _Output(title="统一接口")

            return _Response()

    ai = OpenAICourseAI()
    fake_client = _FakeAnthropicCompatibleClient()
    ai.anthropic_compatible_client = fake_client

    with bind_text_model_selection(
        AIModelSelection(provider="anthropic_compatible", model="claude-router")
    ):
        result = ai._parse("pm", "system", "user", _Output)

    assert result is not None
    assert result.title == "统一接口"
    assert fake_client.payload["model"] == "claude-router"
    entries = _read_log_entries(isolated_ai_log)
    assert entries[0]["event_type"] == "anthropic_compatible_text_call"
    assert entries[0]["payload"]["provider"] == "anthropic_compatible"
    assert entries[0]["payload"]["model"] == "claude-router"


def test_catalog_role_uses_dedicated_openai_model_even_with_text_selection(isolated_ai_log) -> None:
    class _FakeResponses:
        def __init__(self) -> None:
            self.payload = None

        def parse(self, **kwargs):
            self.payload = kwargs

            class _Response:
                id = "catalog_123"
                usage = {"input_tokens": 5, "output_tokens": 8}

                def __init__(self) -> None:
                    payload = {
                        "chapters": [
                            {
                                "title": "入口",
                                "summary": "资料的起点。",
                                "keywords": ["入口"],
                                "level": 1,
                            }
                        ]
                    }
                    self.output_text = json.dumps(payload, ensure_ascii=False)
                    self.output_parsed = GeneratedResourceCatalog.model_validate(payload)

            return _Response()

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    ai = OpenAICourseAI()
    ai.client = _FakeClient()
    ai.config.default_model = "gpt-5.5"
    ai.config.catalog_model = "gpt-5.4-mini"
    ai.config.compat_api = "responses"

    with bind_text_model_selection(AIModelSelection(provider="openai", model="gpt-5.5")):
        result = ai.generate_resource_outline(
            resource_name="material.txt",
            extracted_text="入口说明。" * 80,
        )

    assert result is not None
    assert result.chapters[0].title == "入口"
    assert ai.client.responses.payload["model"] == "gpt-5.4-mini"


def test_chat_route_returns_generic_teacher_reply(monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_teacher_chat",
        lambda **kwargs: CourseChatReply(teacher_message="AI生成：这是一段测试讲解。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _fake_requirement_update)

    lesson_id = _seed_test_user_workspace(store).packages[0].lessons[0].id
    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="请解释一下当前主题的核心问题"),
        user_id=TEST_USER.id,
    )

    assert response.teacher_message == "AI生成：这是一段测试讲解。"
    lesson = response.course_package.lessons[0]
    commit = lesson.history_graph.commits[-1]
    assert commit.metadata["kind"] == "chat_flow"
    assert commit.metadata["user_message"] == "请解释一下当前主题的核心问题"
    assert commit.metadata["assistant_message_source"] == "ai"
    assert commit.metadata["learning_clarification"]["ready_for_board"] is True
    assert response.learning_clarification.progress == 100
    assert response.learning_clarification.checklist[0].is_clear is True
    assert _read_log_entries(isolated_ai_log) == []


def test_requirement_manager_keeps_low_substance_chat_unclear(
    monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_teacher_chat",
        lambda **kwargs: CourseChatReply(teacher_message="你好，我们可以先明确学习目标。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", lambda **kwargs: None)

    lesson_id = _seed_test_user_workspace(store).packages[0].lessons[0].id
    response = chat_service.process_chat_on_lesson(
        lesson_id,
        ChatRequest(message="你好"),
        user_id=TEST_USER.id,
    )

    assert response.learning_clarification.progress < 50
    assert response.learning_clarification.ready_for_board is False
    assert response.learning_clarification.next_question
    assert response.learning_clarification.checklist
    assert response.learning_clarification.checklist[0].is_clear is False
    assert response.learning_clarification.missing_items == ["具体学习目标"]
    assert _read_log_entries(isolated_ai_log) == []


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


def test_document_ai_edit_returns_chat_guidance(
    monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    captured: dict[str, str | None] = {}

    def _fake_chat(**kwargs):
        captured["selection_excerpt"] = kwargs.get("selection_excerpt")
        captured["interaction_mode"] = kwargs.get("interaction_mode")
        return CourseChatReply(teacher_message="AI生成：这是修改建议。")

    monkeypatch.setattr(openai_course_ai, "generate_teacher_chat", _fake_chat)
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _fake_requirement_update)

    lesson_id = _seed_test_user_workspace(store).packages[0].lessons[0].id
    response = chat_service.document_ai_edit_request(
        lesson_id,
        "改写选中内容",
        "原文",
        [],
        user_id=TEST_USER.id,
    )

    assert response.teacher_message == "AI生成：这是修改建议。"
    assert captured == {"selection_excerpt": "原文", "interaction_mode": "direct_edit"}
    assert _read_log_entries(isolated_ai_log) == []


def test_chat_http_endpoint_returns_teacher_reply(
    monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    monkeypatch.setattr(
        openai_course_ai,
        "generate_teacher_chat",
        lambda **kwargs: CourseChatReply(teacher_message="AI生成：HTTP 路由回复。"),
    )
    monkeypatch.setattr(openai_course_ai, "generate_learning_requirement_update", _fake_requirement_update)

    workspace = _seed_test_user_workspace(store)
    lesson_id = workspace.packages[0].lessons[0].id

    main_module.app.dependency_overrides[current_user] = lambda: TEST_USER
    try:
        response = TestClient(main_module.app).post(
            f"/api/lessons/{lesson_id}/chat",
            json={"message": "请解释一下核心公式"},
        )
    finally:
        main_module.app.dependency_overrides.pop(current_user, None)

    assert response.status_code == 200
    assert response.json()["teacher_message"] == "AI生成：HTTP 路由回复。"
    assert _read_log_entries(isolated_ai_log) == []


def test_realtime_transcript_route_reports_removed_workflow(
    monkeypatch: pytest.MonkeyPatch, isolated_ai_log, tmp_path
) -> None:
    store = SqliteCourseStore(tmp_path / "openclass.sqlite3", legacy_json_path=None)
    monkeypatch.setattr(workspace_state, "STORE", store)
    lesson = _seed_test_user_workspace(store).packages[0].lessons[0]

    with pytest.raises(HTTPException) as exc_info:
        realtime_router.log_realtime_event(
            lesson.id,
            RealtimeTranscriptLogRequest(
                client_session_id="realtime_session_1",
                lesson_title="测试课",
                role="assistant",
                transport_event_type="response.audio_transcript.done",
                transcript="测试转写",
            ),
            user=TEST_USER,
        )

    assert exc_info.value.status_code == 410
    assert _read_log_entries(isolated_ai_log) == []
