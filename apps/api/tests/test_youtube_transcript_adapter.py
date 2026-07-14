from app.services import youtube_transcript_adapter
from app.services.youtube_transcript_adapter import YouTubeTranscriptAdapter, is_youtube_url


class _FakeYouTubeTranscriptAdapter(YouTubeTranscriptAdapter):
    def _extract_info(self, source_uri: str):
        return {
            "id": "video_1",
            "title": "Transcript Video",
            "duration": 12,
            "uploader": "OpenClass Test",
            "subtitles": {
                "en": [
                    {
                        "url": "https://caption.local/en.vtt",
                        "ext": "vtt",
                    }
                ]
            },
        }

    def _download_caption(self, caption_uri: str) -> str:
        return """WEBVTT

00:00:01.000 --> 00:00:03.000
First caption line.

00:00:04.000 --> 00:00:05.000
Second <c>caption</c> line.
"""


def test_youtube_transcript_adapter_converts_vtt_to_plain_transcript() -> None:
    transcript = _FakeYouTubeTranscriptAdapter().extract("https://www.youtube.com/watch?v=video_1")

    assert transcript.title == "Transcript Video"
    assert transcript.video_id == "video_1"
    assert transcript.metadata["transcript_kind"] == "subtitles"
    assert "[00:01] First caption line." in transcript.text
    assert "[00:04] Second caption line." in transcript.text


def test_is_youtube_url_matches_standard_hosts() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=abc")
    assert is_youtube_url("https://youtu.be/abc")
    assert not is_youtube_url("https://example.com/watch?v=abc")


def test_youtube_adapter_uses_explicit_browser_cookie_configuration(monkeypatch) -> None:
    captured_options = {}

    class _FakeDownloader:
        def __init__(self, options):
            captured_options.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _source_uri, *, download):
            assert download is False
            return {"id": "video_1"}

    monkeypatch.setenv("OPENCLASS_YTDLP_COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.setenv("OPENCLASS_YTDLP_BROWSER_PROFILE", "Profile 2")
    monkeypatch.setenv("OPENCLASS_YTDLP_COOKIE_FILE", "/private/cookies.txt")
    monkeypatch.setattr(youtube_transcript_adapter.shutil, "which", lambda name: "/usr/local/bin/node" if name == "node" else None)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", type("_YtDlp", (), {"YoutubeDL": _FakeDownloader}))

    info = YouTubeTranscriptAdapter()._extract_info("https://www.youtube.com/watch?v=video_1")

    assert info == {"id": "video_1"}
    assert captured_options["cookiesfrombrowser"] == ("chrome", "Profile 2", None, None)
    assert captured_options["cookiefile"] == "/private/cookies.txt"
    assert captured_options["js_runtimes"] == {"node": {"path": "/usr/local/bin/node"}}


def test_youtube_adapter_downloads_caption_through_authenticated_yt_dlp(monkeypatch) -> None:
    captured_options = {}

    class _FakeResponse:
        def read(self):
            return b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCaption"

    class _FakeDownloader:
        def __init__(self, options):
            captured_options.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def urlopen(self, caption_uri):
            assert caption_uri == "https://caption.local/en.vtt"
            return _FakeResponse()

    monkeypatch.setenv("OPENCLASS_YTDLP_COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", type("_YtDlp", (), {"YoutubeDL": _FakeDownloader}))

    caption = YouTubeTranscriptAdapter()._download_caption("https://caption.local/en.vtt")

    assert caption.startswith("WEBVTT")
    assert captured_options["cookiesfrombrowser"] == ("chrome", None, None, None)


def test_youtube_adapter_explains_authentication_requirement() -> None:
    error = youtube_transcript_adapter._extract_info_error_message(
        RuntimeError("Sign in to confirm you're not a bot")
    )

    assert "OPENCLASS_YTDLP_COOKIES_FROM_BROWSER=chrome" in error
