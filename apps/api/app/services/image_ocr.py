from __future__ import annotations

import json
import subprocess
from pathlib import Path

VISION_OCR_SCRIPT = Path(__file__).with_name("vision_ocr.swift")


def _normalize_ocr_lines(value: object) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    if not isinstance(value, list):
        return normalized
    for index, item in enumerate(value):
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append(
                    {
                        "text": text,
                        "x": 0.0,
                        "y": float(max(0, len(value) - index)),
                        "width": 0.0,
                        "height": 0.0,
                        "page": None,
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "x": float(item.get("x") or 0.0),
                "y": float(item.get("y") or 0.0),
                "width": float(item.get("width") or 0.0),
                "height": float(item.get("height") or 0.0),
                "page": int(item.get("page")) if item.get("page") not in (None, "") else None,
            }
        )
    return normalized


def _run_vision_ocr(args: list[str], *, timeout: int) -> dict[str, object] | None:
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
    lines = _normalize_ocr_lines(parsed.get("lines"))
    if not text and not lines:
        return None
    if not text and lines:
        text = "\n".join(str(line["text"]) for line in lines).strip()
    return {"text": text, "lines": lines}


def extract_image_text(file_path: Path) -> str | None:
    if not file_path.exists() or not VISION_OCR_SCRIPT.exists():
        return None

    result = _run_vision_ocr([str(file_path)], timeout=90)
    if not result:
        return None
    text = str(result.get("text") or "").strip()
    return text or None


def extract_image_ocr_result(file_path: Path) -> dict[str, object] | None:
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
    result = _run_vision_ocr([str(file_path), str(start), str(end), str(pages)], timeout=timeout)
    if not result:
        return None
    text = str(result.get("text") or "").strip()
    return text or None
