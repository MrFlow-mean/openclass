from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path, PurePosixPath

from app.services import workspace_state

MAX_SOURCE_VISUAL_BYTES = 64 * 1024 * 1024

_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
}


class SourceVisualStorageError(ValueError):
    pass


def source_visual_asset_root() -> Path:
    return workspace_state.UPLOAD_DIR / "source-visuals"


def persist_source_visual_asset(content: bytes, *, mime_type: str) -> tuple[str, str]:
    if not content:
        raise SourceVisualStorageError("Source visual content is empty.")
    if len(content) > MAX_SOURCE_VISUAL_BYTES:
        raise SourceVisualStorageError("Source visual exceeds the maximum supported size.")
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    extension = _MIME_EXTENSIONS.get(normalized_mime)
    if extension is None:
        raise SourceVisualStorageError(f"Unsupported source visual media type: {normalized_mime or 'unknown'}")
    content_hash = hashlib.sha256(content).hexdigest()
    storage_key = f"blobs/{content_hash[:2]}/{content_hash}{extension}"
    path = resolve_source_visual_storage_key(storage_key, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_matches = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == content_hash
    if not existing_matches:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{content_hash}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                handle.write(content)
                temporary_path = Path(handle.name)
            temporary_path.replace(path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
    return storage_key, content_hash


def resolve_source_visual_storage_key(storage_key: str, *, must_exist: bool = True) -> Path:
    normalized = PurePosixPath(storage_key)
    if normalized.is_absolute() or not normalized.parts or ".." in normalized.parts:
        raise SourceVisualStorageError("Invalid source visual storage key.")
    root = source_visual_asset_root().resolve()
    path = (root / Path(*normalized.parts)).resolve()
    if root not in path.parents:
        raise SourceVisualStorageError("Source visual storage key escapes its storage root.")
    if must_exist and (not path.is_file() or path.is_symlink()):
        raise SourceVisualStorageError("Source visual asset is unavailable.")
    return path


def read_source_visual_asset(storage_key: str) -> bytes:
    return resolve_source_visual_storage_key(storage_key).read_bytes()
