from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.models import SourceChapter, SourceCodexRun, SourceDocumentPart, SourceDocumentPartKind


class SourceCatalogError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        thread_id: str = "",
        turn_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.thread_id = thread_id
        self.turn_id = turn_id


class SourceCatalogPartProposal(BaseModel):
    kind: SourceDocumentPartKind = "unknown"
    title: str = Field(default="", max_length=240)
    page_start: int = Field(ge=1)
    page_end_exclusive: int = Field(ge=2)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    evidence_page_numbers: list[int] = Field(default_factory=list, max_length=20)


class SourceCatalogDirectoryNode(BaseModel):
    local_key: str = Field(min_length=1, max_length=120)
    candidate_id: str = Field(default="", max_length=120)
    decision: Literal["keep", "reject"] = "keep"
    parent_local_key: str = Field(default="", max_length=120)
    number: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=300)
    level: int = Field(default=1, ge=1, le=12)
    order_index: int = Field(default=0, ge=0)
    body_heading: str = Field(default="", max_length=500)
    body_page_hint: int | None = Field(default=None, ge=1)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    evidence_page_numbers: list[int] = Field(default_factory=list, max_length=20)


class SourceCatalogPlan(BaseModel):
    input_manifest_hash: str
    document_parts: list[SourceCatalogPartProposal] = Field(default_factory=list, max_length=200)
    directory_nodes: list[SourceCatalogDirectoryNode] = Field(default_factory=list, max_length=5_000)
    uncertainties: list[str] = Field(default_factory=list, max_length=100)


class SourceChapterAnchorProposal(BaseModel):
    directory_node_key: str = Field(min_length=1, max_length=120)
    status: Literal["located", "not_found", "ambiguous"] = "not_found"
    page_no: int | None = Field(default=None, ge=1)
    heading_excerpt: str = Field(default="", max_length=500)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)


class SourceShardHeadingCandidate(BaseModel):
    page_no: int = Field(ge=1)
    heading_excerpt: str = Field(min_length=1, max_length=500)
    level: int = Field(default=1, ge=1, le=12)


class SourceShardResult(BaseModel):
    shard_id: str
    plan_hash: str
    input_hash: str
    anchors: list[SourceChapterAnchorProposal] = Field(default_factory=list, max_length=5_000)
    unlisted_headings: list[SourceShardHeadingCandidate] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True)
class SourcePageUnit:
    page_no: int
    text: str
    start_offset: int
    end_offset: int
    content_start_offset: int


@dataclass(frozen=True)
class SourceCatalogImagePage:
    page_no: int
    data_url: str
    sha256: str


@dataclass(frozen=True)
class SourceShard:
    shard_id: str
    page_start: int
    page_end_exclusive: int
    pages: tuple[SourcePageUnit, ...]
    nodes: tuple[SourceCatalogDirectoryNode, ...]
    input_hash: str


@dataclass(frozen=True)
class SourceCatalogResult:
    run: SourceCodexRun
    parts: list[SourceDocumentPart]
    chapters: list[SourceChapter]
    warnings: list[str]
