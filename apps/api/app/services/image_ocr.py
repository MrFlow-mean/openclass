from __future__ import annotations

import json
import subprocess
from pathlib import Path

VISION_OCR_SCRIPT = Path(__file__).with_name("vision_ocr.swift")


def _run_vision_ocr(args: list[str], *, timeout: int) -> str | None:
    if not VISION_OCR_SCRIPT.exists():
        return None

    try:
        result = subprocess.run(
            ["swift", str(VISION_OCR_SCRIPT), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    payload = result.stdout.strip()
    if not payload:
        return None

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None

    text = str(parsed.get("text") or "").strip()
    return text or None


def extract_image_text(file_path: Path) -> str | None:
    if not file_path.exists() or not VISION_OCR_SCRIPT.exists():
        return None

    return _run_vision_ocr([str(file_path)], timeout=90)


def extract_pdf_pages_text(
    file_path: Path,
    *,
    page_start: int,
    page_end: int,
    max_pages: int = 4,
) -> str | None:
    if not file_path.exists() or not VISION_OCR_SCRIPT.exists():
        return None

    start = max(page_start, 1)
    end = max(page_end, start)
    pages = max(1, min(max_pages, end - start + 1))
    timeout = max(120, pages * 60)
    return _run_vision_ocr([str(file_path), str(start), str(end), str(pages)], timeout=timeout)
