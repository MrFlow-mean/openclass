from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.models import AIModelSelection
from app.services import media_transcription


def test_local_whisper_provider_preserves_segment_timestamps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(
        media_transcription,
        "local_transcription_runtime",
        lambda: media_transcription.LocalTranscriptionRuntime(
            available=True,
            python_path="/usr/bin/python3",
            engine="faster_whisper",
            model="small",
        ),
    )
    monkeypatch.setattr(
        media_transcription.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "language": "zh",
                    "segments": [
                        {
                            "start": 1.25,
                            "end": 2.75,
                            "text": "通用转写内容",
                            "confidence": 0.8,
                        }
                    ],
                }
            ),
            stderr="",
        ),
    )

    result = media_transcription.LocalWhisperTranscriptionProvider().transcribe(
        [audio],
        selection=AIModelSelection(provider="openclass_local", model="local-whisper"),
    )

    assert result.language == "zh"
    assert result.provider == "openclass_local"
    assert result.segments[0].start_ms == 1_250
    assert result.segments[0].end_ms == 2_750
    assert result.segments[0].text == "通用转写内容"


def test_local_whisper_provider_is_not_selected_for_unregistered_model() -> None:
    try:
        media_transcription.transcription_provider_for(
            AIModelSelection(provider="google", model="unknown")
        )
    except media_transcription.MediaTranscriptionError as exc:
        assert "not registered" in str(exc)
    else:
        raise AssertionError("unregistered transcription provider was accepted")


def test_local_runtime_preserves_virtual_environment_interpreter_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o700)
    monkeypatch.setenv("OPENCLASS_LOCAL_TRANSCRIPTION_PYTHON", str(interpreter))
    monkeypatch.setattr(media_transcription, "_python_has_module", lambda path, module: module == "faster_whisper" and path == str(interpreter))

    runtime = media_transcription.local_transcription_runtime()

    assert runtime.available is True
    assert runtime.python_path == str(interpreter)
    assert runtime.engine == "faster_whisper"
