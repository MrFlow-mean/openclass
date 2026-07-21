from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class PublicUrlPolicyError(ValueError):
    pass


def validate_public_http_url(raw_uri: str) -> str:
    uri = raw_uri.strip()
    parsed = urlparse(uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PublicUrlPolicyError("Only public http and https URLs are supported.")
    if parsed.username or parsed.password:
        raise PublicUrlPolicyError("Authenticated URLs are not supported.")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise PublicUrlPolicyError("URL hostname is required.")
    try:
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(hostname, None)
        }
    except socket.gaierror as exc:
        raise PublicUrlPolicyError("URL hostname could not be resolved.") from exc
    if not addresses:
        raise PublicUrlPolicyError("URL hostname could not be resolved.")
    if any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise PublicUrlPolicyError("Private network URLs are not allowed for source ingestion.")
    return uri
