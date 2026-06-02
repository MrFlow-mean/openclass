from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import AIModelSelection, RealtimeConnectRequest, RealtimeConnectResponse, new_id
from app.services import workspace_state
from app.services.ai_logging import ai_usage_logger, log_ai_interaction_message
from app.services.ai_model_catalog import (
    OPENAI_DEFAULT_REALTIME_MODEL,
    OPENAI_OFFICIAL_BASE_URL,
    default_realtime_selection,
    realtime_runtime_enabled,
    selection_from_raw,
)
from app.services.config import load_root_dotenv
from app.services.realtime_tool_bridge import RealtimeToolSession, realtime_tool_schemas, start_sideband_session


load_root_dotenv()


class RealtimeServiceError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RealtimeSessionConfig:
    provider: str
    model: str
    voice: str
    tools_enabled: bool
    client_session_id: str
    session_payload: dict[str, Any]


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_optional_secret(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith("your_") or normalized.startswith("你的_"):
        return None
    return normalized


def _openai_api_key() -> str | None:
    return _normalize_optional_secret(os.getenv("OPENAI_API_KEY"))


def _openai_realtime_base_url() -> str:
    return os.getenv("OPENAI_REALTIME_BASE_URL", os.getenv("OPENAI_BASE_URL", OPENAI_OFFICIAL_BASE_URL)).rstrip("/")


def _compact_text(value: str | None, *, limit: int = 1200) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}..."


def _hashed_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def _call_id_from_location(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/").split("/")[-1] or None


def _realtime_instructions(
    *,
    lesson_title: str,
    latest_assistant_message: str | None,
    board_summary: str,
    tools_enabled: bool,
) -> str:
    tool_rule = (
        "可以调用后端工具，但只能调用工具列表中存在的函数；课程状态、板书修改和强推理都由后端工具执行。"
        if tools_enabled
        else "当前实时会话只负责自然语音交流和转写，不直接执行课程工具。"
    )
    return (
        "你是 OpenClass 的 Chatbot 的实时语音形态，和文字 Chatbot 是同一个角色。\n"
        "你负责用自然、简洁、连续的中文和学习者交流；不要声称自己是另一个老师或独立代理。\n"
        "不要根据具体学科、教材、考试或样例写特殊规则；只依据用户输入、课程上下文和后端工具结果回答。\n"
        "你没有直接读取右侧板书文档全文或摘要的权限；如果需要讲解板书，必须调用后端工具，"
        "并只依据工具/板书侧 directive 返回的目标摘录和指令回答。\n"
        f"{tool_rule}\n"
        "如果需要修改文档、生成板书、定位片段、更新学习需求或解决复杂问题，先调用合适工具；"
        "只有工具成功后才说动作已经完成。\n"
        "如果工具返回结果，你要把结果组织成 Chatbot 面向学习者的直接回答，不暴露内部工具名。\n"
        f"当前课程标题：{lesson_title}\n"
        "当前板书摘要：已隔离，实时 Chatbot 不能直接读取；请通过后端工具获取被授权的目标信息。\n"
        f"最近 Chatbot 回复：{_compact_text(latest_assistant_message, limit=500) or '无'}"
    )


def _select_realtime_model(raw: AIModelSelection | None) -> AIModelSelection:
    return selection_from_raw(raw, default=default_realtime_selection())


def build_openai_realtime_session_config(
    *,
    lesson_title: str,
    board_summary: str,
    request: RealtimeConnectRequest,
) -> RealtimeSessionConfig:
    selected = _select_realtime_model(request.realtime_model)
    if selected.provider != "openai":
        raise RealtimeServiceError(400, "当前实时语音工具链只支持 OpenAI WebRTC 模型。")
    model = selected.model or os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL)
    voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")
    client_session_id = request.client_session_id or new_id("realtime")
    tools_enabled = _env_truthy("OPENCLASS_REALTIME_TOOLS_ENABLED")
    session_payload: dict[str, Any] = {
        "type": "realtime",
        "model": model,
        "instructions": _realtime_instructions(
            lesson_title=lesson_title,
            latest_assistant_message=request.latest_assistant_message,
            board_summary=board_summary,
            tools_enabled=tools_enabled,
        ),
        "audio": {
            "input": {
                "transcription": {
                    "model": os.getenv("OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"),
                }
            },
            "output": {"voice": voice},
        },
    }
    if tools_enabled:
        session_payload["tools"] = realtime_tool_schemas()
        session_payload["tool_choice"] = "auto"
    return RealtimeSessionConfig(
        provider="openai",
        model=model,
        voice=voice,
        tools_enabled=tools_enabled,
        client_session_id=client_session_id,
        session_payload=session_payload,
    )


def connect_openai_realtime_session(
    lesson_id: str,
    request: RealtimeConnectRequest,
    *,
    user_id: str,
) -> RealtimeConnectResponse:
    if not realtime_runtime_enabled():
        raise RealtimeServiceError(410, "实时语音后端运行路径未启用；请设置 OPENCLASS_REALTIME_ENABLED=true。")
    api_key = _openai_api_key()
    if not api_key:
        raise RealtimeServiceError(503, "OpenAI realtime 需要配置 OPENAI_API_KEY。")
    if not request.offer_sdp.strip():
        raise RealtimeServiceError(400, "Missing WebRTC offer SDP.")

    workspace = workspace_state.load_workspace_for_user(user_id)
    _package, lesson = workspace_state.find_lesson_package(workspace, lesson_id)
    config = build_openai_realtime_session_config(
        lesson_title=lesson.title,
        board_summary=lesson.board_document.content_text,
        request=request,
    )
    files = {
        "sdp": (None, request.offer_sdp, "application/sdp"),
        "session": (None, json.dumps(config.session_payload, ensure_ascii=False), "application/json"),
    }
    url = f"{_openai_realtime_base_url()}/realtime/calls"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": _hashed_user_id(user_id),
    }
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, files=files)
    except httpx.HTTPError as exc:
        ai_usage_logger.log_event(
            "openai_realtime_call_error",
            lesson_id=lesson_id,
            client_session_id=config.client_session_id,
            model=config.model,
            error=str(exc),
        )
        raise RealtimeServiceError(502, f"OpenAI realtime 连接失败：{exc}") from exc

    if response.status_code >= 400:
        ai_usage_logger.log_event(
            "openai_realtime_call_error",
            lesson_id=lesson_id,
            client_session_id=config.client_session_id,
            model=config.model,
            status_code=response.status_code,
            error=response.text[:1000],
        )
        raise RealtimeServiceError(response.status_code, response.text or "OpenAI realtime 连接失败。")

    call_id = _call_id_from_location(response.headers.get("Location"))
    ai_usage_logger.log_event(
        "openai_realtime_call_created",
        lesson_id=lesson_id,
        client_session_id=config.client_session_id,
        model=config.model,
        voice=config.voice,
        call_id=call_id,
        tools_enabled=config.tools_enabled,
    )
    if config.tools_enabled and call_id:
        start_sideband_session(
            RealtimeToolSession(
                call_id=call_id,
                lesson_id=lesson_id,
                user_id=user_id,
                client_session_id=config.client_session_id,
            ),
            api_key=api_key,
        )
    return RealtimeConnectResponse(
        answer_sdp=response.text,
        provider=config.provider,
        model=config.model,
        voice=config.voice,
        call_id=call_id,
        tools_enabled=config.tools_enabled,
        client_session_id=config.client_session_id,
    )


def log_realtime_transcript_event(lesson_id: str, request, *, user_id: str) -> dict[str, str]:
    if not realtime_runtime_enabled():
        raise RealtimeServiceError(410, "实时语音后端运行路径未启用；请设置 OPENCLASS_REALTIME_ENABLED=true。")
    workspace = workspace_state.load_workspace_for_user(user_id)
    workspace_state.find_lesson_package(workspace, lesson_id)
    direction = "inbound" if request.role == "user" else "outbound"
    log_ai_interaction_message(
        channel="realtime",
        direction=direction,
        role=request.role,
        transport=request.transport_event_type,
        content=request.transcript,
        metadata={
            "lesson_id": lesson_id,
            "lesson_title": request.lesson_title,
            "client_session_id": request.client_session_id,
            "tool_name": request.tool_name,
            "tool_call_id": request.tool_call_id,
            "tool_status": request.tool_status,
        },
    )
    return {"status": "ok"}
