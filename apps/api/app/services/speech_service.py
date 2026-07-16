from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from app.services.config import load_root_dotenv


class SpeechNotConfiguredError(RuntimeError):
    pass


class SpeechGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    media_type: str
    provider: str
    model: str
    voice: str


@dataclass(frozen=True)
class SpeechVoiceOption:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class SpeechOptions:
    provider: str
    model: str
    default_voice: str
    voices: tuple[SpeechVoiceOption, ...]
    minimum_speech_rate: int
    maximum_speech_rate: int
    default_speech_rate: int


SpeechProvider = Callable[[str, str | None, int | None], SpeechAudio]


def _volcengine_provider(text: str, voice: str | None, speech_rate: int | None) -> SpeechAudio:
    from app.services.volcengine_speech import synthesize_volcengine_speech

    return synthesize_volcengine_speech(text, speaker=voice, speech_rate=speech_rate)


SPEECH_PROVIDERS: dict[str, SpeechProvider] = {
    "volcengine": _volcengine_provider,
}


def get_speech_options() -> SpeechOptions:
    load_root_dotenv()
    provider_name = os.getenv("OPENCLASS_SPEECH_PROVIDER", "volcengine").strip().lower() or "volcengine"
    if provider_name != "volcengine":
        raise SpeechNotConfiguredError(f"Unsupported speech provider: {provider_name}")

    from app.services.volcengine_speech import get_volcengine_speech_options

    return get_volcengine_speech_options()


def synthesize_speech(
    text: str,
    *,
    voice: str | None = None,
    speech_rate: int | None = None,
) -> SpeechAudio:
    load_root_dotenv()
    normalized_text = text.strip()
    if not normalized_text:
        raise SpeechGenerationError("Speech input is empty")

    provider_name = os.getenv("OPENCLASS_SPEECH_PROVIDER", "volcengine").strip().lower() or "volcengine"
    provider = SPEECH_PROVIDERS.get(provider_name)
    if provider is None:
        raise SpeechNotConfiguredError(f"Unsupported speech provider: {provider_name}")
    return provider(normalized_text, voice, speech_rate)
