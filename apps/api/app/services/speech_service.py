from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from openai import OpenAI

from app.services.config import load_root_dotenv


logger = logging.getLogger(__name__)

DEFAULT_SPEECH_MODEL = "tts-1"
DEFAULT_SPEECH_VOICE = "marin"
DEFAULT_SPEECH_SPEED = 1.0


class SpeechNotConfiguredError(RuntimeError):
    pass


class SpeechGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    model: str
    voice: str


def _configured_speed() -> float:
    raw_speed = os.getenv("OPENAI_TTS_SPEED", str(DEFAULT_SPEECH_SPEED)).strip()
    try:
        speed = float(raw_speed)
    except ValueError:
        return DEFAULT_SPEECH_SPEED
    return max(0.25, min(4.0, speed))


def synthesize_speech(text: str) -> SpeechAudio:
    load_root_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SpeechNotConfiguredError("OPENAI_API_KEY is not configured")

    normalized_text = text.strip()
    if not normalized_text:
        raise SpeechGenerationError("Speech input is empty")

    model = os.getenv("OPENAI_TTS_MODEL", DEFAULT_SPEECH_MODEL).strip() or DEFAULT_SPEECH_MODEL
    voice = os.getenv("OPENAI_TTS_VOICE", DEFAULT_SPEECH_VOICE).strip() or DEFAULT_SPEECH_VOICE
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    client_options: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_options["base_url"] = base_url

    try:
        client = OpenAI(**client_options)
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            input=normalized_text,
            response_format="mp3",
            speed=_configured_speed(),
        )
        content = response.content
    except Exception as exc:  # The SDK exposes several provider and transport exception types.
        logger.exception("Speech generation failed for model %s", model)
        raise SpeechGenerationError("Speech provider request failed") from exc

    if not content:
        raise SpeechGenerationError("Speech provider returned empty audio")
    return SpeechAudio(content=content, model=model, voice=voice)
