from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import AIModelSelection, RetrievalEvidence, new_id, now_iso


ResearchSearchMode = Literal["text", "semantic", "hybrid"]
ResearchContextMode = Literal["retrieval", "full", "notes", "off"]
ResearchMessageRole = Literal["user", "assistant", "system"]
ResearchArtifactStatus = Literal["queued", "generating", "ready", "failed"]
ResearchArtifactKind = Literal["insight", "summary", "study_guide", "faq", "timeline", "custom", "podcast"]


class ResearchCitation(BaseModel):
    source_ingestion_id: str = ""
    source_title: str = ""
    source_uri: str | None = None
    chapter_id: str = ""
    section_path: list[str] = Field(default_factory=list)
    page_range: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""


class ResearchNote(BaseModel):
    id: str = Field(default_factory=lambda: new_id("note"))
    owner_user_id: str = ""
    package_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    citations: list[ResearchCitation] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchNoteCreate(BaseModel):
    title: str = ""
    content: str
    tags: list[str] = Field(default_factory=list)
    citations: list[ResearchCitation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchNoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    citations: list[ResearchCitation] | None = None
    metadata: dict[str, Any] | None = None


class ResearchSearchRequest(BaseModel):
    query: str
    mode: ResearchSearchMode = "hybrid"
    source_ingestion_ids: list[str] = Field(default_factory=list)
    include_notes: bool = True
    limit: int = Field(default=12, ge=1, le=50)
    token_budget: int = Field(default=6000, ge=256, le=24000)


class ResearchSearchResult(BaseModel):
    kind: Literal["source", "note"]
    score: float = 0.0
    evidence: RetrievalEvidence | None = None
    note: ResearchNote | None = None


class ResearchSearchResponse(BaseModel):
    query: str
    mode: ResearchSearchMode
    results: list[ResearchSearchResult] = Field(default_factory=list)


class ResearchChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchmsg"))
    thread_id: str
    role: ResearchMessageRole
    content: str
    citations: list[ResearchCitation] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchChatThread(BaseModel):
    id: str = Field(default_factory=lambda: new_id("researchthread"))
    owner_user_id: str = ""
    package_id: str
    title: str
    context_mode: ResearchContextMode = "retrieval"
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchChatThreadCreate(BaseModel):
    title: str = ""
    context_mode: ResearchContextMode = "retrieval"
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)


class ResearchChatThreadUpdate(BaseModel):
    title: str | None = None
    context_mode: ResearchContextMode | None = None
    source_ingestion_ids: list[str] | None = None
    note_ids: list[str] | None = None


class ResearchChatRequest(BaseModel):
    message: str
    text_model: AIModelSelection | None = None
    context_mode: ResearchContextMode | None = None
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)


class ResearchChatResponse(BaseModel):
    thread: ResearchChatThread
    message: ResearchChatMessage


class ResearchSpeaker(BaseModel):
    name: str
    role: str = ""
    voice: str = "alloy"
    instructions: str = ""


class ResearchArtifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    owner_user_id: str = ""
    package_id: str
    kind: ResearchArtifactKind
    status: ResearchArtifactStatus = "queued"
    title: str
    content: str = ""
    transcript: str = ""
    audio_url: str | None = None
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    citations: list[ResearchCitation] = Field(default_factory=list)
    error: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchArtifactCreate(BaseModel):
    kind: ResearchArtifactKind
    title: str = ""
    instructions: str = ""
    language: str = ""
    tone: str = ""
    length: Literal["short", "medium", "long"] = "medium"
    segment_count: int | None = Field(default=None, ge=3, le=20)
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    speakers: list[ResearchSpeaker] = Field(default_factory=list)
    text_model: AIModelSelection | None = None
    synthesize_audio: bool = True


class ResearchCapabilities(BaseModel):
    native_ingestion: bool = True
    text_search: bool = True
    semantic_search: bool = True
    notes: bool = True
    persisted_chat: bool = True
    transformations: bool = True
    podcast_script: bool = True
    podcast_audio: bool = False
    supported_source_types: list[str] = Field(default_factory=list)


class ResearchTransformation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("transformation"))
    owner_user_id: str = ""
    package_id: str
    name: str
    instructions: str
    output_kind: ResearchArtifactKind = "custom"
    run_on_import: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchTransformationCreate(BaseModel):
    name: str
    instructions: str
    output_kind: ResearchArtifactKind = "custom"
    run_on_import: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchTransformationUpdate(BaseModel):
    name: str | None = None
    instructions: str | None = None
    output_kind: ResearchArtifactKind | None = None
    run_on_import: bool | None = None
    metadata: dict[str, Any] | None = None


class ResearchTransformationRun(BaseModel):
    title: str = ""
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    text_model: AIModelSelection | None = None


class ResearchSpeakerProfile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("speakerprofile"))
    owner_user_id: str = ""
    package_id: str
    name: str
    speakers: list[ResearchSpeaker]
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ResearchSpeakerProfileCreate(BaseModel):
    name: str
    speakers: list[ResearchSpeaker] = Field(min_length=1, max_length=4)


class ResearchSpeakerProfileUpdate(BaseModel):
    name: str | None = None
    speakers: list[ResearchSpeaker] | None = Field(default=None, min_length=1, max_length=4)


class ResearchEpisodeProfile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("episodeprofile"))
    owner_user_id: str = ""
    package_id: str
    name: str
    language: str = ""
    tone: str = ""
    length: Literal["short", "medium", "long"] = "medium"
    segment_count: int = Field(default=6, ge=3, le=20)
    instructions: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ResearchEpisodeProfileCreate(BaseModel):
    name: str
    language: str = ""
    tone: str = ""
    length: Literal["short", "medium", "long"] = "medium"
    segment_count: int = Field(default=6, ge=3, le=20)
    instructions: str = ""


class ResearchEpisodeProfileUpdate(BaseModel):
    name: str | None = None
    language: str | None = None
    tone: str | None = None
    length: Literal["short", "medium", "long"] | None = None
    segment_count: int | None = Field(default=None, ge=3, le=20)
    instructions: str | None = None


class ResearchAskRequest(BaseModel):
    question: str
    source_ingestion_ids: list[str] = Field(default_factory=list)
    note_ids: list[str] = Field(default_factory=list)
    include_notes: bool = True
    text_model: AIModelSelection | None = None
    max_queries: int = Field(default=5, ge=1, le=5)


class ResearchAskResponse(BaseModel):
    question: str
    search_queries: list[str] = Field(default_factory=list)
    answer: str
    citations: list[ResearchCitation] = Field(default_factory=list)
