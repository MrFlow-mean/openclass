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
AgentTurnRoute = Literal[
    "ordinary_chat",
    "blank_requirement_refine",
    "blank_board_generate",
    "post_generation_teaching_start",
    "board_teaching_continue",
    "board_task_refine_or_execute",
    "interaction_session_turn",
    "resource_grounded_task",
]
AgentActivityStage = Literal[
    "turn_decision",
    "resolve_target",
    "build_context",
    "execute_role",
    "verify",
    "persist_history",
    "final",
]
AgentActivityStatus = Literal["pending", "running", "completed", "blocked", "failed", "skipped"]
InitialLearningWorkMode = Literal["knowledge_board", "narrow_topic", "practice_artifact", "unknown"]
InitialLearningGranularity = Literal[
    "single_knowledge_point",
    "broad_topic",
    "practice_artifact",
    "unclear",
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
    "openai_codex",
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
ResourceScanStrategy = Literal["outline_only", "heading_section", "page_window", "fulltext_match"]
ChatInteractionMode = Literal["ask", "direct_edit"]
FormulaInkAction = Literal["reference", "replace"]
TeachingAction = Literal["continue", "restart"]
BoardGenerationAction = Literal["start"]
BoardWorkflow = Literal["generate_from_scratch", "act_on_existing_board", "unknown"]
LearningRequirementFactCategory = Literal["learning", "level", "vocabulary", "scenario", "output", "other"]
LearningRequirementRunStatus = Literal["collecting", "ready", "frozen", "consumed", "archived"]
LearningRequirementChangeKind = Literal[
    "created",
    "updated",
    "completed",
    "frozen",
    "forced_frozen",
    "consumed",
    "archived",
    "generation_failed",
]
BoardTaskRunStatus = Literal["collecting", "ready", "awaiting_confirmation", "consumed", "not_executed", "archived"]
BoardTaskChangeKind = Literal[
    "created",
    "updated",
    "ready",
    "awaiting_confirmation",
    "consumed",
    "not_executed",
    "archived",
    "execution_failed",
]
BoardTaskRequestedAction = Literal["write", "edit", "explain", "chat"]
BoardTaskConfirmationStatus = Literal["none", "awaiting", "confirmed", "declined"]
BoardTaskRoute = Literal["write", "edit", "explain", "chat", "clarify_location", "await_write_confirmation"]
BoardTaskLocationStatus = Literal["missing", "selected", "resolved", "ambiguous", "content_absent"]
BoardTaskLocationKind = Literal["target_range", "insertion_anchor", "unspecified"]
BoardDocumentOperationStatus = Literal["none", "succeeded", "failed"]
BoardPatchRiskLevel = Literal["low", "medium", "high"]
BoardPatchTargetScope = Literal["focus", "section", "whole_document", "append"]
BoardPatchContentFormat = Literal["markdown", "plain_text"]
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
    node_path: list[int] = Field(default_factory=list)
    title: str | None = None
    content: str | None = None
    content_format: BoardPatchContentFormat = "markdown"
    block: BoardBlock | None = None
    search: str | None = None
    replacement: str | None = None
    expected_text: str | None = None
    expected_text_hash: str | None = None
    style: BlockStyle | None = None
    asset_url: str | None = None
    note: str | None = None


class DiffPreviewItem(BaseModel):
    op: PatchOperationType
    block_id: str | None = None
    node_path: list[int] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    before: BoardBlock | None = None
    after: BoardBlock | None = None
    before_text: str = ""
    after_text: str = ""
    summary: str


class BoardPatchRequest(BaseModel):
    source_commit_id: str | None = None
    source_document_hash: str = ""
    target_scope: BoardPatchTargetScope | None = None
    operations: list[PatchOperation] = Field(default_factory=list)
    summary: str = ""
    risk_level: BoardPatchRiskLevel = "medium"


class BoardPatchValidationResult(BaseModel):
    status: Literal["pass", "failed"] = "pass"
    issues: list[str] = Field(default_factory=list)
    applied_operations: int = 0
    source_commit_id: str | None = None
    source_document_hash: str = ""
    current_document_hash: str = ""


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


class BoardChunk(BaseModel):
    chunk_id: str
    document_id: str
    source_segment_ids: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    order_start: int = 0
    order_end: int = 0
    text: str = ""
    text_hash: str = ""


class BoardSegmentIndex(BaseModel):
    document_id: str
    document_title: str = ""
    segments: list[BoardSegment] = Field(default_factory=list)
    chunks: list[BoardChunk] = Field(default_factory=list)


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


class AgentTurnDecision(BaseModel):
    route: AgentTurnRoute
    reason: str = ""
    required_role: str = "chatbot"
    blockers: list[str] = Field(default_factory=list)
    next_step: str = ""
    needs_user_confirmation: bool = False


class AgentActivityEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("agentevt"))
    turn_id: str
    stage: AgentActivityStage
    label: str
    status: AgentActivityStatus = "completed"
    role: str = "system"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


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
    excerpt_hash: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    display_label: str = ""
    match_id: str | None = None
    source_segment_ids: list[str] = Field(default_factory=list)
    order_start: int | None = None
    order_end: int | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class BoardReadContext(BaseModel):
    target_focus: BoardFocusRef
    target_excerpt: str = ""
    surrounding_context: str = ""
    before_text: str = ""
    after_text: str = ""
    range_label: str = ""
    source_segment_ids: list[str] = Field(default_factory=list)
    order_start: int | None = None
    order_end: int | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class BoardSearchQueryPlan(BaseModel):
    query_text: str = ""
    search_terms: list[str] = Field(default_factory=list)
    structured_target: str = ""
    scope_hint: str = ""
    action_type: BoardTaskAction | None = None


class BoardSearchCandidate(BaseModel):
    match_id: str
    source: str
    chunk_id: str | None = None
    source_segment_ids: list[str] = Field(default_factory=list)
    focus: BoardFocusRef
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    reason: str = ""


class BoardSearchEvidence(BaseModel):
    status: Literal["selected", "found", "ambiguous", "missing", "content_absent"] = "missing"
    query_plan: BoardSearchQueryPlan = Field(default_factory=BoardSearchQueryPlan)
    candidates: list[BoardSearchCandidate] = Field(default_factory=list)
    selected_match_id: str | None = None
    source: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    range_label: str = ""
    order_start: int | None = None
    order_end: int | None = None
    candidate_count: int = 0
    failure_reason_code: str = ""
    read_context: BoardReadContext | None = None
    reason: str = ""


class BoardSearchRerankItem(BaseModel):
    match_id: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class BoardSearchRerankResult(BaseModel):
    ranked: list[BoardSearchRerankItem] = Field(default_factory=list)
    reason: str = ""


class InteractionRuleDraft(BaseModel):
    should_start: bool = False
    rule_text: str = ""
    interaction_goal: str = ""
    target_hint: str = ""
    expected_user_behavior: str = ""
    assistant_behavior: str = ""
    reference_instruction: str = ""


class InteractionRuleStep(BaseModel):
    order_index: int = Field(ge=0)
    expected_user_input: str = ""
    assistant_response: str = ""
    source_excerpt: str = ""
    completed: bool = False


class InteractionSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("interaction"))
    status: InteractionSessionStatus = "active"
    rule_text: str = ""
    interaction_goal: str = ""
    target_focus: BoardFocusRef | None = None
    reference_context: str = ""
    compliant_input_rule: str = ""
    expected_user_behavior: str = ""
    assistant_behavior: str = ""
    progress_note: str = ""
    pause_reason: str = ""
    turn_count: int = Field(default=0, ge=0)
    source_board_task_run_id: str | None = None
    source_board_task_version_id: str | None = None
    source_board_task_route: str | None = None
    rule_steps: list[InteractionRuleStep] = Field(default_factory=list)
    current_step_index: int = Field(default=0, ge=0)
    last_violation_reason: str = ""
    sequence_items: list[BoardFocusRef] = Field(default_factory=list)
    sequence_index: int = Field(default=0, ge=0)
    sequence_mode: str = ""


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
    board_workflow: BoardWorkflow = "unknown"
    work_mode: InitialLearningWorkMode | None = None
    granularity: InitialLearningGranularity | None = None


class BoardTaskRequirementSheet(BaseModel):
    board_workflow: BoardWorkflow = "act_on_existing_board"
    location_kind: BoardTaskLocationKind = "unspecified"
    target_hint: str = ""
    target_location: BoardFocusRef | None = None
    location_status: BoardTaskLocationStatus = "missing"
    requested_action: BoardTaskRequestedAction | None = None
    question_or_topic: str = ""
    interaction_rule_draft: InteractionRuleDraft | None = None
    missing_items: list[str] = Field(default_factory=list)
    progress: int = Field(default=0, ge=0, le=100)
    confirmation_status: BoardTaskConfirmationStatus = "none"
    clarification_question: str = ""
    failure_count: int = Field(default=0, ge=0)


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
    work_mode: InitialLearningWorkMode | None = None
    granularity: InitialLearningGranularity | None = None


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
    board_task_requirements: BoardTaskRequirementSheet | None = None
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


class ResourceSourceUnit(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sourceunit"))
    content_type: str = "text"
    text: str = ""
    page_idx: int | None = None
    page_no: int | None = None
    source_locator: str | None = None
    asset_path: str | None = None
    bbox: list[float] = Field(default_factory=list)
    order_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceLibraryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("resource"))
    name: str
    mime_type: str
    resource_type: str
    size_bytes: int
    uploaded_at: str = Field(default_factory=now_iso)
    scope_lesson_id: str | None = None
    outline: list[LibraryChapter] = Field(default_factory=list)
    concept_index: dict[str, list[str]] = Field(default_factory=dict)
    extracted_text_available: bool = False
    text_content: str | None = None
    source_path: str | None = None
    parser_provider: str = "native"
    parser_artifacts_path: str | None = None
    parser_message: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    source_units: list[ResourceSourceUnit] = Field(default_factory=list)


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
    parser_provider: str = "native"
    parser_artifacts_path: str | None = None
    parser_message: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    source_units: list[ResourceSourceUnit] = Field(default_factory=list)


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
    location_kind: BoardTaskLocationKind | None = None
    lesson_id: str | None = None
    block_id: str | None = None
    document_id: str | None = None
    segment_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    before_text: str = ""
    after_text: str = ""
    text_hash: str | None = None


class FormulaInkPayload(BaseModel):
    image_data_url: str
    source_latex: str | None = None
    action: FormulaInkAction


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


class CodexAccountView(BaseModel):
    type: str | None = None
    email: str | None = None
    plan_type: str | None = None


class CodexProviderStatus(BaseModel):
    enabled: bool
    available: bool
    configured: bool
    account: CodexAccountView | None = None
    rate_limits: dict[str, Any] | None = None
    message: str = ""


class CodexLoginStartResponse(BaseModel):
    login_id: str
    verification_url: str
    user_code: str
    expires_at: str | None = None


class CodexLoginStatusResponse(BaseModel):
    login_id: str
    status: Literal["pending", "succeeded", "failed", "cancelled", "expired"]
    error: str | None = None
    account: CodexAccountView | None = None


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


class ResourceVisualEvidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("visual"))
    content_type: str
    caption: str = ""
    page_no: int | None = None
    page_idx: int | None = None
    bbox: list[float] = Field(default_factory=list)
    source_locator: str | None = None
    relevance_reason: str = ""
    relevance_score: float = 0.0
    image_src: str = Field(default="", exclude=True, repr=False)


class ResourceReferenceContext(BaseModel):
    resource_id: str
    chapter_id: str
    resource_name: str
    chapter_title: str
    summary: str
    teaching_points: list[str] = Field(default_factory=list)
    chunks: list[ResourceContextChunk] = Field(default_factory=list)
    visual_evidence: list[ResourceVisualEvidence] = Field(default_factory=list)
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
    board_model: AIModelSelection | None = None
    selection: SelectionRef | None = None
    formula_ink: FormulaInkPayload | None = None
    interaction_mode: ChatInteractionMode = "ask"
    scope_action: ScopeAction | None = None
    resource_chapter_id: str | None = None
    resource_reference_action: ResourceReferenceAction | None = None
    resource_reference_resource_id: str | None = None
    resource_reference_chapter_id: str | None = None
    board_edit_action: BoardEditConfirmationAction | None = None
    board_edit_topic: str | None = None
    board_generation_action: BoardGenerationAction | None = None
    teaching_action: TeachingAction | None = None
    chat_edit_source_commit_id: str | None = None
    chat_edit_base_commit_id: str | None = None
    chat_edit_original_message: str | None = None
    conversation: list[ConversationTurn] = Field(default_factory=list)


class LessonView(BaseModel):
    id: str
    title: str
    slug: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    board_document: BoardDocument
    learning_requirements: LearningRequirementSheet | None = None
    board_task_requirements: BoardTaskRequirementSheet | None = None
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
    display_name: str | None = None
    avatar_url: str | None = None
    created_at: str
    last_login_at: str | None = None
    auth_identities: list[AuthIdentityView] = Field(default_factory=list)


class AuthRequest(BaseModel):
    identifier: str | None = None
    email: str | None = None
    phone: str | None = None
    guest_token: str | None = None
    password: str = Field(min_length=8, max_length=256)

    def account_identifier(self) -> str:
        return self.identifier or self.email or self.phone or ""


class AuthSessionResponse(BaseModel):
    token: str
    user: UserView


class AuthProviderView(BaseModel):
    id: str
    label: str
    description: str
    configured: bool
    kind: Literal["password", "oauth"] = "oauth"


class AdminStats(BaseModel):
    users: int
    admins: int
    packages: int
    lessons: int
    resources: int


class AdminOverview(BaseModel):
    stats: AdminStats
    users: list[UserView] = Field(default_factory=list)


class ChatResponse(BaseModel):
    chatbot_message: str
    agent_turn_decision: AgentTurnDecision | None = None
    agent_activity: list[AgentActivityEvent] = Field(default_factory=list)
    learning_requirement_sheet: LearningRequirementSheet
    active_requirement_sheet: LearningRequirementSheet | None = None
    active_interaction_session: InteractionSession | None = None
    interaction_decision: InteractionTurnDecision | None = None
    learning_clarification: LearningClarificationStatus
    requirement_run_id: str | None = None
    requirement_version_id: str | None = None
    requirement_phase: LearningRequirementRunStatus | None = None
    board_task_sheet: BoardTaskRequirementSheet | None = None
    active_board_task_sheet: BoardTaskRequirementSheet | None = None
    board_task_run_id: str | None = None
    board_task_version_id: str | None = None
    board_task_phase: BoardTaskRunStatus | None = None
    board_task_questions: list[str] = Field(default_factory=list)
    board_decision: BoardDecision
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    patch_proposal: PatchProposal | None = None
    scope_options: list[ScopeOption] = Field(default_factory=list)
    resource_matches: list[ResourceMatch] = Field(default_factory=list)
    reference_prompt: ResourceReferencePrompt | None = None
    board_edit_prompt: BoardEditPrompt | None = None
    selected_reference: ResourceReferenceContext | None = None
    resolved_focus: BoardFocusRef | None = None
    focus_candidates: list[BoardFocusRef] = Field(default_factory=list)
    board_search_evidence: BoardSearchEvidence | None = None
    requirement_cleared: bool = False
    board_document_operation_status: BoardDocumentOperationStatus = "none"
    board_document_operation_failure_reason: str | None = None
    board_patch_diff: list[DiffPreviewItem] = Field(default_factory=list)
    created_lesson: LessonView | None = None
    teaching_progress: SectionTeachingProgressView | None = None
    course_package: CoursePackageView


class RequirementUpdateStreamPayload(BaseModel):
    learning_requirement_sheet: LearningRequirementSheet
    active_requirement_sheet: LearningRequirementSheet | None = None
    learning_clarification: LearningClarificationStatus
    requirement_run_id: str | None = None
    requirement_version_id: str | None = None
    requirement_phase: LearningRequirementRunStatus | None = None
    clarification_questions: list[str] = Field(default_factory=list)


class BoardTaskUpdateStreamPayload(BaseModel):
    board_task_sheet: BoardTaskRequirementSheet
    active_board_task_sheet: BoardTaskRequirementSheet | None = None
    board_task_run_id: str | None = None
    board_task_version_id: str | None = None
    board_task_phase: BoardTaskRunStatus | None = None
    board_task_questions: list[str] = Field(default_factory=list)


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
