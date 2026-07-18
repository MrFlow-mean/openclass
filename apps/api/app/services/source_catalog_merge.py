from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from typing import Any, Sequence

from app.models import (
    SourceChapter,
    SourceDocumentPart,
    SourceDocumentPartKind,
    SourceIngestionRecord,
)
from app.services.source_chapter_identity import stable_source_chapter_id
from app.services.source_codex_models import (
    SourceCatalogDirectoryNode,
    SourceCatalogError,
    SourceCatalogPlan,
    SourceCatalogPartProposal,
    SourceChapterAnchorProposal,
    SourcePageUnit,
    SourceShard,
)


def _compact(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"

def validate_catalog_plan(
    plan: SourceCatalogPlan,
    *,
    manifest_hash: str,
    candidate_chapters: list[SourceChapter],
    page_min: int,
    page_end_exclusive: int,
) -> None:
    if plan.input_manifest_hash != manifest_hash:
        raise SourceCatalogError("Codex returned a catalog for a different source manifest")
    if not plan.document_parts:
        raise SourceCatalogError("Codex did not classify any document part")
    part_ranges: list[tuple[int, int]] = []
    for part in sorted(plan.document_parts, key=lambda item: item.page_start):
        if part.page_start < page_min or part.page_end_exclusive > page_end_exclusive:
            raise SourceCatalogError("Codex returned a document part outside the source page range")
        if part.page_end_exclusive <= part.page_start:
            raise SourceCatalogError("Codex returned an empty document part")
        if part_ranges and part.page_start < part_ranges[-1][1]:
            raise SourceCatalogError("Codex returned overlapping document parts")
        if any(
            page_no < page_min or page_no >= page_end_exclusive
            for page_no in part.evidence_page_numbers
        ):
            raise SourceCatalogError("Codex returned document-part evidence outside the source range")
        part_ranges.append((part.page_start, part.page_end_exclusive))

    keys: set[str] = set()
    known_candidate_ids = {chapter.id for chapter in candidate_chapters}
    covered_candidate_ids: list[str] = []
    kept_keys: set[str] = set()
    kept_order_indexes: set[int] = set()
    previous_order = -1
    for node in plan.directory_nodes:
        if node.local_key in keys:
            raise SourceCatalogError("Codex returned duplicate directory node keys")
        keys.add(node.local_key)
        if node.candidate_id:
            if node.candidate_id not in known_candidate_ids:
                raise SourceCatalogError("Codex returned an unknown candidate chapter id")
            covered_candidate_ids.append(node.candidate_id)
        if node.decision == "keep":
            if not node.title.strip():
                raise SourceCatalogError("Codex returned a kept directory node without a title")
            if node.parent_local_key and node.parent_local_key not in kept_keys:
                raise SourceCatalogError("Codex returned a child before its parent")
            if node.order_index < previous_order:
                raise SourceCatalogError("Codex returned non-monotonic directory order")
            if node.order_index in kept_order_indexes:
                raise SourceCatalogError("Codex returned duplicate directory order indexes")
            if node.body_page_hint is not None and not (
                page_min <= node.body_page_hint < page_end_exclusive
            ):
                raise SourceCatalogError("Codex returned a chapter page outside the source range")
            if any(
                page_no < page_min or page_no >= page_end_exclusive
                for page_no in node.evidence_page_numbers
            ):
                raise SourceCatalogError("Codex returned chapter evidence outside the source range")
            if not node.candidate_id and not (
                node.body_page_hint is not None or node.evidence_page_numbers
            ):
                raise SourceCatalogError("Codex returned a new directory node without source evidence")
            kept_keys.add(node.local_key)
            kept_order_indexes.add(node.order_index)
            previous_order = node.order_index
        elif node.parent_local_key:
            raise SourceCatalogError("A rejected directory node cannot own a parent relation")
    if len(covered_candidate_ids) != len(set(covered_candidate_ids)):
        raise SourceCatalogError("Codex decided the same candidate chapter more than once")
    if set(covered_candidate_ids) != known_candidate_ids:
        raise SourceCatalogError("Codex did not decide every candidate chapter")


def plan_source_shards(
    *,
    units: list[SourcePageUnit],
    nodes: list[SourceCatalogDirectoryNode],
    candidates: list[SourceChapter],
    text_length: int,
    max_workers: int,
    plan_hash: str,
) -> list[SourceShard]:
    if len(units) < 80 and text_length < 120_000 and len(nodes) < 100:
        return []
    desired = max(
        math.ceil(len(units) / 120),
        math.ceil(max(1, text_length) / 160_000),
        math.ceil(max(1, len(nodes)) / 120),
    )
    count = min(max_workers, max(2, desired), len(units))
    candidate_page = {chapter.id: chapter.page_start for chapter in candidates}
    shards: list[SourceShard] = []
    for index in range(count):
        start_index = math.floor(index * len(units) / count)
        end_index = math.floor((index + 1) * len(units) / count)
        shard_pages = tuple(units[start_index:end_index])
        if not shard_pages:
            continue
        page_start = shard_pages[0].page_no
        page_end_exclusive = shard_pages[-1].page_no + 1
        assigned = tuple(
            node
            for node in nodes
            if page_start
            <= (
                node.body_page_hint
                or candidate_page.get(node.candidate_id)
                or _round_robin_page(node.order_index, units)
            )
            < page_end_exclusive
        )
        if not assigned:
            continue
        input_hash = _hash_json(
            {
                "plan_hash": plan_hash,
                "page_start": page_start,
                "page_end_exclusive": page_end_exclusive,
                "page_hashes": [hashlib.sha256(page.text.encode("utf-8")).hexdigest() for page in shard_pages],
                "node_keys": [node.local_key for node in assigned],
            }
        )
        shards.append(
            SourceShard(
                shard_id=f"shard-{index + 1}",
                page_start=page_start,
                page_end_exclusive=page_end_exclusive,
                pages=shard_pages,
                nodes=assigned,
                input_hash=input_hash,
            )
        )
    return shards


def _round_robin_page(order_index: int, units: list[SourcePageUnit]) -> int:
    if not units:
        return 1
    return units[min(len(units) - 1, max(0, order_index) % len(units))].page_no


def materialize_document_parts(
    *,
    record: SourceIngestionRecord,
    units: list[SourcePageUnit],
    proposals: list[SourceCatalogPartProposal],
) -> list[SourceDocumentPart]:
    if not units:
        return []
    by_page = {unit.page_no: unit for unit in units}
    page_min = units[0].page_no
    page_end = units[-1].page_no + 1
    ordered = sorted(proposals, key=lambda item: (item.page_start, item.page_end_exclusive))
    complete: list[SourceCatalogPartProposal] = []
    cursor = page_min
    for proposal in ordered:
        if proposal.page_start > cursor:
            complete.append(
                SourceCatalogPartProposal(
                    kind="unknown",
                    title="未确定篇幅",
                    page_start=cursor,
                    page_end_exclusive=proposal.page_start,
                    confidence=0.0,
                )
            )
        complete.append(proposal)
        cursor = proposal.page_end_exclusive
    if cursor < page_end:
        complete.append(
            SourceCatalogPartProposal(
                kind="unknown",
                title="未确定篇幅",
                page_start=cursor,
                page_end_exclusive=page_end,
                confidence=0.0,
            )
        )
    if not complete:
        complete = [
            SourceCatalogPartProposal(
                kind="unknown",
                title="未确定篇幅",
                page_start=page_min,
                page_end_exclusive=page_end,
                confidence=0.0,
            )
        ]
    parts: list[SourceDocumentPart] = []
    for order_index, proposal in enumerate(complete):
        start_unit = by_page.get(proposal.page_start)
        end_unit = by_page.get(proposal.page_end_exclusive - 1)
        evidence = sorted(
            {
                page
                for page in proposal.evidence_page_numbers
                if page_min <= page < page_end
            }
        )
        identity = "\x1f".join(
            (
                record.id,
                proposal.kind,
                str(proposal.page_start),
                str(proposal.page_end_exclusive),
                proposal.title.strip(),
            )
        )
        parts.append(
            SourceDocumentPart(
                id=f"sourcepart_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}",
                owner_user_id=record.owner_user_id,
                package_id=record.package_id,
                source_ingestion_id=record.id,
                kind=proposal.kind,
                title=proposal.title.strip() or _default_part_title(proposal.kind),
                order_index=order_index,
                source_locator=f"pages:{proposal.page_start}-{proposal.page_end_exclusive - 1}",
                body_start_offset=start_unit.start_offset if start_unit else None,
                body_end_offset=end_unit.end_offset if end_unit else None,
                page_start=proposal.page_start,
                page_end=proposal.page_end_exclusive,
                anchor_status="verified" if evidence and proposal.confidence >= 0.5 else "unverified",
                confidence=proposal.confidence,
                evidence_page_numbers=evidence,
                metadata={"source": "codex_catalog", "range_semantics": "half_open"},
            )
        )
    return parts


def materialize_catalog_chapters(
    *,
    record: SourceIngestionRecord,
    text: str,
    units: list[SourcePageUnit],
    candidates: list[SourceChapter],
    nodes: list[SourceCatalogDirectoryNode],
    anchors: dict[str, SourceChapterAnchorProposal],
    parts: Sequence[SourceDocumentPart] = (),
) -> list[SourceChapter]:
    candidates_by_id = {chapter.id: chapter for chapter in candidates}
    located: list[dict[str, Any]] = []
    for node in sorted(nodes, key=lambda item: item.order_index):
        candidate = candidates_by_id.get(node.candidate_id)
        anchor = anchors.get(node.local_key)
        exact = _locate_heading(
            units,
            heading=(anchor.heading_excerpt if anchor and anchor.status == "located" else node.body_heading),
            number=node.number,
            title=node.title,
            page_hint=(anchor.page_no if anchor and anchor.status == "located" else node.body_page_hint),
        )
        if exact is not None and _is_non_content_part(_part_for_offset(parts, exact[0])):
            exact = None
        candidate_is_verified = bool(
            candidate
            and candidate.anchor_status == "verified"
            and candidate.body_start_offset is not None
            and not _is_non_content_part(
                _part_for_offset(parts, candidate.body_start_offset)
            )
        )
        if exact is None and candidate_is_verified:
            assert candidate is not None
            start_offset = candidate.body_start_offset
            page_start = candidate.page_start
            source_locator = candidate.source_locator
        elif exact is not None:
            start_offset, page_start = exact
            source_locator = f"codex:page:{page_start}"
        else:
            start_offset = None
            page_start = None
            source_locator = candidate.source_locator if candidate else ""
        located.append(
            {
                "node": node,
                "candidate": candidate,
                "anchor": anchor,
                "start_offset": start_offset,
                "page_start": page_start,
                "source_locator": source_locator,
            }
        )

    for index, item in enumerate(located):
        start_offset = item["start_offset"]
        node = item["node"]
        end_offset = None
        page_end = None
        if start_offset is not None:
            containing_part = _part_for_offset(parts, start_offset)
            boundary = next(
                (
                    later
                    for later in located[index + 1 :]
                    if later["start_offset"] is not None and later["node"].level <= node.level
                ),
                None,
            )
            end_offset = boundary["start_offset"] if boundary else len(text)
            page_end = (
                boundary["page_start"]
                if boundary and boundary["page_start"] is not None
                else (units[-1].page_no + 1 if units else None)
            )
            if (
                containing_part is not None
                and containing_part.body_end_offset is not None
                and containing_part.body_end_offset < end_offset
            ):
                end_offset = containing_part.body_end_offset
                page_end = containing_part.page_end
        item["end_offset"] = end_offset
        item["page_end"] = page_end

    result: list[SourceChapter] = []
    by_key: dict[str, SourceChapter] = {}
    occurrences: Counter[tuple[tuple[str, ...], str, str, int]] = Counter()
    for order_index, item in enumerate(located):
        node: SourceCatalogDirectoryNode = item["node"]
        candidate: SourceChapter | None = item["candidate"]
        parent = by_key.get(node.parent_local_key) if node.parent_local_key else None
        parent_path = parent.path if parent else []
        number = node.number.strip() or (candidate.number if candidate else "")
        title = node.title.strip() or (candidate.title if candidate else "")
        normalized_number = _normalize_number(number)
        level = max(1, node.level)
        semantic_key = (tuple(parent_path), normalized_number, _normalize_text(title), level)
        occurrence = occurrences[semantic_key]
        occurrences[semantic_key] += 1
        start_offset = item["start_offset"]
        end_offset = item["end_offset"]
        verified = start_offset is not None and end_offset is not None and end_offset > start_offset
        confidence = min(
            node.confidence,
            item["anchor"].confidence if item["anchor"] and item["anchor"].status == "located" else 1.0,
        )
        chapter = SourceChapter(
            id=stable_source_chapter_id(
                source_ingestion_id=record.id,
                parent_path=parent_path,
                normalized_number=normalized_number,
                title=title,
                level=level,
                source_locator=item["source_locator"],
                order_index=occurrence,
            ),
            owner_user_id=record.owner_user_id,
            package_id=record.package_id,
            source_ingestion_id=record.id,
            parent_id=parent.id if parent else None,
            number=number,
            normalized_number=normalized_number,
            title=title,
            level=level,
            path=[*parent_path, title],
            order_index=order_index,
            source_locator=item["source_locator"],
            body_start_offset=start_offset if verified else None,
            body_end_offset=end_offset if verified else None,
            page_start=item["page_start"] if verified else None,
            page_end=item["page_end"] if verified else None,
            anchor_status="verified" if verified else "unverified",
            confidence=confidence if verified else min(confidence, 0.49),
            excerpt=(
                _compact(text[start_offset:end_offset], 360)
                if verified and start_offset is not None and end_offset is not None
                else ""
            ),
            metadata={
                **(candidate.metadata if candidate else {}),
                "source": "codex_catalog",
                "codex_local_key": node.local_key,
                "codex_candidate_id": node.candidate_id,
                "codex_anchor_status": item["anchor"].status if item["anchor"] else "coordinator_only",
                "semantic_identity_version": 2,
                "semantic_occurrence": occurrence,
            },
        )
        result.append(chapter)
        by_key[node.local_key] = chapter
    return result


def validate_materialized_catalog(
    *,
    parts: list[SourceDocumentPart],
    chapters: list[SourceChapter],
    text_length: int,
) -> None:
    for previous, current in zip(parts, parts[1:]):
        if previous.page_end is not None and current.page_start is not None and current.page_start < previous.page_end:
            raise SourceCatalogError("Validated document parts overlap")
    chapter_ids = {chapter.id for chapter in chapters}
    for chapter in chapters:
        if chapter.parent_id and chapter.parent_id not in chapter_ids:
            raise SourceCatalogError("Validated directory contains an unknown parent")
        if chapter.anchor_status == "verified":
            if (
                chapter.body_start_offset is None
                or chapter.body_end_offset is None
                or chapter.body_start_offset < 0
                or chapter.body_end_offset > text_length
                or chapter.body_end_offset <= chapter.body_start_offset
            ):
                raise SourceCatalogError("Validated directory contains an invalid body range")


def build_page_units(text: str, pages: Sequence[object]) -> list[SourcePageUnit]:
    units: list[SourcePageUnit] = []
    for page in pages:
        page_no = int(getattr(page, "page_no", 0) or 0)
        if page_no < 1:
            continue
        page_text = str(getattr(page, "text", "") or "")
        start = int(getattr(page, "start_offset", 0) or 0)
        end = int(getattr(page, "end_offset", start + len(page_text)) or (start + len(page_text)))
        content_start = getattr(page, "content_start_offset", None)
        units.append(
            SourcePageUnit(
                page_no=page_no,
                text=page_text,
                start_offset=start,
                end_offset=end,
                content_start_offset=int(content_start if content_start is not None else start),
            )
        )
    if units:
        return sorted(units, key=lambda item: item.page_no)
    return [
        SourcePageUnit(
            page_no=1,
            text=text,
            start_offset=0,
            end_offset=len(text),
            content_start_offset=0,
        )
    ]


def _locate_heading(
    units: list[SourcePageUnit],
    *,
    heading: str,
    number: str,
    title: str,
    page_hint: int | None,
) -> tuple[int, int] | None:
    candidates = [value.strip() for value in (heading, f"{number} {title}", title) if value.strip()]
    if not candidates:
        return None
    ordered_units = sorted(
        units,
        key=lambda unit: (0 if page_hint and unit.page_no == page_hint else 1, unit.page_no),
    )
    matches: list[tuple[int, int]] = []
    for unit in ordered_units:
        for line in unit.text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            normalized_line = _normalize_text(stripped)
            if not any(_normalize_text(candidate) == normalized_line for candidate in candidates):
                continue
            line_index = unit.text.find(line)
            if line_index < 0:
                continue
            matches.append((unit.content_start_offset + line_index + len(line) - len(line.lstrip()), unit.page_no))
    unique = list(dict.fromkeys(matches))
    if page_hint:
        hinted = [match for match in unique if match[1] == page_hint]
        if len(hinted) == 1:
            return hinted[0]
    return unique[0] if len(unique) == 1 else None


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _normalize_number(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().rstrip(".、")


def _part_for_offset(
    parts: Sequence[SourceDocumentPart],
    offset: int,
) -> SourceDocumentPart | None:
    return next(
        (
            part
            for part in parts
            if part.body_start_offset is not None
            and part.body_end_offset is not None
            and part.body_start_offset <= offset < part.body_end_offset
        ),
        None,
    )


def _is_non_content_part(part: SourceDocumentPart | None) -> bool:
    return bool(
        part
        and part.kind
        in {
            "front_cover",
            "half_title",
            "title_page",
            "copyright",
            "dedication",
            "table_of_contents",
            "list_of_figures",
            "list_of_tables",
            "back_cover",
            "unknown",
        }
    )


def _default_part_title(kind: SourceDocumentPartKind) -> str:
    return {
        "front_cover": "封面",
        "half_title": "书名页",
        "title_page": "扉页",
        "copyright": "版权页",
        "dedication": "题献",
        "foreword": "前言",
        "preface": "序言",
        "introduction": "导言",
        "acknowledgements": "致谢",
        "table_of_contents": "目录",
        "list_of_figures": "插图目录",
        "list_of_tables": "表格目录",
        "body": "正文",
        "epilogue": "尾声",
        "afterword": "后记",
        "appendix": "附录",
        "notes": "注释",
        "glossary": "术语表",
        "bibliography": "参考文献",
        "index": "索引",
        "colophon": "出版说明",
        "back_cover": "后封面",
        "unknown": "未确定篇幅",
    }[kind]



def _hash_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
