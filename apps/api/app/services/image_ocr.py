from __future__ import annotations

import json
import subprocess
from pathlib import Path

VISION_OCR_SCRIPT = Path(__file__).with_name("vision_ocr.swift")


def extract_image_text(file_path: Path) -> str | None:
    if not file_path.exists() or not VISION_OCR_SCRIPT.exists():
        return None

    try:
        result = subprocess.run(
            ["swift", str(VISION_OCR_SCRIPT), str(file_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=90,
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
