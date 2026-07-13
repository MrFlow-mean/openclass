from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.services.openai_course_ai import openai_course_ai


class MediaTranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaTranscript:
    text: str
    provider: str
    model: str
    language: str = ""


class MediaTranscriptionProvider(Protocol):
    def transcribe(self, path: Path, *, mime_type: str) -> MediaTranscript: ...


class OpenAITranscriptionProvider:
    def transcribe(self, path: Path, *, mime_type: str) -> MediaTranscript:
        client = openai_course_ai.client
        if client is None:
            raise MediaTranscriptionError("当前没有配置可用的 STT 模型，无法转写音视频资料。")
        model = os.getenv("OPENCLASS_STT_MODEL", "gpt-4o-mini-transcribe")
        try:
            with path.open("rb") as source_file:
                response = client.audio.transcriptions.create(
                    model=model,
                    file=(path.name, source_file, mime_type or "application/octet-stream"),
                    response_format="json",
                )
        except Exception as exc:  # pragma: no cover - provider/runtime dependent
            raise MediaTranscriptionError(str(exc)) from exc
        text = str(getattr(response, "text", "") or "").strip()
        if not text and isinstance(response, dict):
            text = str(response.get("text") or "").strip()
        if not text:
            raise MediaTranscriptionError("STT 模型没有返回可索引的文字稿。")
        language = str(getattr(response, "language", "") or "")
        return MediaTranscript(text=text, provider="openai", model=model, language=language)


media_transcription_provider: MediaTranscriptionProvider = OpenAITranscriptionProvider()
