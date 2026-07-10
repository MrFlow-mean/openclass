from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VISION_OCR_SCRIPT = Path(__file__).with_name("vision_ocr.swift")


@dataclass(frozen=True)
class OCRLineLayout:
    text: str
    x: float
    y: float
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class OCRPageLayout:
    page_no: int
    lines: list[OCRLineLayout] = field(default_factory=list)


def _run_vision_ocr_payload(args: list[str], *, timeout: int) -> dict[str, Any] | None:
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

    return parsed if isinstance(parsed, dict) else None


def _run_vision_ocr(args: list[str], *, timeout: int) -> str | None:
    parsed = _run_vision_ocr_payload(args, timeout=timeout)
    if not parsed:
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


def extract_pdf_pages_layout(
    file_path: Path,
    *,
    page_start: int,
    page_end: int,
    max_pages: int = 12,
) -> list[OCRPageLayout]:
    if not file_path.exists() or not VISION_OCR_SCRIPT.exists():
        return []

    start = max(page_start, 1)
    end = max(page_end, start)
    pages = max(1, min(max_pages, end - start + 1))
    timeout = max(120, pages * 60)
    payload = _run_vision_ocr_payload(
        [str(file_path), str(start), str(end), str(pages)],
        timeout=timeout,
    )
    if not payload:
        return []

    page_layouts: list[OCRPageLayout] = []
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        return []
    for raw_page in raw_pages:
        if not isinstance(raw_page, dict):
            continue
        try:
            page_no = int(raw_page.get("pageNumber") or 0)
        except (TypeError, ValueError):
            continue
        lines: list[OCRLineLayout] = []
        raw_lines = raw_page.get("lines")
        if isinstance(raw_lines, list):
            for raw_line in raw_lines:
                if not isinstance(raw_line, dict):
                    continue
                text = str(raw_line.get("text") or "").strip()
                if not text:
                    continue
                try:
                    lines.append(
                        OCRLineLayout(
                            text=text,
                            x=float(raw_line.get("x") or 0.0),
                            y=float(raw_line.get("y") or 0.0),
                            width=float(raw_line.get("width") or 0.0),
                            height=float(raw_line.get("height") or 0.0),
                        )
                    )
                except (TypeError, ValueError):
                    continue
        page_layouts.append(OCRPageLayout(page_no=page_no, lines=lines))
    return page_layouts
