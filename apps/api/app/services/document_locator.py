from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.models import DocumentEvidence, ResourceLibraryItem
from app.services.document_index_store import DocumentIndexStore, loads


STRUCTURED_REF_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.．]\d{1,3}){1,3})(?!\d)")
PAGE_REF_RE = re.compile(r"(?:第\s*)?(\d{1,5})\s*页")
DOCUMENT_HINT_RE = re.compile(
    r"(原文|目录|章节|小节|页面|页码|呈现|展示|显示|定位|找到|跳到|第.{0,10}[章节页])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocatedBlock:
    resource: ResourceLibraryItem
    block: sqlite3.Row
    confidence: float
    trace: list[str]


def looks_like_document_request(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if not compact:
        return False
    return bool(DOCUMENT_HINT_RE.search(compact) or STRUCTURED_REF_RE.search(compact) or PAGE_REF_RE.search(compact))


def locate_document_evidence(
    database_path: Path,
    *,
    resources: list[ResourceLibraryItem],
    query: str,
    limit: int = 3,
) -> list[DocumentEvidence]:
    ready_resources = [resource for resource in resources if resource.index_status == "ready"]
    if not ready_resources:
        return []
    by_id = {resource.id: resource for resource in ready_resources}
    store = DocumentIndexStore()
    with _connect(database_path) as conn:
        store.create_schema(conn)
        located: list[LocatedBlock] = []
        for resource in ready_resources:
            located.extend(_locate_in_resource(conn, store, resource, query))
        located.extend(_fts_locate(conn, store, by_id, query))

    located.sort(key=lambda item: item.confidence, reverse=True)
    deduped: list[LocatedBlock] = []
    seen: set[tuple[str, str]] = set()
    for item in located:
        key = (item.resource.id, str(item.block["block_id"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return [_evidence_from_block(item) for item in deduped]


def document_evidence_from_id(
    database_path: Path,
    *,
    resources: list[ResourceLibraryItem],
    evidence_id: str,
) -> DocumentEvidence | None:
    decoded = _decode_evidence_id(evidence_id)
    if decoded is None:
        return None
    resource_id, block_id = decoded
    resource = next((item for item in resources if item.id == resource_id), None)
    if resource is None:
        return None
    with _connect(database_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM resource_document_blocks
            WHERE resource_id = ? AND block_id = ?
            """,
            (resource_id, block_id),
        ).fetchone()
    if row is None:
        return None
    return _evidence_from_block(LocatedBlock(resource=resource, block=row, confidence=float(row["confidence"]), trace=["已从证据 ID 重新读取索引块。"]))


def queued_resource_message(resources: list[ResourceLibraryItem]) -> str | None:
    pending = [item for item in resources if item.index_status in {"queued", "processing"}]
    if pending:
        names = "、".join(item.name for item in pending[:3])
        return f"资料“{names}”还在后台索引中。索引完成后我就能按目录、页码和正文为你定位。"
    failed = [item for item in resources if item.index_status in {"failed", "no_text"}]
    if failed and not any(item.index_status == "ready" for item in resources):
        names = "、".join(item.name for item in failed[:3])
        return f"资料“{names}”目前没有可用正文索引：{failed[0].index_message or failed[0].index_status}。"
    return None


def _locate_in_resource(
    conn: sqlite3.Connection,
    store: DocumentIndexStore,
    resource: ResourceLibraryItem,
    query: str,
) -> list[LocatedBlock]:
    pages = store.read_pages(conn, resource.id)
    blocks = store.read_blocks(conn, resource.id)
    located: list[LocatedBlock] = []
    structured = _structured_label(query)
    if structured:
        toc_page = _toc_target_page(pages, structured)
        if toc_page is not None:
            block = _block_for_page(blocks, toc_page.actual_page)
            if block is not None:
                located.append(
                    LocatedBlock(
                        resource=resource,
                        block=block,
                        confidence=0.94 if toc_page.verified else 0.82,
                        trace=toc_page.trace,
                    )
                )
        for block in blocks:
            if structured in _normalize_dots(str(block["text"])) or structured in _normalize_dots(" ".join(loads(block["heading_path_json"], []))):
                located.append(
                    LocatedBlock(
                        resource=resource,
                        block=block,
                        confidence=0.86,
                        trace=[f"正文块直接包含结构编号 {structured}。"],
                    )
                )

    page_number = _page_reference(query)
    if page_number is not None:
        actual_page = _actual_page_for_printed_page(pages, page_number) or page_number
        block = _block_for_page(blocks, actual_page)
        if block is not None:
            located.append(
                LocatedBlock(
                    resource=resource,
                    block=block,
                    confidence=0.78,
                    trace=[f"用户请求页码 {page_number}；映射到实际 PDF 第 {actual_page} 页。"],
                )
            )

    terms = _query_terms(query)
    if terms:
        for block in blocks:
            score = _term_score(terms, " ".join(loads(block["heading_path_json"], [])), str(block["text"]))
            if score <= 0:
                continue
            located.append(
                LocatedBlock(
                    resource=resource,
                    block=block,
                    confidence=min(0.72, 0.32 + score),
                    trace=["按标题路径和正文关键词匹配到候选正文块。"],
                )
            )
    return located


def _fts_locate(
    conn: sqlite3.Connection,
    store: DocumentIndexStore,
    resources: dict[str, ResourceLibraryItem],
    query: str,
) -> list[LocatedBlock]:
    located: list[LocatedBlock] = []
    for row in store.search_blocks(conn, query, limit=8):
        resource = resources.get(str(row["resource_id"]))
        if resource is None:
            continue
        located.append(
            LocatedBlock(
                resource=resource,
                block=row,
                confidence=0.62,
                trace=["FTS 全文索引召回该正文块，作为翻页定位的辅助候选。"],
            )
        )
    return located


@dataclass(frozen=True)
class TocTargetPage:
    actual_page: int
    printed_page: int
    verified: bool
    trace: list[str]


def _toc_target_page(pages: list[sqlite3.Row], label: str) -> TocTargetPage | None:
    toc_limit = min(25, len(pages))
    normalized_label = _normalize_dots(label)
    for page in pages[:toc_limit]:
        for line in str(page["text"]).splitlines():
            normalized_line = _normalize_dots(line)
            if normalized_label not in normalized_line:
                continue
            match = re.search(r"(\d{1,5})\s*$", normalized_line)
            if match is None:
                continue
            printed_page = int(match.group(1))
            actual_page = _actual_page_for_printed_page(pages, printed_page)
            if actual_page is None:
                continue
            verified = _window_contains_label_or_title(pages, actual_page, normalized_label, normalized_line)
            trace = [
                f"目录候选：第 {page['page_number']} 页目录行写着 {label} -> 印刷页 {printed_page}。",
                f"页码锚点：印刷页 {printed_page} 映射到实际 PDF 第 {actual_page} 页。",
            ]
            if verified:
                trace.append("正文校验：目标页附近出现目标编号或标题关键词。")
            else:
                trace.append("正文校验：目标页附近未强命中标题，保留为低置信页码候选。")
            return TocTargetPage(actual_page=actual_page, printed_page=printed_page, verified=verified, trace=trace)
    return None


def _actual_page_for_printed_page(pages: list[sqlite3.Row], printed_page: int) -> int | None:
    for page in pages:
        if page["printed_page"] == printed_page:
            return int(page["page_number"])
    offsets: dict[int, int] = {}
    for page in pages:
        if page["printed_page"] is None:
            continue
        offset = int(page["page_number"]) - int(page["printed_page"])
        offsets[offset] = offsets.get(offset, 0) + 1
    if not offsets:
        return None
    offset, support = max(offsets.items(), key=lambda item: item[1])
    if support < 2:
        return None
    actual = printed_page + offset
    if actual < 1:
        return None
    return actual


def _window_contains_label_or_title(pages: list[sqlite3.Row], actual_page: int, label: str, toc_line: str) -> bool:
    title_terms = [term for term in _query_terms(toc_line) if not term.isdigit()]
    for page in pages:
        number = int(page["page_number"])
        if number < actual_page - 3 or number > actual_page + 3:
            continue
        text = _normalize_dots(str(page["text"]))
        if label in text:
            return True
        if title_terms and any(term in text for term in title_terms[:4]):
            return True
    return False


def _block_for_page(blocks: list[sqlite3.Row], page_number: int) -> sqlite3.Row | None:
    for block in blocks:
        start = block["page_start"]
        end = block["page_end"] or start
        if start is not None and int(start) <= page_number <= int(end):
            return block
    return None


def _evidence_from_block(item: LocatedBlock) -> DocumentEvidence:
    block = item.block
    page_range = _page_range(block["page_start"], block["page_end"])
    printed_page_range = _page_range(block["printed_page_start"], block["printed_page_end"])
    heading_path = loads(block["heading_path_json"], [])
    text = str(block["text"] or "").strip()
    evidence_id = _encode_evidence_id(item.resource.id, str(block["block_id"]))
    preview_url = f"/api/resources/{item.resource.id}/pages/{block['page_start']}/preview" if block["page_start"] else None
    return DocumentEvidence(
        evidence_id=evidence_id,
        resource_id=item.resource.id,
        resource_name=item.resource.name,
        page_range=page_range,
        printed_page_range=printed_page_range,
        heading_path=heading_path,
        excerpt=_excerpt(text),
        confidence=round(float(item.confidence), 3),
        trace=item.trace,
        preview_url=preview_url,
        available_actions=["insert_original", "reference_generate"],
        text_source=str(block["text_source"] or "source_file"),
        full_text=text,
    )


def _encode_evidence_id(resource_id: str, block_id: str) -> str:
    seed = f"{resource_id}|{block_id}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{resource_id}:{block_id}:{digest}"


def _decode_evidence_id(evidence_id: str) -> tuple[str, str] | None:
    parts = evidence_id.split(":")
    if len(parts) != 3:
        return None
    resource_id, block_id, digest = parts
    if hashlib.sha1(f"{resource_id}|{block_id}".encode("utf-8")).hexdigest()[:10] != digest:
        return None
    return resource_id, block_id


def _structured_label(text: str) -> str | None:
    match = STRUCTURED_REF_RE.search(text or "")
    if match is None:
        return None
    return _normalize_dots(match.group(1))


def _page_reference(text: str) -> int | None:
    match = PAGE_REF_RE.search(text or "")
    if match is None:
        return None
    return int(match.group(1))


def _normalize_dots(text: str) -> str:
    return text.replace("．", ".")


def _query_terms(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", (text or "").lower())))[:12]


def _term_score(terms: list[str], heading: str, body: str) -> float:
    hay_heading = heading.lower()
    hay_body = body.lower()
    score = 0.0
    for term in terms:
        if term in hay_heading:
            score += 0.16
        if term in hay_body:
            score += 0.08
    return score


def _page_range(start: object, end: object) -> str | None:
    if start is None:
        return None
    start_int = int(start)
    end_int = int(end or start_int)
    return str(start_int) if start_int == end_int else f"{start_int}-{end_int}"


def _excerpt(text: str, *, limit: int = 900) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}..."


def _connect(database_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
