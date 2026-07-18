from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.models import SourceChapter, SourceStructureQuality
from app.services.ai_logging import ai_usage_logger
from app.services.source_structure_ai import (
    SourcePageEvidence,
    SourceStructureAnalyzer,
    SourceStructureReview,
)
from app.services.source_structure_quality import SourceStructureQualityResult


DEFAULT_MAX_REVIEW_ROUNDS = 3


@dataclass(frozen=True)
class SourceStructureSupervisionCandidate:
    payload: Any
    chapters: list[SourceChapter]
    quality: SourceStructureQualityResult


@dataclass(frozen=True)
class SourceStructureSupervisionOutcome:
    status: str
    candidate: SourceStructureSupervisionCandidate
    rounds: int
    repair_count: int
    summary: str
    issues: list[str]


RepairCandidateBuilder = Callable[
    [SourceStructureReview, Any],
    SourceStructureSupervisionCandidate,
]
ProgressReporter = Callable[[str, int], None]


def supervise_source_structure(
    *,
    owner_user_id: str,
    package_id: str,
    source_ingestion_id: str,
    source_title: str,
    analyzer: SourceStructureAnalyzer,
    evidence_pages: list[SourcePageEvidence],
    initial: SourceStructureSupervisionCandidate,
    build_repair_candidate: RepairCandidateBuilder,
    report_progress: ProgressReporter,
) -> SourceStructureSupervisionOutcome:
    current = initial
    repair_count = 0
    last_review: SourceStructureReview | None = None
    completed_rounds = 0
    evidence_page_numbers = {page.page_no for page in evidence_pages}
    max_rounds = supervision_max_rounds()
    reviewer = getattr(analyzer, "review_pdf_toc", None)
    if not callable(reviewer):
        return SourceStructureSupervisionOutcome(
            status="unsupported",
            candidate=current,
            rounds=0,
            repair_count=0,
            summary="The configured analyzer does not support independent review.",
            issues=["The source-structure analyzer has no review capability."],
        )

    for review_round in range(1, max_rounds + 1):
        completed_rounds = review_round
        report_progress("reviewing_structure", 58 + min(3, review_round))
        try:
            last_review = reviewer(
                source_title=source_title,
                pages=evidence_pages,
                current_nodes=source_chapters_for_review(current.chapters),
                quality_diagnostics=current.quality.quality.diagnostics,
                review_round=review_round,
            )
        except Exception as exc:
            ai_usage_logger.log_event(
                "source_structure_supervision_failed",
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_ingestion_id=source_ingestion_id,
                model=getattr(analyzer, "model", ""),
                review_round=review_round,
                error=str(exc),
            )
            return SourceStructureSupervisionOutcome(
                status="failed",
                candidate=current,
                rounds=review_round,
                repair_count=repair_count,
                summary="Codex supervision failed; the directory was not published.",
                issues=[str(exc)],
            )

        review_matches = review_nodes_match_chapters(
            last_review,
            current.chapters,
            evidence_page_numbers=evidence_page_numbers,
        )
        if (
            last_review.verdict == "pass"
            and review_matches
            and quality_passes_structure_supervision(current.quality)
        ):
            ai_usage_logger.log_event(
                "source_structure_supervision_passed",
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_ingestion_id=source_ingestion_id,
                model=analyzer.model,
                review_round=review_round,
                repair_count=repair_count,
                quality=current.quality.quality,
            )
            return SourceStructureSupervisionOutcome(
                status="passed",
                candidate=current,
                rounds=review_round,
                repair_count=repair_count,
                summary=last_review.summary,
                issues=[],
            )
        if last_review.verdict == "blocked" or not last_review.nodes:
            break

        report_progress("repairing_structure", 60 + min(2, review_round))
        candidate = build_repair_candidate(last_review, current.payload)
        repair_mismatches = review_node_mismatches(
            last_review,
            candidate.chapters,
            evidence_page_numbers=evidence_page_numbers,
        )
        if repair_mismatches:
            ai_usage_logger.log_event(
                "source_structure_supervision_repair_rejected",
                owner_user_id=owner_user_id,
                package_id=package_id,
                source_ingestion_id=source_ingestion_id,
                model=analyzer.model,
                review_round=review_round,
                mismatches=repair_mismatches[:20],
            )
            break
        candidate_rank = source_structure_quality_rank(candidate.quality.quality)
        current_rank = source_structure_quality_rank(current.quality.quality)
        if candidate_rank < current_rank:
            break
        if (
            candidate_rank == current_rank
            and source_chapter_review_signature(candidate.chapters)
            == source_chapter_review_signature(current.chapters)
        ):
            break
        current = candidate
        repair_count += 1
        ai_usage_logger.log_event(
            "source_structure_supervision_repair_applied",
            owner_user_id=owner_user_id,
            package_id=package_id,
            source_ingestion_id=source_ingestion_id,
            model=analyzer.model,
            review_round=review_round,
            repair_count=repair_count,
            quality=current.quality.quality,
            issue_count=len(last_review.issues),
        )

    return SourceStructureSupervisionOutcome(
        status="blocked",
        candidate=current,
        rounds=completed_rounds,
        repair_count=repair_count,
        summary=(
            last_review.summary
            if last_review is not None
            else "Codex could not complete the source-structure review."
        ),
        issues=(
            [issue.message for issue in last_review.issues]
            if last_review is not None and last_review.issues
            else list(current.quality.quality.diagnostics)
        ),
    )


def supervision_max_rounds() -> int:
    raw = os.getenv(
        "OPENCLASS_CODEX_SOURCE_SUPERVISION_MAX_ROUNDS",
        str(DEFAULT_MAX_REVIEW_ROUNDS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_REVIEW_ROUNDS
    return max(1, min(5, value))


def quality_passes_structure_supervision(
    result: SourceStructureQualityResult,
) -> bool:
    quality = result.quality
    return bool(
        quality.text_readiness in {"ready", "unknown"}
        and quality.total_chapter_count > 0
        and quality.verified_ratio >= 0.98
        and quality.boundary_valid_ratio >= 0.98
        and quality.body_coverage_ratio >= 0.8
        and quality.duplicate_range_count == 0
        and quality.overlap_ratio == 0.0
        and quality.non_monotonic_count == 0
        and quality.oversized_leaf_count == 0
    )


def source_chapters_for_review(
    chapters: list[SourceChapter],
) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "number": chapter.number,
            "title": _review_field_title(chapter),
            "display_title": chapter.title,
            "level": chapter.level,
            "toc_page": chapter_toc_page(chapter),
            "printed_page": chapter_printed_page(chapter),
            "body_page": chapter.page_start or 0,
            "verified": chapter.anchor_status == "verified",
            "repair_provenance": _page_repair_debug_metadata(chapter),
        }
        for index, chapter in enumerate(chapters[:300])
    ]


def review_nodes_match_chapters(
    review: SourceStructureReview,
    chapters: list[SourceChapter],
    *,
    evidence_page_numbers: set[int],
) -> bool:
    return not review_node_mismatches(
        review,
        chapters,
        evidence_page_numbers=evidence_page_numbers,
    )


def review_node_mismatches(
    review: SourceStructureReview,
    chapters: list[SourceChapter],
    *,
    evidence_page_numbers: set[int],
) -> list[str]:
    expected = [
        node for node in review.nodes if node.toc_page in evidence_page_numbers
    ]
    actual = [
        chapter
        for chapter in chapters
        if chapter_toc_page(chapter) in evidence_page_numbers
    ]
    if not expected:
        return ["review returned no source-grounded directory nodes"]
    if len(actual) != len(expected):
        return [f"node count differs: review={len(expected)} candidate={len(actual)}"]
    mismatches: list[str] = []
    for index, (node, chapter) in enumerate(zip(expected, actual)):
        expected_signature = proposal_review_signature(
            number=node.number,
            title=node.title,
            level=node.level,
            toc_page=node.toc_page,
            printed_page=node.printed_page,
        )
        actual_signature = chapter_review_signature(chapter)
        if expected_signature[:4] != actual_signature[:4]:
            mismatches.append(
                f"node {index} identity differs: review={expected_signature[:4]} "
                f"candidate={actual_signature[:4]}"
            )
            continue
        if (
            expected_signature[4]
            and expected_signature[4] != actual_signature[4]
            and not _chapter_has_grounded_printed_page_repair(
                chapter,
                original_printed_page=expected_signature[4],
            )
        ):
            mismatches.append(
                f"node {index} printed page differs: review={expected_signature[4]} "
                f"candidate={actual_signature[4]} metadata="
                f"{_page_repair_debug_metadata(chapter)}"
            )
    return mismatches


def source_chapter_review_signature(
    chapters: list[SourceChapter],
) -> list[tuple[str, str, int, int, int]]:
    return [chapter_review_signature(chapter) for chapter in chapters]


def chapter_review_signature(
    chapter: SourceChapter,
) -> tuple[str, str, int, int, int]:
    source_title = str(chapter.metadata.get("ocr_title") or chapter.title)
    return proposal_review_signature(
        number=chapter.number,
        title=source_title,
        level=chapter.level,
        toc_page=chapter_toc_page(chapter),
        printed_page=chapter_printed_page(chapter),
    )


def proposal_review_signature(
    *,
    number: str,
    title: str,
    level: int,
    toc_page: int,
    printed_page: int,
) -> tuple[str, str, int, int, int]:
    display_title = _display_title(number, title)
    return (
        _normalize_number(number or _number_from_title(display_title)),
        _normalize_text(display_title),
        max(1, min(6, level)),
        max(0, toc_page),
        max(0, printed_page),
    )


def chapter_toc_page(chapter: SourceChapter) -> int:
    match = re.search(r"toc-page:(\d+)", chapter.source_locator)
    return int(match.group(1)) if match else 0


def chapter_printed_page(chapter: SourceChapter) -> int:
    match = re.search(r"printed:(\d+)", chapter.source_locator)
    return int(match.group(1)) if match else 0


def _chapter_has_grounded_printed_page_repair(
    chapter: SourceChapter,
    *,
    original_printed_page: int,
) -> bool:
    discarded = chapter.metadata.get("discarded_non_monotonic_printed_page")
    return bool(
        chapter.anchor_status == "verified"
        and chapter.metadata.get("printed_page_inferred")
        and isinstance(discarded, int)
        and discarded == original_printed_page
    )


def _page_repair_debug_metadata(chapter: SourceChapter) -> dict[str, object]:
    return {
        key: chapter.metadata.get(key)
        for key in (
            "body_number_candidates",
            "discarded_non_monotonic_printed_page",
            "printed_page_inferred",
            "printed_page_mapping_support",
            "body_anchor_title",
            "anchor_source",
            "verification",
        )
        if chapter.metadata.get(key) is not None
    }


def source_structure_quality_rank(quality: SourceStructureQuality) -> tuple[float, ...]:
    level_rank = {
        "search_only": 0,
        "unverified": 1,
        "partially_verified": 2,
        "fully_verified": 3,
    }.get(quality.level, 0)
    return (
        float(level_rank),
        float(-quality.oversized_leaf_count),
        float(quality.verified_leaf_count),
        float(quality.verified_chapter_count),
        quality.body_coverage_ratio,
        quality.confidence,
    )


def _display_title(number: str, title: str) -> str:
    cleaned_title = " ".join((title or "").split())
    cleaned_number = " ".join((number or "").split())
    if not cleaned_number:
        return cleaned_title
    normalized_title = _normalize_text(cleaned_title)
    normalized_number = _normalize_text(cleaned_number)
    if normalized_title.startswith(normalized_number):
        return cleaned_title
    return f"{cleaned_number} {cleaned_title}".strip()


def _number_from_title(title: str) -> str:
    match = re.match(r"^\s*((?:\d+|[A-Za-z]|[IVXLCDM]+)(?:\.\d+)*)\b", title)
    return match.group(1) if match else ""


def _normalize_number(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _normalize_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _review_field_title(chapter: SourceChapter) -> str:
    number = chapter.number.strip()
    if not number or not re.fullmatch(r"\d+(?:\.\d+)*\.?", number):
        return chapter.title
    return re.sub(
        rf"^\s*{re.escape(number.rstrip('.'))}\s*[.、:]?\s*",
        "",
        chapter.title,
        count=1,
    ) or chapter.title
