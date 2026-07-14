from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app.services import source_url_snapshot
from app.services import workspace_state
from app.models import SourceIngestionRecord
from app.services.source_url_snapshot import SourceUrlSnapshotError


class _Response:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> None:
        self.status = status
        self._headers = {key.lower(): value for key, value in (headers or {}).items()}
        self._content = content
        self._offset = 0

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name.lower(), default)

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else min(len(self._content), self._offset + size)
        chunk = self._content[self._offset:end]
        self._offset = end
        return chunk


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.request_target = ""
        self.closed = False

    def request(self, _method: str, target: str, *, headers: dict[str, str]) -> None:
        self.request_target = target
        assert headers["Host"]

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_url_snapshot_revalidates_redirect_and_rejects_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_addresses: list[str] = []

    def resolve(url: str):
        if url == "https://public.example/source":
            return urlparse(url), ("93.184.216.34",)
        assert url == "http://127.0.0.1/internal"
        return None

    def open_connection(_parsed, address: str, *, timeout: float):
        assert timeout == 20.0
        opened_addresses.append(address)
        return _Connection(
            _Response(
                status=302,
                headers={"location": "http://127.0.0.1/internal"},
            )
        )

    monkeypatch.setattr(source_url_snapshot, "_resolve_public_http_url", resolve)
    monkeypatch.setattr(source_url_snapshot, "_open_pinned_http_connection", open_connection)

    with pytest.raises(SourceUrlSnapshotError, match="disallowed"):
        source_url_snapshot._fetch_public_text("https://public.example/source")

    assert opened_addresses == ["93.184.216.34"]


def test_pinned_http_connection_uses_validated_address_without_second_dns_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected: list[tuple[tuple[str, int], float | None]] = []
    sentinel_socket = object()

    def create_connection(address, timeout, _source_address):
        connected.append((address, timeout))
        return sentinel_socket

    monkeypatch.setattr(source_url_snapshot.socket, "create_connection", create_connection)
    connection = source_url_snapshot._PinnedHTTPConnection(
        "public.example",
        80,
        connect_host="93.184.216.34",
        timeout=7.0,
    )

    connection.connect()

    assert connection.sock is sentinel_socket
    assert connected == [(('93.184.216.34', 80), 7.0)]


def test_url_snapshot_stream_limit_applies_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_url_snapshot, "MAX_URL_SNAPSHOT_BYTES", 8)
    monkeypatch.setattr(
        source_url_snapshot,
        "_resolve_public_http_url",
        lambda url: (urlparse(url), ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        source_url_snapshot,
        "_open_pinned_http_connection",
        lambda *_args, **_kwargs: _Connection(
            _Response(
                status=200,
                headers={"content-type": "text/plain"},
                content=b"0123456789",
            )
        ),
    )

    with pytest.raises(SourceUrlSnapshotError, match="readable text"):
        source_url_snapshot._fetch_public_text("https://public.example/source")


def test_html_snapshot_preserves_dom_and_final_redirect_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        source_url_snapshot,
        "_fetch_public_text",
        lambda _url: (
            b'<html><body><p>Readable text</p><img src="images/chart.png">'
            b'<table><tr><td>A</td></tr></table></body></html>',
            "text/html; charset=utf-8",
            "https://cdn.example/final/page.html",
        ),
    )
    record = SourceIngestionRecord(
        id="source_html_snapshot",
        owner_user_id="owner",
        package_id="package",
        title="Web reference",
        source_type="web_url",
        source_uri="https://public.example/start",
        mime_type="text/html",
        status="fetching",
    )

    metadata = source_url_snapshot.fetch_url_source_snapshot(record, record.source_uri or "")

    saved = source_url_snapshot.Path(metadata["local_source_path"])
    assert saved.suffix == ".html"
    assert '<img src="images/chart.png">' in saved.read_text(encoding="utf-8")
    assert "<table>" in saved.read_text(encoding="utf-8")
    assert metadata["resolved_source_uri"] == "https://cdn.example/final/page.html"
