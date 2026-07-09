from __future__ import annotations

import re
from typing import Any

from app.models import (
    BoardTaskRequirementSheet,
    EvidenceBundle,
    EvidencePurpose,
    LearningRequirementSheet,
    RetrievalEvidence,
)
from app.services.open_notebook_adapter import (
    OpenNotebookAdapter,
    OpenNotebookAdapterError,
    open_notebook_adapter,
)
from app.services.source_evidence_store import SourceEvidenceStore, source_evidence_store
from app.services.source_structure_store import SourceStructureStore, source_structure_store


SOURCE_INTENT_PATTERNS = (
    "资料",
    "文件",
    "上传",
    "网页",
    "链接",
    "url",
    "URL",
    "source",
    "sources",
    "file",
    "document",
    "reference",
    "视频",
    "字幕",
    "音频",
    "youtube",
    "YouTube",
)

CHAT_CHUNK_LIMIT = 4
BOARD_CHUNK_LIMIT = 8
CHAT_TOKEN_BUDGET = 2000
BOARD_TOKEN_BUDGET = 6000


class ResourceResolver:
    def __init__(
        self,
        *,
        adapter: OpenNotebookAdapter = open_notebook_adapter,
        store: SourceEvidenceStore = source_evidence_store,
        structure_store: SourceStructureStore | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.structure_store = structure_store or _structure_store_for_source_store(store)

    def should_use_sources(self, message: str) -> bool:
        lowered = message.lower()
        return any(pattern.lower() in lowered for pattern in SOURCE_INTENT_PATTERNS)

    def resolve_for_learning_requirement(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        requirements: LearningRequirementSheet | None,
        requirement_run_id: str | None = None,
        purpose: EvidencePurpose = "board_generation",
    ) -> EvidenceBundle | None:
        query = _learning_query(user_message=user_message, requirements=requirements)
        return self._resolve(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            requirement_run_id=requirement_run_id,
        )

    def resolve_for_board_task(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        user_message: str,
        board_task: BoardTaskRequirementSheet,
        board_task_run_id: str | None = None,
        purpose: EvidencePurpose = "board_edit",
    ) -> EvidenceBundle | None:
        query = _board_task_query(user_message=user_message, board_task=board_task)
        return self._resolve(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            board_task_run_id=board_task_run_id,
        )

    def latest_confirmed_bundle(
        self,
        *,
        owner_user_id: str,
        lesson_id: str,
        purpose: EvidencePurpose | None = None,
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
    ) -> EvidenceBundle | None:
        return self.store.latest_bundle(
            owner_user_id=owner_user_id,
            lesson_id=lesson_id,
            status="confirmed",
            purpose=purpose,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
        )

    def _resolve(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        query: str,
        purpose: EvidencePurpose,
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
    ) -> EvidenceBundle | None:
        notebook_id = self.store.get_notebook_id(owner_user_id=owner_user_id, package_id=package_id)
        ready_sources = self.store.ready_sources(owner_user_id=owner_user_id, package_id=package_id)
        if not ready_sources or not query.strip():
            return None
        limit = BOARD_CHUNK_LIMIT if purpose in {"board_generation", "board_edit"} else CHAT_CHUNK_LIMIT
        token_budget = BOARD_TOKEN_BUDGET if purpose in {"board_generation", "board_edit"} else CHAT_TOKEN_BUDGET
        chapter_evidence = self._resolve_verified_chapter(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
        )
        if chapter_evidence:
            return self._save_bundle(
                owner_user_id=owner_user_id,
                package_id=package_id,
                lesson_id=lesson_id,
                query=query,
                purpose=purpose,
                evidence=chapter_evidence,
                requirement_run_id=requirement_run_id,
                board_task_run_id=board_task_run_id,
                metadata={"resolver": "source_structure_index", "retrieval_mode": "verified_chapter"},
            )
        open_notebook_source_ids = [source.open_notebook_source_id for source in ready_sources if source.open_notebook_source_id]
        if notebook_id and open_notebook_source_ids:
            try:
                raw_results = self.adapter.search(
                    notebook_id=notebook_id,
                    query=query,
                    limit=limit * 2,
                    source_ids=open_notebook_source_ids,
                )
            except OpenNotebookAdapterError:
                raw_results = []
            evidence = self._normalize_results(
                owner_user_id=owner_user_id,
                package_id=package_id,
                raw_results=raw_results,
                limit=limit,
                token_budget=token_budget,
                allowed_source_ids=set(open_notebook_source_ids),
            )
            if evidence:
                return self._save_bundle(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    lesson_id=lesson_id,
                    query=query,
                    purpose=purpose,
                    evidence=evidence,
                    requirement_run_id=requirement_run_id,
                    board_task_run_id=board_task_run_id,
                    metadata={"resolver": "open_notebook_search", "retrieval_mode": "semantic_search"},
                )
        local_evidence = self.structure_store.chunk_evidence_search(
            owner_user_id=owner_user_id,
            package_id=package_id,
            query=query,
            limit=limit,
            token_budget=token_budget,
        )
        if not local_evidence:
            return None
        return self._save_bundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            query=query,
            purpose=purpose,
            evidence=local_evidence,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
            metadata={"resolver": "source_structure_index", "retrieval_mode": "local_chunk_search"},
        )

    def _save_bundle(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        lesson_id: str,
        query: str,
        purpose: EvidencePurpose,
        evidence: list[RetrievalEvidence],
        requirement_run_id: str | None = None,
        board_task_run_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> EvidenceBundle:
        context_text = format_evidence_context(evidence)
        bundle = EvidenceBundle(
            owner_user_id=owner_user_id,
            package_id=package_id,
            lesson_id=lesson_id,
            requirement_run_id=requirement_run_id,
            board_task_run_id=board_task_run_id,
            purpose=purpose,
            status="candidate",
            query=query,
            evidence_items=evidence,
            context_text=context_text,
            token_count=sum(item.token_count for item in evidence),
            confirmed_by_user=False,
            metadata=metadata or {},
        )
        return self.store.save_bundle(bundle)

    def _resolve_verified_chapter(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        query: str,
        limit: int,
        token_budget: int,
    ) -> list[RetrievalEvidence]:
        chapter_id = _explicit_source_chapter_id(query)
        if chapter_id:
            return self.structure_store.chapter_evidence_by_id(
                owner_user_id=owner_user_id,
                package_id=package_id,
                chapter_id=chapter_id,
                limit=limit,
                token_budget=token_budget,
            )
        number = _explicit_chapter_number(query)
        if not number:
            return []
        return self.structure_store.chapter_evidence_by_number(
            owner_user_id=owner_user_id,
            package_id=package_id,
            normalized_number=number,
            limit=limit,
            token_budget=token_budget,
        )

    def _normalize_results(
        self,
        *,
        owner_user_id: str,
        package_id: str,
        raw_results: list[dict[str, Any]],
        limit: int,
        token_budget: int,
        allowed_source_ids: set[str],
    ) -> list[RetrievalEvidence]:
        items: list[RetrievalEvidence] = []
        seen: set[str] = set()
        current_tokens = 0
        for raw in raw_results:
            source_id = _text(raw, "source_id", "source", "parent_id")
            if not source_id or source_id not in allowed_source_ids:
                continue
            source_record = (
                self.store.get_source_by_open_notebook_id(
                    owner_user_id=owner_user_id,
                    package_id=package_id,
                    open_notebook_source_id=source_id,
                )
                if source_id
                else None
            )
            excerpt = _text(raw, "excerpt", "text", "content", "snippet", "chunk")
            expanded = _text(raw, "expanded_text", "context", "content", "text") or excerpt
            if not excerpt and expanded:
                excerpt = _compact(expanded, 360)
            chunk_id = _text(raw, "chunk_id", "id", "embedding_id")
            dedupe_key = f"{source_id}:{chunk_id}:{excerpt[:80]}"
            if not excerpt or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            expanded_text = _trim_to_token_budget(expanded or excerpt, max_tokens=900)
            token_count = _estimate_tokens(expanded_text or excerpt)
            if current_tokens and current_tokens + token_count > token_budget:
                break
            current_tokens += token_count
            items.append(
                RetrievalEvidence(
                    source_ingestion_id=source_record.id if source_record else "",
                    open_notebook_source_id=source_id,
                    source_title=_text(raw, "source_title", "title") or (source_record.title if source_record else ""),
                    source_uri=_text(raw, "source_uri", "url") or (source_record.source_uri if source_record else None),
                    section_path=_section_path(raw),
                    page_range=_page_range(raw),
                    chunk_ids=[chunk_id] if chunk_id else [],
                    excerpt=excerpt,
                    expanded_text=expanded_text,
                    relevance_score=_score(raw),
                    reason=_text(raw, "reason", "match_reason"),
                    token_count=token_count,
                    metadata=raw,
                )
            )
            if len(items) >= limit:
                break
        return items


def format_evidence_context(items: list[RetrievalEvidence]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        location_parts = [
            item.source_title or "未命名资料",
            " > ".join(item.section_path) if item.section_path else "",
            item.page_range,
        ]
        location = " / ".join(part for part in location_parts if part)
        lines.append(
            f"[资料证据 {index}] {location}\n"
            f"来源ID: {item.source_ingestion_id or item.open_notebook_source_id}\n"
            f"摘录: {item.expanded_text or item.excerpt}"
        )
    return "\n\n".join(lines)


def evidence_metadata(bundle: EvidenceBundle | None) -> dict[str, object]:
    if bundle is None:
        return {
            "evidence_bundle_id": None,
            "source_ids": [],
            "chunk_ids": [],
            "confirmed_by_user": False,
        }
    source_ids = [
        item.source_ingestion_id or item.open_notebook_source_id
        for item in bundle.evidence_items
        if item.source_ingestion_id or item.open_notebook_source_id
    ]
    chunk_ids = [chunk_id for item in bundle.evidence_items for chunk_id in item.chunk_ids]
    chapter_ids = [item.chapter_id for item in bundle.evidence_items if item.chapter_id]
    return {
        "evidence_bundle_id": bundle.id,
        "source_ids": source_ids,
        "chunk_ids": chunk_ids,
        "chapter_ids": chapter_ids,
        "confirmed_by_user": bundle.confirmed_by_user,
        "evidence_bundle_status": bundle.status,
        "evidence_purpose": bundle.purpose,
    }


def _learning_query(*, user_message: str, requirements: LearningRequirementSheet | None) -> str:
    parts = [user_message]
    if requirements is not None:
        parts.extend(
            [
                requirements.learning_goal,
                requirements.theme,
                " ".join(requirements.current_questions),
                requirements.output_preference,
                requirements.success_criteria,
            ]
        )
    return _compact(" ".join(part for part in parts if part), 900)


def _board_task_query(*, user_message: str, board_task: BoardTaskRequirementSheet) -> str:
    parts = [
        user_message,
        board_task.question_or_topic,
        board_task.target_hint,
        board_task.requested_action or "",
    ]
    if board_task.interaction_rule_draft is not None:
        parts.append(board_task.interaction_rule_draft.reference_instruction)
        parts.append(board_task.interaction_rule_draft.interaction_goal)
    return _compact(" ".join(part for part in parts if part), 900)


def _text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    nested = raw.get("source")
    if isinstance(nested, dict):
        nested_text = _text(nested, *keys)
        if nested_text:
            return nested_text
    return ""


def _section_path(raw: dict[str, Any]) -> list[str]:
    value = raw.get("section_path") or raw.get("heading_path") or raw.get("path")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"\s*[>/]\s*", value) if part.strip()]
    return []


def _page_range(raw: dict[str, Any]) -> str:
    value = _text(raw, "page_range", "pages")
    if value:
        return value
    page = _text(raw, "page", "page_no", "page_number")
    return f"p. {page}" if page else ""


def _score(raw: dict[str, Any]) -> float:
    for key in ("score", "relevance_score", "similarity"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _explicit_chapter_number(query: str) -> str:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,8})(?![\d.])", query)
    if not match:
        return ""
    return ".".join(str(int(part)) for part in match.group(1).split(".") if part.isdigit())


def _explicit_source_chapter_id(query: str) -> str:
    match = re.search(r"\bsource_chapter_id\s*=\s*([A-Za-z0-9_-]{8,})\b", query)
    return match.group(1) if match else ""


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ascii_chars = sum(1 for char in stripped if ord(char) < 128)
    non_ascii_chars = len(stripped) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def _trim_to_token_budget(text: str, *, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text.strip()
    # Conservative char trim; final token estimate is approximate and service-side only.
    return text.strip()[: max_tokens * 3].rstrip()


def _compact(text: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", text).strip()
    return compacted if len(compacted) <= limit else compacted[: limit - 1].rstrip() + "…"


def _structure_store_for_source_store(store: SourceEvidenceStore) -> SourceStructureStore:
    if getattr(store, "_path", None) is None:
        return source_structure_store
    return SourceStructureStore(store.path)


resource_resolver = ResourceResolver()
