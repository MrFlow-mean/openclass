from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Callable

from app.models import (
    AIModelSelection,
    MediaPackageManifest,
    SourceIngestionRecord,
    SourceStructure,
    SourceStructureQuality,
    SourceVisualAsset,
    TimedTranscriptSegment,
    now_iso,
)
from app.services import workspace_state
from app.services.media_adapters import (
    MediaAdapterError,
    download_subtitle,
    media_adapter_registry,
    parse_subtitle_segments,
)
from app.services.media_chaptering import MediaChapteringError, build_media_chapters
from app.services.media_package_store import MediaPackageStore, media_package_store
from app.services.media_transcription import (
    MediaTranscriptionError,
    extract_audio_segments,
    openai_transcription_provider,
    probe_duration_seconds,
)
from app.services.media_visual_extraction import (
    MediaVisualExtractionError,
    extract_media_visuals,
)
from app.services.source_structure_store import SourceStructureStore, source_structure_store
from app.services.source_visual_storage import (
    persist_source_visual_asset,
    source_visual_staging,
)


class MediaIngestionPipelineError(RuntimeError):
    pass


ProgressCallback = Callable[[str, int, dict[str, object]], None]


class MediaIngestionPipeline:
    def __init__(
        self,
        *,
        package_store: MediaPackageStore = media_package_store,
        structure_store: SourceStructureStore = source_structure_store,
    ) -> None:
        self.package_store = package_store
        self.structure_store = structure_store

    def process(
        self,
        record: SourceIngestionRecord,
        *,
        transcription_model: AIModelSelection,
        vision_model: AIModelSelection,
        catalog_model: AIModelSelection,
        force_transcription: bool = False,
        reuse_active_transcript: bool = False,
        reuse_active_visuals: bool = False,
        force_download: bool = False,
        progress: ProgressCallback | None = None,
    ) -> SourceIngestionRecord:
        if record.source_type != "video_url" or not record.source_uri:
            raise MediaIngestionPipelineError("Media pipeline requires a video URL source.")
        temp_root = workspace_state.UPLOAD_DIR / "media-temp" / record.id
        package_root = workspace_state.UPLOAD_DIR / "media-packages" / record.id
        temp_root.mkdir(parents=True, exist_ok=True)
        package_root.mkdir(parents=True, exist_ok=True)
        previous_manifest = self.package_store.get_manifest(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
        )
        target_package_version = (
            previous_manifest.version + 1
            if previous_manifest and previous_manifest.active_transcript_version > 0
            else 1
        )
        transcript_version = (
            previous_manifest.active_transcript_version + 1
            if previous_manifest and previous_manifest.active_transcript_version > 0
            else 1
        )
        manifest = MediaPackageManifest(
            version=previous_manifest.version if previous_manifest else 1,
            duration_ms=previous_manifest.duration_ms if previous_manifest else 0,
            language=previous_manifest.language if previous_manifest else "",
            source_content_hash=previous_manifest.source_content_hash if previous_manifest else "",
            active_transcript_version=(
                previous_manifest.active_transcript_version if previous_manifest else 0
            ),
            transcription_model=transcription_model,
            vision_model=vision_model,
            catalog_model=catalog_model,
            transcript_status=(
                previous_manifest.transcript_status
                if reuse_active_transcript and previous_manifest
                else "running"
            ),
            visual_status="running",
            chapter_status=previous_manifest.chapter_status if previous_manifest else "pending",
            transcript_segment_count=(
                previous_manifest.transcript_segment_count if previous_manifest else 0
            ),
            chapter_count=previous_manifest.chapter_count if previous_manifest else 0,
            visual_count=previous_manifest.visual_count if previous_manifest else 0,
            warnings=list(previous_manifest.warnings) if previous_manifest else [],
            raw_media_retained=True,
        )
        manifest = self._save_manifest(record, manifest)
        try:
            _emit(progress, "resolving_media", 5, {})
            adapter, resolved = media_adapter_registry.resolve(record.source_uri, title=record.title)
            _emit(
                progress,
                "downloading_media",
                10,
                {"duration_seconds": resolved.duration_seconds, "provider": resolved.provider},
            )
            video_path = None if force_download else self._reusable_temp_video(
                temp_root,
                expected_hash=previous_manifest.source_content_hash if previous_manifest else "",
            )
            if video_path is None:
                if force_download:
                    for cached_path in temp_root.glob("source-video*"):
                        if cached_path.is_file():
                            cached_path.unlink(missing_ok=True)
                video_path = adapter.download(
                    resolved,
                    destination=temp_root / "source-video",
                    progress=lambda downloaded, total: _emit(
                        progress,
                        "downloading_media",
                        _download_progress(downloaded, total),
                        {"downloaded_bytes": downloaded, "total_bytes": total or 0},
                    ),
                )
            source_hash = _file_hash(video_path)
            duration_seconds = resolved.duration_seconds or probe_duration_seconds(video_path)
            max_duration = int(os.getenv("OPENCLASS_MEDIA_MAX_DURATION_SECONDS") or 14_400)
            if duration_seconds <= 0 or duration_seconds > max_duration:
                raise MediaIngestionPipelineError("Video duration is unavailable or exceeds the configured limit.")
            manifest = manifest.model_copy(
                update={
                    "duration_ms": int(duration_seconds * 1000),
                    "source_content_hash": source_hash,
                }
            )
            self._save_manifest(record, manifest)
            if (
                reuse_active_transcript
                and previous_manifest
                and previous_manifest.source_content_hash == source_hash
            ):
                segments = self.package_store.list_transcript(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_id=record.id,
                    version=previous_manifest.active_transcript_version,
                )
                if not segments:
                    raise MediaIngestionPipelineError("The active transcript version is unavailable.")
                transcript_version = previous_manifest.active_transcript_version
                language = segments[0].language
            else:
                _emit(progress, "extracting_subtitles", 35, {})
                segments = (
                    []
                    if force_transcription
                    else self._subtitle_segments(record, resolved.subtitles, version=transcript_version)
                )
                if segments:
                    language = segments[0].language
                else:
                    _emit(progress, "transcribing_audio", 40, {})
                    audio_segment_seconds = 1200 if transcription_model.model == "whisper-1" else 300
                    audio_files = extract_audio_segments(
                        video_path,
                        temp_root / "audio",
                        segment_seconds=audio_segment_seconds,
                    )
                    transcription = openai_transcription_provider.transcribe(
                        audio_files,
                        selection=transcription_model,
                        segment_seconds=audio_segment_seconds,
                        progress=lambda completed, total: _emit(
                            progress,
                            "transcribing_audio",
                            min(61, 40 + int(completed / max(1, total) * 21)),
                            {
                                "processed_audio_seconds": min(
                                    manifest.duration_ms // 1000,
                                    completed * audio_segment_seconds,
                                ),
                                "audio_segment_count": total,
                            },
                        ),
                    )
                    language = transcription.language
                    segments = [
                        TimedTranscriptSegment(
                            source_ingestion_id=record.id,
                            version=transcript_version,
                            order_index=index,
                            start_ms=item.start_ms,
                            end_ms=item.end_ms,
                            text=item.text,
                            language=transcription.language,
                            source_kind="model_transcription",
                            provider=transcription.provider,
                            model=transcription.model,
                            confidence=item.confidence,
                        )
                        for index, item in enumerate(transcription.segments)
                    ]
                self.package_store.replace_transcript_version(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_id=record.id,
                    version=transcript_version,
                    segments=segments,
                )
            transcript_path = package_root / f"transcript-v{transcript_version}.txt"
            transcript_path.write_text(_transcript_text(segments), encoding="utf-8")
            manifest = manifest.model_copy(
                update={
                    "language": language,
                    "active_transcript_version": transcript_version,
                    "transcript_status": "ready",
                    "transcript_segment_count": len(segments),
                }
            )
            self._save_manifest(record, manifest)

            _emit(progress, "extracting_keyframes", 62, {"duration_ms": manifest.duration_ms})
            visual_candidates = []
            visual_warning = ""
            reused_visuals = False
            if (
                reuse_active_visuals
                and previous_manifest
                and previous_manifest.source_content_hash == source_hash
                and previous_manifest.visual_status in {"ready", "empty"}
            ):
                visual_candidates = self._reuse_visual_candidates(record)
                reused_visuals = previous_manifest.visual_status == "empty" or bool(visual_candidates)
                if reused_visuals:
                    visual_status = previous_manifest.visual_status
            if not reused_visuals:
                try:
                    visual_candidates = extract_media_visuals(
                        video_path,
                        duration_ms=manifest.duration_ms,
                        selection=vision_model,
                        owner_user_id=record.owner_user_id,
                    )
                    visual_status = "ready" if visual_candidates else "empty"
                except MediaVisualExtractionError as exc:
                    visual_status = "failed"
                    visual_warning = str(exc)
            manifest = manifest.model_copy(
                update={
                    "visual_status": visual_status,
                    "visual_count": len(visual_candidates),
                    "warnings": [*manifest.warnings, *([visual_warning] if visual_warning else [])],
                }
            )
            self._save_manifest(record, manifest)
            _emit(
                progress,
                "analyzing_keyframes",
                78,
                {"candidate_keyframes": len(visual_candidates)},
            )

            _emit(progress, "building_media_chapters", 82, {"transcript_segments": len(segments)})
            manifest = manifest.model_copy(update={"chapter_status": "running"})
            self._save_manifest(record, manifest)
            chaptering = build_media_chapters(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_id=record.id,
                source_content_hash=source_hash,
                segments=segments,
                selection=catalog_model,
            )
            structure = SourceStructure(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                status="ready",
                strategy="media_timeline",
                has_verified_toc=True,
                visual_index_status=(
                    "ready" if visual_status in {"ready", "empty"} else "failed"
                ),
                visual_index_version=1,
                confidence=1.0,
                catalog_version=target_package_version,
                catalog_updated_at=now_iso(),
                source_content_hash=source_hash,
                catalog_schema_version="media_timeline_v1",
                catalog_model=catalog_model.model,
                warnings=[visual_warning] if visual_warning else [],
                quality=SourceStructureQuality(
                    level="fully_verified",
                    text_readiness="ready",
                    confidence=1.0,
                    total_chapter_count=len(chaptering.chapters),
                    verified_chapter_count=len(chaptering.chapters),
                    verified_leaf_count=len(chaptering.chapters),
                    expected_leaf_count=len(chaptering.chapters),
                    verified_ratio=1.0,
                    boundary_valid_ratio=1.0,
                    body_coverage_ratio=1.0,
                    independent_anchor_ratio=1.0,
                ),
                metadata={"media_package_version": target_package_version},
            )
            _emit(progress, "persisting_media_package", 94, {"chapters": len(chaptering.chapters)})
            with source_visual_staging():
                visuals = self._materialize_visuals(
                    record=record,
                    structure=structure,
                    chapters=list(chaptering.chapters),
                    candidates=visual_candidates,
                )
                structure = self.structure_store.save_structure_bundle(
                    structure=structure,
                    chapters=list(chaptering.chapters),
                    chunks=list(chaptering.chunks),
                    visuals=visuals,
                    preserve_visual_history=True,
                )
            manifest = manifest.model_copy(
                update={
                    "version": target_package_version,
                    "chapter_status": "ready",
                    "chapter_count": len(chaptering.chapters),
                    "visual_count": len(visuals),
                    "raw_media_retained": False,
                }
            )
            manifest = self._save_manifest(record, manifest)
            shutil.rmtree(temp_root, ignore_errors=True)
            _emit(progress, "media_package_ready", 100, {})
            return record.model_copy(
                update={
                    "title": resolved.title,
                    "file_name": transcript_path.name,
                    "mime_type": "text/plain",
                    "size_bytes": transcript_path.stat().st_size,
                    "status": "ready",
                    "error": "",
                    "structure_status": "ready",
                    "structure_strategy": "media_timeline",
                    "structure_has_verified_toc": True,
                    "structure_quality": structure.quality,
                    "structure_updated_at": structure.updated_at,
                    "media_package": manifest,
                    "metadata": {
                        **record.metadata,
                        "adapter": "media_pipeline_v1",
                        "media_provider": resolved.provider,
                        "media_id": resolved.media_id,
                        "webpage_url": resolved.webpage_url,
                        "local_source_path": str(transcript_path),
                        "content_hash": source_hash,
                        "media_package_version": manifest.version,
                        "original_mime_type": resolved.mime_type,
                    },
                }
            )
        except (
            MediaAdapterError,
            MediaTranscriptionError,
            MediaChapteringError,
            MediaIngestionPipelineError,
        ) as exc:
            failed_updates: dict[str, object] = {"raw_media_retained": temp_root.is_dir()}
            if manifest.transcript_status == "running":
                failed_updates["transcript_status"] = "failed"
            if manifest.visual_status == "running":
                failed_updates["visual_status"] = "failed"
            if manifest.chapter_status == "running":
                failed_updates["chapter_status"] = "failed"
            self._save_manifest(record, manifest.model_copy(update=failed_updates))
            raise MediaIngestionPipelineError(str(exc)) from exc

    def _subtitle_segments(
        self,
        record: SourceIngestionRecord,
        tracks,
        *,
        version: int,
    ) -> list[TimedTranscriptSegment]:
        for track in tracks:
            try:
                parsed = parse_subtitle_segments(download_subtitle(track), track.extension)
            except MediaAdapterError:
                continue
            if not parsed:
                continue
            return [
                TimedTranscriptSegment(
                    source_ingestion_id=record.id,
                    version=version,
                    order_index=index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    language=track.language,
                    source_kind=track.kind,
                    provider="source_subtitle",
                    model="",
                    confidence=1.0 if track.kind == "official_subtitle" else 0.8,
                )
                for index, (start_ms, end_ms, text) in enumerate(parsed)
                if end_ms > start_ms and text.strip()
            ]
        return []

    def _materialize_visuals(self, *, record, structure, chapters, candidates):
        visuals: list[SourceVisualAsset] = []
        for order_index, candidate in enumerate(candidates):
            chapter = next(
                (
                    item
                    for item in chapters
                    if item.media_time_range
                    and item.media_time_range.start_ms <= candidate.timestamp_ms <= item.media_time_range.end_ms
                ),
                None,
            )
            storage_key, content_hash = persist_source_visual_asset(
                candidate.content,
                mime_type="image/jpeg",
            )
            visuals.append(
                SourceVisualAsset(
                    owner_user_id=record.owner_user_id,
                    package_id=record.package_id,
                    source_ingestion_id=record.id,
                    structure_id=structure.id,
                    structure_version=structure.catalog_version,
                    chapter_id=chapter.id if chapter else None,
                    kind="image",
                    source_locator=f"video:frame:{candidate.timestamp_ms}",
                    timestamp_ms=candidate.timestamp_ms,
                    media_role=candidate.role,
                    bbox=list(candidate.content_region or ()),
                    caption=candidate.caption,
                    surrounding_text=chapter.excerpt if chapter else "",
                    anchor_status="verified" if chapter else "unverified",
                    mime_type="image/jpeg",
                    storage_key=storage_key,
                    order_index=order_index,
                    content_hash=content_hash,
                    position_hash=hashlib.sha256(
                        f"{record.id}:{candidate.timestamp_ms}:{candidate.role}".encode()
                    ).hexdigest(),
                    confidence=candidate.confidence,
                    metadata={
                        "timestamp_ms": candidate.timestamp_ms,
                        "media_role": candidate.role,
                        "content_region": list(candidate.content_region or ()),
                        "media_package_version": structure.catalog_version,
                    },
                )
            )
        return visuals

    def _reuse_visual_candidates(self, record):
        from app.services.media_visual_extraction import MediaVisualCandidate

        view = self.structure_store.get_structure_view(source=record, chunk_limit=0)
        candidates: list[MediaVisualCandidate] = []
        for visual in view.visuals:
            if visual.timestamp_ms is None or visual.media_role is None:
                continue
            stored = self.structure_store.read_visual_bytes(
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_id=record.id,
                visual_id=visual.id,
            )
            if stored is None:
                continue
            _asset, content = stored
            candidates.append(
                MediaVisualCandidate(
                    timestamp_ms=visual.timestamp_ms,
                    content=content,
                    role=visual.media_role,
                    caption=visual.caption,
                    confidence=visual.confidence,
                    content_region=(tuple(visual.bbox) if len(visual.bbox) == 4 else None),
                )
            )
        return candidates

    @staticmethod
    def _reusable_temp_video(temp_root: Path, *, expected_hash: str) -> Path | None:
        if not expected_hash:
            return None
        for path in sorted(temp_root.glob("source-video*")):
            if path.is_file() and not path.name.endswith((".part", ".ytdl")):
                if _file_hash(path) == expected_hash:
                    return path
        return None

    def _save_manifest(self, record, manifest):
        return self.package_store.save_manifest(
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_id=record.id,
            manifest=manifest,
        )


def _download_progress(downloaded: int, total: int | None) -> int:
    if not total or total <= 0:
        return 15
    return min(34, 10 + int(downloaded / total * 24))


def _emit(callback: ProgressCallback | None, phase: str, progress: int, metadata: dict[str, object]) -> None:
    if callback:
        callback(phase, progress, metadata)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _transcript_text(segments: list[TimedTranscriptSegment]) -> str:
    return "\n".join(
        f"[{_format_time(item.start_ms)}] {item.text.strip()}" for item in segments
    )


def _format_time(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


media_ingestion_pipeline = MediaIngestionPipeline()
