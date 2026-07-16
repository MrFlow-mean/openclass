from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.models import UserView
from app.routers.auth import current_user
from app.services.speech_service import (
    SpeechGenerationError,
    SpeechNotConfiguredError,
    synthesize_speech,
)


router = APIRouter(prefix="/api")


class SpeechSynthesisRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4096)


@router.post("/speech")
def create_speech(
    payload: SpeechSynthesisRequest,
    _: UserView = Depends(current_user),
) -> Response:
    try:
        audio = synthesize_speech(payload.input)
    except SpeechNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail="语音播报尚未配置 OPENAI_API_KEY",
        ) from exc
    except SpeechGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail="语音模型没有成功生成音频",
        ) from exc

    return Response(
        content=audio.content,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Speech-Model": audio.model,
            "X-Speech-Voice": audio.voice,
        },
    )
