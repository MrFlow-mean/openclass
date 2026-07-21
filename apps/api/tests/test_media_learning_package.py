from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.models import (
    AIModelSelection,
    MediaPackageManifest,
    MediaTimeRange,
    SourceChapter,
    SourceChunk,
    SourceIngestionRecord,
    SourceStructure,
    SourceStructureQuality,
    SourceVisualAsset,
    TimedTranscriptSegment,
)
from app.services import ai_model_catalog
from app.services import media_ingestion_pipeline as media_pipeline_module
from app.services import workspace_state
from app.services.media_adapters import MediaAdapterError, YtDlpMediaAdapter, parse_subtitle_segments
from app.services.media_chaptering import build_media_chapters
from app.services.media_package_store import MediaPackageStore
from app.services.media_ingestion_pipeline import MediaIngestionPipeline
from app.services.media_chaptering import MediaChapteringResult
from app.services.media_adapters import ResolvedMedia
from app.services.source_structure_store import SourceStructureStore
from app.services.media_visual_extraction import FrameSample, detect_reset_intervals
from app.services.public_url_policy import PublicUrlPolicyError, validate_public_http_url


def _segment(index: int, *, version: int = 1) -> TimedTranscriptSegment:
    return TimedTranscriptSegment(
        source_ingestion_id="source_media",
        version=version,
        order_index=index,
        start_ms=index * 1_000,
        end_ms=(index + 1) * 1_000,
        text=f"segment {index}",
        language="en",
        source_kind="model_transcription",
        provider="openai",
        model="gpt-4o-mini-transcribe",
        confidence=0.9,
    )


def test_public_url_policy_rejects_private_and_authenticated_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 0))],
    )
    try:
        validate_public_http_url("https://example.com/video")
    except PublicUrlPolicyError as exc:
        assert "Private network" in str(exc)
    else:
        raise AssertionError("private URL was accepted")

    try:
        validate_public_http_url("https://user:pass@example.com/video")
    except PublicUrlPolicyError as exc:
        assert "Authenticated" in str(exc)
    else:
        raise AssertionError("authenticated URL was accepted")


def test_subtitle_parser_preserves_timestamps_and_text() -> None:
    parsed = parse_subtitle_segments(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nfirst line\nsecond line\n",
        "vtt",
    )
    assert parsed == [(1000, 3500, "first line second line")]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"entries": [{"id": "one"}]}, "playlists"),
        ({"is_live": True}, "Live video"),
        ({"has_drm": True}, "DRM-protected"),
        ({"availability": "needs_auth"}, "requires authentication"),
    ],
)
def test_ytdlp_adapter_rejects_non_v1_media(monkeypatch, metadata, message) -> None:
    import yt_dlp

    class FakeDownloader:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {
                "id": "video",
                "title": "Video",
                "duration": 10,
                "webpage_url": "https://example.com/video",
                **metadata,
            }

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloader)

    with pytest.raises(MediaAdapterError, match=message):
        YtDlpMediaAdapter().resolve("https://example.com/video")


def test_ytdlp_adapter_prefers_official_subtitles(monkeypatch) -> None:
    import yt_dlp

    class FakeDownloader:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {
                "id": "video",
                "title": "Video",
                "duration": 10,
                "webpage_url": "https://example.com/video",
                "subtitles": {"en": [{"ext": "vtt", "url": "https://example.com/official.vtt"}]},
                "automatic_captions": {"en": [{"ext": "vtt", "url": "https://example.com/auto.vtt"}]},
            }

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDownloader)

    resolved = YtDlpMediaAdapter().resolve("https://example.com/video")

    assert len(resolved.subtitles) == 1
    assert resolved.subtitles[0].kind == "official_subtitle"


def test_reset_detection_ignores_one_frame_occlusion_and_keeps_persistent_erasure() -> None:
    full = np.zeros((24, 24), dtype=np.uint8)
    full[4:20, 4:20] = 255
    empty = np.zeros((24, 24), dtype=np.uint8)
    encoded = b"jpeg"

    occlusion = [
        FrameSample(0, full, encoded),
        FrameSample(10_000, empty, encoded),
        FrameSample(20_000, full, encoded),
    ]
    assert detect_reset_intervals(occlusion) == []

    erased = [
        FrameSample(0, full, encoded),
        FrameSample(10_000, empty, encoded),
        FrameSample(20_000, empty, encoded),
    ]
    assert [(before.timestamp_ms, after.timestamp_ms) for before, after in detect_reset_intervals(erased)] == [
        (0, 10_000)
    ]


def test_media_store_versions_transcripts_and_serializes_leases(tmp_path: Path) -> None:
    store = MediaPackageStore(tmp_path / "media.sqlite3")
    manifest = store.save_manifest(
        owner_user_id="user",
        package_id="package",
        source_id="source_media",
        manifest=MediaPackageManifest(
            duration_ms=2_000,
            active_transcript_version=1,
            transcript_status="ready",
        ),
    )
    store.replace_transcript_version(
        owner_user_id="user",
        package_id="package",
        source_id="source_media",
        version=1,
        segments=[_segment(0), _segment(1)],
    )

    assert manifest.active_transcript_version == 1
    assert [item.text for item in store.list_transcript(
        owner_user_id="user", package_id="package", source_id="source_media", version=1
    )] == ["segment 0", "segment 1"]
    assert store.claim_source(source_id="source_media", worker_id="worker-a") is True
    assert store.claim_source(source_id="source_media", worker_id="worker-b") is False
    store.release_lease(source_id="source_media", worker_id="worker-a")
    assert store.claim_source(source_id="source_media", worker_id="worker-b") is True


def test_media_chapters_must_cover_every_transcript_segment(monkeypatch) -> None:
    class FakeAdapter:
        def parse_structured(self, **_kwargs):
            return SimpleNamespace(
                output_parsed={
                    "chapters": [
                        {"title": "First", "summary": "One", "first_segment": 0, "last_segment": 1},
                        {"title": "Second", "summary": "Two", "first_segment": 2, "last_segment": 3},
                    ]
                }
            )

    monkeypatch.setattr(
        "app.services.media_chaptering.build_ai_execution_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    segments = [_segment(index) for index in range(4)]
    result = build_media_chapters(
        owner_user_id="user",
        package_id="package",
        source_id="source_media",
        source_content_hash="a" * 64,
        segments=segments,
        selection=AIModelSelection(provider="openai_codex", model="gpt-5.6-sol"),
    )

    assert len(result.chapters) == 2
    assert result.chapters[0].media_time_range.start_ms == 0
    assert result.chapters[-1].media_time_range.end_ms == 4_000
    assert [item for chunk in result.chunks for item in chunk.transcript_segment_ids] == [
        segment.id for segment in segments
    ]


def test_model_catalog_only_exposes_verified_vision_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setattr(
        ai_model_catalog,
        "codex_provider_status",
        lambda *_args, **_kwargs: SimpleNamespace(configured=True),
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "list_codex_models",
        lambda _user_id: [{"model": "gpt-5.6-sol", "displayName": "GPT-5.6-Sol", "isDefault": True}],
    )

    catalog = ai_model_catalog.build_model_catalog("user")

    assert {option.provider for option in catalog.vision} == {"openai_codex"}
    assert all(option.capability == "transcription" for option in catalog.transcription)
    assert catalog.defaults["vision"].model == "gpt-5.6-sol"


def test_pipeline_keeps_subtitle_package_usable_when_visuals_are_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeAdapter:
        def download(self, _resolved, *, destination, progress=None):
            destination.write_bytes(b"video-content")
            if progress:
                progress(13, 13)
            return destination

    resolved = ResolvedMedia(
        source_uri="https://example.com/watch",
        title="Lecture",
        provider="test",
        media_id="one",
        duration_seconds=4,
        webpage_url="https://example.com/watch",
    )
    monkeypatch.setattr(
        media_pipeline_module.media_adapter_registry,
        "resolve",
        lambda *_args, **_kwargs: (FakeAdapter(), resolved),
    )
    monkeypatch.setattr(media_pipeline_module, "probe_duration_seconds", lambda _path: 4)
    monkeypatch.setattr(media_pipeline_module, "extract_media_visuals", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        media_pipeline_module.openai_transcription_provider,
        "transcribe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("subtitle path must not transcribe")),
    )
    segments = [_segment(index) for index in range(4)]
    chapter = SourceChapter(
        owner_user_id="user",
        package_id="package",
        source_ingestion_id="source_media",
        number="1",
        normalized_number="1",
        title="Chapter",
        path=["Chapter"],
        source_locator="video:0-4000",
        body_start_offset=0,
        body_end_offset=40,
        media_time_range=MediaTimeRange(start_ms=0, end_ms=4_000, display_label="00:00:00–00:00:04"),
        anchor_status="verified",
        mapping_status="verified",
        source_content_hash="a" * 64,
        confidence=1,
    )
    chunk = SourceChunk(
        owner_user_id="user",
        package_id="package",
        source_ingestion_id="source_media",
        chapter_id=chapter.id,
        text="complete transcript",
        end_offset=19,
        media_time_range=chapter.media_time_range,
        transcript_segment_ids=[item.id for item in segments],
    )
    monkeypatch.setattr(
        media_pipeline_module,
        "build_media_chapters",
        lambda **_kwargs: MediaChapteringResult(chapters=(chapter,), chunks=(chunk,)),
    )
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    database = tmp_path / "media.sqlite3"
    package_store = MediaPackageStore(database)
    pipeline = MediaIngestionPipeline(
        package_store=package_store,
        structure_store=SourceStructureStore(database),
    )
    monkeypatch.setattr(pipeline, "_subtitle_segments", lambda *_args, **_kwargs: segments)
    record = SourceIngestionRecord(
        owner_user_id="user",
        package_id="package",
        title="URL",
        source_type="video_url",
        source_uri="https://example.com/watch",
    )
    text_model = AIModelSelection(provider="openai_codex", model="gpt-5.6-sol")
    ready = pipeline.process(
        record,
        transcription_model=AIModelSelection(provider="openai", model="gpt-4o-mini-transcribe"),
        vision_model=text_model,
        catalog_model=text_model,
    )

    assert ready.status == "ready"
    assert ready.media_package.visual_status == "empty"
    assert Path(str(ready.metadata["local_source_path"])).is_file()
    assert not (workspace_state.UPLOAD_DIR / "media-temp" / record.id).exists()


def test_media_rebuild_keeps_old_visual_ids_for_frozen_evidence(tmp_path: Path) -> None:
    store = SourceStructureStore(tmp_path / "structure.sqlite3")
    source = SourceIngestionRecord(
        owner_user_id="user",
        package_id="package",
        title="Video",
        source_type="video_url",
        source_uri="https://example.com/video",
        status="ready",
    )

    def save_version(version: int) -> SourceVisualAsset:
        chapter = SourceChapter(
            owner_user_id="user",
            package_id="package",
            source_ingestion_id=source.id,
            title=f"Chapter {version}",
            path=[f"Chapter {version}"],
            source_locator=f"video:{version}",
            body_start_offset=0,
            body_end_offset=10,
            media_time_range=MediaTimeRange(start_ms=0, end_ms=1_000, display_label="00:00:00–00:00:01"),
            anchor_status="verified",
            mapping_status="verified",
            source_content_hash="a" * 64,
        )
        visual = SourceVisualAsset(
            owner_user_id="user",
            package_id="package",
            source_ingestion_id=source.id,
            structure_version=version,
            chapter_id=chapter.id,
            source_locator=f"video:frame:{version}",
            timestamp_ms=version * 100,
            media_role="board_final",
            anchor_status="verified",
            content_hash=str(version) * 64,
            position_hash=str(version + 1) * 64,
        )
        store.save_structure_bundle(
            structure=SourceStructure(
                owner_user_id="user",
                package_id="package",
                source_ingestion_id=source.id,
                status="ready",
                strategy="media_timeline",
                catalog_version=version,
                source_content_hash="a" * 64,
                quality=SourceStructureQuality(level="fully_verified", text_readiness="ready"),
            ),
            chapters=[chapter],
            chunks=[],
            visuals=[visual],
            preserve_visual_history=True,
        )
        return visual

    old_visual = save_version(1)
    current_visual = save_version(2)

    assert store.get_visual(
        owner_user_id="user",
        package_id="package",
        source_id=source.id,
        visual_id=old_visual.id,
    ) is not None
    view = store.get_structure_view(source=source)
    assert [item.id for item in view.visuals] == [current_visual.id]
