from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.models import AIModelSelection


class MediaTranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscribedSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float = 0.0


@dataclass(frozen=True)
class MediaTranscriptionResult:
    language: str
    provider: str
    model: str
    segments: tuple[TranscribedSegment, ...]


def media_runtime_status() -> dict[str, bool]:
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
    }


def probe_duration_seconds(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MediaTranscriptionError("ffprobe is required for video ingestion.")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise MediaTranscriptionError(completed.stderr.strip() or "Video duration could not be read.")
    try:
        return max(0.0, float(completed.stdout.strip()))
    except ValueError as exc:
        raise MediaTranscriptionError("Video duration metadata is invalid.") from exc


def extract_audio_segments(video_path: Path, output_dir: Path, *, segment_seconds: int = 1200) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MediaTranscriptionError("ffmpeg is required for video transcription.")
    output_dir.mkdir(parents=True, exist_ok=True)
    template = output_dir / "audio-%04d.mp3"
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(template),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if completed.returncode != 0:
        raise MediaTranscriptionError(completed.stderr.strip() or "Audio extraction failed.")
    outputs = sorted(output_dir.glob("audio-*.mp3"))
    if not outputs:
        raise MediaTranscriptionError("Video did not contain a usable audio track.")
    return outputs


class OpenAITranscriptionProvider:
    def transcribe(
        self,
        audio_files: list[Path],
        *,
        selection: AIModelSelection,
        segment_seconds: int = 1200,
        progress: Callable[[int, int], None] | None = None,
    ) -> MediaTranscriptionResult:
        if selection.provider != "openai":
            raise MediaTranscriptionError("Selected model is not registered for transcription.")
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise MediaTranscriptionError("OPENAI_API_KEY is required for the selected transcription model.")
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise MediaTranscriptionError("OpenAI SDK is unavailable.") from exc
        client = OpenAI(
            api_key=api_key,
            base_url=(os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        )
        all_segments: list[TranscribedSegment] = []
        detected_language = ""
        for file_index, audio_path in enumerate(audio_files):
            offset_ms = file_index * segment_seconds * 1000
            try:
                with audio_path.open("rb") as handle:
                    request: dict[str, Any] = {
                        "model": selection.model,
                        "file": handle,
                        "response_format": (
                            "verbose_json" if selection.model == "whisper-1" else "json"
                        ),
                    }
                    if selection.model == "whisper-1":
                        request["timestamp_granularities"] = ["segment"]
                    response = client.audio.transcriptions.create(**request)
            except Exception as exc:
                raise MediaTranscriptionError(f"Audio transcription failed: {exc}") from exc
            payload = response.model_dump() if hasattr(response, "model_dump") else response
            if not isinstance(payload, dict):
                raise MediaTranscriptionError("Transcription model returned an invalid response.")
            detected_language = detected_language or str(payload.get("language") or "")
            raw_segments = payload.get("segments")
            if not isinstance(raw_segments, list):
                text = str(payload.get("text") or "").strip()
                duration = float(payload.get("duration") or segment_seconds)
                if text:
                    all_segments.append(
                        TranscribedSegment(
                            start_ms=offset_ms,
                            end_ms=offset_ms + max(1, int(duration * 1000)),
                            text=text,
                        )
                    )
                if progress:
                    progress(file_index + 1, len(audio_files))
                continue
            for raw in raw_segments:
                item = _as_mapping(raw)
                text = str(item.get("text") or "").strip()
                start = float(item.get("start") or 0)
                end = float(item.get("end") or start)
                if text and end > start:
                    all_segments.append(
                        TranscribedSegment(
                            start_ms=offset_ms + int(start * 1000),
                            end_ms=offset_ms + int(end * 1000),
                            text=" ".join(text.split()),
                            confidence=_segment_confidence(item),
                        )
                    )
            if progress:
                progress(file_index + 1, len(audio_files))
        if not all_segments:
            raise MediaTranscriptionError("Transcription completed without usable text.")
        return MediaTranscriptionResult(
            language=detected_language,
            provider=selection.provider,
            model=selection.model,
            segments=tuple(all_segments),
        )


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        payload = value.model_dump()
        return payload if isinstance(payload, dict) else {}
    return {}


def _segment_confidence(item: dict[str, Any]) -> float:
    probability = item.get("avg_logprob")
    if isinstance(probability, (int, float)):
        # Preserve a bounded quality signal without pretending log-probability is calibrated confidence.
        return max(0.0, min(1.0, 1.0 + float(probability)))
    return 0.0


openai_transcription_provider = OpenAITranscriptionProvider()
