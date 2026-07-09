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
