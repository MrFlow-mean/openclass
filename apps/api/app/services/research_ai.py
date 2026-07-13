from __future__ import annotations

import os
import re
from pathlib import Path

from app.models import AIModelSelection
from app.research_models import ResearchSpeaker
from app.services import workspace_state
from app.services.openai_course_ai import bind_text_model_selection, openai_course_ai


class ResearchAIError(RuntimeError):
    pass


class ResearchAIService:
    def generate_text(
        self,
        *,
        instruction: str,
        context: str,
        conversation: str = "",
        text_model: AIModelSelection | None = None,
    ) -> str:
        with bind_text_model_selection(text_model):
            reply = openai_course_ai.generate_basic_chat_reply(
                board_document_state={"status": "not_provided"},
                conversation_summary=conversation,
                user_message=instruction,
                resource_summary=context,
            )
        if reply is None or not reply.chatbot_message.strip():
            raise ResearchAIError("当前没有可用的文本模型，无法完成资料生成任务。")
        return reply.chatbot_message.strip()

    def synthesize_podcast(
        self,
        *,
        artifact_id: str,
        transcript: str,
        speakers: list[ResearchSpeaker],
    ) -> Path:
        client = openai_course_ai.client
        if client is None:
            raise ResearchAIError("当前没有可用的 TTS 模型，播客文字稿已保留但无法生成音频。")
        segments = _podcast_segments(transcript, speakers)
        if not segments:
            raise ResearchAIError("播客文字稿没有可合成的语音片段。")
        output_dir = workspace_state.EXPORT_DIR / "research-audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{artifact_id}.mp3"
        audio_parts: list[bytes] = []
        model = os.getenv("OPENCLASS_TTS_MODEL", "gpt-4o-mini-tts")
        for speaker, text in segments:
            for chunk in _split_tts_text(text):
                response = client.audio.speech.create(
                    model=model,
                    voice=speaker.voice or "alloy",
                    input=chunk,
                    instructions=speaker.instructions or None,
                )
                content = getattr(response, "content", None)
                if isinstance(content, bytes):
                    audio_parts.append(content)
                elif hasattr(response, "read"):
                    audio_parts.append(response.read())
                else:
                    raise ResearchAIError("TTS 服务没有返回可保存的音频数据。")
        output_path.write_bytes(b"".join(audio_parts))
        return output_path

    def podcast_audio_available(self) -> bool:
        return openai_course_ai.client is not None


def _podcast_segments(transcript: str, speakers: list[ResearchSpeaker]) -> list[tuple[ResearchSpeaker, str]]:
    usable_speakers = speakers[:4]
    if not usable_speakers:
        return []
    by_name = {speaker.name.casefold(): speaker for speaker in usable_speakers}
    segments: list[tuple[ResearchSpeaker, str]] = []
    current = usable_speakers[0]
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(?:\[)?([^\]:：]{1,60})(?:\])?\s*[:：]\s*(.+)$", line)
        if match:
            candidate = by_name.get(match.group(1).strip().casefold())
            if candidate is not None:
                current = candidate
                line = match.group(2).strip()
        line = re.sub(r"^#{1,6}\s+", "", line).strip()
        if line:
            segments.append((current, line))
    return segments


def _split_tts_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        boundary = max(remaining.rfind(mark, 0, limit) for mark in ("。", "！", "？", ".", "!", "?", "\n"))
        if boundary < limit // 3:
            boundary = limit
        else:
            boundary += 1
        parts.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return [part for part in parts if part]


research_ai_service = ResearchAIService()
