from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

DEFAULT_TRANSCRIPT_LANGUAGES = ("zh-Hans", "zh-CN", "zh", "en", "en-US")
CAPTION_EXT_PRIORITY = ("vtt", "json3", "srv3", "ttml")


@dataclass(frozen=True)
class YouTubeTranscript:
    title: str
    video_id: str
    language: str
    text: str
    metadata: dict[str, Any]


class YouTubeTranscriptAdapterError(RuntimeError):
    pass


class YouTubeTranscriptAdapter:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def extract(self, source_uri: str, *, title: str = "") -> YouTubeTranscript:
        info = self._extract_info(source_uri)
        track = _select_caption_track(info)
        if not track:
            raise YouTubeTranscriptAdapterError("YouTube 视频没有可用字幕或自动字幕；V1 暂不做音频转写。")
        raw_caption = self._download_caption(str(track["url"]))
        segments = _caption_segments(raw_caption, str(track.get("ext") or ""))
        if not segments:
            raise YouTubeTranscriptAdapterError("YouTube 字幕轨道为空或格式暂不支持。")
        video_title = title.strip() or _string_value(info, "title") or source_uri
        video_id = _string_value(info, "id")
        language = str(track.get("language") or track.get("language_code") or "")
        text = _transcript_text(
            title=video_title,
            source_uri=source_uri,
            language=language,
            segments=segments,
        )
        return YouTubeTranscript(
            title=video_title,
            video_id=video_id,
            language=language,
            text=text,
            metadata={
                "adapter": "youtube_transcript",
                "media_provider": "youtube",
                "media_kind": "video",
                "video_id": video_id,
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "webpage_url": info.get("webpage_url") or source_uri,
                "transcript_language": language,
                "transcript_kind": track.get("kind") or "",
                "transcript_ext": track.get("ext") or "",
            },
        )

    def _extract_info(self, source_uri: str) -> dict[str, Any]:
        try:
            import yt_dlp  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency is installed in normal runtime.
            raise YouTubeTranscriptAdapterError("服务器缺少 yt-dlp，无法读取 YouTube 字幕。") from exc
        options = {
            "extract_flat": False,
            "noplaylist": True,
            "quiet": True,
            "skip_download": True,
            "writesubtitles": False,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(source_uri, download=False)
        except Exception as exc:  # yt-dlp raises several extractor-specific exception classes.
            raise YouTubeTranscriptAdapterError(f"YouTube 字幕信息读取失败：{exc}") from exc
        if not isinstance(info, dict):
            raise YouTubeTranscriptAdapterError("YouTube 字幕信息格式异常。")
        return info

    def _download_caption(self, caption_uri: str) -> str:
        try:
            response = httpx.get(caption_uri, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise YouTubeTranscriptAdapterError(f"YouTube 字幕下载失败：{exc}") from exc
        return response.text


def is_youtube_url(source_uri: str) -> bool:
    hostname = (urlparse(source_uri).hostname or "").lower()
    return hostname in YOUTUBE_HOSTS


def _select_caption_track(info: dict[str, Any]) -> dict[str, Any] | None:
    for kind, tracks_by_language in (
        ("subtitles", info.get("subtitles")),
        ("automatic_captions", info.get("automatic_captions")),
    ):
        if not isinstance(tracks_by_language, dict):
            continue
        track = _select_caption_track_from_languages(tracks_by_language, kind=kind)
        if track:
            return track
    return None


def _select_caption_track_from_languages(tracks_by_language: dict[str, Any], *, kind: str) -> dict[str, Any] | None:
    candidate_languages = list(DEFAULT_TRANSCRIPT_LANGUAGES)
    candidate_languages.extend(language for language in tracks_by_language if language not in candidate_languages)
    for language in candidate_languages:
        tracks = tracks_by_language.get(language)
        if not isinstance(tracks, list):
            continue
        track = _best_track(tracks)
        if track:
            return {**track, "language": language, "kind": kind}
    return None


def _best_track(tracks: list[Any]) -> dict[str, Any] | None:
    valid_tracks = [track for track in tracks if isinstance(track, dict) and isinstance(track.get("url"), str)]
    for ext in CAPTION_EXT_PRIORITY:
        for track in valid_tracks:
            if str(track.get("ext") or "").lower() == ext:
                return track
    return valid_tracks[0] if valid_tracks else None


def _caption_segments(raw: str, ext: str) -> list[tuple[float | None, str]]:
    normalized_ext = ext.lower().strip()
    if normalized_ext == "json3":
        return _json3_segments(raw)
    if normalized_ext in {"srv3", "ttml"} or raw.lstrip().startswith("<"):
        return _xml_segments(raw)
    return _vtt_segments(raw)


def _vtt_segments(raw: str) -> list[tuple[float | None, str]]:
    segments: list[tuple[float | None, str]] = []
    current_time: float | None = None
    text_parts: list[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            _append_segment(segments, current_time, " ".join(text_parts))
            current_time = None
            text_parts = []
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE", "REGION")) or line.isdigit():
            continue
        if "-->" in line:
            _append_segment(segments, current_time, " ".join(text_parts))
            current_time = _seconds_from_timestamp(line.split("-->", 1)[0].strip())
            text_parts = []
            continue
        text = _clean_caption_text(line)
        if text:
            text_parts.append(text)
    _append_segment(segments, current_time, " ".join(text_parts))
    return segments


def _json3_segments(raw: str) -> list[tuple[float | None, str]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    segments: list[tuple[float | None, str]] = []
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        text = "".join(str(segment.get("utf8") or "") for segment in event.get("segs", []) if isinstance(segment, dict))
        _append_segment(segments, _milliseconds_to_seconds(event.get("tStartMs")), text)
    return segments


def _xml_segments(raw: str) -> list[tuple[float | None, str]]:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    segments: list[tuple[float | None, str]] = []
    for element in root.iter():
        if not element.tag.lower().endswith(("p", "text")):
            continue
        text = _clean_caption_text(" ".join(element.itertext()))
        timestamp = _milliseconds_to_seconds(element.attrib.get("t"))
        if timestamp is None:
            timestamp = _seconds_from_timestamp(str(element.attrib.get("begin") or ""))
        _append_segment(segments, timestamp, text)
    return segments


def _append_segment(segments: list[tuple[float | None, str]], timestamp: float | None, text: str) -> None:
    cleaned = _clean_caption_text(text)
    if cleaned:
        segments.append((timestamp, cleaned))


def _clean_caption_text(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", "", text)
    cleaned = html.unescape(no_tags).replace("\u200b", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _transcript_text(
    *,
    title: str,
    source_uri: str,
    language: str,
    segments: list[tuple[float | None, str]],
) -> str:
    header = [
        f"Title: {title}",
        f"Source: {source_uri}",
        "Media type: YouTube video",
        f"Transcript language: {language or 'unknown'}",
        "",
        "Transcript:",
    ]
    lines = [f"[{_format_timestamp(timestamp)}] {text}" if timestamp is not None else text for timestamp, text in segments]
    return "\n".join([*header, *lines]).strip()


def _seconds_from_timestamp(value: str) -> float | None:
    timestamp = value.strip().split(" ", 1)[0]
    if not timestamp:
        return None
    parts = timestamp.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def _milliseconds_to_seconds(value: Any) -> float | None:
    try:
        return float(value) / 1000
    except (TypeError, ValueError):
        return None


def _format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _string_value(info: dict[str, Any], key: str) -> str:
    value = info.get(key)
    return value.strip() if isinstance(value, str) else ""


youtube_transcript_adapter = YouTubeTranscriptAdapter()
