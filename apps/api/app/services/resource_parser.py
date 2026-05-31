from __future__ import annotations

import os
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NATIVE_RESOURCE_PARSER_NAME = "openclass-native"
NATIVE_RESOURCE_PARSER_VERSION = "1"


@dataclass(frozen=True)
class ResourceParserSpec:
    name: str
    version: str


@dataclass(frozen=True)
class ParsedResourceBlock:
    text: str
    page_range: str | None = None
    heading_path: list[str] | None = None
    metadata: dict[str, Any] | None = None

    def to_resource_block(self, *, parser: ResourceParserSpec) -> dict[str, Any]:
        return {
            "text": self.text,
            "page_range": self.page_range,
            "heading_path": self.heading_path or [],
            "metadata": self.metadata or {},
            "parser_name": parser.name,
            "parser_version": parser.version,
        }


@dataclass(frozen=True)
class ParsedResourceHeading:
    title: str
    level: int = 1
    page_range: str | None = None
    heading_path: list[str] | None = None
    order_index: int = 0


@dataclass(frozen=True)
class ParsedResourceText:
    parser: ResourceParserSpec
    text: str = ""
    markdown: str = ""
    blocks: list[ParsedResourceBlock] | None = None
    headings: list[ParsedResourceHeading] | None = None
    metadata: dict[str, Any] | None = None
    status: str = "success"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success" and bool(self.content_text or self.blocks)

    @property
    def content_text(self) -> str:
        if self.markdown.strip():
            return self.markdown.strip()
        if self.text.strip():
            return self.text.strip()
        return "\n\n".join(block.text for block in self.blocks or [] if block.text).strip()


def current_resource_parser_spec() -> ResourceParserSpec:
    name = (os.getenv("OPENCLASS_RESOURCE_PARSER") or NATIVE_RESOURCE_PARSER_NAME).strip()
    version = (os.getenv("OPENCLASS_RESOURCE_PARSER_VERSION") or NATIVE_RESOURCE_PARSER_VERSION).strip()
    return ResourceParserSpec(
        name=name or NATIVE_RESOURCE_PARSER_NAME,
        version=version or NATIVE_RESOURCE_PARSER_VERSION,
    )


def external_resource_parser_command() -> str:
    return (os.getenv("OPENCLASS_RESOURCE_PARSER_COMMAND") or "").strip()


def parse_with_external_resource_parser(file_path: Path) -> ParsedResourceText | None:
    command = external_resource_parser_command()
    if not command:
        return None
    spec = current_resource_parser_spec()
    command_parts = shlex.split(command)
    if not command_parts:
        return _parser_failure(spec, "external_parser_empty_command")
    try:
        timeout = int(os.getenv("OPENCLASS_RESOURCE_PARSER_TIMEOUT_SECONDS", "180"))
        result = subprocess.run(
            [*command_parts, str(file_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _parser_failure(spec, "external_parser_timeout")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        return _parser_failure(spec, detail or f"external_parser_exit_{exc.returncode}")
    except (OSError, ValueError) as exc:
        return _parser_failure(spec, str(exc) or exc.__class__.__name__)

    payload = result.stdout.strip()
    if not payload:
        return _parser_failure(spec, "external_parser_empty_output")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        if payload[:1] in {"{", "["}:
            return _parser_failure(spec, "external_parser_malformed_json")
        return ParsedResourceText(parser=spec, text=payload)

    if not isinstance(parsed, dict):
        return _parser_failure(spec, "external_parser_unsupported_json")

    return _parsed_resource_from_json(parsed, fallback_spec=spec)


def _parsed_resource_from_json(payload: dict[str, Any], *, fallback_spec: ResourceParserSpec) -> ParsedResourceText:
    parser = ResourceParserSpec(
        name=_coerce_string(payload.get("parser_name")) or fallback_spec.name,
        version=_coerce_string(payload.get("parser_version")) or fallback_spec.version,
    )
    markdown = _coerce_string(payload.get("markdown"))
    text = _coerce_string(payload.get("text"))
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    blocks = _blocks_from_payload(payload)
    headings = _headings_from_payload(payload)
    parsed = ParsedResourceText(
        parser=parser,
        text=text,
        markdown=markdown,
        blocks=blocks,
        headings=headings,
        metadata=metadata,
    )
    if not parsed.ok:
        return _parser_failure(parser, "external_parser_no_text", metadata=metadata)
    return parsed


def _parser_failure(
    spec: ResourceParserSpec,
    error: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ParsedResourceText:
    return ParsedResourceText(
        parser=spec,
        status="failed",
        error=error,
        metadata=metadata or {},
    )


def _blocks_from_payload(payload: dict[str, Any]) -> list[ParsedResourceBlock]:
    blocks: list[ParsedResourceBlock] = []
    for page in _list_of_dicts(payload.get("pages")):
        page_range = _page_range_from_mapping(page)
        heading_path = _heading_path_from_value(page.get("heading_path") or page.get("path"))
        text = _coerce_string(page.get("markdown") or page.get("text"))
        if text:
            blocks.append(
                ParsedResourceBlock(
                    text=text,
                    page_range=page_range,
                    heading_path=heading_path,
                    metadata=_metadata_from_mapping(page),
                )
            )
        for child in _list_of_dicts(page.get("blocks")):
            block = _block_from_mapping(
                child,
                inherited_page_range=page_range,
                inherited_heading_path=heading_path,
            )
            if block:
                blocks.append(block)

    for raw_block in _list_of_dicts(payload.get("blocks")):
        block = _block_from_mapping(raw_block)
        if block:
            blocks.append(block)
    return blocks


def _block_from_mapping(
    raw_block: dict[str, Any],
    *,
    inherited_page_range: str | None = None,
    inherited_heading_path: list[str] | None = None,
) -> ParsedResourceBlock | None:
    text = _coerce_string(raw_block.get("markdown") or raw_block.get("text") or raw_block.get("content"))
    if not text:
        return None
    heading_path = _heading_path_from_value(
        raw_block.get("heading_path") or raw_block.get("path"),
        fallback=inherited_heading_path,
    )
    return ParsedResourceBlock(
        text=text,
        page_range=_page_range_from_mapping(raw_block) or inherited_page_range,
        heading_path=heading_path,
        metadata=_metadata_from_mapping(raw_block),
    )


def _headings_from_payload(payload: dict[str, Any]) -> list[ParsedResourceHeading]:
    headings: list[ParsedResourceHeading] = []
    for index, raw_heading in enumerate(_list_of_dicts(payload.get("headings"))):
        title = _coerce_string(raw_heading.get("title") or raw_heading.get("text"))
        if not title:
            continue
        headings.append(
            ParsedResourceHeading(
                title=title[:120],
                level=_coerce_level(raw_heading.get("level")),
                page_range=_page_range_from_mapping(raw_heading),
                heading_path=_heading_path_from_value(raw_heading.get("heading_path") or raw_heading.get("path")),
                order_index=index,
            )
        )
    return headings


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _coerce_string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(level, 6))


def _heading_path_from_value(value: Any, *, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        path = [_coerce_string(item) for item in value]
        return [item for item in path if item]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return list(fallback or [])


def _page_range_from_mapping(value: dict[str, Any]) -> str | None:
    page_range = _coerce_string(value.get("page_range"))
    if page_range:
        return page_range
    page_number = value.get("page_number")
    if page_number is None:
        page_number = value.get("page")
    try:
        page = int(page_number)
    except (TypeError, ValueError):
        return None
    return str(page) if page > 0 else None


def _metadata_from_mapping(value: dict[str, Any]) -> dict[str, Any]:
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else {}
