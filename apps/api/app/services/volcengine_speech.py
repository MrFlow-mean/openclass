from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import uuid
from collections.abc import Iterable
from typing import Any

import httpx

from app.services.config import load_root_dotenv
from app.services.speech_service import (
    SpeechAudio,
    SpeechGenerationError,
    SpeechNotConfiguredError,
)


logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_SPEAKER = "zh_female_vv_uranus_bigtts"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_SPEECH_RATE = 0
DEFAULT_TIMEOUT_SECONDS = 30.0
SUCCESS_FRAME_CODE = 0
FINISHED_FRAME_CODE = 20_000_000


def _configured_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _configured_timeout() -> float:
    raw_value = os.getenv("VOLCENGINE_TTS_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(120.0, value))


def _decode_audio_frames(lines: Iterable[str]) -> bytes:
    audio_parts: list[bytes] = []
    finished = False

    for line in lines:
        normalized = line.strip()
        if not normalized:
            continue
        try:
            frame = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise SpeechGenerationError("Volcengine returned an invalid speech frame") from exc

        if not isinstance(frame, dict):
            raise SpeechGenerationError("Volcengine returned an invalid speech frame")
        code = frame.get("code")
        message = str(frame.get("message") or "").strip()
        if code == FINISHED_FRAME_CODE:
            finished = True
            continue
        if code != SUCCESS_FRAME_CODE:
            detail = f": {message}" if message else ""
            raise SpeechGenerationError(f"Volcengine speech request failed{detail}")

        encoded_audio = frame.get("data")
        if encoded_audio is None:
            continue
        if not isinstance(encoded_audio, str):
            raise SpeechGenerationError("Volcengine returned an invalid audio frame")
        try:
            audio_parts.append(base64.b64decode(encoded_audio, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise SpeechGenerationError("Volcengine returned invalid base64 audio") from exc

    if not finished:
        raise SpeechGenerationError("Volcengine speech stream ended before completion")
    content = b"".join(audio_parts)
    if not content:
        raise SpeechGenerationError("Volcengine returned empty speech audio")
    return content


def _request_payload(text: str, *, speaker: str, sample_rate: int, speech_rate: int) -> dict[str, Any]:
    return {
        "user": {"uid": "openclass"},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": "mp3",
                "sample_rate": sample_rate,
                "speech_rate": speech_rate,
            },
            "additions": json.dumps(
                {
                    "disable_markdown_filter": True,
                    "cache_config": {"text_type": 1, "use_cache": True},
                },
                ensure_ascii=False,
            ),
        },
    }


def synthesize_volcengine_speech(text: str) -> SpeechAudio:
    load_root_dotenv()
    api_key = os.getenv("VOLCENGINE_TTS_API_KEY", "").strip()
    if not api_key:
        raise SpeechNotConfiguredError("VOLCENGINE_TTS_API_KEY is not configured")

    endpoint = os.getenv("VOLCENGINE_TTS_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    resource_id = os.getenv("VOLCENGINE_TTS_RESOURCE_ID", DEFAULT_RESOURCE_ID).strip() or DEFAULT_RESOURCE_ID
    speaker = os.getenv("VOLCENGINE_TTS_SPEAKER", DEFAULT_SPEAKER).strip() or DEFAULT_SPEAKER
    sample_rate = _configured_integer(
        "VOLCENGINE_TTS_SAMPLE_RATE",
        DEFAULT_SAMPLE_RATE,
        8000,
        48000,
    )
    speech_rate = _configured_integer(
        "VOLCENGINE_TTS_SPEECH_RATE",
        DEFAULT_SPEECH_RATE,
        -50,
        100,
    )
    request_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": request_id,
    }

    try:
        with httpx.stream(
            "POST",
            endpoint,
            headers=headers,
            json=_request_payload(
                text,
                speaker=speaker,
                sample_rate=sample_rate,
                speech_rate=speech_rate,
            ),
            timeout=_configured_timeout(),
        ) as response:
            response.raise_for_status()
            content = _decode_audio_frames(response.iter_lines())
            log_id = response.headers.get("X-Tt-Logid", "")
    except SpeechGenerationError:
        raise
    except httpx.HTTPError as exc:
        logger.exception("Volcengine speech transport failed for request %s", request_id)
        raise SpeechGenerationError("Volcengine speech request failed") from exc

    logger.info(
        "Volcengine speech generated request_id=%s log_id=%s resource_id=%s speaker=%s bytes=%s",
        request_id,
        log_id,
        resource_id,
        speaker,
        len(content),
    )
    return SpeechAudio(
        content=content,
        media_type="audio/mpeg",
        provider="volcengine",
        model=resource_id,
        voice=speaker,
    )
