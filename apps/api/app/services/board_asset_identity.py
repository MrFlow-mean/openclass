from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_BOARD_ASSET_URL_TEMPLATE = "/api/board-assets/{asset_id}/content"
_JSON_ASSET_ID_KEYS = frozenset({"assetId"})
_JSON_ASSET_URL_KEYS = frozenset({"originalSrc", "asset_url"})
_JSON_ASSET_ID_LIST_KEYS = frozenset({"board_asset_ids"})
_HTML_ATTRIBUTE_RE = re.compile(
    r"(?P<prefix>\b(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)


def stable_board_asset_id(*, owner_user_id: str, content_hash: str) -> str:
    owner = owner_user_id.strip()
    digest = content_hash.strip().lower()
    if not owner or len(digest) < 32:
        raise ValueError("Board asset identity requires an owner and content hash.")
    owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
    return f"basset_{owner_hash}_{digest[:32]}"


def rewrite_board_asset_json(raw: str, *, old_asset_id: str, new_asset_id: str) -> str:
    """Rewrite only schema-owned board asset reference fields in serialized JSON."""

    if not raw or old_asset_id not in raw or old_asset_id == new_asset_id:
        return raw
    value = json.loads(raw)
    rewritten = _rewrite_json_node(value, old_asset_id=old_asset_id, new_asset_id=new_asset_id)
    return json.dumps(rewritten, ensure_ascii=False, separators=(",", ":"))


def rewrite_board_asset_html(raw: str, *, old_asset_id: str, new_asset_id: str) -> str:
    """Rewrite controlled board asset attributes without touching visible HTML text."""

    if not raw or old_asset_id not in raw or old_asset_id == new_asset_id:
        return raw
    old_url = _BOARD_ASSET_URL_TEMPLATE.format(asset_id=old_asset_id)
    new_url = _BOARD_ASSET_URL_TEMPLATE.format(asset_id=new_asset_id)

    def replace_attribute(match: re.Match[str]) -> str:
        name = match.group("name").lower()
        value = match.group("value")
        if name == "data-board-asset-id" and value == old_asset_id:
            value = new_asset_id
        elif value == old_url:
            value = new_url
        return f"{match.group('prefix')}{match.group('quote')}{value}{match.group('quote')}"

    return _HTML_ATTRIBUTE_RE.sub(replace_attribute, raw)


def rewrite_board_asset_markdown(raw: str, *, old_asset_id: str, new_asset_id: str) -> str:
    """Rewrite only controlled board asset destinations in Markdown links and images."""

    if not raw or old_asset_id not in raw or old_asset_id == new_asset_id:
        return raw
    old_url = _BOARD_ASSET_URL_TEMPLATE.format(asset_id=old_asset_id)
    new_url = _BOARD_ASSET_URL_TEMPLATE.format(asset_id=new_asset_id)
    destination = re.compile(
        r"(?P<prefix>!?\[(?:\\.|[^\]\r\n])*\]\(\s*)"
        + re.escape(old_url)
        + r"(?P<suffix>\s*\))"
    )
    return destination.sub(
        lambda match: f"{match.group('prefix')}{new_url}{match.group('suffix')}",
        raw,
    )


def _rewrite_json_node(value: Any, *, old_asset_id: str, new_asset_id: str) -> Any:
    if isinstance(value, list):
        return [
            _rewrite_json_node(item, old_asset_id=old_asset_id, new_asset_id=new_asset_id)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    rewritten: dict[Any, Any] = {}
    for key, item in value.items():
        if key in _JSON_ASSET_ID_KEYS:
            rewritten[key] = _rewrite_exact_asset_id(
                item,
                old_asset_id=old_asset_id,
                new_asset_id=new_asset_id,
            )
        elif key in _JSON_ASSET_URL_KEYS:
            rewritten[key] = _rewrite_exact_asset_reference(
                item,
                old_asset_id=old_asset_id,
                new_asset_id=new_asset_id,
            )
        elif key in _JSON_ASSET_ID_LIST_KEYS:
            rewritten[key] = _rewrite_asset_id_collection(
                item,
                old_asset_id=old_asset_id,
                new_asset_id=new_asset_id,
            )
        else:
            rewritten[key] = _rewrite_json_node(
                item,
                old_asset_id=old_asset_id,
                new_asset_id=new_asset_id,
            )
    return rewritten


def _rewrite_exact_asset_id(value: Any, *, old_asset_id: str, new_asset_id: str) -> Any:
    return new_asset_id if isinstance(value, str) and value == old_asset_id else value


def _rewrite_exact_asset_reference(value: Any, *, old_asset_id: str, new_asset_id: str) -> Any:
    if not isinstance(value, str):
        return value
    if value == old_asset_id:
        return new_asset_id
    old_url = _BOARD_ASSET_URL_TEMPLATE.format(asset_id=old_asset_id)
    if value == old_url:
        return _BOARD_ASSET_URL_TEMPLATE.format(asset_id=new_asset_id)
    return value


def _rewrite_asset_id_collection(value: Any, *, old_asset_id: str, new_asset_id: str) -> Any:
    if isinstance(value, str):
        return new_asset_id if value == old_asset_id else value
    if isinstance(value, list):
        return [
            _rewrite_asset_id_collection(item, old_asset_id=old_asset_id, new_asset_id=new_asset_id)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _rewrite_asset_id_collection(item, old_asset_id=old_asset_id, new_asset_id=new_asset_id)
            for item in value
        )
    return value
