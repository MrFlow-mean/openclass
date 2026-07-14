from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import ParseResult, urljoin, urlparse

from app.models import SourceIngestionRecord
from app.services import workspace_state


class SourceUrlSnapshotError(RuntimeError):
    pass


MAX_URL_SNAPSHOT_BYTES = 8 * 1024 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def fetch_url_source_snapshot(record: SourceIngestionRecord, source_uri: str) -> dict[str, str]:
    content, content_type, resolved_source_uri = _fetch_public_text(source_uri)
    text = _decode_text(content, content_type)
    if "text/html" in content_type:
        if not text.strip():
            raise SourceUrlSnapshotError("URL did not return readable HTML.")
        snapshot_metadata = _save_local_source_text(record, text, suffix=".html")
    else:
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not text:
            raise SourceUrlSnapshotError("URL did not return readable text.")
        snapshot_metadata = _save_local_source_text(record, text, suffix=".txt")
    return {
        **snapshot_metadata,
        "local_source_snapshot_mime_type": content_type or "text/plain",
        "resolved_source_uri": resolved_source_uri,
    }


def _fetch_public_text(url: str) -> tuple[bytes, str, str]:
    current = url
    for hop in range(5):
        resolved = _resolve_public_http_url(current)
        if resolved is None:
            raise SourceUrlSnapshotError("URL resolved to a disallowed or unavailable network address.")
        parsed, addresses = resolved
        redirect_url = ""
        last_error: Exception | None = None
        for address in addresses:
            connection: http.client.HTTPConnection | None = None
            try:
                connection = _open_pinned_http_connection(parsed, address, timeout=20.0)
                connection.request(
                    "GET",
                    _http_request_target(parsed),
                    headers={
                        "Accept": "text/html,text/plain,text/*;q=0.9",
                        "Accept-Encoding": "identity",
                        "Host": _http_host_header(parsed),
                        "User-Agent": "OpenClassSourceIngestion/1.0",
                    },
                )
                response = connection.getresponse()
                if response.status in _REDIRECT_STATUSES:
                    location = str(response.getheader("location") or "").strip()
                    if not location:
                        raise SourceUrlSnapshotError("URL redirect did not include a destination.")
                    if hop >= 4:
                        raise SourceUrlSnapshotError("URL exceeded the redirect limit.")
                    redirect_url = urljoin(current, location)
                    break
                if response.status < 200 or response.status >= 300:
                    raise SourceUrlSnapshotError(f"URL returned HTTP status {response.status}.")
                content_type = str(response.getheader("content-type") or "").strip().lower()
                normalized_mime = content_type.split(";", 1)[0].strip()
                if normalized_mime and not normalized_mime.startswith("text/"):
                    raise SourceUrlSnapshotError(
                        "Only text and HTML URLs are supported by the native URL importer."
                    )
                content_encoding = str(response.getheader("content-encoding") or "").strip().lower()
                if content_encoding not in {"", "identity"}:
                    raise SourceUrlSnapshotError("Compressed URL responses are not accepted.")
                declared_length = str(response.getheader("content-length") or "").strip()
                if declared_length:
                    try:
                        length = int(declared_length)
                    except ValueError as exc:
                        raise SourceUrlSnapshotError("URL returned an invalid content length.") from exc
                    if length < 0 or length > MAX_URL_SNAPSHOT_BYTES:
                        raise SourceUrlSnapshotError("URL response exceeds the snapshot size limit.")
                content = _read_limited_http_body(response)
                if not content:
                    raise SourceUrlSnapshotError("URL did not return readable text.")
                return content, content_type or "text/plain", current
            except SourceUrlSnapshotError:
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
                continue
            finally:
                if connection is not None:
                    connection.close()
        if redirect_url:
            current = redirect_url
            continue
        if last_error is not None:
            raise SourceUrlSnapshotError(f"URL request failed: {last_error}") from last_error
        raise SourceUrlSnapshotError("URL request failed for every validated address.")
    raise SourceUrlSnapshotError("URL exceeded the redirect limit.")


def _resolve_public_http_url(url: str) -> tuple[ParseResult, tuple[str, ...]] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = tuple(
            dict.fromkeys(
                str(ipaddress.ip_address(item[4][0]))
                for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
            )
        )
    except (OSError, ValueError):
        return None
    if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
        return None
    return parsed, addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, origin_host: str, port: int, *, connect_host: str, timeout: float) -> None:
        super().__init__(origin_host, port=port, timeout=timeout)
        self._connect_host = connect_host

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._connect_host, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, origin_host: str, port: int, *, connect_host: str, timeout: float) -> None:
        super().__init__(origin_host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._connect_host = connect_host

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_host, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def _open_pinned_http_connection(
    parsed: ParseResult,
    resolved_address: str,
    *,
    timeout: float,
) -> http.client.HTTPConnection:
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    return connection_type(parsed.hostname, port, connect_host=resolved_address, timeout=timeout)


def _http_request_target(parsed: ParseResult) -> str:
    target = parsed.path or "/"
    return f"{target}?{parsed.query}" if parsed.query else target


def _http_host_header(parsed: ParseResult) -> str:
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{hostname}:{parsed.port}" if parsed.port and parsed.port != default_port else hostname


def _read_limited_http_body(response: http.client.HTTPResponse) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = MAX_URL_SNAPSHOT_BYTES - total
        chunk = response.read(min(64 * 1024, remaining + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_URL_SNAPSHOT_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_text(content: bytes, content_type: str) -> str:
    charset_match = re.search(r"(?:^|;)\s*charset\s*=\s*['\"]?([^;\s'\"]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _save_local_source_text(
    record: SourceIngestionRecord,
    text: str,
    *,
    suffix: str = ".txt",
) -> dict[str, str]:
    safe_name = _safe_file_name(record.file_name or record.title or record.id)
    source_dir = workspace_state.UPLOAD_DIR / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / f"{record.id}_{safe_name}{suffix}"
    path.write_text(text, encoding="utf-8")
    return {"local_source_path": str(path)}


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(html)
    return "\n".join(part.strip() for part in parser.parts if part.strip())


def _safe_file_name(file_name: str) -> str:
    name = Path(file_name).name.strip() or "source"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:180]
