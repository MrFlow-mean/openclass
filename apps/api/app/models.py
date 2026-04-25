from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


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
    "await_reference_choice",
]
SelectionKind = Literal["chat", "board"]
ConversationRole = Literal["user", "assistant"]
ResourceReferenceAction = Literal["confirm", "skip"]
ResourceScanStrategy = Literal["outline_only", "heading_section", "page_window", "fulltext_match"]
ChatInteractionMode = Literal["ask", "direct_edit"]
DocumentMarginPreset = Literal["narrow", "normal", "wide"]
DocumentOrientation = Literal["portrait", "landscape"]
DocumentPageSize = Literal["a4", "letter", "a3"]
DocumentBackgroundStyle = Literal["plain", "warm", "grid"]


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


class DocumentPageSettings(BaseModel):
    margin_preset: DocumentMarginPreset = "normal"
    orientation: DocumentOrientation = "portrait"
    page_size: DocumentPageSize = "a4"
    columns: Literal[1, 2] = 1
    page_border: bool = True
    background_style: DocumentBackgroundStyle = "plain"
    watermark_text: str = ""
    line_numbers: bool = False
    show_page_number: bool = False
    header_text: str = ""
    footer_text: str = ""


def _plain_text_to_tiptap_doc(text: str) -> dict[str, Any]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ]
    return {"type": "doc", "content": paragraphs or [{"type": "paragraph"}]}


def _legacy_blocks_to_rich_document(blocks: list[Any]) -> tuple[str, str]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    for raw_block in blocks:
        block = raw_block.model_dump(mode="json") if hasattr(raw_block, "model_dump") else raw_block
        if not isinstance(block, dict):
            continue
        title = str(block.get("title") or "").strip()
        content = str(block.get("content") or "").strip()
        block_type = block.get("type")
        if title:
            tag = "h1" if block_type == "heading" else "h2"
            html_parts.append(f"<{tag}>{title}</{tag}>")
            text_parts.append(title)
        if content:
            paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
            for paragraph in paragraphs:
                html_parts.append(f"<p>{paragraph}</p>")
                text_parts.append(paragraph)
    return "\n".join(html_parts), "\n".join(text_parts)


class BoardDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("doc"))
    title: str
    content_json: dict[str, Any] = Field(default_factory=lambda: {"type": "doc", "content": [{"type": "paragraph"}]})
    content_html: str = ""
    content_text: str = ""
    page_settings: DocumentPageSettings = Field(default_factory=DocumentPageSettings)

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_blocks(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "blocks" not in value:
            return value
        if value.get("content_html") or value.get("content_text") or value.get("content_json"):
            return value
        html, text = _legacy_blocks_to_rich_document(value.get("blocks") or [])
        return {
            "id": value.get("id"),
            "title": value.get("title") or "Untitled document",
            "content_json": _plain_text_to_tiptap_doc(text),
            "content_html": html,
            "content_text": text,
        }


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


class LearningClarificationStatus(BaseModel):
    progress: int = Field(ge=0, le=100)
    label: str
    reason: str
    missing_items: list[str] = Field(default_factory=list)
    can_start: bool = False
    forced_start: bool = False


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
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    board_teaching_guide: BoardTeachingGuide | None = None
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
    page_start: int | None = None
    page_end: int | None = None
    summary: str
    keywords: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    parent_title: str | None = None
    path: list[str] = Field(default_factory=list)
    locator_hint: str | None = None
    order_index: int = 0
    scan_strategy: ResourceScanStrategy = "outline_only"


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
    text_content: str | None = None
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


class WorkspaceState(BaseModel):
    packages: list[CoursePackage] = Field(default_factory=list)
    active_package_id: str | None = None

    @model_validator(mode="after")
    def ensure_active_package(self) -> "WorkspaceState":
        if self.active_package_id and any(package.id == self.active_package_id for package in self.packages):
            return self
        self.active_package_id = self.packages[0].id if self.packages else None
        return self


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
    score: float = 0.0
    is_high_overlap: bool = False


class ResourceReferencePrompt(BaseModel):
    resource_id: str
    chapter_id: str
    resource_name: str
    chapter_title: str
    question: str
    reason: str
    confirm_label: str = "参考这一章节"
    skip_label: str = "先不参考"
    score: float = 0.0


class ResourceContextChunk(BaseModel):
    title: str
    excerpt: str
    teaching_hint: str


class ResourceReferenceContext(BaseModel):
    resource_id: str
    chapter_id: str
    resource_name: str
    chapter_title: str
    summary: str
    teaching_points: list[str] = Field(default_factory=list)
    chunks: list[ResourceContextChunk] = Field(default_factory=list)
    full_text: str = Field(default="", exclude=True, repr=False)


class BoardDecision(BaseModel):
    action: BoardAction
    reason: str


class BoardTeachingSelectedItem(BaseModel):
    excerpt: str
    source_heading: str | None = None
    reason: str
    mapped_needs: list[str] = Field(default_factory=list)
    teaching_role: str = "main_idea"
    order_index: int = 0


class BoardNeedMapping(BaseModel):
    need: str
    matched_excerpt: str
    source_heading: str | None = None
    rationale: str


class BoardTeachingGuide(BaseModel):
    board_document_id: str = ""
    board_snapshot_hash: str = ""
    board_title: str = ""
    selected_items: list[BoardTeachingSelectedItem] = Field(default_factory=list)
    need_mappings: list[BoardNeedMapping] = Field(default_factory=list)
    teaching_flow: list[str] = Field(default_factory=list)
    generation_rationale: str = ""
    teacher_brief: str = ""


class ChatRequest(BaseModel):
    message: str
    selection: SelectionRef | None = None
    interaction_mode: ChatInteractionMode = "ask"
    scope_action: ScopeAction | None = None
    resource_chapter_id: str | None = None
    resource_reference_action: ResourceReferenceAction | None = None
    resource_reference_resource_id: str | None = None
    resource_reference_chapter_id: str | None = None
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


class WorkspaceStateView(BaseModel):
    packages: list[CoursePackageView] = Field(default_factory=list)
    active_package_id: str | None = None


class ChatResponse(BaseModel):
    teacher_message: str
    learning_requirement_sheet: LearningRequirementSheet
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    patch_proposal: PatchProposal | None = None
    scope_options: list[ScopeOption] = Field(default_factory=list)
    resource_matches: list[ResourceMatch] = Field(default_factory=list)
    reference_prompt: ResourceReferencePrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    created_lesson: LessonView | None = None
    course_package: CoursePackageView


class CreatePackageRequest(BaseModel):
    title: str
    summary: str = ""


class MoveLessonRequest(BaseModel):
    target_package_id: str


class GenerateLessonRequest(BaseModel):
    topic: str
    branch_from_lesson_id: str | None = None
    start_blank: bool = False


class ManualCommitRequest(BaseModel):
    document: BoardDocument | None = None
    operations: list[PatchOperation] = Field(default_factory=list)
    label: str = "Manual document edit"
    message: str = "Saved rich document changes from the editor"


class DocumentSaveRequest(BaseModel):
    document: BoardDocument
    label: str = "Manual document edit"
    message: str = "Saved rich document changes from the editor"


class DocumentAIEditRequest(BaseModel):
    instruction: str
    selection_text: str | None = None
    replace_whole: bool = False
    conversation: list[ConversationTurn] = Field(default_factory=list)


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
