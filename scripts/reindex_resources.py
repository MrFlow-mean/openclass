#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.resource_reindex import ResourceReindexOptions, reindex_resources  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild OpenClass uploaded-resource indexes.")
    parser.add_argument("--database", type=Path, default=_default_database_path())
    parser.add_argument("--resource-id")
    parser.add_argument("--package-id")
    parser.add_argument("--owner-user-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true", help="Write rebuilt indexes back to SQLite.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect only. This is the default.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    options = ResourceReindexOptions(
        database_path=args.database,
        apply=bool(args.apply),
        resource_id=args.resource_id,
        package_id=args.package_id,
        owner_user_id=args.owner_user_id,
        limit=args.limit,
        create_backup=True,
    )
    report = reindex_resources(options)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report.to_dict())
    return 0


def _default_database_path() -> Path:
    raw_path = os.getenv("OPENCLASS_DATABASE_PATH")
    if raw_path:
        return Path(raw_path)
    return ROOT / "apps" / "api" / "data" / "openclass.sqlite3"


def _print_report(report: dict) -> None:
    mode = "dry-run" if report["dry_run"] else "apply"
    print(f"Resource reindex {mode}")
    print(f"Database: {report['database_path']}")
    if report["backup_path"]:
        print(f"Backup: {report['backup_path']}")
    if not report["dry_run"]:
        print("Operational note: run this with OpenClass services stopped in production.")
    print(
        "Summary: "
        f"scanned={report['scanned_count']} "
        f"rebuildable={report['rebuildable_count']} "
        f"applied={report['applied_count']} "
        f"missing_source={report['missing_source_count']} "
        f"still_missing_text={report['still_missing_text_count']} "
        f"errors={report['error_count']}"
    )
    for item in report["resources"]:
        print(
            f"- [{item['status']}] {item['resource_id']} {item['name']} "
            f"segments {item['old_segment_count']}->{item['new_segment_count']} "
            f"text {item['old_extracted_text_available']}->{item['new_extracted_text_available']} "
            f"reason={item['reason']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
