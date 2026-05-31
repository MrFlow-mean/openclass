import json
import subprocess

import pytest

from app.services import resource_parser
from app.services.resource_library import build_resource_item


def _write_source(path) -> None:
    path.write_text("Native fallback evidence.", encoding="utf-8")


def _enable_parser(monkeypatch, *, name: str = "mock-parser", version: str = "1") -> None:
    monkeypatch.setenv("OPENCLASS_RESOURCE_PARSER_COMMAND", "mock-parser --format openclass")
    monkeypatch.setenv("OPENCLASS_RESOURCE_PARSER", name)
    monkeypatch.setenv("OPENCLASS_RESOURCE_PARSER_VERSION", version)


def test_external_parser_raw_text_builds_resource_segments(tmp_path, monkeypatch) -> None:
    source_path = tmp_path / "source.bin"
    _write_source(source_path)
    _enable_parser(monkeypatch, name="raw-parser", version="2026")

    def fake_run(command, **kwargs):
        assert command[:-1] == ["mock-parser", "--format", "openclass"]
        assert command[-1] == str(source_path)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Raw parser evidence from stdout.",
            stderr="",
        )

    monkeypatch.setattr(resource_parser.subprocess, "run", fake_run)

    resource = build_resource_item(source_path, "source.bin")

    assert resource.extracted_text_available is True
    assert resource.parser_name == "raw-parser"
    assert resource.segments
    assert resource.segments[0].parser_name == "raw-parser"
    assert resource.segments[0].parser_version == "2026"
    assert resource.segments[0].text_source == "external_parser"
    assert "Raw parser evidence" in resource.segments[0].text


def test_external_parser_json_markdown_uses_parser_metadata(tmp_path, monkeypatch) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-1.4\n")
    _enable_parser(monkeypatch)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "markdown": "# Parsed Heading\nMarkdown parser evidence.",
                    "parser_name": "json-parser",
                    "parser_version": "2.1",
                    "metadata": {"engine": "wrapper"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(resource_parser.subprocess, "run", fake_run)

    resource = build_resource_item(source_path, "source.pdf")

    assert resource.outline[0].title == "Parsed Heading"
    assert resource.parser_metadata == {"engine": "wrapper"}
    assert resource.segments[0].parser_name == "json-parser"
    assert resource.segments[0].parser_version == "2.1"
    assert resource.segments[0].text_source == "external_parser"
    assert resource.segments[0].text == "Markdown parser evidence."


def test_external_parser_blocks_and_pages_preserve_page_and_heading_path(tmp_path, monkeypatch) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-1.4\n")
    _enable_parser(monkeypatch)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "parser_name": "block-parser",
                    "parser_version": "3",
                    "headings": [{"title": "Parsed Root", "level": 1, "page_range": "5"}],
                    "pages": [
                        {
                            "page_number": 5,
                            "blocks": [
                                {
                                    "text": "Block parser evidence.",
                                    "heading_path": ["Parsed Root", "Nested"],
                                }
                            ],
                        }
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(resource_parser.subprocess, "run", fake_run)

    resource = build_resource_item(source_path, "source.pdf")

    assert resource.outline[0].title == "Parsed Root"
    assert resource.segments[0].page_range == "5"
    assert resource.segments[0].heading_path == ["Parsed Root", "Nested"]
    assert resource.segments[0].parser_name == "block-parser"
    assert resource.segments[0].parser_version == "3"
    assert resource.segments[0].text_source == "external_parser"


@pytest.mark.parametrize(
    ("stdout", "expected_error"),
    [
        ("", "external_parser_empty_output"),
        ("{not-json", "external_parser_malformed_json"),
    ],
)
def test_external_parser_empty_or_malformed_json_returns_failure(
    tmp_path,
    monkeypatch,
    stdout,
    expected_error,
) -> None:
    source_path = tmp_path / "source.md"
    _write_source(source_path)
    _enable_parser(monkeypatch)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(resource_parser.subprocess, "run", fake_run)

    parsed = resource_parser.parse_with_external_resource_parser(source_path)

    assert parsed is not None
    assert parsed.status == "failed"
    assert parsed.error == expected_error
