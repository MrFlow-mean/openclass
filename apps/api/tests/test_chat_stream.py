import json
import threading
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
from app.services.openai_course_ai import AIStreamCancelledError, emit_ai_stream_event
from app.services.route_context import bind_ai_request_context
from app.services.workspace_state import package_view_for_lesson


def _chat_response(
    lesson_id: str = "lesson_stream_test",
    *,
    chatbot_message: str = "已经完成。",
    document_text: str = "",
    board_document_operation_status: str = "none",
) -> ChatResponse:
    lesson = create_empty_lesson("流式回合")
    lesson.id = lesson_id
    lesson.board_document.content_text = document_text
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
        chatbot_message=chatbot_message,
        learning_requirement_sheet=lesson.learning_requirements,
        learning_clarification=LearningClarificationStatus(
            progress=100,
            label="已完成",
            reason="测试回合已完成。",
        ),
        board_decision=BoardDecision(action="no_change", reason="测试不修改板书。"),
        board_document_operation_status=board_document_operation_status,
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


def _joined_delta(events: list[tuple[str, dict]], event_name: str) -> str:
    return "".join(str(payload.get("delta") or "") for event, payload in events if event == event_name)


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
    assert [event["stream_event"] for event in logged_events] == [
        "stream_started",
        "process_chat_returned",
        "first_chat_delta_sent",
        "stream_final_sent",
    ]
    lifecycle_elapsed = [
        event["elapsed_ms"]
        for event in logged_events
        if event["stream_event"] in {"stream_started", "process_chat_returned", "stream_final_sent"}
    ]
    assert lifecycle_elapsed == sorted(lifecycle_elapsed)
    first_chat_delta = next(event for event in logged_events if event["stream_event"] == "first_chat_delta_sent")
    assert first_chat_delta["role"] == "chatbot"
    assert first_chat_delta["field"] == "chatbot_message"
    assert logged_events[-1]["produced_commit_id"] is not None


def test_chat_stream_routes_pm_chatbot_message_as_chat_delta(monkeypatch) -> None:
    logged_events: list[dict] = []

    def process_with_pm_stream(*args, **kwargs) -> ChatResponse:
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "pm",
                "field": "chatbot_message",
                "delta": "正在自然收敛需求。",
                "value": "正在自然收敛需求。",
            }
        )
        return _chat_response("lesson_stream_test", chatbot_message="正在自然收敛需求。")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_pm_stream)
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="我想学一个宽泛主题"),
            user_id="user_stream_test",
        )
    )

    assert _joined_delta(events, "chat_delta") == "正在自然收敛需求。"
    assert [event for event, _payload in events].count("final") == 1
    first_chat_delta_events = [
        event for event in logged_events if event["stream_event"] == "first_chat_delta_sent"
    ]
    assert len(first_chat_delta_events) == 1
    assert first_chat_delta_events[0]["role"] == "pm"
    assert first_chat_delta_events[0]["field"] == "chatbot_message"


def test_chat_stream_synthesizes_final_chatbot_message_as_delta(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response("lesson_stream_test", chatbot_message="最终回复也要流式出现。"),
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="随便聊聊"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert _joined_delta(events, "chat_delta") == "最终回复也要流式出现。"
    assert event_names.index("chat_delta") < event_names.index("final")


def test_chat_stream_paces_visible_chat_deltas(monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(chat_router, "CHAT_STREAM_CHAT_DELTA_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(chat_router, "time", type("FakeTime", (), {"sleep": staticmethod(sleep_calls.append)}))
    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response("lesson_stream_test", chatbot_message="流式"),
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="随便聊聊"),
            user_id="user_stream_test",
        )
    )

    assert _joined_delta(events, "chat_delta") == "流式"
    assert sleep_calls == [0.02, 0.02]


def test_chat_stream_synthesizes_document_delta_for_succeeded_board_operation(monkeypatch) -> None:
    logged_events: list[dict] = []

    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response(
            "lesson_stream_test",
            chatbot_message="板书已生成。",
            document_text="# 新板书\n\n这里是生成后的板书内容。",
            board_document_operation_status="succeeded",
        ),
    )
    monkeypatch.setattr(
        chat_router.ai_usage_logger,
        "log_event",
        lambda event_type, **payload: logged_events.append({"event_type": event_type, **payload}) or payload,
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="生成板书"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert _joined_delta(events, "document_delta") == "# 新板书\n\n这里是生成后的板书内容。"
    assert event_names.index("document_delta") < event_names.index("final")
    first_document_delta_events = [
        event for event in logged_events if event["stream_event"] == "first_document_delta_sent"
    ]
    assert len(first_document_delta_events) == 1
    assert first_document_delta_events[0]["role"] == "board"
    assert first_document_delta_events[0]["field"] == "content_text"


def test_chat_stream_does_not_emit_unvalidated_board_draft(monkeypatch) -> None:
    def process_with_rejected_board_draft(*args, **kwargs) -> ChatResponse:
        emit_ai_stream_event(
            {
                "type": "field_delta",
                "role": "board",
                "field": "content_text",
                "delta": "# 被拒绝的中间稿\n\n这段不应进入右侧预览。",
                "value": "# 被拒绝的中间稿\n\n这段不应进入右侧预览。",
            }
        )
        return _chat_response(
            "lesson_stream_test",
            chatbot_message="",
            document_text="# 最终通过版\n\n这里只显示质量门禁通过后的板书。",
            board_document_operation_status="succeeded",
        )

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", process_with_rejected_board_draft)

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="开始生成板书", board_generation_action="start"),
            user_id="user_stream_test",
        )
    )

    document_text = _joined_delta(events, "document_delta")
    assert document_text == "# 最终通过版\n\n这里只显示质量门禁通过后的板书。"
    assert "被拒绝的中间稿" not in document_text


def test_chat_stream_does_not_synthesize_chat_delta_for_silent_board_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        chat_router,
        "process_chat_on_lesson",
        lambda *args, **kwargs: _chat_response(
            "lesson_stream_test",
            chatbot_message="",
            document_text="# 新板书\n\n## 1. 可定位小节\n\n这里是生成后的板书内容。",
            board_document_operation_status="succeeded",
        ),
    )

    events = _collect_events(
        chat_router._chat_stream_events(
            "lesson_stream_test",
            ChatRequest(message="开始生成板书", board_generation_action="start"),
            user_id="user_stream_test",
        )
    )

    event_names = [event for event, _payload in events]
    assert "chat_delta" not in event_names
    assert _joined_delta(events, "document_delta") == "# 新板书\n\n## 1. 可定位小节\n\n这里是生成后的板书内容。"
    assert event_names.index("document_delta") < event_names.index("final")


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


def test_chat_stream_cancels_worker_after_disconnect(monkeypatch) -> None:
    logged_events: list[dict] = []
    allow_model_delta = threading.Event()
    cancellation_seen = threading.Event()

    def cancellable_process_chat_on_lesson(*args, **kwargs) -> ChatResponse:
        allow_model_delta.wait(timeout=1)
        try:
            emit_ai_stream_event(
                {
                    "type": "field_delta",
                    "role": "chatbot",
                    "field": "chatbot_message",
                    "delta": "不会继续输出",
                }
            )
        except AIStreamCancelledError:
            cancellation_seen.set()
            raise
        return _chat_response("lesson_stream_test")

    monkeypatch.setattr(chat_router, "process_chat_on_lesson", cancellable_process_chat_on_lesson)
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
    allow_model_delta.set()

    assert cancellation_seen.wait(timeout=1)
    stream_events = [event["stream_event"] for event in logged_events]
    assert "stream_disconnected_or_no_final" in stream_events
    assert "stream_cancelled" in stream_events
    assert "stream_final_sent" not in stream_events


def test_route_context_reuses_outer_stream_trace() -> None:
    with ai_log_context(trace_id="chat_outer_trace"):
        with bind_ai_request_context("/api/example", trace_prefix="chat") as context:
            assert context["trace_id"] == "chat_outer_trace"
