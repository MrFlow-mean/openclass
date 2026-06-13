from __future__ import annotations

import pytest

from app.models import CoursePackage, ResourceLibraryItem
from app.services.resource_copyright_audit import (
    ResourcePublicationBlockedError,
    SearchResult,
    assert_package_publication_allowed,
    audit_resource_public_distribution,
    copyright_metadata_query,
)


class FakeProvider:
    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None, *, fail: bool = False) -> None:
        self.results = results or []
        self.fail = fail
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("search unavailable")
        return self.results


def test_metadata_query_does_not_include_resource_body_text() -> None:
    resource = ResourceLibraryItem(
        name="original-notes.md",
        mime_type="text/markdown",
        resource_type="document",
        size_bytes=12,
        text_content="private body text that must not be sent to search",
    )

    query = copyright_metadata_query(resource)

    assert "original notes" in query
    assert "private body" not in query


def test_audit_blocks_commercial_publication_match() -> None:
    provider = FakeProvider(
        [
            SearchResult(
                title="Catalog listing",
                url="https://example.com/catalog",
                snippet="Publisher listing with ISBN, hardcover, paperback, and ebook editions.",
            )
        ]
    )

    audit = audit_resource_public_distribution(
        ResourceLibraryItem(name="catalog-title.pdf", mime_type="application/pdf", resource_type="document", size_bytes=10),
        search_provider=provider,
    )

    assert audit.status == "public_blocked"
    assert audit.public_distribution == "blocked"
    assert "commercial_publication_match" in audit.signals


def test_audit_allows_open_license_evidence() -> None:
    provider = FakeProvider(
        [
            SearchResult(
                title="Open course material",
                url="https://example.org/open",
                snippet="Released under Creative Commons CC BY 4.0.",
            )
        ]
    )

    audit = audit_resource_public_distribution(
        ResourceLibraryItem(name="open-material.md", mime_type="text/markdown", resource_type="document", size_bytes=10),
        search_provider=provider,
    )

    assert audit.status == "clear"
    assert audit.public_distribution == "allowed"
    assert "open_license_evidence" in audit.signals


def test_audit_marks_provider_errors_without_blocking_private_upload() -> None:
    audit = audit_resource_public_distribution(
        ResourceLibraryItem(name="notes.md", mime_type="text/markdown", resource_type="document", size_bytes=10),
        search_provider=FakeProvider(fail=True),
    )

    assert audit.status == "error"
    assert audit.public_distribution == "pending"
    assert "external_search_failed" in audit.signals


def test_publication_gate_requires_allowed_resources() -> None:
    blocked = ResourceLibraryItem(name="blocked.pdf", mime_type="application/pdf", resource_type="document", size_bytes=1)
    blocked.copyright_audit.public_distribution = "blocked"
    allowed = ResourceLibraryItem(name="allowed.md", mime_type="text/markdown", resource_type="document", size_bytes=1)
    allowed.copyright_audit.public_distribution = "allowed"

    with pytest.raises(ResourcePublicationBlockedError) as exc_info:
        assert_package_publication_allowed(CoursePackage(title="Package", summary="", lessons=[], resources=[allowed, blocked]))

    assert exc_info.value.resource_names == ["blocked.pdf"]
