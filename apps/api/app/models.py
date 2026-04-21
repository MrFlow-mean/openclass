from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


BlockType = Literal[
    "heading",
    "paragraph",
    "formula",
    "table",
    "image",
    "note",
    "exercise",
    "dialogue",
]

PatchOperationType = Literal[
    "insert_block",
    "delete_block",
    "update_block_content",
    "replace_range_in_block",
    "move_block",
    "update_block_style",
    "attach_asset",
]

CourseEdgeType = Literal[
    "recommended_next",
    "prerequisite",
    "deep_dive",
    "alternate_path",
    "derived_from",
]

TeachingMode = Literal["definition", "intuition", "analogy", "example", "dialogue"]
ScopeAction = Literal[
    "patch_current_lesson",
    "append_section",
    "create_branch",
    "create_child_lesson",
    "create_new_lesson",
]
BoardAction = Literal[
    "clarify_request",
    "no_change",
    "edit_board",
    "append_section",
    "create_new_lesson",
    "await_scope_choice",
]
SelectionKind = Literal["chat", "board"]
ConversationRole = Literal["user", "assistant"]


class BlockStyle(BaseModel):
    font_family: str = "sans"
    font_size: Literal["sm", "md", "lg", "xl"] = "md"
    alignment: Literal["left", "center", "right"] = "left"
    emphasis: Literal["plain", "accent", "callout"] = "plain"
    width: Literal["normal", "wide", "full"] = "normal"


class BoardBlock(BaseModel):
    id: str = Field(default_factory=lambda: new_id("block"))
    type: BlockType
    title: str
    content: str
    style: BlockStyle = Field(default_factory=BlockStyle)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoardDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("doc"))
    title: str
    blocks: list[BoardBlock]


class PatchOperation(BaseModel):
    op: PatchOperationType
    block_id: str | None = None
    after_block_id: str | None = None
    title: str | None = None
    content: str | None = None
    block: BoardBlock | None = None
    search: str | None = None
    replacement: str | None = None
    style: BlockStyle | None = None
    asset_url: str | None = None
    note: str | None = None


class DiffPreviewItem(BaseModel):
    op: PatchOperationType
    block_id: str | None = None
    before: BoardBlock | None = None
    after: BoardBlock | None = None
    summary: str


class PatchProposal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("proposal"))
    rationale: str
    commit_label: str
    operations: list[PatchOperation]
    diff_preview: list[DiffPreviewItem]
    target_action: ScopeAction = "patch_current_lesson"
    suggested_title: str | None = None


class LearningRequirementSheet(BaseModel):
    theme: str
    learning_goal: str
    level: str
    known_background: str
    current_questions: list[str]
    target_depth: str
    output_preference: str
    boundary: str
    board_scope: list[str]
    success_criteria: str
    risk_notes: list[str] = Field(default_factory=list)


class TeachingGuideMapping(BaseModel):
    block_id: str
    supports_goal: str
    teaching_mode: TeachingMode
    focus_points: list[str]
    optional_points: list[str] = Field(default_factory=list)
    difficult_points: list[str] = Field(default_factory=list)
    check_questions: list[str] = Field(default_factory=list)


class TeachingGuide(BaseModel):
    lesson_id: str
    summary: str
    structure_note: str
    pacing: str
    mappings: list[TeachingGuideMapping]
    strategy: str


class CommitRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("commit"))
    label: str
    message: str
    branch_name: str
    created_at: str = Field(default_factory=now_iso)
    parent_ids: list[str] = Field(default_factory=list)
    operations: list[PatchOperation] = Field(default_factory=list)
    snapshot: BoardDocument


class BranchRef(BaseModel):
    name: str
    head_commit_id: str
    base_commit_id: str
    created_at: str = Field(default_factory=now_iso)


class LessonHistoryGraph(BaseModel):
    branches: dict[str, BranchRef]
    commits: list[CommitRecord]
    current_branch: str = "main"


class Lesson(BaseModel):
    id: str = Field(default_factory=lambda: new_id("lesson"))
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    board_document: BoardDocument
    learning_requirements: LearningRequirementSheet | None = None
    teaching_guide: TeachingGuide
    history_graph: LessonHistoryGraph
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class CourseGraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: new_id("edge"))
    source_lesson_id: str
    target_lesson_id: str
    relationship: CourseEdgeType


class LibraryChapter(BaseModel):
    id: str = Field(default_factory=lambda: new_id("chapter"))
    title: str
    level: int = 1
    page_range: str | None = None
    summary: str
    keywords: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)


class ResourceLibraryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("resource"))
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str = Field(default_factory=now_iso)
    outline: list[LibraryChapter] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False
    source_path: str | None = None


class CoursePackage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("course"))
    title: str
    summary: str
    lessons: list[Lesson]
    course_graph: list[CourseGraphEdge] = Field(default_factory=list)
    resources: list[ResourceLibraryItem] = Field(default_factory=list)
    open_lesson_ids: list[str] = Field(default_factory=list)
    active_lesson_id: str | None = None
    workspace_tab_order: list[str] = Field(default_factory=list)


class SelectionRef(BaseModel):
    kind: SelectionKind
    excerpt: str
    lesson_id: str | None = None
    block_id: str | None = None


class ConversationTurn(BaseModel):
    role: ConversationRole
    content: str


class ScopeOption(BaseModel):
    action: ScopeAction
    label: str
    description: str
    resource_chapter_id: str | None = None


class ResourceMatch(BaseModel):
    resource_id: str
    chapter_id: str
    resource_name: str
    chapter_title: str
    reason: str


class BoardDecision(BaseModel):
    action: BoardAction
    reason: str


class ChatRequest(BaseModel):
    message: str
    selection: SelectionRef | None = None
    scope_action: ScopeAction | None = None
    resource_chapter_id: str | None = None
    conversation: list[ConversationTurn] = Field(default_factory=list)


class LessonView(BaseModel):
    id: str
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    board_document: BoardDocument
    learning_requirements: LearningRequirementSheet | None = None
    history_graph: LessonHistoryGraph
    created_at: str
    updated_at: str


class CoursePackageView(BaseModel):
    id: str
    title: str
    summary: str
    lessons: list[LessonView]
    course_graph: list[CourseGraphEdge] = Field(default_factory=list)
    resources: list[ResourceLibraryItem] = Field(default_factory=list)
    open_lesson_ids: list[str] = Field(default_factory=list)
    active_lesson_id: str | None = None
    workspace_tab_order: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    teacher_message: str
    learning_requirement_sheet: LearningRequirementSheet
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    patch_proposal: PatchProposal | None = None
    scope_options: list[ScopeOption] = Field(default_factory=list)
    resource_matches: list[ResourceMatch] = Field(default_factory=list)
    created_lesson: LessonView | None = None
    course_package: CoursePackageView


class GenerateLessonRequest(BaseModel):
    topic: str
    branch_from_lesson_id: str | None = None


class ManualCommitRequest(BaseModel):
    operations: list[PatchOperation]
    label: str = "Manual board edit"
    message: str = "Updated board blocks from the editor"


class CreateBranchRequest(BaseModel):
    name: str
    from_commit_id: str | None = None


class SwitchBranchRequest(BaseModel):
    name: str


class RestoreCommitRequest(BaseModel):
    commit_id: str
    label: str = "Restore snapshot"


class ReorderTabsRequest(BaseModel):
    ordered_lesson_ids: list[str]
    active_lesson_id: str | None = None
