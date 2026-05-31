from __future__ import annotations

import json
import re
import sqlite3
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import LibraryChapter, ResourceLibraryItem, ResourceSegment
from app.services.image_ocr import PdfOcrPageResult, extract_pdf_page_texts
from app.services.resource_library import (
    _normalize_extracted_text,
    _resource_segment_hash,
    _split_resource_text_into_segments,
    build_resource_item,
    build_resource_segments,
    resource_has_text_evidence,
)
from app.services.resource_parser import current_resource_parser_spec, parse_with_external_resource_parser
from app.services.resource_segment_store import ResourceSegmentStore


@dataclass(frozen=True)
class ResourceReindexOptions:
    database_path: Path
    apply: bool = False
    resource_id: str | None = None
    package_id: str | None = None
    owner_user_id: str | None = None
    limit: int | None = None
    create_backup: bool = True
    ocr_pdf: bool = False
    ocr_max_pages: int = 80
    ocr_only_missing_text: bool = True
    ocr_page_timeout_seconds: int = 90


@dataclass(frozen=True)
class ResourceReindexItemResult:
    resource_id: str
    package_id: str
    owner_user_id: str | None
    name: str
    status: str
    reason: str
    old_segment_count: int
    new_segment_count: int
    old_extracted_text_available: bool
    new_extracted_text_available: bool
    applied: bool
    ocr_attempted: bool = False
    ocr_page_count: int = 0
    ocr_text_page_count: int = 0
    ocr_empty_page_count: int = 0
    ocr_error_page_count: int = 0
    parser_status: str = "disabled"
    parser_error: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None


@dataclass(frozen=True)
class ResourceReindexReport:
    database_path: str
    dry_run: bool
    backup_path: str | None
    scanned_count: int
    rebuildable_count: int
    applied_count: int
    missing_source_count: int
    still_missing_text_count: int
    error_count: int
    ocr_attempted_count: int
    ocr_text_page_count: int
    ocr_error_page_count: int
    parser_success_count: int
    parser_error_count: int
    resources: list[ResourceReindexItemResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _ResourceRow:
    id: str
    package_id: str
    owner_user_id: str | None
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str
    scope_lesson_id: str | None
    concept_index_json: str
    extracted_text_available: bool
    text_content: str | None
    source_path: str | None
    old_segment_count: int


@dataclass(frozen=True)
class _OcrReindexResult:
    resource: ResourceLibraryItem | None
    reason: str
    page_count: int
    text_page_count: int
    empty_page_count: int
    error_page_count: int


def reindex_resources(options: ResourceReindexOptions) -> ResourceReindexReport:
    database_path = options.database_path
    backup_path: Path | None = None
    if options.apply and options.create_backup:
        backup_path = backup_database(database_path)

    segment_store = ResourceSegmentStore()
    results: list[ResourceReindexItemResult] = []
    with _connect(database_path, write=options.apply) as conn:
        if options.apply:
            segment_store.create_schema(conn)
        rows = _resource_rows(conn, options)
        for row in rows:
            results.append(_reindex_one(conn, row, options=options, segment_store=segment_store))

    return ResourceReindexReport(
        database_path=str(database_path),
        dry_run=not options.apply,
        backup_path=str(backup_path) if backup_path else None,
        scanned_count=len(results),
        rebuildable_count=sum(1 for result in results if result.new_segment_count > 0),
        applied_count=sum(1 for result in results if result.applied),
        missing_source_count=sum(1 for result in results if result.status == "missing_source"),
        still_missing_text_count=sum(1 for result in results if result.new_extracted_text_available is False),
        error_count=sum(1 for result in results if result.status == "error"),
        ocr_attempted_count=sum(1 for result in results if result.ocr_attempted),
        ocr_text_page_count=sum(result.ocr_text_page_count for result in results),
        ocr_error_page_count=sum(result.ocr_error_page_count for result in results),
        parser_success_count=sum(1 for result in results if result.parser_status == "success"),
        parser_error_count=sum(1 for result in results if result.parser_status == "failed"),
        resources=results,
    )


def backup_database(database_path: Path) -> Path:
    if not database_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = database_path.with_name(f"{database_path.name}.reindex-{timestamp}.bak")
    with sqlite3.connect(database_path) as source:
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
    return backup_path


def _connect(database_path: Path, *, write: bool) -> sqlite3.Connection:
    if not database_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    conn = sqlite3.connect(database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if write:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _resource_rows(conn: sqlite3.Connection, options: ResourceReindexOptions) -> list[_ResourceRow]:
    segment_count_expr = "0"
    if _table_exists(conn, "resource_segments"):
        segment_count_expr = "(SELECT COUNT(*) FROM resource_segments WHERE resource_id = resources.id)"

    where: list[str] = []
    params: list[Any] = []
    if options.resource_id:
        where.append("resources.id = ?")
        params.append(options.resource_id)
    if options.package_id:
        where.append("resources.package_id = ?")
        params.append(options.package_id)
    if options.owner_user_id:
        where.append("course_packages.owner_user_id = ?")
        params.append(options.owner_user_id)

    sql = f"""
        SELECT
            resources.*,
            course_packages.owner_user_id AS owner_user_id,
            {segment_count_expr} AS old_segment_count
        FROM resources
        JOIN course_packages ON course_packages.id = resources.package_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY course_packages.sort_order, resources.sort_order, resources.id"
    if options.limit is not None:
        sql += " LIMIT ?"
        params.append(max(options.limit, 0))

    rows = conn.execute(sql, params).fetchall()
    return [
        _ResourceRow(
            id=row["id"],
            package_id=row["package_id"],
            owner_user_id=row["owner_user_id"],
            name=row["name"],
            mime_type=row["mime_type"],
            resource_type=row["resource_type"],
            size_bytes=row["size_bytes"],
            uploaded_at=row["uploaded_at"],
            scope_lesson_id=row["scope_lesson_id"],
            concept_index_json=row["concept_index_json"],
            extracted_text_available=bool(row["extracted_text_available"]),
            text_content=row["text_content"],
            source_path=row["source_path"],
            old_segment_count=int(row["old_segment_count"] or 0),
        )
        for row in rows
    ]


def _reindex_one(
    conn: sqlite3.Connection,
    row: _ResourceRow,
    *,
    options: ResourceReindexOptions,
    segment_store: ResourceSegmentStore,
) -> ResourceReindexItemResult:
    if not row.source_path:
        return _item_result(row, status="missing_source", reason="resource has no source_path")

    source_path = Path(row.source_path)
    if not source_path.exists():
        return _item_result(row, status="missing_source", reason="source_path does not exist")

    try:
        old_chapters = _read_old_chapters(conn, row.id)
        parser_result = parse_with_external_resource_parser(source_path)
        rebuilt = build_resource_item(source_path, row.name, external_parse=parser_result)
        rebuilt = _preserve_resource_identity(rebuilt, row, old_chapters)
        ocr_rebuild = _maybe_rebuild_pdf_with_ocr(source_path, row, old_chapters, rebuilt, options)
        if ocr_rebuild.resource is not None:
            rebuilt = ocr_rebuild.resource
    except Exception as exc:
        return _item_result(row, status="error", reason=str(exc) or exc.__class__.__name__)

    status = "would_reindex" if options.apply is False else "reindexed"
    if ocr_rebuild.page_count > 0:
        reason = ocr_rebuild.reason
        if ocr_rebuild.resource is None and rebuilt.segments:
            reason = f"segments rebuilt; {ocr_rebuild.reason}"
    else:
        reason = "segments rebuilt" if rebuilt.segments else "no text evidence after reindex"
    if not rebuilt.extracted_text_available:
        status = "would_mark_no_text" if options.apply is False else "marked_no_text"

    if options.apply:
        with conn:
            _replace_resource(conn, rebuilt, segment_store)

    return ResourceReindexItemResult(
        resource_id=row.id,
        package_id=row.package_id,
        owner_user_id=row.owner_user_id,
        name=row.name,
        status=status,
        reason=reason,
        old_segment_count=row.old_segment_count,
        new_segment_count=len(rebuilt.segments),
        old_extracted_text_available=row.extracted_text_available,
        new_extracted_text_available=rebuilt.extracted_text_available,
        applied=options.apply,
        ocr_attempted=ocr_rebuild.page_count > 0,
        ocr_page_count=ocr_rebuild.page_count,
        ocr_text_page_count=ocr_rebuild.text_page_count,
        ocr_empty_page_count=ocr_rebuild.empty_page_count,
        ocr_error_page_count=ocr_rebuild.error_page_count,
        parser_status=parser_result.status if parser_result is not None else "disabled",
        parser_error=parser_result.error if parser_result is not None else None,
        parser_name=parser_result.parser.name if parser_result is not None else None,
        parser_version=parser_result.parser.version if parser_result is not None else None,
    )


def _item_result(_row: _ResourceRow, *, status: str, reason: str) -> ResourceReindexItemResult:
    return ResourceReindexItemResult(
        resource_id=_row.id,
        package_id=_row.package_id,
        owner_user_id=_row.owner_user_id,
        name=_row.name,
        status=status,
        reason=reason,
        old_segment_count=_row.old_segment_count,
        new_segment_count=0,
        old_extracted_text_available=_row.extracted_text_available,
        new_extracted_text_available=False,
        applied=False,
    )


def _maybe_rebuild_pdf_with_ocr(
    source_path: Path,
    row: _ResourceRow,
    old_chapters: list[LibraryChapter],
    rebuilt: ResourceLibraryItem,
    options: ResourceReindexOptions,
) -> _OcrReindexResult:
    if not options.ocr_pdf:
        return _empty_ocr_reindex_result()
    if source_path.suffix.lower() != ".pdf":
        return _empty_ocr_reindex_result()
    if options.ocr_only_missing_text and rebuilt.extracted_text_available:
        return _empty_ocr_reindex_result()

    ocr_results = extract_pdf_page_texts(
        source_path,
        max_pages=options.ocr_max_pages,
        page_timeout=options.ocr_page_timeout_seconds,
    )
    page_count = len(ocr_results)
    text_page_count = sum(1 for result in ocr_results if _normalize_extracted_text(result.text))
    empty_page_count = sum(1 for result in ocr_results if result.status == "empty")
    error_page_count = sum(1 for result in ocr_results if result.status == "error")
    text_pages = [
        PdfOcrPageResult(
            page_number=result.page_number,
            text=_normalize_extracted_text(result.text),
            status=result.status,
            error=result.error,
        )
        for result in ocr_results
        if _normalize_extracted_text(result.text)
    ]

    if not text_pages:
        reason = _ocr_reason(
            prefix="ocr_no_text",
            page_count=page_count,
            text_page_count=text_page_count,
            empty_page_count=empty_page_count,
            error_page_count=error_page_count,
            errors=[result.error for result in ocr_results if result.error],
        )
        return _OcrReindexResult(
            resource=None,
            reason=reason,
            page_count=page_count,
            text_page_count=text_page_count,
            empty_page_count=empty_page_count,
            error_page_count=error_page_count,
        )

    resource = _build_ocr_pdf_resource(row, old_chapters, text_pages)
    reason = _ocr_reason(
        prefix="ocr_segments_rebuilt",
        page_count=page_count,
        text_page_count=text_page_count,
        empty_page_count=empty_page_count,
        error_page_count=error_page_count,
        errors=[result.error for result in ocr_results if result.error],
    )
    return _OcrReindexResult(
        resource=resource,
        reason=reason,
        page_count=page_count,
        text_page_count=text_page_count,
        empty_page_count=empty_page_count,
        error_page_count=error_page_count,
    )


def _empty_ocr_reindex_result() -> _OcrReindexResult:
    return _OcrReindexResult(
        resource=None,
        reason="",
        page_count=0,
        text_page_count=0,
        empty_page_count=0,
        error_page_count=0,
    )


def _build_ocr_pdf_resource(
    row: _ResourceRow,
    old_chapters: list[LibraryChapter],
    text_pages: list[PdfOcrPageResult],
) -> ResourceLibraryItem:
    title = Path(row.name).stem
    joined_text = "\n\n".join(page.text for page in text_pages if page.text)
    summary = "OCR 已按实际页码抽取 PDF 正文片段。"
    snippet = _summary_snippet(joined_text)
    if snippet:
        summary = f"{summary}内容摘要：{snippet}"
    outline = _preserve_chapter_ids(
        [
            LibraryChapter(
                title=title,
                summary=summary,
                keywords=_keywords_from_text(f"{title}\n{joined_text}"),
                locator_hint=f"source=pdf_ocr;pages={len(text_pages)}",
                order_index=0,
                scan_strategy="fulltext_match",
            )
        ],
        old_chapters,
    )
    resource = ResourceLibraryItem(
        id=row.id,
        name=row.name,
        mime_type=row.mime_type,
        resource_type=row.resource_type,
        size_bytes=row.size_bytes,
        uploaded_at=row.uploaded_at,
        scope_lesson_id=row.scope_lesson_id,
        outline=outline,
        concept_index=_build_concept_index(outline),
        extracted_text_available=False,
        text_content=None,
        source_path=row.source_path,
    )
    resource.segments = _build_ocr_resource_segments(resource, outline[0], text_pages)
    resource.extracted_text_available = resource_has_text_evidence(resource)
    return resource


def _build_ocr_resource_segments(
    resource: ResourceLibraryItem,
    chapter: LibraryChapter,
    text_pages: list[PdfOcrPageResult],
) -> list[ResourceSegment]:
    parser_spec = current_resource_parser_spec()
    heading_path = chapter.path or [chapter.title]
    segments: list[ResourceSegment] = []
    for page in text_pages:
        for text in _split_resource_text_into_segments(page.text):
            text_hash = _resource_segment_hash(text)
            order_index = len(segments)
            stable_seed = f"{resource.id}:{chapter.id}:ocr:{order_index}:{page.page_number}:{text_hash}"
            segment_id = f"rseg_{hashlib.sha1(stable_seed.encode('utf-8')).hexdigest()[:12]}"
            segments.append(
                ResourceSegment(
                    segment_id=segment_id,
                    resource_id=resource.id,
                    chapter_id=chapter.id,
                    heading_path=heading_path,
                    order_index=order_index,
                    text=text,
                    text_hash=text_hash,
                    keywords=_keywords_from_text(f"{' '.join(heading_path)}\n{text}")[:12],
                    page_range=str(page.page_number),
                    parser_name=parser_spec.name,
                    parser_version=parser_spec.version,
                    text_source="ocr",
                )
            )
    return _link_segments(segments)


def _link_segments(segments: list[ResourceSegment]) -> list[ResourceSegment]:
    for index, segment in enumerate(segments):
        segments[index] = segment.model_copy(
            update={
                "before_segment_id": segments[index - 1].segment_id if index > 0 else None,
                "after_segment_id": segments[index + 1].segment_id if index + 1 < len(segments) else None,
            }
        )
    return segments


def _summary_snippet(text: str, *, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", _normalize_extracted_text(text)).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}..."


def _ocr_reason(
    *,
    prefix: str,
    page_count: int,
    text_page_count: int,
    empty_page_count: int,
    error_page_count: int,
    errors: list[str | None],
) -> str:
    parts = [
        prefix,
        f"pages={page_count}",
        f"text_pages={text_page_count}",
        f"empty_pages={empty_page_count}",
        f"error_pages={error_page_count}",
    ]
    first_error = next((error for error in errors if error), None)
    if first_error:
        parts.append(f"first_error={first_error[:120]}")
    return " ".join(parts)


def _read_old_chapters(conn: sqlite3.Connection, resource_id: str) -> list[LibraryChapter]:
    rows = conn.execute(
        """
        SELECT *
        FROM resource_chapters
        WHERE resource_id = ?
        ORDER BY sort_order, id
        """,
        (resource_id,),
    ).fetchall()
    return [
        LibraryChapter(
            id=row["id"],
            title=row["title"],
            level=row["level"],
            page_range=row["page_range"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            summary=row["summary"],
            keywords=_loads(row["keywords_json"], []),
            prerequisites=_loads(row["prerequisites_json"], []),
            parent_id=row["parent_id"],
            parent_title=row["parent_title"],
            path=_loads(row["path_json"], []),
            locator_hint=row["locator_hint"],
            order_index=row["order_index"],
            scan_strategy=row["scan_strategy"],
        )
        for row in rows
    ]


def _preserve_resource_identity(
    rebuilt: ResourceLibraryItem,
    row: _ResourceRow,
    old_chapters: list[LibraryChapter],
) -> ResourceLibraryItem:
    outline = _preserve_chapter_ids(rebuilt.outline, old_chapters)
    resource = rebuilt.model_copy(
        update={
            "id": row.id,
            "uploaded_at": row.uploaded_at,
            "scope_lesson_id": row.scope_lesson_id,
            "outline": outline,
            "concept_index": _build_concept_index(outline),
            "segments": [],
            "source_path": row.source_path,
        }
    )
    resource.segments = build_resource_segments(resource)
    resource.extracted_text_available = resource_has_text_evidence(resource)
    return resource


def _preserve_chapter_ids(
    new_chapters: list[LibraryChapter],
    old_chapters: list[LibraryChapter],
) -> list[LibraryChapter]:
    old_by_title: dict[tuple[str, int], list[LibraryChapter]] = {}
    for chapter in old_chapters:
        old_by_title.setdefault((_normalize_title(chapter.title), chapter.level), []).append(chapter)

    used_old_ids: set[str] = set()
    preserved: list[LibraryChapter] = []
    for index, chapter in enumerate(new_chapters):
        replacement_id = None
        title_matches = old_by_title.get((_normalize_title(chapter.title), chapter.level), [])
        while title_matches:
            candidate = title_matches.pop(0)
            if candidate.id not in used_old_ids:
                replacement_id = candidate.id
                break
        if replacement_id is None and index < len(old_chapters):
            candidate = old_chapters[index]
            if candidate.id not in used_old_ids and candidate.level == chapter.level:
                replacement_id = candidate.id
        if replacement_id is not None:
            used_old_ids.add(replacement_id)
            chapter = chapter.model_copy(update={"id": replacement_id})
        preserved.append(chapter)

    return _attach_outline_hierarchy(preserved)


def _attach_outline_hierarchy(chapters: list[LibraryChapter]) -> list[LibraryChapter]:
    stack: list[LibraryChapter] = []
    enriched: list[LibraryChapter] = []
    for chapter in chapters:
        while stack and stack[-1].level >= chapter.level:
            stack.pop()
        parent = stack[-1] if stack else None
        path = [*(parent.path if parent else []), chapter.title]
        enriched_chapter = chapter.model_copy(
            update={
                "parent_id": parent.id if parent else None,
                "parent_title": parent.title if parent else None,
                "path": path,
            }
        )
        enriched.append(enriched_chapter)
        stack.append(enriched_chapter)
    return enriched


def _build_concept_index(chapters: list[LibraryChapter]) -> dict[str, list[str]]:
    concept_index: dict[str, list[str]] = {}
    for chapter in chapters:
        for keyword in [*chapter.keywords, *_keywords_from_text(" ".join(chapter.path))]:
            concept_index.setdefault(keyword, []).append(chapter.id)
    return concept_index


def _replace_resource(
    conn: sqlite3.Connection,
    resource: ResourceLibraryItem,
    segment_store: ResourceSegmentStore,
) -> None:
    conn.execute(
        """
        UPDATE resources
        SET mime_type = ?,
            resource_type = ?,
            size_bytes = ?,
            concept_index_json = ?,
            extracted_text_available = ?,
            text_content = ?,
            source_path = ?
        WHERE id = ?
        """,
        (
            resource.mime_type,
            resource.resource_type,
            resource.size_bytes,
            _dumps(resource.concept_index),
            int(resource.extracted_text_available),
            resource.text_content,
            resource.source_path,
            resource.id,
        ),
    )
    conn.execute("DELETE FROM resource_chapters WHERE resource_id = ?", (resource.id,))
    for chapter_index, chapter in enumerate(resource.outline):
        conn.execute(
            """
            INSERT INTO resource_chapters(
                id, resource_id, sort_order, title, level, page_range, page_start, page_end,
                summary, keywords_json, prerequisites_json, parent_id, parent_title, path_json,
                locator_hint, order_index, scan_strategy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chapter.id,
                resource.id,
                chapter_index,
                chapter.title,
                chapter.level,
                chapter.page_range,
                chapter.page_start,
                chapter.page_end,
                chapter.summary,
                _dumps(chapter.keywords),
                _dumps(chapter.prerequisites),
                chapter.parent_id,
                chapter.parent_title,
                _dumps(chapter.path),
                chapter.locator_hint,
                chapter.order_index,
                chapter.scan_strategy,
            ),
        )
    segment_store.replace_segments(conn, resource)


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).lower()


def _keywords_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text):
        keyword = token.lower()
        if keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords[:12]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)
