from __future__ import annotations

import base64
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from app.models import AIModelSelection, AgentActivityEvent
from app.services.ai_execution_adapter import build_ai_execution_adapter
from app.services.media_transcription import ffmpeg_executable


class MediaVisualExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrameSample:
    timestamp_ms: int
    image: object
    encoded_jpeg: bytes


@dataclass(frozen=True)
class MediaVisualCandidate:
    timestamp_ms: int
    content: bytes
    role: Literal["board_final", "slide_final", "teaching_visual"]
    caption: str
    confidence: float
    content_region: tuple[float, float, float, float] | None = None


class _AnalyzedFrame(BaseModel):
    image_index: int = Field(ge=0)
    role: Literal["board_final", "slide_final", "teaching_visual", "discard"]
    caption: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    content_region: list[float] = Field(default_factory=list, min_length=0, max_length=4)


class _FrameAnalysisBatch(BaseModel):
    frames: list[_AnalyzedFrame] = Field(default_factory=list)


def extract_media_visuals(
    video_path: Path,
    *,
    duration_ms: int,
    selection: AIModelSelection,
    owner_user_id: str,
    on_activity: Callable[[AgentActivityEvent], None] | None = None,
) -> list[MediaVisualCandidate]:
    if not ffmpeg_executable():
        raise MediaVisualExtractionError("ffmpeg is required for video keyframe extraction.")
    try:
        import cv2  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise MediaVisualExtractionError("opencv-python-headless is required for video keyframe extraction.") from exc
    if duration_ms <= 0:
        return []
    max_coarse_samples = max(12, int(os.getenv("OPENCLASS_MEDIA_MAX_COARSE_FRAMES") or 480))
    sample_interval_ms = max(
        10_000,
        int(math.ceil(duration_ms / max_coarse_samples / 1000)) * 1000,
    )
    coarse_times = list(range(0, duration_ms, sample_interval_ms))
    if not coarse_times or coarse_times[-1] != max(0, duration_ms - 500):
        coarse_times.append(max(0, duration_ms - 500))
    samples = [_extract_frame(video_path, timestamp_ms=time_ms, cv2=cv2) for time_ms in coarse_times]
    candidate_samples = _candidate_samples(video_path, samples=samples, cv2=cv2)
    candidate_samples = _dedupe_samples(candidate_samples)
    candidate_samples = bounded_frame_samples(
        candidate_samples,
        limit=max(12, int(os.getenv("OPENCLASS_MEDIA_MAX_VISION_FRAMES") or 96)),
    )
    if not candidate_samples:
        return []
    analyzed: list[MediaVisualCandidate] = []
    adapter = build_ai_execution_adapter(selection, owner_user_id=owner_user_id)
    for offset in range(0, len(candidate_samples), 12):
        batch = candidate_samples[offset : offset + 12]
        image_inputs = [
            f"data:image/jpeg;base64,{base64.b64encode(sample.encoded_jpeg).decode('ascii')}"
            for sample in batch
        ]
        prompt = (
            "Classify each supplied candidate teaching frame by its zero-based image_index. "
            "Use board_final only when the frame shows a substantially complete cumulative writing surface; "
            "use slide_final for a complete presentation slide; use teaching_visual for another important "
            "diagram or demonstration; otherwise discard. Describe only visible content, preserve uncertainty, "
            "and do not infer subject-specific facts that are not visible. For retained frames, return a "
            "normalized [x0,y0,x1,y1] content_region for the teaching surface when it is clear."
        )
        try:
            result = adapter.parse_structured(
                system_prompt="You classify bounded video keyframe candidates for source evidence.",
                user_prompt=prompt,
                schema=_FrameAnalysisBatch,
                image_inputs=image_inputs,
                on_activity=on_activity,
            )
            parsed = _FrameAnalysisBatch.model_validate(result.output_parsed)
        except Exception as exc:
            raise MediaVisualExtractionError(f"Visual model analysis failed: {exc}") from exc
        seen: set[int] = set()
        for item in parsed.frames:
            if item.image_index in seen or item.image_index >= len(batch) or item.role == "discard":
                continue
            seen.add(item.image_index)
            sample = batch[item.image_index]
            analyzed.append(
                MediaVisualCandidate(
                    timestamp_ms=sample.timestamp_ms,
                    content=sample.encoded_jpeg,
                    role=item.role,
                    caption=item.caption.strip(),
                    confidence=item.confidence,
                    content_region=(
                        tuple(float(value) for value in item.content_region)
                        if len(item.content_region) == 4
                        else None
                    ),
                )
            )
    return sorted(analyzed, key=lambda item: item.timestamp_ms)


def bounded_frame_samples(samples: list[FrameSample], *, limit: int) -> list[FrameSample]:
    ordered = sorted(samples, key=lambda item: item.timestamp_ms)
    if limit < 2 or len(ordered) <= limit:
        return ordered[: max(0, limit)]
    indexes = {
        round(index * (len(ordered) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [ordered[index] for index in sorted(indexes)]


def detect_reset_intervals(samples: list[FrameSample]) -> list[tuple[FrameSample, FrameSample]]:
    """Find persistent content-loss brackets without treating one occluded frame as an erase."""
    if len(samples) < 3:
        return []
    intervals: list[tuple[FrameSample, FrameSample]] = []
    for index in range(1, len(samples) - 1):
        previous = samples[index - 1]
        current = samples[index]
        following = samples[index + 1]
        if _retention_ratio(previous.image, current.image) < 0.48 and _retention_ratio(
            previous.image, following.image
        ) < 0.58:
            intervals.append((previous, current))
    return intervals


def _candidate_samples(video_path: Path, *, samples: list[FrameSample], cv2: object) -> list[FrameSample]:
    candidates: list[FrameSample] = []
    reset_intervals = detect_reset_intervals(samples)
    for before, after in reset_intervals:
        boundary_ms = _binary_search_erase(
            video_path,
            before_ms=before.timestamp_ms,
            after_ms=after.timestamp_ms,
            reference=before,
            cv2=cv2,
        )
        local = [
            _extract_frame(video_path, timestamp_ms=time_ms, cv2=cv2)
            for time_ms in range(max(before.timestamp_ms, boundary_ms - 5_000), boundary_ms + 1, 500)
        ]
        if local:
            candidates.append(_select_stable_complete_frame(local, reference=before))
    for previous, current in zip(samples, samples[1:]):
        if _visual_change(previous.image, current.image) > 0.36:
            boundary_ms = _binary_search_visual_change(
                video_path,
                before_ms=previous.timestamp_ms,
                after_ms=current.timestamp_ms,
                reference=previous,
                cv2=cv2,
            )
            local = [
                _extract_frame(video_path, timestamp_ms=time_ms, cv2=cv2)
                for time_ms in range(max(previous.timestamp_ms, boundary_ms - 5_000), boundary_ms + 1, 500)
            ]
            candidates.append(_select_last_stable_frame(local) if local else previous)
    candidates.append(samples[-1])
    return candidates


def _binary_search_erase(
    video_path: Path,
    *,
    before_ms: int,
    after_ms: int,
    reference: FrameSample,
    cv2: object,
) -> int:
    low = before_ms
    high = after_ms
    while high - low > 500:
        midpoint = (low + high) // 2
        nearby = [
            _extract_frame(video_path, timestamp_ms=max(0, midpoint + delta), cv2=cv2)
            for delta in (-200, 0, 200)
        ]
        retention = sorted(_retention_ratio(reference.image, item.image) for item in nearby)[1]
        if retention >= 0.58:
            low = midpoint
        else:
            high = midpoint
    return high


def _binary_search_visual_change(
    video_path: Path,
    *,
    before_ms: int,
    after_ms: int,
    reference: FrameSample,
    cv2: object,
) -> int:
    low = before_ms
    high = after_ms
    while high - low > 500:
        midpoint = (low + high) // 2
        sample = _extract_frame(video_path, timestamp_ms=midpoint, cv2=cv2)
        if _visual_change(reference.image, sample.image) <= 0.36:
            low = midpoint
        else:
            high = midpoint
    return high


def _extract_frame(video_path: Path, *, timestamp_ms: int, cv2: object) -> FrameSample:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        raise MediaVisualExtractionError("ffmpeg is required for video keyframe extraction.")
    with tempfile.NamedTemporaryFile(prefix="openclass-frame-", suffix=".jpg", delete=False) as handle:
        output = Path(handle.name)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp_ms / 1000:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=min(1280\\,iw):-2",
                str(output),
            ],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if completed.returncode != 0 or not output.is_file():
            raise MediaVisualExtractionError("Video frame could not be extracted.")
        encoded = output.read_bytes()
        frame = cv2.imdecode(_numpy_buffer(encoded), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise MediaVisualExtractionError("Extracted video frame is invalid.")
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        edge_mask = cv2.Canny(frame, 60, 160)
        return FrameSample(timestamp_ms=timestamp_ms, image=edge_mask, encoded_jpeg=encoded)
    finally:
        output.unlink(missing_ok=True)


def _numpy_buffer(content: bytes):
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise MediaVisualExtractionError("numpy is required for video keyframe extraction.") from exc
    return np.frombuffer(content, dtype=np.uint8)


def _retention_ratio(before: object, after: object) -> float:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise MediaVisualExtractionError("numpy is required for video keyframe extraction.") from exc
    before_mask = np.asarray(before) > 0
    after_mask = np.asarray(after) > 0
    if before_mask.shape != after_mask.shape:
        return 0.0
    before_count = int(before_mask.sum())
    if before_count == 0:
        return 1.0
    overlap = int(np.logical_and(before_mask, after_mask).sum())
    return overlap / before_count


def _visual_change(before: object, after: object) -> float:
    return 1.0 - min(_retention_ratio(before, after), _retention_ratio(after, before))


def _frame_quality_score(sample: FrameSample) -> float:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise MediaVisualExtractionError("numpy is required for video keyframe extraction.") from exc
    mask = np.asarray(sample.image)
    edge_density = float((mask > 0).mean())
    variation = float(mask.var()) / (255.0 * 255.0)
    return edge_density * 0.7 + variation * 0.3


def _select_stable_complete_frame(
    samples: list[FrameSample],
    *,
    reference: FrameSample,
) -> FrameSample:
    def score(index: int) -> float:
        sample = samples[index]
        neighbors = samples[max(0, index - 1) : min(len(samples), index + 2)]
        stability = sum(
            1.0 - _visual_change(sample.image, neighbor.image)
            for neighbor in neighbors
        ) / max(1, len(neighbors))
        completeness = _retention_ratio(reference.image, sample.image)
        return completeness * 0.55 + stability * 0.3 + _frame_quality_score(sample) * 0.15

    return max(enumerate(samples), key=lambda item: score(item[0]))[1]


def _select_last_stable_frame(samples: list[FrameSample]) -> FrameSample:
    latest = max(sample.timestamp_ms for sample in samples)
    earliest = min(sample.timestamp_ms for sample in samples)
    span = max(1, latest - earliest)

    def score(index: int) -> float:
        sample = samples[index]
        neighbors = samples[max(0, index - 1) : min(len(samples), index + 2)]
        stability = sum(
            1.0 - _visual_change(sample.image, neighbor.image)
            for neighbor in neighbors
        ) / max(1, len(neighbors))
        recency = (sample.timestamp_ms - earliest) / span
        return stability * 0.55 + recency * 0.3 + _frame_quality_score(sample) * 0.15

    return max(enumerate(samples), key=lambda item: score(item[0]))[1]


def _dedupe_samples(samples: list[FrameSample]) -> list[FrameSample]:
    selected: list[FrameSample] = []
    hashes: list[int] = []
    for sample in sorted(samples, key=lambda item: item.timestamp_ms):
        digest = _average_hash(sample.image)
        if any((digest ^ existing).bit_count() <= 2 for existing in hashes):
            continue
        selected.append(sample)
        hashes.append(digest)
    return selected


def _average_hash(image: object) -> int:
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np
    except ModuleNotFoundError as exc:
        raise MediaVisualExtractionError(
            "numpy and opencv-python-headless are required for keyframe deduplication."
        ) from exc
    resized = cv2.resize(np.asarray(image), (8, 8), interpolation=cv2.INTER_AREA)
    threshold = float(resized.mean())
    digest = 0
    for value in resized.flatten():
        digest = (digest << 1) | int(float(value) >= threshold)
    return digest
