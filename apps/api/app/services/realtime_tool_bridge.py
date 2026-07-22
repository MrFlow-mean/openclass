from __future__ import annotations

from typing import Any

from app.models import ChatRequest, RealtimeToolCallRequest, RealtimeToolCallResponse
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger
from app.services.realtime_board_context import read_realtime_board_context


def realtime_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "read_board_context",
            "description": (
                "Read a bounded, authorized range from the current OpenClass board. "
                "Call this before discussing, explaining, quoting, or role-playing from board content. "
                "Use mode=current_selection for the learner's active board references; the client may return an "
                "ordered references array when the learner accumulated more than one selection. Use every item "
                "in that array without replacing an earlier reference with a later one. Use mode=outline to inspect headings, "
                "or mode=target with a location, heading, example, phrase, or section description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["target", "current_selection", "outline"]},
                    "target": {"type": "string", "description": "Requested board location or content description."},
                    "max_chars": {"type": "integer", "minimum": 800, "maximum": 12000},
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "run_chatbot_workflow",
            "description": (
                "Run the existing OpenClass Chatbot workflow for a learner request that needs document edits, "
                "board generation, durable teaching progress, clarification, or deeper orchestration. "
                "After success, present chatbot_message faithfully to the learner."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The learner request to process."},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    ]


def execute_realtime_tool(
    *,
    lesson_id: str,
    user_id: str,
    request: RealtimeToolCallRequest,
) -> RealtimeToolCallResponse:
    workspace = workspace_state.load_workspace_for_user(user_id)
    workspace_state.find_lesson_package(workspace, lesson_id)
    try:
        if request.name == "read_board_context":
            result = read_realtime_board_context(
                lesson_id=lesson_id,
                user_id=user_id,
                arguments=request.arguments,
                selection=request.selection,
            )
            response = RealtimeToolCallResponse(
                status="ok",
                model_output=result.model_output,
                resolved_focus=result.focus,
            )
        elif request.name == "run_chatbot_workflow":
            message = str(request.arguments.get("message") or "").strip()
            if not message:
                raise ValueError("message is required")
            from app.services.chat_service import process_chat_on_lesson

            chat_response = process_chat_on_lesson(
                lesson_id,
                ChatRequest(message=message, selection=request.selection),
                user_id=user_id,
                commit_metadata={
                    "chat_visibility": "hidden",
                    "interaction_channel": "realtime_tool",
                    "realtime_client_session_id": request.client_session_id,
                    "realtime_turn_id": request.turn_id or "",
                },
            )
            focus = _latest_resolved_focus(chat_response.course_package, lesson_id)
            response = RealtimeToolCallResponse(
                status="ok",
                model_output={
                    "status": "ok",
                    "chatbot_message": chat_response.chatbot_message,
                    "needs_clarification": chat_response.needs_clarification,
                    "clarification_questions": chat_response.clarification_questions,
                    "instruction": "Present chatbot_message faithfully and naturally. Do not claim an action beyond this result.",
                },
                resolved_focus=focus,
                course_package=chat_response.course_package,
            )
        else:  # pragma: no cover - guarded by the request schema
            raise ValueError(f"Unsupported realtime tool: {request.name}")
        ai_usage_logger.log_event(
            "realtime_tool_call",
            tool_name=request.name,
            tool_call_id=request.call_id,
            lesson_id=lesson_id,
            client_session_id=request.client_session_id,
            status=response.status,
        )
        return response
    except Exception as exc:
        ai_usage_logger.log_event(
            "realtime_tool_call_error",
            tool_name=request.name,
            tool_call_id=request.call_id,
            lesson_id=lesson_id,
            client_session_id=request.client_session_id,
            error=str(exc),
        )
        return RealtimeToolCallResponse(
            status="error",
            model_output={"status": "error", "message": str(exc)},
        )


def _latest_resolved_focus(course_package, lesson_id: str):
    lesson = next((item for item in course_package.lessons if item.id == lesson_id), None)
    if lesson is None:
        return None
    branch = lesson.history_graph.branches.get(lesson.history_graph.current_branch)
    commit_id = branch.head_commit_id if branch else None
    commit = next((item for item in lesson.history_graph.commits if item.id == commit_id), None)
    raw_focus = commit.metadata.get("resolved_focus") if commit and isinstance(commit.metadata, dict) else None
    if not isinstance(raw_focus, dict):
        return None
    from app.models import BoardFocusRef

    try:
        return BoardFocusRef.model_validate(raw_focus)
    except ValueError:
        return None
