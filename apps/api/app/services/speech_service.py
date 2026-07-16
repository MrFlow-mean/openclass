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


SpeechProvider = Callable[[str], SpeechAudio]


def _volcengine_provider(text: str) -> SpeechAudio:
    from app.services.volcengine_speech import synthesize_volcengine_speech

    return synthesize_volcengine_speech(text)


SPEECH_PROVIDERS: dict[str, SpeechProvider] = {
    "volcengine": _volcengine_provider,
}


def synthesize_speech(text: str) -> SpeechAudio:
    load_root_dotenv()
    normalized_text = text.strip()
    if not normalized_text:
        raise SpeechGenerationError("Speech input is empty")

    provider_name = os.getenv("OPENCLASS_SPEECH_PROVIDER", "volcengine").strip().lower() or "volcengine"
    provider = SPEECH_PROVIDERS.get(provider_name)
    if provider is None:
        raise SpeechNotConfiguredError(f"Unsupported speech provider: {provider_name}")
    return provider(normalized_text)
