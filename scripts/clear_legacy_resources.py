#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_PATH = ROOT / "apps" / "api"
sys.path.insert(0, str(API_PATH))

from app.services.config import DATA_DIR, ROOT_DIR, load_root_dotenv  # noqa: E402


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _default_database_path() -> Path:
    load_root_dotenv()
    return _path_from_env("OPENCLASS_DATABASE_PATH", DATA_DIR / "openclass.sqlite3")


def _default_upload_dir() -> Path:
    load_root_dotenv()
    return _path_from_env("OPENCLASS_UPLOAD_DIR", DATA_DIR / "uploads")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual') AND name = ?",
        (name,),
    ).fetchone() is not None


def _safe_resource_files(conn: sqlite3.Connection, upload_dir: Path) -> list[Path]:
    if not _table_exists(conn, "resources"):
        return []
    try:
        upload_root = upload_dir.resolve(strict=False)
    except OSError:
        return []
    files: list[Path] = []
    rows = conn.execute("SELECT source_path FROM resources WHERE source_path IS NOT NULL").fetchall()
    for row in rows:
        source = Path(str(row[0]))
        try:
            resolved = source.resolve(strict=False)
        except OSError:
            continue
        if resolved == upload_root or upload_root not in resolved.parents:
            continue
        files.append(source)
    return files


def _backup_database(database_path: Path) -> Path:
    backup_path = database_path.with_suffix(f"{database_path.suffix}.resources-clear-{_timestamp()}.bak")
    with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def _backup_uploads(upload_dir: Path) -> Path | None:
    if not upload_dir.exists():
        return None
    backup_dir = upload_dir.with_name(f"{upload_dir.name}.resources-clear-{_timestamp()}.bak")
    shutil.copytree(upload_dir, backup_dir)
    return backup_dir


def clear_resources(database_path: Path, upload_dir: Path, *, apply: bool = False) -> dict[str, object]:
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        files = _safe_resource_files(conn, upload_dir)
        resource_count = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] if _table_exists(conn, "resources") else 0
        report: dict[str, object] = {
            "apply": apply,
            "database": str(database_path),
            "upload_dir": str(upload_dir),
            "resource_count": resource_count,
            "source_file_count": len(files),
            "source_files": [str(path) for path in files],
            "database_backup": None,
            "upload_backup": None,
        }
        if not apply:
            return report

        report["database_backup"] = str(_backup_database(database_path))
        upload_backup = _backup_uploads(upload_dir)
        report["upload_backup"] = str(upload_backup) if upload_backup else None

        with conn:
            for table in [
                "resource_document_blocks_fts",
                "resource_document_blocks",
                "resource_document_pages",
                "resource_index_jobs",
                "resource_segment_embeddings",
                "resource_segments_fts",
                "resource_segments",
                "resource_chapters",
                "resource_events",
                "resources",
            ]:
                if _table_exists(conn, table):
                    conn.execute(f"DELETE FROM {table}")
        for source in files:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup and clear legacy OpenClass resource records and uploaded files.")
    parser.add_argument("--database", type=Path, default=_default_database_path())
    parser.add_argument("--upload-dir", type=Path, default=_default_upload_dir())
    parser.add_argument("--apply", action="store_true", help="Actually clear resource records and controlled upload files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = clear_resources(args.database, args.upload_dir, apply=args.apply)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Legacy resource clear {mode}")
    print(f"database={report['database']}")
    print(f"upload_dir={report['upload_dir']}")
    print(f"resources={report['resource_count']} source_files={report['source_file_count']}")
    if args.apply:
        print(f"database_backup={report['database_backup']}")
        print(f"upload_backup={report['upload_backup']}")


if __name__ == "__main__":
    main()
