from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

import websockets

from app.models import ChatRequest
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.openai_course_ai import ComplexProblemSolution, openai_course_ai

logger = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _compact_text(value: str | None, *, limit: int = 1200) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}..."


def realtime_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "run_chatbot_workflow",
            "description": (
                "Send the learner's final utterance into the same OpenClass Chatbot workflow used by text chat. "
                "Use this for lesson-scoped learning, board targeting, document operations, and normal Chatbot replies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lesson_id": {"type": "string"},
                    "client_session_id": {"type": "string"},
                    "message": {"type": "string", "description": "The learner utterance to process."},
                },
                "required": ["lesson_id", "client_session_id", "message"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "solve_complex_problem",
            "description": (
                "Use a hidden stronger reasoning model for difficult lesson-scoped questions. "
                "The result is private support material for Chatbot; it must not directly edit documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lesson_id": {"type": "string"},
                    "client_session_id": {"type": "string"},
                    "question": {"type": "string"},
                    "target_excerpt": {
                        "type": "string",
                        "description": "Optional board or resource excerpt that the learner is asking about.",
                    },
                    "desired_output": {
                        "type": "string",
                        "description": "Optional answer shape requested by the learner.",
                    },
                },
                "required": ["lesson_id", "client_session_id", "question"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass(frozen=True)
class RealtimeToolSession:
    call_id: str
    lesson_id: str
    user_id: str
    client_session_id: str


def _validate_tool_scope(session: RealtimeToolSession, arguments: dict[str, Any]) -> None:
    if str(arguments.get("lesson_id") or "") != session.lesson_id:
        raise PermissionError("Tool call lesson_id does not match the realtime session")
    if str(arguments.get("client_session_id") or "") != session.client_session_id:
        raise PermissionError("Tool call client_session_id does not match the realtime session")
    workspace = workspace_state.load_workspace_for_user(session.user_id)
    workspace_state.find_lesson_package(workspace, session.lesson_id)


def _safe_focus_payload(response) -> dict[str, Any] | None:
    focus = getattr(response, "resolved_focus", None)
    if not focus:
        return None
    return {
        "source": focus.source,
        "lesson_id": focus.lesson_id,
        "document_id": focus.document_id,
        "segment_id": focus.segment_id,
        "heading_path": focus.heading_path,
        "excerpt": _compact_text(focus.excerpt, limit=500),
        "confidence": focus.confidence,
        "reason": focus.reason,
    }


def _solution_payload(solution: ComplexProblemSolution | None) -> dict[str, Any]:
    if solution is None:
        return {
            "status": "unavailable",
            "message": "The hidden reasoning tool is not configured or did not return a usable result.",
        }
    return {
        "status": "ok",
        "model": solution.model,
        "reasoning_effort": solution.reasoning_effort,
        "summary": solution.summary,
        "answer": solution.answer,
        "confidence": solution.confidence,
        "limits": solution.limits,
    }


def execute_realtime_tool(
    session: RealtimeToolSession,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        _validate_tool_scope(session, arguments)
        if tool_name == "run_chatbot_workflow":
            message = _compact_text(str(arguments.get("message") or ""), limit=4000)
            if not message:
                raise ValueError("message is required")
            from app.services.chat_service import process_chat_on_lesson

            response = process_chat_on_lesson(
                session.lesson_id,
                ChatRequest(message=message),
                user_id=session.user_id,
            )
            payload = {
                "status": "ok",
                "chatbot_message": response.chatbot_message,
                "board_action": response.board_decision.action,
                "needs_clarification": response.needs_clarification,
                "requirement_cleared": response.requirement_cleared,
                "resolved_focus": _safe_focus_payload(response),
            }
        elif tool_name == "solve_complex_problem":
            question = _compact_text(str(arguments.get("question") or ""), limit=4000)
            if not question:
                raise ValueError("question is required")
            workspace = workspace_state.load_workspace_for_user(session.user_id)
            _, lesson = workspace_state.find_lesson_package(workspace, session.lesson_id)
            solution = openai_course_ai.solve_complex_problem(
                lesson_title=lesson.title,
                question=question,
                target_excerpt=_compact_text(str(arguments.get("target_excerpt") or ""), limit=1600),
                board_summary=_compact_text(lesson.board_document.content_text, limit=2400),
                resource_summary="",
                desired_output=_compact_text(str(arguments.get("desired_output") or ""), limit=800),
                high_value=False,
            )
            payload = _solution_payload(solution)
        else:
            raise ValueError(f"Unsupported realtime tool: {tool_name}")
        ai_usage_logger.log_event(
            "realtime_tool_call",
            tool_name=tool_name,
            call_id=session.call_id,
            lesson_id=session.lesson_id,
            client_session_id=session.client_session_id,
            status=payload.get("status", "ok"),
        )
        return payload
    except Exception as exc:
        ai_usage_logger.log_event(
            "realtime_tool_call_error",
            tool_name=tool_name,
            call_id=session.call_id,
            lesson_id=session.lesson_id,
            client_session_id=session.client_session_id,
            error=str(exc),
        )
        return {"status": "error", "message": str(exc)}


def _tool_call_from_event(event: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    if event.get("type") == "response.function_call_arguments.done":
        raw_args = event.get("arguments") or "{}"
        return str(event.get("call_id") or ""), str(event.get("name") or ""), json.loads(raw_args)
    item = event.get("item") if isinstance(event.get("item"), dict) else None
    if item and item.get("type") == "function_call" and item.get("status") in {None, "completed"}:
        raw_args = item.get("arguments") or "{}"
        return str(item.get("call_id") or ""), str(item.get("name") or ""), json.loads(raw_args)
    response = event.get("response") if isinstance(event.get("response"), dict) else None
    output_items = response.get("output", []) if response else []
    if isinstance(output_items, list):
        for output_item in output_items:
            if not isinstance(output_item, dict):
                continue
            if output_item.get("type") != "function_call" or output_item.get("status") not in {None, "completed"}:
                continue
            raw_args = output_item.get("arguments") or "{}"
            return str(output_item.get("call_id") or ""), str(output_item.get("name") or ""), json.loads(raw_args)
    return None


async def _run_sideband(session: RealtimeToolSession, *, api_key: str) -> None:
    base_url = os.getenv("OPENAI_REALTIME_WS_BASE_URL", "wss://api.openai.com/v1/realtime").rstrip("/")
    url = f"{base_url}?call_id={session.call_id}"
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {api_key}"}) as websocket:
        async for raw_event in websocket:
            try:
                event = json.loads(raw_event)
                tool_call = _tool_call_from_event(event)
                if tool_call is None:
                    continue
                call_id, tool_name, arguments = tool_call
                result = execute_realtime_tool(session, tool_name, arguments)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(result, ensure_ascii=False),
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                await websocket.send(json.dumps({"type": "response.create"}))
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning("Realtime sideband event handling failed: %s", exc)


def start_sideband_session(session: RealtimeToolSession, *, api_key: str) -> threading.Thread | None:
    if not _env_truthy("OPENCLASS_REALTIME_TOOLS_ENABLED"):
        return None

    def run() -> None:
        try:
            asyncio.run(_run_sideband(session, api_key=api_key))
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            ai_usage_logger.log_event(
                "realtime_sideband_error",
                call_id=session.call_id,
                lesson_id=session.lesson_id,
                client_session_id=session.client_session_id,
                error=str(exc),
            )
            logger.warning("Realtime sideband session failed: %s", exc)

    thread = threading.Thread(target=run, name=f"realtime-sideband-{session.call_id}", daemon=True)
    thread.start()
    return thread
