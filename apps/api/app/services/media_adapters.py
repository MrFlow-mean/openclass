from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin

import httpx

from app.services.public_url_policy import PublicUrlPolicyError, validate_public_http_url


MAX_MEDIA_BYTES_DEFAULT = 2 * 1024 * 1024 * 1024
MAX_MEDIA_DURATION_SECONDS_DEFAULT = 4 * 60 * 60
DIRECT_MEDIA_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/mpeg",
}


class MediaAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaSubtitleTrack:
    language: str
    kind: str
    extension: str
    url: str


@dataclass(frozen=True)
class ResolvedMedia:
    source_uri: str
    title: str
    provider: str
    media_id: str
    duration_seconds: float
    webpage_url: str
    direct_media_url: str = ""
    mime_type: str = "video/mp4"
    subtitles: tuple[MediaSubtitleTrack, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class MediaAdapter(Protocol):
    def resolve(self, source_uri: str, *, title: str = "") -> ResolvedMedia: ...

    def download(
        self,
        resolved: ResolvedMedia,
        *,
        destination: Path,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path: ...


class DirectHttpMediaAdapter:
    def __init__(self, *, max_bytes: int = MAX_MEDIA_BYTES_DEFAULT) -> None:
        self.max_bytes = max_bytes

    def resolve(self, source_uri: str, *, title: str = "") -> ResolvedMedia:
        try:
            validated = validate_public_http_url(source_uri)
        except PublicUrlPolicyError as exc:
            raise MediaAdapterError(str(exc)) from exc
        try:
            with httpx.Client(follow_redirects=False, timeout=30) as client:
                response = _request_with_public_redirects(client, "HEAD", validated)
                if response.status_code >= 400 or response.status_code == 405:
                    response = _request_with_public_redirects(
                        client,
                        "GET",
                        validated,
                        headers={"Range": "bytes=0-0"},
                    )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MediaAdapterError(f"Video URL could not be inspected: {exc}") from exc
        try:
            final_url = validate_public_http_url(str(response.url))
        except PublicUrlPolicyError as exc:
            raise MediaAdapterError("Video URL redirected to a non-public address.") from exc
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in DIRECT_MEDIA_TYPES:
            raise MediaAdapterError("URL is not a supported direct video response.")
        content_length = _positive_int(response.headers.get("content-length"))
        if content_length and content_length > self.max_bytes:
            raise MediaAdapterError("Video exceeds the configured media size limit.")
        display_title = title.strip() or Path(response.url.path).name or final_url
        return ResolvedMedia(
            source_uri=validated,
            title=display_title,
            provider="direct_http",
            media_id="",
            duration_seconds=0,
            webpage_url=validated,
            direct_media_url=final_url,
            mime_type=content_type,
            metadata={"content_length": content_length},
        )

    def download(
        self,
        resolved: ResolvedMedia,
        *,
        destination: Path,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        total: int | None = None
        try:
            with httpx.stream("GET", resolved.direct_media_url, follow_redirects=False, timeout=60) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise MediaAdapterError("Video download redirect changed after URL validation.")
                validate_public_http_url(str(response.url))
                total = _positive_int(response.headers.get("content-length")) or None
                if total and total > self.max_bytes:
                    raise MediaAdapterError("Video exceeds the configured media size limit.")
                with destination.open("wb") as handle:
                    for block in response.iter_bytes(chunk_size=1024 * 1024):
                        downloaded += len(block)
                        if downloaded > self.max_bytes:
                            raise MediaAdapterError("Video exceeds the configured media size limit.")
                        handle.write(block)
                        if progress:
                            progress(downloaded, total)
        except MediaAdapterError:
            destination.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, PublicUrlPolicyError) as exc:
            destination.unlink(missing_ok=True)
            raise MediaAdapterError(f"Video download failed: {exc}") from exc
        return destination


class YtDlpMediaAdapter:
    def __init__(
        self,
        *,
        max_bytes: int = MAX_MEDIA_BYTES_DEFAULT,
        max_duration_seconds: int = MAX_MEDIA_DURATION_SECONDS_DEFAULT,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_duration_seconds = max_duration_seconds

    def resolve(self, source_uri: str, *, title: str = "") -> ResolvedMedia:
        try:
            validated = validate_public_http_url(source_uri)
        except PublicUrlPolicyError as exc:
            raise MediaAdapterError(str(exc)) from exc
        try:
            import yt_dlp  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            raise MediaAdapterError("Server is missing yt-dlp.") from exc
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": False,
            "cachedir": False,
            "socket_timeout": 30,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(validated, download=False)
        except Exception as exc:
            raise MediaAdapterError(f"Video page could not be resolved: {exc}") from exc
        if not isinstance(info, dict):
            raise MediaAdapterError("Video page returned invalid metadata.")
        entries = info.get("entries")
        if isinstance(entries, list):
            raise MediaAdapterError("V1 accepts one on-demand video, not playlists or multi-video pages.")
        if str(info.get("_type") or "").lower() in {"playlist", "multi_video"}:
            raise MediaAdapterError("V1 accepts one on-demand video, not playlists or multi-video pages.")
        if info.get("is_live") or str(info.get("live_status") or "").lower() in {"is_live", "post_live"}:
            raise MediaAdapterError("Live video is not supported.")
        if info.get("has_drm"):
            raise MediaAdapterError("DRM-protected video is not supported.")
        if str(info.get("availability") or "").lower() in {
            "private",
            "subscriber_only",
            "premium_only",
            "needs_auth",
        }:
            raise MediaAdapterError("Video requires authentication and is not supported.")
        duration = float(info.get("duration") or 0)
        if duration > self.max_duration_seconds:
            raise MediaAdapterError("Video exceeds the configured duration limit.")
        estimated_bytes = _positive_int(info.get("filesize") or info.get("filesize_approx"))
        if estimated_bytes and estimated_bytes > self.max_bytes:
            raise MediaAdapterError("Video exceeds the configured media size limit.")
        webpage_url = str(info.get("webpage_url") or validated)
        validate_public_http_url(webpage_url)
        for media_format in info.get("formats", []) if isinstance(info.get("formats"), list) else []:
            if isinstance(media_format, dict) and media_format.get("url"):
                try:
                    validate_public_http_url(str(media_format["url"]))
                except PublicUrlPolicyError as exc:
                    raise MediaAdapterError("Video metadata contains a non-public media URL.") from exc
        subtitles = tuple(_subtitle_tracks(info))
        return ResolvedMedia(
            source_uri=validated,
            title=title.strip() or str(info.get("title") or validated),
            provider=str(info.get("extractor_key") or info.get("extractor") or "yt_dlp"),
            media_id=str(info.get("id") or ""),
            duration_seconds=duration,
            webpage_url=webpage_url,
            mime_type="video/mp4",
            subtitles=subtitles,
            metadata={
                "uploader": info.get("uploader"),
                "license": info.get("license"),
                "description": str(info.get("description") or "")[:4000],
                "estimated_bytes": estimated_bytes,
            },
        )

    def download(
        self,
        resolved: ResolvedMedia,
        *,
        destination: Path,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        try:
            import yt_dlp  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            raise MediaAdapterError("Server is missing yt-dlp.") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)

        def hook(payload: dict[str, Any]) -> None:
            if payload.get("status") != "downloading":
                return
            downloaded = int(payload.get("downloaded_bytes") or 0)
            total = int(payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0) or None
            if downloaded > self.max_bytes or (total and total > self.max_bytes):
                raise MediaAdapterError("Video exceeds the configured media size limit.")
            if progress:
                progress(downloaded, total)

        template = str(destination.with_suffix(".%(ext)s"))
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "cachedir": False,
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mp4",
            "outtmpl": template,
            "progress_hooks": [hook],
            "socket_timeout": 30,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(resolved.source_uri, download=True)
                final_name = downloader.prepare_filename(info)
        except MediaAdapterError:
            raise
        except Exception as exc:
            raise MediaAdapterError(f"Video download failed: {exc}") from exc
        candidates = [destination.with_suffix(".mp4"), Path(final_name)]
        candidates.extend(
            path
            for path in destination.parent.glob(f"{destination.stem}.*")
            if not path.name.endswith((".part", ".ytdl"))
        )
        output = next((path for path in candidates if path.is_file()), None)
        if output is None:
            raise MediaAdapterError("Video downloader did not produce a media file.")
        if output.stat().st_size > self.max_bytes:
            output.unlink(missing_ok=True)
            raise MediaAdapterError("Video exceeds the configured media size limit.")
        return output


class MediaAdapterRegistry:
    def __init__(self) -> None:
        max_bytes = int(os.getenv("OPENCLASS_MEDIA_MAX_BYTES") or MAX_MEDIA_BYTES_DEFAULT)
        max_duration = int(
            os.getenv("OPENCLASS_MEDIA_MAX_DURATION_SECONDS")
            or MAX_MEDIA_DURATION_SECONDS_DEFAULT
        )
        self.direct = DirectHttpMediaAdapter(max_bytes=max_bytes)
        self.yt_dlp = YtDlpMediaAdapter(
            max_bytes=max_bytes,
            max_duration_seconds=max_duration,
        )

    def resolve(self, source_uri: str, *, title: str = "") -> tuple[MediaAdapter, ResolvedMedia]:
        try:
            resolved = self.direct.resolve(source_uri, title=title)
            return self.direct, resolved
        except MediaAdapterError as direct_error:
            if "not a supported direct video" not in str(direct_error).lower():
                # Network and policy failures should not be hidden by a generic extractor retry.
                if any(term in str(direct_error).lower() for term in ("private", "authenticated", "size limit")):
                    raise
        return self.yt_dlp, self.yt_dlp.resolve(source_uri, title=title)


def download_subtitle(track: MediaSubtitleTrack) -> str:
    try:
        uri = validate_public_http_url(track.url)
        with httpx.Client(follow_redirects=False, timeout=30) as client:
            response = _request_with_public_redirects(client, "GET", uri)
        validate_public_http_url(str(response.url))
    except (httpx.HTTPError, PublicUrlPolicyError) as exc:
        raise MediaAdapterError(f"Subtitle download failed: {exc}") from exc
    if len(response.content) > 16 * 1024 * 1024:
        raise MediaAdapterError("Subtitle track exceeds the configured size limit.")
    return response.text


def _request_with_public_redirects(
    client: httpx.Client,
    method: str,
    uri: str,
    *,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
) -> httpx.Response:
    current = validate_public_http_url(uri)
    for _attempt in range(max_redirects + 1):
        response = client.request(method, current, headers=headers)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        if not location:
            raise PublicUrlPolicyError("Redirect response is missing a location.")
        current = validate_public_http_url(urljoin(current, location))
    raise PublicUrlPolicyError("URL exceeded the redirect limit.")


def parse_subtitle_segments(raw: str, extension: str) -> list[tuple[int, int, str]]:
    if extension.lower() == "json3":
        return _parse_json3(raw)
    return _parse_vtt_or_srt(raw)


def _subtitle_tracks(info: dict[str, Any]) -> list[MediaSubtitleTrack]:
    preferred_languages = ("zh-Hans", "zh-CN", "zh", "en", "en-US")
    tracks: list[MediaSubtitleTrack] = []
    for kind, field in (("official_subtitle", "subtitles"), ("automatic_subtitle", "automatic_captions")):
        by_language = info.get(field)
        if not isinstance(by_language, dict):
            continue
        languages = [*preferred_languages, *[str(key) for key in by_language if key not in preferred_languages]]
        for language in languages:
            candidates = by_language.get(language)
            if not isinstance(candidates, list):
                continue
            ranked = sorted(
                (item for item in candidates if isinstance(item, dict) and item.get("url")),
                key=lambda item: {"vtt": 0, "json3": 1, "srt": 2}.get(str(item.get("ext") or ""), 9),
            )
            if ranked:
                item = ranked[0]
                tracks.append(
                    MediaSubtitleTrack(
                        language=language,
                        kind=kind,
                        extension=str(item.get("ext") or "vtt"),
                        url=str(item["url"]),
                    )
                )
                break
        if tracks:
            break
    return tracks


def _parse_vtt_or_srt(raw: str) -> list[tuple[int, int, str]]:
    pattern = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})[^\n]*\n"
        r"(?P<text>.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    segments: list[tuple[int, int, str]] = []
    for match in pattern.finditer(raw.replace("\r\n", "\n")):
        text = re.sub(r"<[^>]+>", "", match.group("text"))
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if text:
            segments.append((_timestamp_ms(match.group("start")), _timestamp_ms(match.group("end")), text))
    return segments


def _parse_json3(raw: str) -> list[tuple[int, int, str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    segments: list[tuple[int, int, str]] = []
    for event in payload.get("events", []) if isinstance(payload, dict) else []:
        if not isinstance(event, dict):
            continue
        text = "".join(
            str(item.get("utf8") or "")
            for item in event.get("segs", [])
            if isinstance(item, dict)
        ).strip()
        start = int(event.get("tStartMs") or 0)
        duration = int(event.get("dDurationMs") or 0)
        if text and duration > 0:
            segments.append((start, start + duration, " ".join(text.split())))
    return segments


def _timestamp_ms(value: str) -> int:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return int(round((hours * 3600 + minutes * 60 + seconds) * 1000))


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


media_adapter_registry = MediaAdapterRegistry()
