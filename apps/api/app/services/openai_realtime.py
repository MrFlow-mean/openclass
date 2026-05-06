from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import AIModelSelection, Lesson
from app.services.ai_model_catalog import (
    GOOGLE_DEFAULT_REALTIME_MODEL,
    OPENAI_DEFAULT_REALTIME_MODEL,
    OPENAI_GATEWAY_BASE_URL,
)
from app.services.ai_logging import ai_usage_logger


def _load_root_dotenv() -> None:
    root_env = Path(__file__).resolve().parents[4] / ".env"
    if root_env.exists():
        load_dotenv(root_env)
        return
    load_dotenv()


_load_root_dotenv()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _env_realtime_or_shared(name: str, shared_name: str) -> str | None:
    if name in os.environ:
        return _normalize_optional_api_key(os.getenv(name))
    return _normalize_optional_api_key(os.getenv(shared_name) or os.getenv("AI_API_KEY"))


def _env_realtime_base_url() -> str:
    return (
        os.getenv("OPENAI_REALTIME_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or OPENAI_GATEWAY_BASE_URL
    )


def _single_api_key_mode() -> bool:
    return (os.getenv("AI_SINGLE_API_KEY_MODE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _openai_realtime_allowed() -> bool:
    if _normalize_optional_api_key(os.getenv("OPENAI_REALTIME_API_KEY")):
        return True
    base_url = _env_realtime_base_url().lower()
    return not _single_api_key_mode() or "api.openai.com" in base_url


def _google_api_key() -> str | None:
    return _normalize_optional_api_key(
        os.getenv("GOOGLE_REALTIME_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )


def _normalize_optional_api_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"none", "null", "disabled", "false", "0"}:
        return None
    if normalized.startswith("你的_") or normalized.startswith("your_"):
        return None
    return normalized


def build_realtime_instructions(*, lesson: Lesson, latest_assistant_message: str | None) -> str:
    board_context = {
        "lesson_title": lesson.title,
        "lesson_summary": lesson.summary,
        "lesson_tags": lesson.tags,
        "learning_requirements": (
            lesson.learning_requirements.model_dump(mode="json")
            if lesson.learning_requirements
            else None
        ),
        "board_document": {
            "title": lesson.board_document.title,
            "content_text": lesson.board_document.content_text,
            "content_html": lesson.board_document.content_html,
        },
        "latest_teacher_message": latest_assistant_message,
    }

    return (
        "你是 OpenClass 的实时语音 PM AI，负责和学习者自然对话，探寻学习需求。"
        "你的目标不是直接生成讲义，也不是长篇讲课，而是快速弄清楚：学习主题、当前水平/已学背景、学习目的或使用场景、"
        "希望的讲义形态、目标深度、必须覆盖或避免的内容。"
        "每轮最多问一个高价值问题；如果用户已经说得足够清楚，就简短确认“需求已经够了，后台会整理需求清单并进入下一步”。"
        "当用户纠正自己时，以最新表达为准，替换旧需求。"
        "不要暴露 JSON、模型名、后台流程或字段名；用自然简体中文口语交流。"
        "后台会把你和用户的完整转写交给 GPT-5.4 nano 整理成 LearningRequirementSheet，所以你只需要把对话推进清楚。"
        "当前课程上下文 JSON：\n"
        f"{_json(board_context)}"
    )


class OpenAIRealtimeConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: _env_realtime_or_shared("OPENAI_REALTIME_API_KEY", "OPENAI_API_KEY"))
    base_url: str | None = Field(default_factory=_env_realtime_base_url)
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_REALTIME_MODEL", OPENAI_DEFAULT_REALTIME_MODEL))
    voice: str = Field(default_factory=lambda: os.getenv("OPENAI_REALTIME_VOICE", "marin"))
    transcription_model: str = Field(
        default_factory=lambda: os.getenv(
            "OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
        )
    )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class OpenAIRealtimeTeacher:
    def __init__(self) -> None:
        self.config = OpenAIRealtimeConfig()
        self.client = (
            OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
            if self.config.enabled and _openai_realtime_allowed()
            else None
        )

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": "openai",
            "model": self.config.model,
            "voice": self.config.voice,
        }

    def _build_instructions(self, *, lesson: Lesson, latest_assistant_message: str | None) -> str:
        return build_realtime_instructions(
            lesson=lesson,
            latest_assistant_message=latest_assistant_message,
        )

    def create_call(
        self,
        *,
        lesson: Lesson,
        offer_sdp: str,
        latest_assistant_message: str | None,
        model_selection: AIModelSelection | None = None,
    ) -> str:
        model = (
            model_selection.model
            if model_selection and model_selection.provider == "openai" and model_selection.model
            else self.config.model
        )
        instructions = self._build_instructions(
            lesson=lesson,
            latest_assistant_message=latest_assistant_message,
        )
        session = {
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": {
                        "model": self.config.transcription_model,
                        "language": "zh",
                        "prompt": instructions,
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                        "interrupt_response": True,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 650,
                    },
                },
                "output": {
                    "voice": self.config.voice,
                },
            },
        }
        if not self.client:
            ai_usage_logger.log_event(
                "openai_realtime_session_error",
                model=model,
                voice=self.config.voice,
                transcription_model=self.config.transcription_model,
                offer_sdp=offer_sdp,
                session=session,
                error="client_disabled",
            )
            raise RuntimeError("OpenAI Realtime is not configured")

        try:
            response = self.client.realtime.calls.create(
                sdp=offer_sdp,
                session=session,
            )
        except Exception as exc:
            ai_usage_logger.log_event(
                "openai_realtime_session_error",
                model=model,
                voice=self.config.voice,
                transcription_model=self.config.transcription_model,
                offer_sdp=offer_sdp,
                session=session,
                error=str(exc),
            )
            raise

        ai_usage_logger.log_event(
            "openai_realtime_session",
            model=model,
            voice=self.config.voice,
            transcription_model=self.config.transcription_model,
            offer_sdp=offer_sdp,
            session=session,
            response_id=getattr(response, "id", None),
            answer_sdp=response.text,
        )
        return response.text


class GoogleRealtimeConfig(BaseModel):
    api_key: str | None = Field(default_factory=_google_api_key)
    base_url: str = Field(
        default_factory=lambda: os.getenv(
            "GOOGLE_GENERATIVE_LANGUAGE_BASE_URL",
            "https://generativelanguage.googleapis.com",
        )
    )
    model: str = Field(default_factory=lambda: os.getenv("GOOGLE_REALTIME_MODEL", GOOGLE_DEFAULT_REALTIME_MODEL))
    voice: str = Field(default_factory=lambda: os.getenv("GOOGLE_REALTIME_VOICE", "Aoede"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class GoogleRealtimeTeacher:
    def __init__(self) -> None:
        self.config = GoogleRealtimeConfig()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": "google",
            "model": self.config.model,
            "voice": self.config.voice,
        }

    def create_live_session(
        self,
        *,
        lesson: Lesson,
        latest_assistant_message: str | None,
        model_selection: AIModelSelection | None = None,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            ai_usage_logger.log_event(
                "google_realtime_session_error",
                model=self.config.model,
                voice=self.config.voice,
                error="client_disabled",
            )
            raise RuntimeError("Google Gemini Live is not configured")

        model = (
            model_selection.model
            if model_selection and model_selection.provider == "google" and model_selection.model
            else self.config.model
        )
        model_path = model if model.startswith("models/") else f"models/{model}"
        instructions = build_realtime_instructions(
            lesson=lesson,
            latest_assistant_message=latest_assistant_message,
        )
        setup = {
            "setup": {
                "model": model_path,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": self.config.voice,
                            }
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": instructions}],
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }

        ai_usage_logger.log_event(
            "google_realtime_session",
            model=model,
            voice=self.config.voice,
            setup=setup,
        )
        return {
            "provider": "google",
            "model": model,
            "voice": self.config.voice,
            "websocket_url": "",
            "setup": setup,
        }

    def websocket_url(self) -> str:
        if not self.config.api_key:
            raise RuntimeError("Google Gemini Live is not configured")

        base_url = self.config.base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        return (
            f"{base_url}/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={urllib.parse.quote(self.config.api_key)}"
        )


openai_realtime_teacher = OpenAIRealtimeTeacher()
google_realtime_teacher = GoogleRealtimeTeacher()
