from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import ResourceSourceUnit


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpeg",
    ".jpg",
    ".bmp",
    ".tiff",
    ".tif",
    ".gif",
    ".webp",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".txt",
    ".md",
}
EPUB_SUFFIX = ".epub"
NATIVE_FIRST_AUTO_SUFFIXES = {".txt", ".md"}
TEXT_CONTENT_LIMIT = 500_000


@dataclass
class RAGAnythingParseResult:
    parser_provider: str
    parser_artifacts_path: str | None
    parser_message: str
    source_units: list[ResourceSourceUnit]
    text_content: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class RAGAnythingParseAttempt:
    result: RAGAnythingParseResult | None = None
    warnings: list[str] = field(default_factory=list)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resource_parser_mode() -> str:
    raw = os.getenv("OPENCLASS_RESOURCE_PARSER", "auto").strip().lower()
    return raw if raw in {"auto", "native", "raganything"} else "auto"


def _rag_anything_path() -> Path:
    configured = os.getenv("OPENCLASS_RAG_ANYTHING_PATH")
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (_repo_root() / path).resolve()
    repo_local = (_repo_root() / "RAG-Anything-main").resolve()
    if repo_local.exists():
        return repo_local
    return (_repo_root().parent / "RAG-Anything-main").resolve()


def _rag_anything_output_base() -> Path:
    configured = os.getenv("OPENCLASS_RAG_ANYTHING_OUTPUT_DIR")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (_repo_root() / path).resolve()
    return _repo_root() / "apps" / "api" / "data" / "raganything"


def _rag_parser_name() -> str:
    return os.getenv("OPENCLASS_RAG_ANYTHING_PARSER", "mineru").strip().lower() or "mineru"


def _rag_parse_method() -> str:
    raw = os.getenv("OPENCLASS_RAG_ANYTHING_PARSE_METHOD", "auto").strip().lower()
    return raw if raw in {"auto", "ocr", "txt"} else "auto"


def _safe_artifact_dir(base_dir: Path, file_path: Path) -> Path:
    resolved = file_path.resolve()
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.stem).strip("._") or "resource"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:10]
    return base_dir / f"{safe_stem}_{digest}"


def _load_get_parser(rag_path: Path):
    if not rag_path.exists():
        raise RuntimeError(f"RAG-Anything path does not exist: {rag_path}")
    rag_path_text = str(rag_path)
    if rag_path_text not in sys.path:
        sys.path.insert(0, rag_path_text)
    importlib.invalidate_caches()
    module = importlib.import_module("raganything.parser")
    get_parser = getattr(module, "get_parser", None)
    if get_parser is None:
        raise RuntimeError("raganything.parser.get_parser is unavailable.")
    return get_parser


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"-?\d+", value):
            return int(value)
    return None


def _page_values(item: dict[str, Any]) -> tuple[int | None, int | None]:
    page_idx = _as_int(item.get("page_idx"))
    if page_idx is None:
        page_idx = _as_int(item.get("page_id"))
    if page_idx is not None:
        return page_idx, page_idx + 1 if page_idx >= 0 else None
    for key in ("page_no", "page_number", "page"):
        page_no = _as_int(item.get(key))
        if page_no is not None:
            return page_no - 1 if page_no > 0 else None, page_no
    return None, None


def _content_type(item: dict[str, Any]) -> str:
    raw = str(item.get("type") or item.get("content_type") or item.get("category") or "text").strip().lower()
    if raw in {"image", "img", "figure"}:
        return "image"
    if raw in {"table", "tabular"}:
        return "table"
    if raw in {"equation", "formula", "latex"}:
        return "equation"
    return "text"


def _string_parts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_string_parts(item))
        return parts
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_string_parts(item))
        return parts
    return []


def _unit_text(item: dict[str, Any], content_type: str) -> str:
    keys_by_type = {
        "text": ("text", "content", "markdown", "md", "html"),
        "table": ("table_body", "table_caption", "table_footnote", "text", "content", "html", "markdown"),
        "equation": ("latex", "text", "content"),
        "image": ("caption", "img_caption", "image_caption", "text", "content"),
    }
    parts: list[str] = []
    for key in keys_by_type.get(content_type, ("text", "content")):
        parts.extend(_string_parts(item.get(key)))
    return "\n".join(dict.fromkeys(part for part in parts if part)).strip()


def _asset_path(item: dict[str, Any]) -> str | None:
    for key in ("img_path", "image_path", "asset_path", "file_path", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _bbox(item: dict[str, Any]) -> list[float]:
    value = item.get("bbox") or item.get("poly")
    if not isinstance(value, list):
        return []
    numbers: list[float] = []
    for element in value:
        if isinstance(element, (int, float)) and not isinstance(element, bool):
            numbers.append(float(element))
        elif isinstance(element, list):
            numbers.extend(float(number) for number in element if isinstance(number, (int, float)))
    return numbers[:16]


def _heading_path(item: dict[str, Any]) -> list[str]:
    for key in ("heading_path", "headings", "section_path", "title_path", "breadcrumbs", "breadcrumb"):
        parts = _string_parts(item.get(key))
        if len(parts) == 1 and (">" in parts[0] or "/" in parts[0]):
            parts = [part.strip() for part in re.split(r"\s*(?:>|/)\s*", parts[0])]
        cleaned = [re.sub(r"\s+", " ", part).strip()[:120] for part in parts if part.strip()]
        if cleaned:
            return list(dict.fromkeys(cleaned))[:6]
    return []


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "text",
        "content",
        "html",
        "markdown",
        "md",
        "table_body",
        "latex",
        "caption",
        "img_caption",
        "image_caption",
        "heading_path",
        "headings",
        "section_path",
        "title_path",
        "breadcrumbs",
        "breadcrumb",
    }
    metadata: dict[str, Any] = {}
    for key, value in item.items():
        if key in excluded:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
        elif isinstance(value, list) and len(json.dumps(value, ensure_ascii=False, default=str)) <= 800:
            metadata[key] = value
        elif isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False, default=str)) <= 800:
            metadata[key] = value
    return metadata


def source_units_to_rag_content_list(units: list[ResourceSourceUnit]) -> list[dict[str, Any]]:
    content_list: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda item: item.order_index):
        content_type = unit.content_type.strip().lower() or "text"
        item: dict[str, Any] = {"type": content_type}
        if unit.page_idx is not None:
            item["page_idx"] = unit.page_idx
        if content_type == "image":
            if unit.asset_path:
                item["img_path"] = unit.asset_path
            if unit.text:
                item["image_caption"] = [unit.text]
        elif content_type == "table":
            item["table_body"] = unit.text
        elif content_type == "equation":
            item["latex"] = unit.text
        else:
            item["text"] = unit.text
        if unit.metadata:
            item.update(unit.metadata)
        if unit.heading_path:
            item["heading_path"] = unit.heading_path
        if unit.bbox:
            item["bbox"] = unit.bbox
        if unit.source_locator:
            item["source_locator"] = unit.source_locator
        if any(value for key, value in item.items() if key not in {"type", "page_idx", "bbox", "source_locator"}):
            content_list.append(item)
    return content_list


def _map_content_list(content_list: Any) -> list[ResourceSourceUnit]:
    if not isinstance(content_list, list):
        raise RuntimeError("RAG-Anything parser returned a non-list content payload.")
    units: list[ResourceSourceUnit] = []
    for index, raw_item in enumerate(content_list):
        if not isinstance(raw_item, dict):
            continue
        content_type = _content_type(raw_item)
        text = _unit_text(raw_item, content_type)
        asset_path = _asset_path(raw_item)
        if not text and not asset_path:
            continue
        page_idx, page_no = _page_values(raw_item)
        locator_parts = [f"raganything:{content_type}", f"item={index}"]
        if page_no is not None:
            locator_parts.append(f"page={page_no}")
        units.append(
            ResourceSourceUnit(
                content_type=content_type,
                text=text,
                page_idx=page_idx,
                page_no=page_no,
                source_locator=":".join(locator_parts),
                asset_path=asset_path,
                bbox=_bbox(raw_item),
                heading_path=_heading_path(raw_item),
                order_index=index,
                metadata=_metadata(raw_item),
            )
        )
    return units


def _join_unit_text(units: list[ResourceSourceUnit]) -> str:
    chunks = [unit.text.strip() for unit in units if unit.text.strip()]
    return "\n\n".join(chunks).strip()[:TEXT_CONTENT_LIMIT]


def parse_with_rag_anything(file_path: Path, original_name: str, mime_type: str) -> RAGAnythingParseAttempt:
    mode = _resource_parser_mode()
    if mode == "native":
        return RAGAnythingParseAttempt()

    suffix = Path(original_name).suffix.lower() or file_path.suffix.lower()
    if mode == "auto" and suffix in NATIVE_FIRST_AUTO_SUFFIXES:
        return RAGAnythingParseAttempt()
    if suffix == EPUB_SUFFIX:
        return RAGAnythingParseAttempt(
            warnings=["EPUB uses the native OpenClass parser in this RAG-Anything adapter version."]
        )
    if suffix not in SUPPORTED_SUFFIXES and not mime_type.startswith("image/"):
        message = f"RAG-Anything parser does not support this file type yet: {suffix or mime_type}"
        if mode == "raganything":
            raise RuntimeError(message)
        return RAGAnythingParseAttempt(warnings=[f"{message}; used native parser."])

    parser_name = _rag_parser_name()
    parse_method = _rag_parse_method()
    artifacts_dir = _safe_artifact_dir(_rag_anything_output_base(), file_path)

    try:
        get_parser = _load_get_parser(_rag_anything_path())
        parser = get_parser(parser_name)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        content_list = parser.parse_document(
            str(file_path),
            output_dir=str(artifacts_dir),
            method=parse_method,
        )
        units = _map_content_list(content_list)
        text_content = _join_unit_text(units)
        if not units or not text_content:
            raise RuntimeError("RAG-Anything parser returned no usable text/source units.")
        return RAGAnythingParseAttempt(
            result=RAGAnythingParseResult(
                parser_provider=f"raganything:{parser_name}",
                parser_artifacts_path=str(artifacts_dir),
                parser_message=f"Parsed by RAG-Anything using {parser_name}/{parse_method}.",
                source_units=units,
                text_content=text_content,
            )
        )
    except Exception as exc:
        message = f"RAG-Anything parse failed: {exc}"
        if mode == "raganything":
            raise RuntimeError(message) from exc
        return RAGAnythingParseAttempt(warnings=[f"{message}; used native parser."])
