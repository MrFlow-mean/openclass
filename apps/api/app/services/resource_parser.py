from __future__ import annotations

import os
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


NATIVE_RESOURCE_PARSER_NAME = "openclass-native"
NATIVE_RESOURCE_PARSER_VERSION = "1"


@dataclass(frozen=True)
class ResourceParserSpec:
    name: str
    version: str


@dataclass(frozen=True)
class ParsedResourceText:
    text: str
    parser: ResourceParserSpec


def current_resource_parser_spec() -> ResourceParserSpec:
    name = (os.getenv("OPENCLASS_RESOURCE_PARSER") or NATIVE_RESOURCE_PARSER_NAME).strip()
    version = (os.getenv("OPENCLASS_RESOURCE_PARSER_VERSION") or NATIVE_RESOURCE_PARSER_VERSION).strip()
    return ResourceParserSpec(
        name=name or NATIVE_RESOURCE_PARSER_NAME,
        version=version or NATIVE_RESOURCE_PARSER_VERSION,
    )


def parse_with_external_resource_parser(file_path: Path) -> ParsedResourceText | None:
    command = (os.getenv("OPENCLASS_RESOURCE_PARSER_COMMAND") or "").strip()
    if not command:
        return None
    spec = current_resource_parser_spec()
    try:
        result = subprocess.run(
            [command, str(file_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=int(os.getenv("OPENCLASS_RESOURCE_PARSER_TIMEOUT_SECONDS", "180")),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return None
    payload = result.stdout.strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        text = payload
    else:
        text = str(parsed.get("markdown") or parsed.get("text") or "").strip()
    return ParsedResourceText(text=text, parser=spec) if text else None
