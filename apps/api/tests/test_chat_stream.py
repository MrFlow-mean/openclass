import json
import time

from app.models import (
    BoardDecision,
    ChatRequest,
    ChatResponse,
    CoursePackage,
    LearningClarificationStatus,
    WorkspaceState,
)
from app.routers import chat as chat_router
from app.services.ai_logging import ai_log_context
from app.services.lesson_factory import create_empty_lesson
from app.services.openai_course_ai import emit_ai_stream_event
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import package_view_for_lesson


def _chat_response(lesson_id: str = "lesson_stream_test") -> ChatResponse:
    lesson = create_empty_lesson("流式回合")
    lesson.id = lesson_id
    assert lesson.learning_requirements is not None
    package = CoursePackage(
        id="course_stream_test",
        title="课程包",
        summary="",
        lessons=[lesson],
        open_lesson_ids=[lesson.id],
        active_lesson_id=lesson.id,
        workspace_tab_order=[lesson.id],
    )
    workspace = WorkspaceState(packages=[package], active_package_id=package.id)
    return ChatResponse(
        chatbot_message="已经完成。",
        learning_requirement_sheet=lesson.learning_requirements,
        learning_clarification=LearningClarificationStatus(
            progress=100,
            label="已完成",
            reason="测试回合已完成。",
        ),
        board_decision=BoardDecision(action="no_change", reason="测试不修改板书。"),
        course_package=package_view_for_lesson(workspace, package, lesson.id),
    )


def _parse_sse(block: str) -> tuple[str, dict]:
    event = "message"
    data_lines: list[str] = []
    for line in block.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    return event, json.loads("\n".join(data_lines))


def _collect_events(stream) -> list[tuple[str, dict]]:
    return [_parse_sse(block) for block in stream]


def test_chat_stream_emits_heartbeat_before_final(monkeypatch) -> None:
    logged_events: list[dict] = []

    def slow_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        time.sleep(0.03)
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "CHAT_STREAM_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(chat_router, "process_chat_on_lesson", slow_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "heartbeat" in event_names
    assert event_names[-1] == "final"
    assert [event["stream_event"] for event in logged_events] == ["stream_started", "stream_final_sent"]
    assert logged_events[-1]["produced_commit_id"] is not None


def test_chat_stream_worker_error_emits_error_and_lifecycle_log(monkeypatch) -> None:
    logged_events: list[dict] = []

    def failing_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", failing_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "final" not in event_names
    error_payload = next(payload for event, payload in events if event == "error")
    assert error_payload["message"] == "model unavailable"
    assert error_payload["trace_id"].startswith("chat_")
    assert [event["stream_event"] for event in logged_events] == ["stream_started", "stream_error"]
    assert logged_events[-1]["error_message"] == "model unavailable"


def test_chat_stream_emits_only_validated_chatbot_message(monkeypatch) -> None:
    def process_with_unvalidated_chatbot_delta(*args, **kwargs) -> ChatResponse:
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "chatbot",
                "field": "chatbot_message",
                "delta": "未验收草稿",
            }
        )
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_unvalidated_chatbot_delta)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    streamed_chat = "".join(payload["delta"] for event, payload in events if event == "chat_delta")
    event_names = [event for event, _payload in events]
    assert streamed_chat == "已经完成。"
    assert "未验收草稿" not in streamed_chat
    assert event_names.index("chat_delta") < event_names.index("final")


def test_chat_stream_still_emits_board_document_preview_delta(monkeypatch) -> None:
    def process_with_board_delta(*args, **kwargs) -> ChatResponse:
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "board",
                "field": "content_text",
                "delta": "板书预览",
            }
        )
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_board_delta)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="帮我继续"),
            user_id="user_stream_test",
        )
    )

    streamed_document = "".join(payload["delta"] for event, payload in events if event == "document_delta")
    assert streamed_document == "板书预览"


def test_chat_stream_logs_disconnect_before_final(monkeypatch) -> None:
    logged_events: list[dict] = []

    def slow_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        time.sleep(0.05)
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", slow_process_chat_on_lesson)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    stream = chat_router._chat_stream_events(
        "lesson_stream_test",
        ChatRequest(message="帮我继续"),
        user_id="user_stream_test",
    )
    next(stream)
    stream.close()

    assert "stream_disconnected_or_no_final" in [event["stream_event"] for event in logged_events]


def test_route_context_reuses_outer_stream_trace() -> None:
    with ai_log_context(trace_id="chat_outer_trace"):
        with bind_ai_request_context("/api/example", trace_prefix="chat") as context:
            assert context["trace_id"] == "chat_outer_trace"
