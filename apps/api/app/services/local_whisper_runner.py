from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _mlx_transcribe(audio_path: Path, model: str) -> dict[str, Any]:
    import mlx_whisper  # type: ignore[import-untyped]

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        word_timestamps=False,
    )
    segments = [
        {
            "start": float(item.get("start") or 0),
            "end": float(item.get("end") or item.get("start") or 0),
            "text": str(item.get("text") or "").strip(),
            "confidence": 0.0,
        }
        for item in result.get("segments", [])
        if isinstance(item, dict)
    ]
    return {
        "language": str(result.get("language") or ""),
        "segments": segments,
        "text": str(result.get("text") or "").strip(),
    }


def _faster_whisper_transcribe(audio_path: Path, model: str) -> dict[str, Any]:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    whisper = WhisperModel(model, device="auto", compute_type="default")
    iterable, info = whisper.transcribe(
        str(audio_path),
        vad_filter=True,
        word_timestamps=False,
    )
    segments = []
    for item in iterable:
        probability = max(0.0, min(1.0, 1.0 + float(item.avg_logprob or 0.0)))
        segments.append(
            {
                "start": float(item.start),
                "end": float(item.end),
                "text": str(item.text or "").strip(),
                "confidence": probability,
            }
        )
    return {
        "language": str(info.language or ""),
        "segments": segments,
        "text": " ".join(item["text"] for item in segments if item["text"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("mlx_whisper", "faster_whisper"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    args = parser.parse_args()
    if not args.audio.is_file():
        raise SystemExit("audio input is not a regular file")
    if args.engine == "mlx_whisper":
        payload = _mlx_transcribe(args.audio, args.model)
    else:
        payload = _faster_whisper_transcribe(args.audio, args.model)
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
