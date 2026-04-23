from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.models import Lesson
from app.services.ai_logging import ai_usage_logger

load_dotenv()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


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
            "model": self.config.model,
            "voice": self.config.voice,
        }

    def _build_instructions(self, *, lesson: Lesson, latest_assistant_message: str | None) -> str:
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

    def create_call(
        self,
        *,
        lesson: Lesson,
        offer_sdp: str,
        latest_assistant_message: str | None,
    ) -> str:
        instructions = self._build_instructions(
            lesson=lesson,
            latest_assistant_message=latest_assistant_message,
        )
        session = {
            "type": "realtime",
            "model": self.config.model,
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
                model=self.config.model,
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
                model=self.config.model,
                voice=self.config.voice,
                transcription_model=self.config.transcription_model,
                offer_sdp=offer_sdp,
                session=session,
                error=str(exc),
            )
            raise

        ai_usage_logger.log_event(
            "openai_realtime_session",
            model=self.config.model,
            voice=self.config.voice,
            transcription_model=self.config.transcription_model,
            offer_sdp=offer_sdp,
            session=session,
            response_id=getattr(response, "id", None),
            answer_sdp=response.text,
        )
        return response.text


openai_realtime_teacher = OpenAIRealtimeTeacher()
