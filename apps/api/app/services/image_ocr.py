from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

VISION_OCR_SCRIPT = Path(__file__).with_name("vision_ocr.swift")


@dataclass(frozen=True)
class PdfOcrPageResult:
    page_number: int
    text: str = ""
    status: str = "empty"
    error: str | None = None


@dataclass(frozen=True)
class _VisionOcrRunResult:
    payload: dict | None = None
    error: str | None = None


def _run_vision_ocr_payload(args: list[str], *, timeout: int) -> _VisionOcrRunResult:
    if not VISION_OCR_SCRIPT.exists():
        return _VisionOcrRunResult(error="vision_ocr_script_missing")

    try:
        result = subprocess.run(
            ["swift", str(VISION_OCR_SCRIPT), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _VisionOcrRunResult(error="vision_ocr_timeout")
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or "").strip()
        return _VisionOcrRunResult(error=error or "vision_ocr_failed")
    except OSError as exc:
        return _VisionOcrRunResult(error=str(exc) or exc.__class__.__name__)

    payload = result.stdout.strip()
    if not payload:
        return _VisionOcrRunResult(error="vision_ocr_empty_output")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return _VisionOcrRunResult(error="vision_ocr_invalid_json")
    if not isinstance(parsed, dict):
        return _VisionOcrRunResult(error="vision_ocr_invalid_json")

    return _VisionOcrRunResult(payload=parsed)


def _run_vision_ocr(args: list[str], *, timeout: int) -> str | None:
    result = _run_vision_ocr_payload(args, timeout=timeout)
    if result.payload is None:
        return None

    text = str(result.payload.get("text") or "").strip()
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


def extract_pdf_page_texts(
    file_path: Path,
    *,
    page_start: int = 1,
    page_end: int | None = None,
    max_pages: int = 80,
    page_timeout: int = 90,
) -> list[PdfOcrPageResult]:
    if not file_path.exists():
        return []

    try:
        reader = PdfReader(str(file_path))
        total_pages = len(reader.pages)
    except Exception as exc:
        return [PdfOcrPageResult(page_number=1, status="error", error=str(exc) or exc.__class__.__name__)]

    if total_pages <= 0:
        return []

    start = max(page_start, 1)
    requested_end = page_end if page_end is not None else total_pages
    end = min(max(requested_end, start), total_pages)
    limit = max(max_pages, 0)
    if limit == 0:
        return []

    results: list[PdfOcrPageResult] = []
    for page_number in range(start, end + 1):
        if len(results) >= limit:
            break
        run_result = _run_vision_ocr_payload(
            [str(file_path), str(page_number), str(page_number), "1"],
            timeout=max(page_timeout, 1),
        )
        if run_result.payload is None:
            results.append(
                PdfOcrPageResult(
                    page_number=page_number,
                    status="error",
                    error=run_result.error or "vision_ocr_failed",
                )
            )
            continue
        text = str(run_result.payload.get("text") or "").strip()
        results.append(
            PdfOcrPageResult(
                page_number=page_number,
                text=text,
                status="text" if text else "empty",
            )
        )
    return results
