#!/usr/bin/env python3
"""Remove Cursor agent co-author trailers from the current HEAD commit message."""
from __future__ import annotations

import subprocess
import sys

TRAILER = "Co-authored-by: Cursor <cursoragent@cursor.com>"


def clean_message(message: str) -> str:
    lines = [line for line in message.splitlines() if line.strip() != TRAILER]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    message = subprocess.check_output(["git", "log", "-1", "--format=%B"], text=True)
    cleaned = clean_message(message)
    subprocess.run(
        ["git", "commit", "--amend", "--reset-author", "-F", "-"],
        input=cleaned,
        text=True,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
