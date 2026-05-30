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
    "await_focus_choice",
]
SelectionKind = Literal["chat", "board"]
BoardFocusSource = Literal["board", "resource", "chat"]
BoardFocusLocationStatus = Literal["missing", "selected", "resolved", "ambiguous"]
BoardSegmentKind = Literal["heading", "paragraph", "list", "table", "code", "image", "formula", "other"]
BoardTaskAction = Literal[
    "generate_board",
    "append_section",
    "explain_target",
    "rewrite_target",
    "expand_target",
    "simplify_target",
]
InteractionSessionStatus = Literal["active", "paused"]
InteractionTurnRoute = Literal[
    "continue_rule",
    "rule_violation",
    "side_learning_request",
    "resume_rule",
    "exit_rule",
    "new_task",
]
ConversationRole = Literal["user", "assistant"]
RealtimeTranscriptRole = Literal["user", "assistant", "tool"]
AIProvider = Literal[
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "kimi",
    "minimax",
    "openai_compatible",
    "anthropic_compatible",
]
AIModelCapability = Literal["text", "realtime"]
AIRealtimeTransport = Literal["openai_webrtc", "gemini_live_websocket"]
ResourceReferenceAction = Literal["confirm", "skip"]
BoardEditConfirmationAction = Literal["confirm", "skip"]
StrongReasoningAction = Literal["confirm", "skip"]
ResourceScanStrategy = Literal["outline_only", "heading_section", "page_window", "fulltext_match"]
ChatInteractionMode = Literal["ask", "direct_edit"]
TeachingAction = Literal["continue", "restart"]
BoardGenerationAction = Literal["start"]
LearningRequirementFactCategory = Literal["learning", "level", "vocabulary", "scenario", "output", "other"]
DocumentMarginPreset = Literal["narrow", "normal", "wide"]
DocumentOrientation = Literal["portrait", "landscape"]
DocumentPageSize = Literal["a4", "letter", "a3"]
DocumentBackgroundStyle = Literal["plain", "warm", "grid"]
CourseContributionStatus = Literal["open", "changes_requested", "merged", "closed"]
CourseContributionReviewAction = Literal["request_changes", "close", "merge"]
CourseMaintainerRole = Literal["owner", "maintainer"]
CourseChangeStatus = Literal["unchanged", "edited", "added", "deleted"]


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


class BoardSegment(BaseModel):
    segment_id: str
    document_id: str
    kind: BoardSegmentKind
    heading_path: list[str] = Field(default_factory=list)
    order_index: int = 0
    text: str = ""
    html: str = ""
    text_hash: str = ""
    parent_id: str | None = None
    before_segment_id: str | None = None
    after_segment_id: str | None = None


class BoardSegmentIndex(BaseModel):
    document_id: str
    document_title: str = ""
    segments: list[BoardSegment] = Field(default_factory=list)


class DocumentSegmentSearchResult(BaseModel):
    package_id: str
    package_title: str
    lesson_id: str
    lesson_title: str
    document_id: str
    document_title: str
    segment_id: str
    kind: BoardSegmentKind
    heading_path: list[str] = Field(default_factory=list)
    order_index: int = 0
    text: str = ""
    text_hash: str = ""


class DocumentSegmentSearchResponse(BaseModel):
    query: str = ""
    kind: BoardSegmentKind | None = None
    results: list[DocumentSegmentSearchResult] = Field(default_factory=list)


class BoardFocusRef(BaseModel):
    source: BoardFocusSource = "board"
    lesson_id: str | None = None
    document_id: str | None = None
    segment_id: str | None = None
    kind: BoardSegmentKind | None = None
    heading_path: list[str] = Field(default_factory=list)
    excerpt: str = ""
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class InteractionRuleDraft(BaseModel):
    should_start: bool = False
    rule_text: str = ""
    interaction_goal: str = ""
    target_hint: str = ""
    expected_user_behavior: str = ""
    assistant_behavior: str = ""
    reference_instruction: str = ""


class InteractionSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("interaction"))
    status: InteractionSessionStatus = "active"
    rule_text: str = ""
    interaction_goal: str = ""
    target_focus: BoardFocusRef | None = None
    reference_context: str = ""
    expected_user_behavior: str = ""
    assistant_behavior: str = ""
    progress_note: str = ""
    pause_reason: str = ""
    turn_count: int = Field(default=0, ge=0)


class InteractionTurnDecision(BaseModel):
    route: InteractionTurnRoute
    reason: str = ""
    progress_note: str = ""
    user_intent: str = ""


class LearningRequirementSheet(BaseModel):
    theme: str
    learning_goal: str
    level: str
    known_background: str
    current_questions: list[str]
    learning_need_checklist: list[str] = Field(default_factory=list)
    target_depth: str
    output_preference: str
    boundary: str
    board_scope: list[str]
    success_criteria: str
    risk_notes: list[str] = Field(default_factory=list)
    target_location: BoardFocusRef | None = None
    location_status: BoardFocusLocationStatus = "missing"
    action_type: BoardTaskAction | None = None
    action_instruction: str = ""
    location_clarification_question: str = ""
    interaction_rule_draft: InteractionRuleDraft | None = None


class LearningRequirementChecklistItem(BaseModel):
    title: str
    is_clear: bool = False
    evidence: str = ""


class LearningRequirementKeyFact(BaseModel):
    label: str
    value: str
    evidence: str = ""
    category: LearningRequirementFactCategory | None = None


class LearningClarificationStatus(BaseModel):
    progress: int = Field(ge=0, le=100)
    label: str
    reason: str
    missing_items: list[str] = Field(default_factory=list)
    can_start: bool = False
    forced_start: bool = False
    summary: str = ""
    key_facts: list[LearningRequirementKeyFact] = Field(default_factory=list)
    checklist: list[LearningRequirementChecklistItem] = Field(default_factory=list)
    next_question: str = ""
    ready_for_board: bool = False


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
    board_teaching_progress: BoardTeachingProgress | None = None
    learning_requirements: LearningRequirementSheet | None = None
    active_interaction_session: InteractionSession | None = None
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


class ResourceSegment(BaseModel):
    segment_id: str
    resource_id: str
    chapter_id: str
    heading_path: list[str] = Field(default_factory=list)
    order_index: int = 0
    text: str = ""
    text_hash: str = ""
    keywords: list[str] = Field(default_factory=list)
    page_range: str | None = None
    before_segment_id: str | None = None
    after_segment_id: str | None = None
    embedding: list[float] = Field(default_factory=list, exclude=True, repr=False)
    embedding_provider: str | None = Field(default=None, exclude=True, repr=False)
    embedding_model: str | None = Field(default=None, exclude=True, repr=False)


class ResourceLibraryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("resource"))
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str = Field(default_factory=now_iso)
    scope_lesson_id: str | None = None
    outline: list[LibraryChapter] = Field(default_factory=list)
    segments: list[ResourceSegment] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False
    text_content: str | None = None
    source_path: str | None = None


class ResourceLibraryItemView(BaseModel):
    id: str
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str
    scope_lesson_id: str | None = None
    outline: list[LibraryChapter] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False


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
    document_id: str | None = None
    segment_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None


class ConversationTurn(BaseModel):
    role: ConversationRole
    content: str


class AIModelSelection(BaseModel):
    provider: AIProvider
    model: str


class AIModelOption(BaseModel):
    provider: AIProvider
    model: str
    label: str
    capability: AIModelCapability
    enabled: bool = False
    configured: bool = False
    default: bool = False
    transport: AIRealtimeTransport | None = None


class AIModelCatalog(BaseModel):
    text: list[AIModelOption] = Field(default_factory=list)
    realtime: list[AIModelOption] = Field(default_factory=list)
    defaults: dict[AIModelCapability, AIModelSelection]


class ScopeOption(BaseModel):
    action: ScopeAction
    label: str
    description: str
    resource_chapter_id: str | None = None


class ResourceMatchEvidence(BaseModel):
    label: str
    value: str


class ResourceMatch(BaseModel):
    resource_id: str
    chapter_id: str
    segment_id: str | None = None
    resource_name: str
    chapter_title: str
    heading_path: list[str] = Field(default_factory=list)
    excerpt: str = ""
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None
    reason: str
    evidence: list[ResourceMatchEvidence] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    is_high_overlap: bool = False


class ResourceReferencePrompt(BaseModel):
    resource_id: str
    chapter_id: str
    segment_id: str | None = None
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
    segment_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None


class ResourceReferenceContext(BaseModel):
    resource_id: str
    chapter_id: str
    segment_id: str | None = None
    resource_name: str
    chapter_title: str
    summary: str
    teaching_points: list[str] = Field(default_factory=list)
    chunks: list[ResourceContextChunk] = Field(default_factory=list)
    full_text: str = Field(default="", exclude=True, repr=False)


class BoardDecision(BaseModel):
    action: BoardAction
    reason: str


class BoardEditPrompt(BaseModel):
    topic: str
    question: str
    reason: str
    confirm_label: str = "是"
    skip_label: str = "否"


class StrongReasoningPrompt(BaseModel):
    question: str
    reason: str
    confirm_label: str = "确认推理"
    skip_label: str = "先不用"
    model_label: str | None = None


# Reserved AI teaching workflow schema.
# Current public routes preserve these fields for stored lesson compatibility,
# but realtime teaching execution is disabled and the next orchestration layer
# should decide whether these models still match the product workflow.
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


class BoardSectionTeachingPlan(BaseModel):
    order_index: int = 0
    heading: str
    board_excerpt: str = ""
    core_points: list[str] = Field(default_factory=list)
    teaching_steps: list[str] = Field(default_factory=list)
    teaching_method: str = ""
    example_or_analogy: str = ""
    common_pitfalls: list[str] = Field(default_factory=list)
    check_question: str = ""
    transition_to_next: str = ""


class BoardTeachingGuide(BaseModel):
    board_document_id: str = ""
    board_snapshot_hash: str = ""
    board_title: str = ""
    selected_items: list[BoardTeachingSelectedItem] = Field(default_factory=list)
    need_mappings: list[BoardNeedMapping] = Field(default_factory=list)
    teaching_flow: list[str] = Field(default_factory=list)
    generation_rationale: str = ""
    chatbot_brief: str = ""
    lecture_handout: str = ""
    section_plans: list[BoardSectionTeachingPlan] = Field(default_factory=list)


class BoardTeachingProgress(BaseModel):
    board_document_id: str = ""
    board_snapshot_hash: str = ""
    current_section_index: int = 0
    completed_section_indexes: list[int] = Field(default_factory=list)
    waiting_for_continue: bool = False


class SectionTeachingProgressView(BaseModel):
    section_index: int = 0
    section_count: int = 0
    current_section_title: str = ""
    has_next_section: bool = False
    waiting_for_continue: bool = False


class ChatRequest(BaseModel):
    message: str
    text_model: AIModelSelection | None = None
    selection: SelectionRef | None = None
    interaction_mode: ChatInteractionMode = "ask"
    scope_action: ScopeAction | None = None
    resource_chapter_id: str | None = None
    resource_reference_action: ResourceReferenceAction | None = None
    resource_reference_resource_id: str | None = None
    resource_reference_chapter_id: str | None = None
    resource_reference_segment_id: str | None = None
    board_edit_action: BoardEditConfirmationAction | None = None
    board_edit_topic: str | None = None
    strong_reasoning_action: StrongReasoningAction | None = None
    board_generation_action: BoardGenerationAction | None = None
    teaching_action: TeachingAction | None = None
    conversation: list[ConversationTurn] = Field(default_factory=list)


class LessonView(BaseModel):
    id: str
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    board_document: BoardDocument
    learning_requirements: LearningRequirementSheet | None = None
    active_interaction_session: InteractionSession | None = None
    history_graph: LessonHistoryGraph
    created_at: str
    updated_at: str


class CoursePackageView(BaseModel):
    id: str
    title: str
    summary: str
    is_standalone: bool = False
    lessons: list[LessonView]
    course_graph: list[CourseGraphEdge] = Field(default_factory=list)
    resources: list[ResourceLibraryItemView] = Field(default_factory=list)
    open_lesson_ids: list[str] = Field(default_factory=list)
    active_lesson_id: str | None = None
    workspace_tab_order: list[str] = Field(default_factory=list)


class PublicUserView(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None


class OpenCourseStats(BaseModel):
    lessons: int = 0
    resources: int = 0
    forks: int = 0
    open_contributions: int = 0
    contributors: int = 0
    maintainers: int = 0


class OpenCourseSummary(BaseModel):
    id: str
    package_id: str
    owner: PublicUserView
    title: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    stats: OpenCourseStats = Field(default_factory=OpenCourseStats)
    published_at: str
    updated_at: str


class CourseMaintainerView(BaseModel):
    publication_id: str
    user: PublicUserView
    role: CourseMaintainerRole
    added_at: str


class CourseContributionEventView(BaseModel):
    id: str
    actor: PublicUserView
    event_type: str
    message: str
    created_at: str


class ContributionLessonChange(BaseModel):
    status: CourseChangeStatus
    source_lesson_id: str | None = None
    fork_lesson_id: str | None = None
    title: str
    base_summary: str = ""
    current_summary: str = ""
    proposed_summary: str = ""
    current_changed: bool = False


class ContributionResourceChange(BaseModel):
    status: CourseChangeStatus
    source_resource_id: str | None = None
    fork_resource_id: str | None = None
    name: str


class CourseForkView(BaseModel):
    id: str
    publication_id: str
    fork_package_id: str
    source_package_id: str
    created_at: str
    updated_at: str


class CourseContributionSummary(BaseModel):
    id: str
    publication_id: str
    fork_id: str
    title: str
    description: str
    status: CourseContributionStatus
    contributor: PublicUserView
    lesson_changes: list[ContributionLessonChange] = Field(default_factory=list)
    resource_changes: list[ContributionResourceChange] = Field(default_factory=list)
    created_at: str
    updated_at: str
    reviewed_by: PublicUserView | None = None
    reviewed_at: str | None = None


class CourseContributionView(CourseContributionSummary):
    course: OpenCourseSummary
    baseline_package: CoursePackageView | None = None
    proposed_package: CoursePackageView | None = None
    source_package: CoursePackageView | None = None
    events: list[CourseContributionEventView] = Field(default_factory=list)


class OpenCourseDetail(BaseModel):
    course: OpenCourseSummary
    package: CoursePackageView
    maintainers: list[CourseMaintainerView] = Field(default_factory=list)
    contributions: list[CourseContributionSummary] = Field(default_factory=list)
    viewer_can_review: bool = False
    viewer_is_owner: bool = False
    viewer_fork: CourseForkView | None = None


class OpenCourseListResponse(BaseModel):
    courses: list[OpenCourseSummary] = Field(default_factory=list)


class PublishPackageRequest(BaseModel):
    summary: str | None = None


class ForkCourseResponse(BaseModel):
    fork: CourseForkView
    course_package: CoursePackageView


class SubmitContributionRequest(BaseModel):
    title: str
    description: str = ""


class ReviewContributionRequest(BaseModel):
    action: CourseContributionReviewAction
    message: str = ""


class AddMaintainerRequest(BaseModel):
    email: str


class WorkspaceStateView(BaseModel):
    packages: list[CoursePackageView] = Field(default_factory=list)
    active_package_id: str | None = None


class AuthIdentityView(BaseModel):
    provider: str
    provider_label: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    created_at: str
    last_login_at: str | None = None


class UserView(BaseModel):
    id: str
    email: str
    phone: str | None = None
    role: Literal["user", "admin", "guest"]
    status: Literal["active", "disabled"] = "active"
    display_name: str | None = None
    avatar_url: str | None = None
    created_at: str
    updated_at: str | None = None
    last_login_at: str | None = None
    email_verified_at: str | None = None
    session_count: int | None = None
    package_count: int | None = None
    auth_identities: list[AuthIdentityView] = Field(default_factory=list)


class AuthRequest(BaseModel):
    identifier: str | None = None
    email: str | None = None
    phone: str | None = None
    guest_token: str | None = None
    next_path: str | None = None
    password: str = Field(min_length=8, max_length=256)

    def account_identifier(self) -> str:
        return self.identifier or self.email or self.phone or ""


class RegisterResponse(BaseModel):
    email: str
    verification_required: Literal[True] = True


class AuthSessionResponse(BaseModel):
    token: str
    user: UserView


class AuthUserResponse(BaseModel):
    user: UserView


class AuthProviderView(BaseModel):
    id: str
    label: str
    description: str
    configured: bool
    kind: Literal["password", "oauth"] = "oauth"


class AuthEmailRequest(BaseModel):
    email: str
    next_path: str | None = None


class AuthPasswordResetRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=256)


class AuthMessageResponse(BaseModel):
    message: str


class AdminUserUpdateRequest(BaseModel):
    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None


class AdminAuditLogView(BaseModel):
    id: str
    actor_user_id: str
    target_user_id: str | None = None
    action: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: str
    actor_email: str | None = None
    target_email: str | None = None


class AdminAuditLogResponse(BaseModel):
    logs: list[AdminAuditLogView] = Field(default_factory=list)


class AdminStats(BaseModel):
    users: int
    admins: int
    packages: int
    lessons: int
    resources: int
    disabled_users: int = 0
    unverified_users: int = 0
    active_sessions: int = 0


class AdminOverview(BaseModel):
    stats: AdminStats
    users: list[UserView] = Field(default_factory=list)
    mail_delivery_configured: bool = False
    mail_delivery_mode: str = "unconfigured"


class ChatResponse(BaseModel):
    chatbot_message: str
    learning_requirement_sheet: LearningRequirementSheet
    active_requirement_sheet: LearningRequirementSheet | None = None
    active_interaction_session: InteractionSession | None = None
    interaction_decision: InteractionTurnDecision | None = None
    learning_clarification: LearningClarificationStatus
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    patch_proposal: PatchProposal | None = None
    scope_options: list[ScopeOption] = Field(default_factory=list)
    resource_matches: list[ResourceMatch] = Field(default_factory=list)
    reference_prompt: ResourceReferencePrompt | None = None
    board_edit_prompt: BoardEditPrompt | None = None
    strong_reasoning_prompt: StrongReasoningPrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    resolved_focus: BoardFocusRef | None = None
    focus_candidates: list[BoardFocusRef] = Field(default_factory=list)
    requirement_cleared: bool = False
    created_lesson: LessonView | None = None
    teaching_progress: SectionTeachingProgressView | None = None
    course_package: CoursePackageView


class CreatePackageRequest(BaseModel):
    title: str
    summary: str = ""


class UpdatePackageRequest(BaseModel):
    title: str | None = None
    summary: str | None = None


class MoveLessonRequest(BaseModel):
    target_package_id: str


class GenerateLessonRequest(BaseModel):
    topic: str
    branch_from_lesson_id: str | None = None
    target_package_id: str | None = None
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
    metadata: dict[str, Any] = Field(default_factory=dict)
    base_commit_id: str | None = None


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


MergeBranchChoice = Literal["target", "source"]
MergeBranchSectionStatus = Literal["no_change", "source_only", "target_only", "conflict"]


class MergeBranchPreviewRequest(BaseModel):
    source_branch: str
    target_branch: str | None = None


class MergeBranchSectionPreview(BaseModel):
    status: MergeBranchSectionStatus
    recommended_choice: MergeBranchChoice = "target"
    requires_confirmation: bool = False
    base_summary: str = ""
    target_summary: str = ""
    source_summary: str = ""


class MergeBranchPreviewResponse(BaseModel):
    source_branch: str
    target_branch: str
    base_commit_id: str
    target_head_commit_id: str
    source_head_commit_id: str
    can_merge: bool = True
    already_merged: bool = False
    document: MergeBranchSectionPreview
    requirements: MergeBranchSectionPreview
    session: MergeBranchSectionPreview


class MergeBranchRequest(BaseModel):
    source_branch: str
    target_branch: str | None = None
    expected_target_head_commit_id: str
    expected_source_head_commit_id: str
    document_choice: MergeBranchChoice = "target"
    requirements_choice: MergeBranchChoice = "target"
    session_choice: MergeBranchChoice = "target"


class RestoreCommitRequest(BaseModel):
    commit_id: str
    label: str = "Restore snapshot"


class ReorderTabsRequest(BaseModel):
    ordered_lesson_ids: list[str]
    active_lesson_id: str | None = None


class RealtimeConnectRequest(BaseModel):
    offer_sdp: str
    latest_assistant_message: str | None = None
    client_session_id: str | None = None
    realtime_model: AIModelSelection | None = None


class RealtimeConnectResponse(BaseModel):
    answer_sdp: str
    provider: str = "openai"
    model: str
    voice: str
    call_id: str | None = None
    tools_enabled: bool = False
    client_session_id: str | None = None


class GoogleRealtimeSessionRequest(BaseModel):
    latest_assistant_message: str | None = None
    client_session_id: str | None = None
    realtime_model: AIModelSelection | None = None


class GoogleRealtimeSessionResponse(BaseModel):
    websocket_url: str
    setup: dict[str, object]
    provider: str = "google"
    model: str
    voice: str


class RealtimeTranscriptLogRequest(BaseModel):
    client_session_id: str | None = None
    lesson_title: str | None = None
    role: RealtimeTranscriptRole
    transport_event_type: str
    transcript: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_status: str | None = None
