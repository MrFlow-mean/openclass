from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import AIModelSelection, Lesson
from app.services.ai_model_catalog import GOOGLE_DEFAULT_REALTIME_MODEL
from app.services.ai_logging import ai_usage_logger

load_dotenv()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


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
        "You are Teacher AI for an AI blackboard teaching workbench. "
        "Speak in Chinese, with short and clear sentences that are easy to follow in voice mode. "
        "Teach from the current lesson and board only. "
        "If the learner asks for a board edit, lesson creation, branch change, or any persistent change, "
        "explain briefly that voice mode is for real-time teaching conversation and ask them to use the text chat for structural edits. "
        "Prefer concrete examples, repeat critical formulas slowly, and keep answers supportive and accessible. "
        "Here is the current lesson context as JSON:\n"
        f"{_json(board_context)}"
    )


class OpenAIRealtimeConfig(BaseModel):
    api_key: str | None = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    base_url: str | None = Field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")
    )
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
            if self.config.enabled
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
            "max_output_tokens": 1024,
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": {
                        "model": self.config.transcription_model,
                        "language": "zh",
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
                    "speed": 1.0,
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
    api_key: str | None = Field(default_factory=lambda: os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
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
            "config": {
                "model": model_path,
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": self.config.voice,
                        }
                    }
                },
                "systemInstruction": {
                    "parts": [{"text": instructions}],
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }
        }

        token = self._create_ephemeral_token(model_path=model_path)
        websocket_url = (
            f"{self.config.base_url.rstrip('/').replace('https://', 'wss://').replace('http://', 'ws://')}"
            "/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
            f"?access_token={urllib.parse.quote(token)}"
        )
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
            "websocket_url": websocket_url,
            "setup": setup,
        }

    def _create_ephemeral_token(self, *, model_path: str) -> str:
        payload = {"uses": 1}
        data = json.dumps(payload).encode("utf-8")
        url = (
            f"{self.config.base_url.rstrip('/')}/v1alpha/authTokens"
            f"?key={urllib.parse.quote(self.config.api_key or '')}"
        )
        request = urllib.request.Request(
            url,
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            ai_usage_logger.log_event(
                "google_realtime_session_error",
                model=model_path,
                voice=self.config.voice,
                error=f"Google auth token error {exc.code}: {body}",
            )
            raise RuntimeError(f"Google Live token failed: {body}") from exc

        token = raw.get("name") or raw.get("token") or raw.get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Google Live token response did not include a usable token")
        return token


openai_realtime_teacher = OpenAIRealtimeTeacher()
google_realtime_teacher = GoogleRealtimeTeacher()
