from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
import sqlite3
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path

from app.models import now_iso
from app.services.board_asset_identity import stable_board_asset_id
from app.services.config import DATA_DIR, ROOT_DIR, load_root_dotenv


_MAX_BOARD_ASSET_BYTES = 25 * 1024 * 1024
MAX_BOARD_ASSET_PIXELS = 40_000_000
_MIME_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_BOARD_ASSET_URL_RE = re.compile(r"^/api/board-assets/(?P<asset_id>basset_[A-Za-z0-9_-]+)/content$")


class BoardAssetError(ValueError):
    pass


@dataclass(frozen=True)
class BoardAssetRecord:
    id: str
    owner_user_id: str
    lesson_id: str
    content_hash: str
    mime_type: str
    size_bytes: int
    storage_key: str
    file_name: str
    source_visual_id: str
    created_at: str

    @property
    def content_url(self) -> str:
        return f"/api/board-assets/{self.id}/content"


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


class BoardAssetStore:
    """Permanent, content-addressed storage for assets referenced by board history."""

    def __init__(self, database_path: Path, asset_dir: Path) -> None:
        self.database_path = database_path
        self.asset_dir = asset_dir.resolve()
        self._lock = threading.RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def put_bytes(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        content: bytes,
        mime_type: str,
        file_name: str = "",
        source_visual_id: str = "",
    ) -> BoardAssetRecord:
        owner = owner_user_id.strip()
        lesson = lesson_id.strip()
        normalized_mime = mime_type.split(";", 1)[0].strip().lower()
        input_mime = normalized_mime
        if not owner or not lesson:
            raise BoardAssetError("Board assets require an owner and lesson.")
        if not content:
            raise BoardAssetError("Board asset content is empty.")
        if len(content) > _MAX_BOARD_ASSET_BYTES:
            raise BoardAssetError("Board asset exceeds the 25 MiB size limit.")
        content, normalized_mime = _normalize_legacy_raster(content, normalized_mime)
        if len(content) > _MAX_BOARD_ASSET_BYTES:
            raise BoardAssetError("Normalized board asset exceeds the 25 MiB size limit.")
        extension = _MIME_EXTENSIONS.get(normalized_mime)
        if extension is None:
            raise BoardAssetError(f"Unsupported board asset MIME type: {normalized_mime or 'unknown'}.")
        if not _matches_image_signature(content, normalized_mime):
            raise BoardAssetError("Board asset bytes do not match the declared image MIME type.")
        _validate_board_raster(content, normalized_mime)

        content_hash = hashlib.sha256(content).hexdigest()
        asset_id = stable_board_asset_id(owner_user_id=owner, content_hash=content_hash)
        storage_key = f"{content_hash[:2]}/{content_hash}{extension}"
        safe_file_name = Path(file_name).name if file_name else f"{asset_id}{extension}"
        if normalized_mime != input_mime:
            safe_file_name = f"{Path(safe_file_name).stem}{extension}"
        elif not Path(safe_file_name).suffix:
            safe_file_name = f"{safe_file_name}{extension}"
        created_at = now_iso()

        with self._lock:
            existing = self.get_by_content_hash(owner_user_id=owner, content_hash=content_hash)
            if existing is not None:
                verified = self.read_bytes(existing.id, owner)
                if verified is not None:
                    return existing

            target = self._resolve_storage_key(storage_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

            with self._connect() as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO board_assets(
                            id, owner_user_id, lesson_id, content_hash, mime_type,
                            size_bytes, storage_key, file_name, source_visual_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            lesson_id = excluded.lesson_id,
                            file_name = CASE
                                WHEN board_assets.file_name = '' THEN excluded.file_name
                                ELSE board_assets.file_name
                            END,
                            source_visual_id = CASE
                                WHEN board_assets.source_visual_id = '' THEN excluded.source_visual_id
                                ELSE board_assets.source_visual_id
                            END
                        """,
                        (
                            asset_id,
                            owner,
                            lesson,
                            content_hash,
                            normalized_mime,
                            len(content),
                            storage_key,
                            safe_file_name,
                            source_visual_id.strip(),
                            created_at,
                        ),
                    )
            record = self.get(asset_id, owner)
            if record is None:
                raise BoardAssetError("Board asset metadata was not persisted.")
            return record

    def get(self, asset_id: str, owner_user_id: str) -> BoardAssetRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, owner_user_id, lesson_id, content_hash, mime_type,
                           size_bytes, storage_key, file_name, source_visual_id, created_at
                    FROM board_assets
                    WHERE id = ? AND owner_user_id = ?
                    """,
                    (asset_id, owner_user_id),
                ).fetchone()
        return _record_from_row(row) if row is not None else None

    def get_by_content_hash(
        self,
        *,
        owner_user_id: str,
        content_hash: str,
    ) -> BoardAssetRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id, owner_user_id, lesson_id, content_hash, mime_type,
                           size_bytes, storage_key, file_name, source_visual_id, created_at
                    FROM board_assets
                    WHERE owner_user_id = ? AND content_hash = ?
                    """,
                    (owner_user_id, content_hash),
                ).fetchone()
        return _record_from_row(row) if row is not None else None

    def read_bytes(self, asset_id: str, owner_user_id: str) -> tuple[BoardAssetRecord, bytes] | None:
        record = self.get(asset_id, owner_user_id)
        if record is None:
            return None
        path = self.resolve_path(record)
        if not path.is_file():
            return None
        content = path.read_bytes()
        if len(content) != record.size_bytes or hashlib.sha256(content).hexdigest() != record.content_hash:
            return None
        return record, content

    def resolve_path(self, record: BoardAssetRecord) -> Path:
        return self._resolve_storage_key(record.storage_key)

    def _resolve_storage_key(self, storage_key: str) -> Path:
        candidate = (self.asset_dir / storage_key).resolve()
        if candidate != self.asset_dir and self.asset_dir not in candidate.parents:
            raise BoardAssetError("Board asset storage key escapes the asset directory.")
        return candidate

    def _initialize(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS board_assets (
                        id TEXT PRIMARY KEY,
                        owner_user_id TEXT NOT NULL,
                        lesson_id TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        storage_key TEXT NOT NULL,
                        file_name TEXT NOT NULL DEFAULT '',
                        source_visual_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_board_assets_owner_hash
                    ON board_assets(owner_user_id, content_hash)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_board_assets_lesson
                    ON board_assets(owner_user_id, lesson_id)
                    """
                )
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn


def _record_from_row(row: sqlite3.Row) -> BoardAssetRecord:
    return BoardAssetRecord(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        lesson_id=str(row["lesson_id"]),
        content_hash=str(row["content_hash"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        storage_key=str(row["storage_key"]),
        file_name=str(row["file_name"]),
        source_visual_id=str(row["source_visual_id"]),
        created_at=str(row["created_at"]),
    )


load_root_dotenv()
_DATABASE_PATH = _path_from_env("OPENCLASS_DATABASE_PATH", DATA_DIR / "openclass.sqlite3")
_ASSET_DIR = _path_from_env("OPENCLASS_BOARD_ASSET_DIR", _DATABASE_PATH.parent / "board-assets")
board_asset_store = BoardAssetStore(_DATABASE_PATH, _ASSET_DIR)


def get_board_asset_store() -> BoardAssetStore:
    return board_asset_store


def guess_image_mime_type(file_name: str) -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def board_asset_id_from_url(value: str) -> str:
    match = _BOARD_ASSET_URL_RE.fullmatch((value or "").strip())
    return match.group("asset_id") if match else ""


def _matches_image_signature(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _normalize_legacy_raster(content: bytes, mime_type: str) -> tuple[bytes, str]:
    if mime_type not in {"image/tiff", "image/bmp"}:
        return content, mime_type
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_BOARD_ASSET_PIXELS:
                    raise BoardAssetError("Legacy raster board asset dimensions are too large.")
                converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                output = io.BytesIO()
                converted.save(output, format="PNG", optimize=True)
    except BoardAssetError:
        raise
    except Exception as exc:
        raise BoardAssetError("Legacy raster board asset could not be normalized.") from exc
    return output.getvalue(), "image/png"


def _validate_board_raster(content: bytes, mime_type: str) -> None:
    expected_format = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/gif": "GIF",
        "image/webp": "WEBP",
    }.get(mime_type)
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_BOARD_ASSET_PIXELS:
                    raise BoardAssetError("Board asset dimensions are too large.")
                if str(image.format or "").upper() != expected_format:
                    raise BoardAssetError("Board asset media type does not match its image bytes.")
                image.verify()
    except BoardAssetError:
        raise
    except Exception as exc:
        raise BoardAssetError("Board asset bytes are not a valid raster image.") from exc
