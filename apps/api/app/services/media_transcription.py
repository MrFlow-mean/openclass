from __future__ import annotations

import os
import json
import platform
import re
import shutil
import subprocess
import sys
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


@dataclass(frozen=True)
class LocalTranscriptionRuntime:
    available: bool
    python_path: str
    engine: str
    model: str
    error: str = ""


def media_runtime_status() -> dict[str, object]:
    local = local_transcription_runtime()
    ffmpeg = ffmpeg_executable()
    ffprobe = ffprobe_executable()
    return {
        "ffmpeg": bool(ffmpeg),
        "ffmpeg_path": ffmpeg or "",
        "ffprobe": bool(ffprobe),
        "ffprobe_path": ffprobe or "",
        "local_transcription": local.available,
        "local_transcription_engine": local.engine,
        "local_transcription_model": local.model,
        "local_transcription_error": local.error,
    }


def probe_duration_seconds(path: Path) -> float:
    ffprobe = ffprobe_executable()
    if ffprobe:
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
        if completed.returncode == 0:
            try:
                return max(0.0, float(completed.stdout.strip()))
            except ValueError:
                pass
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        raise MediaTranscriptionError("ffmpeg or ffprobe is required for video ingestion.")
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr)
    if not match:
        raise MediaTranscriptionError("Video duration could not be read.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def extract_audio_segments(video_path: Path, output_dir: Path, *, segment_seconds: int = 1200) -> list[Path]:
    ffmpeg = ffmpeg_executable()
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


def ffmpeg_executable() -> str | None:
    configured = _configured_executable("OPENCLASS_FFMPEG_PATH")
    if configured:
        return configured
    discovered = shutil.which("ffmpeg")
    if discovered:
        return str(Path(discovered).resolve())
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    except (ImportError, RuntimeError, OSError):
        return None
    return str(bundled) if bundled.is_file() and os.access(bundled, os.X_OK) else None


def ffprobe_executable() -> str | None:
    configured = _configured_executable("OPENCLASS_FFPROBE_PATH")
    if configured:
        return configured
    discovered = shutil.which("ffprobe")
    return str(Path(discovered).resolve()) if discovered else None


def _configured_executable(env_name: str) -> str | None:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise MediaTranscriptionError(f"{env_name} must be an absolute path.")
    resolved = candidate.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise MediaTranscriptionError(f"{env_name} does not identify an executable file.")
    return str(resolved)


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


class LocalWhisperTranscriptionProvider:
    def transcribe(
        self,
        audio_files: list[Path],
        *,
        selection: AIModelSelection,
        segment_seconds: int = 1200,
        progress: Callable[[int, int], None] | None = None,
    ) -> MediaTranscriptionResult:
        if selection.provider != "openclass_local" or selection.model != "local-whisper":
            raise MediaTranscriptionError("Selected model is not registered for local transcription.")
        runtime = local_transcription_runtime()
        if not runtime.available:
            raise MediaTranscriptionError(runtime.error or "Local Whisper is unavailable.")
        runner = Path(__file__).with_name("local_whisper_runner.py")
        timeout = max(
            60,
            int(os.getenv("OPENCLASS_LOCAL_TRANSCRIPTION_TIMEOUT_SECONDS") or 3600),
        )
        all_segments: list[TranscribedSegment] = []
        language = ""
        for file_index, audio_path in enumerate(audio_files):
            completed = subprocess.run(
                [
                    runtime.python_path,
                    str(runner),
                    "--engine",
                    runtime.engine,
                    "--model",
                    runtime.model,
                    "--audio",
                    str(audio_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()
                raise MediaTranscriptionError(
                    "Local audio transcription failed: "
                    + (detail[-1] if detail else "local runner exited without output")
                )
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise MediaTranscriptionError(
                    "Local audio transcription returned invalid JSON."
                ) from exc
            if not isinstance(payload, dict):
                raise MediaTranscriptionError("Local audio transcription returned an invalid payload.")
            language = language or str(payload.get("language") or "")
            offset_ms = file_index * segment_seconds * 1000
            raw_segments = payload.get("segments")
            if isinstance(raw_segments, list):
                for raw in raw_segments:
                    if not isinstance(raw, dict):
                        continue
                    text = str(raw.get("text") or "").strip()
                    start = float(raw.get("start") or 0)
                    end = float(raw.get("end") or start)
                    if text and end > start:
                        all_segments.append(
                            TranscribedSegment(
                                start_ms=offset_ms + int(start * 1000),
                                end_ms=offset_ms + int(end * 1000),
                                text=" ".join(text.split()),
                                confidence=max(
                                    0.0,
                                    min(1.0, float(raw.get("confidence") or 0.0)),
                                ),
                            )
                        )
            if not raw_segments:
                text = str(payload.get("text") or "").strip()
                if text:
                    all_segments.append(
                        TranscribedSegment(
                            start_ms=offset_ms,
                            end_ms=offset_ms + segment_seconds * 1000,
                            text=" ".join(text.split()),
                        )
                    )
            if progress:
                progress(file_index + 1, len(audio_files))
        if not all_segments:
            raise MediaTranscriptionError("Local transcription completed without usable text.")
        return MediaTranscriptionResult(
            language=language,
            provider=selection.provider,
            model=f"{runtime.engine}:{runtime.model}",
            segments=tuple(all_segments),
        )


def local_transcription_runtime() -> LocalTranscriptionRuntime:
    provider = (os.getenv("OPENCLASS_LOCAL_TRANSCRIPTION_PROVIDER") or "auto").strip().lower()
    if provider not in {"auto", "mlx_whisper", "faster_whisper", "disabled"}:
        return LocalTranscriptionRuntime(
            available=False,
            python_path="",
            engine=provider,
            model="",
            error="OPENCLASS_LOCAL_TRANSCRIPTION_PROVIDER is invalid.",
        )
    if provider == "disabled":
        return LocalTranscriptionRuntime(False, "", "disabled", "", "Local transcription is disabled.")
    python_path = _local_transcription_python()
    if not python_path:
        return LocalTranscriptionRuntime(False, "", provider, "", "Local transcription Python is unavailable.")
    candidates = (
        ("mlx_whisper", "mlx-community/whisper-small-mlx"),
        ("faster_whisper", "small"),
    )
    if provider != "auto":
        candidates = tuple(item for item in candidates if item[0] == provider)
    elif platform.system() != "Darwin" or platform.machine() != "arm64":
        candidates = tuple(reversed(candidates))
    for engine, default_model in candidates:
        if _python_has_module(python_path, engine):
            configured_model = (
                os.getenv("OPENCLASS_LOCAL_TRANSCRIPTION_MODEL") or default_model
            ).strip()
            return LocalTranscriptionRuntime(
                available=True,
                python_path=python_path,
                engine=engine,
                model=configured_model,
            )
    return LocalTranscriptionRuntime(
        available=False,
        python_path=python_path,
        engine=provider,
        model="",
        error="Neither mlx_whisper nor faster_whisper is installed in the configured Python runtime.",
    )


def transcription_provider_for(selection: AIModelSelection):
    if selection.provider == "openclass_local":
        return local_whisper_transcription_provider
    if selection.provider == "openai":
        return openai_transcription_provider
    raise MediaTranscriptionError("Selected transcription provider is not registered.")


def _local_transcription_python() -> str:
    configured = (os.getenv("OPENCLASS_LOCAL_TRANSCRIPTION_PYTHON") or "").strip()
    candidate = Path(configured).expanduser() if configured else Path(sys.executable)
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        return ""
    # Preserve a virtual environment's interpreter path: resolving its symlink would
    # discard that environment's site-packages when the subprocess starts.
    return str(candidate.absolute())


def _python_has_module(python_path: str, module: str) -> bool:
    completed = subprocess.run(
        [
            python_path,
            "-c",
            "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)",
            module,
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    return completed.returncode == 0


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
local_whisper_transcription_provider = LocalWhisperTranscriptionProvider()
