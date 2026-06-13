from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.models import CoursePackage, ResourceCopyrightAudit, ResourceCopyrightEvidencePacket, ResourceLibraryItem, now_iso
from app.services.config import load_root_dotenv


_MAX_QUERY_TERMS = 8
_MAX_EVIDENCE_URLS = 6
_OPEN_LICENSE_PATTERNS = (
    "creative commons",
    "cc by",
    "cc-by",
    "cc0",
    "mit license",
    "apache license",
    "bsd license",
    "gnu general public license",
)
_PUBLIC_DOMAIN_PATTERNS = (
    "public domain",
    "project gutenberg",
    "internet archive",
    "hathitrust",
)
_COMMERCIAL_PUBLICATION_PATTERNS = (
    "publisher",
    "published by",
    "isbn",
    "edition",
    "hardcover",
    "paperback",
    "ebook",
    "google books",
    "worldcat",
    "amazon",
    "springer",
    "elsevier",
    "pearson",
    "wiley",
    "oup",
    "cambridge university press",
)
_UNAUTHORIZED_DISTRIBUTION_PATTERNS = (
    "free pdf download",
    "download pdf",
    "torrent",
    "pirated",
    "unauthorized",
    "z-library",
    "libgen",
    "1lib",
)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class ResourceCopyrightReviewResult:
    summary: str = ""
    signals: tuple[str, ...] = ()


class ExternalSearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        ...


class ResourceCopyrightReviewer:
    """Optional AI reviewer adapter; deterministic audit works when this is disabled."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def review(self, packet: ResourceCopyrightEvidencePacket) -> ResourceCopyrightReviewResult | None:
        if not self.enabled:
            return None
        return None


class BraveSearchProvider:
    name = "brave"

    def __init__(self, api_key: str, *, base_url: str = "https://api.search.brave.com/res/v1/web/search") -> None:
        self.api_key = api_key
        self.base_url = base_url

    def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        response = httpx.get(
            self.base_url,
            params={"q": query, "count": min(max(limit, 1), 20)},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("web", {}).get("results", [])
        if not isinstance(results, list):
            return []
        parsed: list[SearchResult] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            parsed.append(
                SearchResult(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    snippet=str(item.get("description") or item.get("snippet") or "").strip(),
                )
            )
        return parsed


class ResourcePublicationBlockedError(ValueError):
    def __init__(self, resource_names: list[str]) -> None:
        self.resource_names = resource_names
        super().__init__("Some resources are not allowed for public distribution")


def default_search_provider() -> ExternalSearchProvider | None:
    load_root_dotenv()
    provider_name = (os.getenv("OPENCLASS_COPYRIGHT_SEARCH_PROVIDER") or "brave").strip().lower()
    if provider_name in {"", "none", "disabled", "off"}:
        return None
    if provider_name == "brave":
        api_key = _env_any("OPENCLASS_BRAVE_SEARCH_API_KEY", "BRAVE_SEARCH_API_KEY")
        if not api_key:
            return None
        return BraveSearchProvider(api_key)
    return None


def audit_resource_public_distribution(
    resource: ResourceLibraryItem,
    *,
    search_provider: ExternalSearchProvider | None = None,
    reviewer: ResourceCopyrightReviewer | None = None,
) -> ResourceCopyrightAudit:
    file_hash = resource_file_hash(resource)
    reviewer_result = _review_evidence_packet(resource.copyright_probe, reviewer)
    provider = search_provider if search_provider is not None else default_search_provider()
    if provider is None:
        return _with_probe_context(
            ResourceCopyrightAudit(
                status="needs_review",
                public_distribution="pending",
                risk_level="unknown",
                signals=["external_search_not_configured", *_reviewer_signals(reviewer_result)],
                checked_at=now_iso(),
                reason="没有配置版权检索服务，公开传播前需要平台复核。",
                provider=None,
                file_hash=file_hash,
            ),
            resource,
            reviewer_result=reviewer_result,
        )

    query = copyright_metadata_query(resource)
    if not query:
        return _with_probe_context(
            ResourceCopyrightAudit(
                status="needs_review",
                public_distribution="pending",
                risk_level="unknown",
                signals=["metadata_insufficient", *_reviewer_signals(reviewer_result)],
                checked_at=now_iso(),
                reason="资料缺少可用于公开传播审核的标题或出版元数据。",
                provider=provider.name,
                file_hash=file_hash,
            ),
            resource,
            reviewer_result=reviewer_result,
        )

    try:
        results = provider.search(query, limit=8)
    except Exception as exc:
        return _with_probe_context(
            ResourceCopyrightAudit(
                status="error",
                public_distribution="pending",
                risk_level="unknown",
                signals=["external_search_failed", *_reviewer_signals(reviewer_result)],
                checked_at=now_iso(),
                reason=f"版权检索服务暂时不可用：{exc.__class__.__name__}",
                provider=provider.name,
                file_hash=file_hash,
            ),
            resource,
            reviewer_result=reviewer_result,
        )

    audit = classify_search_results(
        results,
        provider=provider.name,
        file_hash=file_hash,
    )
    if reviewer_result and reviewer_result.signals:
        audit = audit.model_copy(update={"signals": [*audit.signals, *_reviewer_signals(reviewer_result)]})
    return _with_probe_context(audit, resource, reviewer_result=reviewer_result)


def classify_search_results(
    results: list[SearchResult],
    *,
    provider: str,
    file_hash: str | None = None,
) -> ResourceCopyrightAudit:
    if not results:
        return ResourceCopyrightAudit(
            status="needs_review",
            public_distribution="pending",
            risk_level="unknown",
            signals=["no_external_matches"],
            evidence_urls=[],
            checked_at=now_iso(),
            reason="没有检索到足够判断公开传播权限的外部证据。",
            provider=provider,
            file_hash=file_hash,
        )

    evidence_urls = _evidence_urls(results)
    combined = "\n".join(f"{item.title}\n{item.url}\n{item.snippet}" for item in results).lower()
    signals: list[str] = []

    if _contains_any(combined, _UNAUTHORIZED_DISTRIBUTION_PATTERNS):
        signals.append("unauthorized_distribution_source")
    if _contains_any(combined, _COMMERCIAL_PUBLICATION_PATTERNS):
        signals.append("commercial_publication_match")
    if _contains_any(combined, _OPEN_LICENSE_PATTERNS):
        signals.append("open_license_evidence")
    if _contains_any(combined, _PUBLIC_DOMAIN_PATTERNS):
        signals.append("public_domain_evidence")

    if "unauthorized_distribution_source" in signals or (
        "commercial_publication_match" in signals
        and "open_license_evidence" not in signals
        and "public_domain_evidence" not in signals
    ):
        return ResourceCopyrightAudit(
            status="public_blocked",
            public_distribution="blocked",
            risk_level="high",
            signals=signals,
            evidence_urls=evidence_urls,
            checked_at=now_iso(),
            reason="外部检索显示该资料可能是受版权保护或未经授权传播的出版物，禁止公开传播。",
            provider=provider,
            file_hash=file_hash,
        )

    if "open_license_evidence" in signals or "public_domain_evidence" in signals:
        return ResourceCopyrightAudit(
            status="clear",
            public_distribution="allowed",
            risk_level="low",
            signals=signals,
            evidence_urls=evidence_urls,
            checked_at=now_iso(),
            reason="外部检索显示存在开放许可或公版证据，可进入公开传播流程。",
            provider=provider,
            file_hash=file_hash,
        )

    return ResourceCopyrightAudit(
        status="needs_review",
        public_distribution="pending",
        risk_level="unknown",
        signals=signals or ["evidence_insufficient"],
        evidence_urls=evidence_urls,
        checked_at=now_iso(),
        reason="外部证据不足以确认该资料可以公开传播，需要平台复核。",
        provider=provider,
        file_hash=file_hash,
    )


def copyright_metadata_query(resource: ResourceLibraryItem) -> str:
    packet = resource.copyright_probe
    candidates: list[str] = []
    for value in packet.isbn_candidates:
        candidates.append(value)
    title = _first_metadata_value(packet.title_candidates)
    author = _first_metadata_value(packet.author_candidates)
    publisher = _first_metadata_value(packet.publisher_candidates)
    if title and author:
        candidates.append(f"{title} {author}")
    if title and publisher:
        candidates.append(f"{title} {publisher}")
    if title:
        candidates.append(title)
    if publisher and not title:
        candidates.append(publisher)
    for value in [*packet.license_candidates[:2], *packet.rights_candidates[:2]]:
        if len(value) <= 140:
            candidates.append(value)
    if not candidates:
        stem = Path(resource.name).stem.strip()
        if stem:
            candidates.append(stem)
        for value in _isbn_candidates(resource.name):
            candidates.append(value)

    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _metadata_phrase(candidate)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(normalized)
        if len(terms) >= _MAX_QUERY_TERMS:
            break
    if not terms:
        return ""
    return " ".join(terms)


def resource_file_hash(resource: ResourceLibraryItem) -> str | None:
    if not resource.source_path:
        return None
    source_path = Path(resource.source_path)
    try:
        with source_path.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def assert_package_publication_allowed(package: CoursePackage) -> None:
    blocked = [
        resource.name
        for resource in package.resources
        if resource.copyright_audit.public_distribution != "allowed"
    ]
    if blocked:
        raise ResourcePublicationBlockedError(blocked)


def audit_after_admin_approval(
    audit: ResourceCopyrightAudit,
    *,
    appeal_id: str,
) -> ResourceCopyrightAudit:
    return audit.model_copy(
        update={
            "status": "clear",
            "public_distribution": "allowed",
            "risk_level": "low",
            "override_source": "admin_appeal",
            "appeal_id": appeal_id,
            "checked_at": now_iso(),
            "reason": "平台管理员已根据资源申诉批准公开传播。",
        }
    )


def _review_evidence_packet(
    packet: ResourceCopyrightEvidencePacket,
    reviewer: ResourceCopyrightReviewer | None,
) -> ResourceCopyrightReviewResult | None:
    if reviewer is None:
        return None
    try:
        return reviewer.review(packet)
    except Exception:
        return ResourceCopyrightReviewResult(
            summary="AI reviewer was unavailable; deterministic audit continued.",
            signals=("ai_reviewer_unavailable",),
        )


def _reviewer_signals(result: ResourceCopyrightReviewResult | None) -> list[str]:
    return list(result.signals) if result else []


def _with_probe_context(
    audit: ResourceCopyrightAudit,
    resource: ResourceLibraryItem,
    *,
    reviewer_result: ResourceCopyrightReviewResult | None = None,
) -> ResourceCopyrightAudit:
    packet = resource.copyright_probe
    signals = [*audit.signals]
    if packet.isbn_candidates:
        signals.append("metadata_isbn")
    if packet.publisher_candidates:
        signals.append("metadata_publisher")
    if packet.rights_candidates:
        signals.append("metadata_rights")
    if packet.license_candidates:
        signals.append("metadata_license")
    signals = _dedupe(signals)
    summary = _probe_summary(packet)
    if reviewer_result and reviewer_result.summary:
        summary = f"{summary} Reviewer: {reviewer_result.summary}".strip()
    return audit.model_copy(
        update={
            "signals": signals,
            "probe_summary": summary,
            "probe_section_count": len(packet.probe_sections),
            "metadata_sources": _dedupe([*packet.metadata_sources, *packet.source_markers]),
        }
    )


def _probe_summary(packet: ResourceCopyrightEvidencePacket) -> str:
    parts: list[str] = []
    if packet.title_candidates:
        parts.append(f"title={packet.title_candidates[0]}")
    if packet.author_candidates:
        parts.append(f"author={packet.author_candidates[0]}")
    if packet.publisher_candidates:
        parts.append(f"publisher={packet.publisher_candidates[0]}")
    if packet.isbn_candidates:
        parts.append(f"isbn={packet.isbn_candidates[0]}")
    if packet.license_candidates:
        parts.append(f"license={packet.license_candidates[0]}")
    if packet.rights_candidates:
        parts.append(f"rights={packet.rights_candidates[0]}")
    if packet.probe_sections:
        roles = _dedupe([section.role for section in packet.probe_sections])
        parts.append(f"sections={len(packet.probe_sections)}:{','.join(roles)}")
    return "; ".join(parts)[:600]


def _first_metadata_value(values: list[str]) -> str | None:
    for value in values:
        normalized = _metadata_phrase(value)
        if normalized:
            return normalized
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _evidence_urls(results: list[SearchResult]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for result in results:
        url = result.url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= _MAX_EVIDENCE_URLS:
            break
    return urls


def _isbn_candidates(text: str) -> list[str]:
    compact = re.sub(r"[^0-9Xx]", "", text)
    matches: list[str] = []
    for length in (13, 10):
        for index in range(0, max(0, len(compact) - length + 1)):
            value = compact[index : index + length]
            if len(value) == length:
                matches.append(value.upper())
    return matches[:2]


def _metadata_phrase(value: str) -> str:
    value = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", value)
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .,:;()[]{}")
    return value[:120]
