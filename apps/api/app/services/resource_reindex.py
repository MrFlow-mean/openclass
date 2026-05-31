from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import LibraryChapter, ResourceLibraryItem
from app.services.resource_library import build_resource_item, build_resource_segments, resource_has_text_evidence
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
        rebuilt = build_resource_item(source_path, row.name)
        rebuilt = _preserve_resource_identity(rebuilt, row, old_chapters)
    except Exception as exc:
        return _item_result(row, status="error", reason=str(exc) or exc.__class__.__name__)

    status = "would_reindex" if options.apply is False else "reindexed"
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
