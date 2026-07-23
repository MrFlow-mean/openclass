from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field

from app.models import (
    AIModelSelection,
    AgentActivityEvent,
    MediaTimeRange,
    SourceChapter,
    SourceChunk,
    TimedTranscriptSegment,
    new_id,
)
from app.services.ai_execution_adapter import build_ai_execution_adapter


class MediaChapteringError(RuntimeError):
    pass


class _ChapterBoundary(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=1000)
    first_segment: int = Field(ge=0)
    last_segment: int = Field(ge=0)


class _ChapterPlan(BaseModel):
    chapters: list[_ChapterBoundary] = Field(min_length=1)


@dataclass(frozen=True)
class MediaChapteringResult:
    chapters: tuple[SourceChapter, ...]
    chunks: tuple[SourceChunk, ...]


def build_media_chapters(
    *,
    owner_user_id: str,
    package_id: str,
    source_id: str,
    source_content_hash: str,
    segments: list[TimedTranscriptSegment],
    selection: AIModelSelection,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
) -> MediaChapteringResult:
    if not segments:
        raise MediaChapteringError("A transcript is required before chapter generation.")
    adapter = build_ai_execution_adapter(selection, owner_user_id=owner_user_id)
    boundaries: list[_ChapterBoundary] = []
    for batch_start, batch_end in _contiguous_batches(segments):
        transcript = "\n".join(
            f"[{index}] {_format_time(item.start_ms)}-{_format_time(item.end_ms)} {item.text}"
            for index, item in enumerate(segments[batch_start : batch_end + 1], start=batch_start)
        )
        prompt = (
            "Divide this continuous transcript range into a small number of semantic learning chapters. "
            "Use only the supplied segment indexes. The first chapter must start at the first supplied index, "
            "the last chapter must end at the last supplied index, chapters must be contiguous with no gaps or "
            "overlap, and every segment must be covered exactly once. Titles and summaries must describe the "
            "actual material without adding subject facts.\n\n"
            f"TRANSCRIPT\n{transcript}"
        )
        try:
            result = adapter.parse_structured(
                system_prompt="You build a source-grounded chapter directory for one bounded video transcript.",
                user_prompt=prompt,
                schema=_ChapterPlan,
                on_activity=on_activity,
            )
            plan = _ChapterPlan.model_validate(result.output_parsed)
        except Exception as exc:
            raise MediaChapteringError(f"Video chapter model failed: {exc}") from exc
        ordered_plan = sorted(plan.chapters, key=lambda item: item.first_segment)
        _validate_plan(ordered_plan, first=batch_start, last=batch_end)
        boundaries.extend(ordered_plan)
    _validate_plan(boundaries, first=0, last=len(segments) - 1)
    chapters: list[SourceChapter] = []
    chunks: list[SourceChunk] = []
    text_offset = 0
    for order_index, boundary in enumerate(boundaries):
        selected = segments[boundary.first_segment : boundary.last_segment + 1]
        time_range = MediaTimeRange(
            start_ms=selected[0].start_ms,
            end_ms=selected[-1].end_ms,
            display_label=f"{_format_time(selected[0].start_ms)}–{_format_time(selected[-1].end_ms)}",
        )
        chapter_id = new_id("sourcechapter")
        chapter_text = "\n".join(
            f"[{_format_time(item.start_ms)}] {item.text}" for item in selected
        )
        chapter = SourceChapter(
            id=chapter_id,
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_ingestion_id=source_id,
            number=str(order_index + 1),
            normalized_number=str(order_index + 1),
            title=boundary.title.strip(),
            level=1,
            path=[boundary.title.strip()],
            order_index=order_index,
            source_locator=f"video:{time_range.start_ms}-{time_range.end_ms}",
            body_start_offset=text_offset,
            body_end_offset=text_offset + len(chapter_text),
            media_time_range=time_range,
            anchor_status="verified",
            mapping_status="verified",
            source_content_hash=source_content_hash,
            confidence=1.0,
            excerpt=boundary.summary.strip() or chapter_text[:500],
            metadata={
                "media_time_range": time_range.model_dump(mode="json"),
                "first_transcript_segment": boundary.first_segment,
                "last_transcript_segment": boundary.last_segment,
            },
        )
        chunk = SourceChunk(
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_ingestion_id=source_id,
            chapter_id=chapter_id,
            order_index=order_index,
            source_locator=chapter.source_locator,
            text=chapter_text,
            start_offset=text_offset,
            end_offset=text_offset + len(chapter_text),
            media_time_range=time_range,
            transcript_segment_ids=[item.id for item in selected],
            token_count=max(1, len(chapter_text) // 4),
            metadata={
                "media_time_range": time_range.model_dump(mode="json"),
                "transcript_segment_ids": [item.id for item in selected],
            },
        )
        chapters.append(chapter)
        chunks.append(chunk)
        text_offset += len(chapter_text) + 1
    return MediaChapteringResult(chapters=tuple(chapters), chunks=tuple(chunks))


def _contiguous_batches(segments: list[TimedTranscriptSegment], *, max_characters: int = 90_000):
    start = 0
    running = 0
    for index, segment in enumerate(segments):
        size = len(segment.text) + 40
        if index > start and running + size > max_characters:
            yield start, index - 1
            start = index
            running = 0
        running += size
    yield start, len(segments) - 1


def _validate_plan(chapters: list[_ChapterBoundary], *, first: int, last: int) -> None:
    ordered = sorted(chapters, key=lambda item: item.first_segment)
    if not ordered or ordered[0].first_segment != first or ordered[-1].last_segment != last:
        raise MediaChapteringError("Video chapter plan does not cover the complete transcript range.")
    expected = first
    for chapter in ordered:
        if chapter.first_segment != expected or chapter.last_segment < chapter.first_segment:
            raise MediaChapteringError("Video chapter plan contains a gap or overlap.")
        expected = chapter.last_segment + 1
    if expected != last + 1:
        raise MediaChapteringError("Video chapter plan does not cover every transcript segment.")


def _format_time(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
