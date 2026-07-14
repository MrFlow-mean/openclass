export type ScopeAction =
  | "patch_current_lesson"
  | "append_section"
  | "create_branch"
  | "create_child_lesson"
  | "create_new_lesson";

export type BoardAction =
  | "clarify_request"
  | "no_change"
  | "edit_board"
  | "append_section"
  | "create_new_lesson"
  | "await_scope_choice"
  | "await_focus_choice";

export type BoardEditConfirmationAction = "confirm" | "skip";
export type ChatInteractionMode = "ask" | "direct_edit";
export type BoardTaskAction =
  | "generate_board"
  | "append_section"
  | "explain_target"
  | "rewrite_target"
  | "expand_target"
  | "simplify_target";
export type AIProvider =
  | "openai"
  | "openai_codex"
  | "anthropic"
  | "google"
  | "deepseek"
  | "kimi"
  | "minimax"
  | "openai_compatible"
  | "anthropic_compatible";
export type AIModelCapability = "text" | "realtime";
export type AIRealtimeTransport = "openai_webrtc" | "gemini_live_websocket";
export type DocumentMarginPreset = "narrow" | "normal" | "wide";
export type DocumentOrientation = "portrait" | "landscape";
export type DocumentPageSize = "a4" | "letter" | "a3";
export type DocumentBackgroundStyle = "plain" | "warm" | "grid";

export interface DocumentPageSettings {
  margin_preset: DocumentMarginPreset;
  orientation: DocumentOrientation;
  page_size: DocumentPageSize;
  columns: 1 | 2;
  page_border: boolean;
  background_style: DocumentBackgroundStyle;
  watermark_text: string;
  line_numbers: boolean;
  show_page_number: boolean;
  header_text: string;
  footer_text: string;
}

export interface BoardDocument {
  id: string;
  title: string;
  content_json: Record<string, unknown>;
  content_html: string;
  content_text: string;
  page_settings: DocumentPageSettings;
}

export type PatchOperationType =
  | "insert_block"
  | "delete_block"
  | "update_block_content"
  | "replace_range_in_block"
  | "move_block"
  | "update_block_style"
  | "attach_asset";

export interface DiffPreviewItem {
  op: PatchOperationType;
  block_id?: string | null;
  node_path?: number[];
  heading_path?: string[];
  before_text?: string;
  after_text?: string;
  summary: string;
}

export type LearningSourceConfirmationStatus = "none" | "confirmed" | "skipped" | "stale";

export interface LearningSourceReference {
  evidence_bundle_id: string;
  source_ingestion_id: string;
  source_title: string;
  source_chapter_id: string;
  chapter_number: string;
  chapter_title: string;
  section_path: string[];
  source_locator: string;
  page_range: string;
  page_start?: number | null;
  page_end?: number | null;
  body_start_offset?: number | null;
  body_end_offset?: number | null;
  chunk_ids: string[];
  source_structure_id: string;
  source_structure_updated_at: string;
  content_hash: string;
}

export interface LearningSourceGrounding {
  requested_by_user: boolean;
  confirmation_status: LearningSourceConfirmationStatus;
  confirmed_bundle_id: string;
  confirmed_at?: string | null;
  confirmed_references: LearningSourceReference[];
}

export interface LearningRequirementSheet {
  theme: string;
  learning_goal: string;
  level: string;
  known_background: string;
  current_questions: string[];
  learning_need_checklist: string[];
  target_depth: string;
  output_preference: string;
  boundary: string;
  board_scope: string[];
  success_criteria: string;
  risk_notes: string[];
  target_location?: BoardFocusRef | null;
  location_status?: "missing" | "selected" | "resolved" | "ambiguous";
  action_type?: BoardTaskAction | null;
  action_instruction?: string;
  location_clarification_question?: string;
  interaction_rule_draft?: InteractionRuleDraft | null;
  board_workflow?: BoardWorkflow | null;
  work_mode?: InitialLearningWorkMode | null;
  granularity?: InitialLearningGranularity | null;
  source_grounding?: LearningSourceGrounding;
}

export type BoardWorkflow = "generate_from_scratch" | "act_on_existing_board" | "unknown";
export type InitialLearningWorkMode = "knowledge_board" | "narrow_topic" | "practice_artifact" | "unknown";
export type InitialLearningGranularity = "single_knowledge_point" | "source_chapter" | "broad_topic" | "practice_artifact" | "unclear";
export type BoardTaskRunStatus = "collecting" | "ready" | "awaiting_confirmation" | "consumed" | "not_executed" | "archived";
export type BoardDocumentOperationStatus = "none" | "succeeded" | "failed";
export type BoardTaskRequestedAction = "write" | "edit" | "explain" | "chat";
export type BoardTaskConfirmationStatus = "none" | "awaiting" | "confirmed" | "declined";
export type BoardTaskLocationStatus = "missing" | "selected" | "resolved" | "ambiguous" | "content_absent";
export type BoardTaskLocationKind = "target_range" | "insertion_anchor" | "unspecified";

export interface BoardTaskRequirementSheet {
  board_workflow?: BoardWorkflow | null;
  location_kind?: BoardTaskLocationKind;
  target_hint: string;
  target_location?: BoardFocusRef | null;
  location_status: BoardTaskLocationStatus;
  requested_action?: BoardTaskRequestedAction | null;
  question_or_topic: string;
  interaction_rule_draft?: InteractionRuleDraft | null;
  missing_items: string[];
  progress: number;
  confirmation_status: BoardTaskConfirmationStatus;
  clarification_question: string;
  failure_count: number;
}

export interface InteractionRuleDraft {
  should_start: boolean;
  rule_text: string;
  interaction_goal: string;
  target_hint: string;
  expected_user_behavior: string;
  assistant_behavior: string;
  reference_instruction: string;
}

export interface InteractionRuleStep {
  order_index: number;
  expected_user_input: string;
  assistant_response: string;
  source_excerpt: string;
  completed: boolean;
}

export interface LearningRequirementChecklistItem {
  title: string;
  is_clear: boolean;
  evidence: string;
}

export interface LearningRequirementKeyFact {
  label: string;
  value: string;
  evidence: string;
  category?: "learning" | "level" | "vocabulary" | "scenario" | "output" | "other" | null;
}

export interface LearningClarificationStatus {
  progress: number;
  label: string;
  reason: string;
  missing_items: string[];
  can_start: boolean;
  forced_start: boolean;
  summary: string;
  key_facts: LearningRequirementKeyFact[];
  checklist: LearningRequirementChecklistItem[];
  next_question: string;
  ready_for_board: boolean;
  work_mode?: InitialLearningWorkMode | null;
  granularity?: InitialLearningGranularity | null;
}

export type LearningRequirementRunStatus = "collecting" | "ready" | "frozen" | "consumed" | "archived";

export interface TeachingGuideMapping {
  block_id: string;
  supports_goal: string;
  teaching_mode: "definition" | "intuition" | "analogy" | "example" | "dialogue";
  focus_points: string[];
  optional_points: string[];
  difficult_points: string[];
  check_questions: string[];
}

export interface TeachingGuide {
  lesson_id: string;
  summary: string;
  structure_note: string;
  pacing: string;
  mappings: TeachingGuideMapping[];
  strategy: string;
}

export interface CommitRecord {
  id: string;
  label: string;
  message: string;
  branch_name: string;
  created_at: string;
  parent_ids: string[];
  operations: Array<Record<string, unknown>>;
  snapshot: BoardDocument;
  metadata?: Record<string, unknown> & {
    board_patch_diff?: DiffPreviewItem[];
    board_patch_risk_level?: "low" | "medium" | "high";
    history_node_kind?: "chat" | "document" | "restore" | "system";
    history_node_title?: string;
    history_node_summary?: string;
  };
}

export interface BranchRef {
  name: string;
  head_commit_id: string;
  base_commit_id: string;
  created_at: string;
}

export interface LessonHistoryGraph {
  branches: Record<string, BranchRef>;
  commits: CommitRecord[];
  current_branch: string;
}

export interface Lesson {
  id: string;
  title: string;
  slug: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
  learning_requirements?: LearningRequirementSheet | null;
  board_task_requirements?: BoardTaskRequirementSheet | null;
  active_interaction_session?: InteractionSession | null;
  history_graph: LessonHistoryGraph;
  created_at: string;
  updated_at: string;
}

export interface CourseGraphEdge {
  id: string;
  source_lesson_id: string;
  target_lesson_id: string;
  relationship:
    | "recommended_next"
    | "prerequisite"
    | "deep_dive"
    | "alternate_path"
    | "derived_from";
}

export interface LibraryChapter {
  id: string;
  title: string;
  level: number;
  page_range?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  summary: string;
  keywords: string[];
  prerequisites: string[];
  parent_id?: string | null;
  parent_title?: string | null;
  path: string[];
  locator_hint?: string | null;
  order_index: number;
  scan_strategy: "outline_only" | "heading_section" | "page_window" | "fulltext_match";
}

export interface ResourceSourceUnit {
  id: string;
  content_type: string;
  text: string;
  page_idx?: number | null;
  page_no?: number | null;
  source_locator?: string | null;
  url?: string | null;
  heading_path: string[];
  paragraph_index?: number | null;
  timestamp_start?: number | null;
  timestamp_end?: number | null;
  asset_path?: string | null;
  bbox: number[];
  order_index: number;
  metadata: Record<string, unknown>;
}

export type ResourceSourceType =
  | "local_file"
  | "web_url"
  | "audio_file"
  | "video_file"
  | "video_url"
  | "pasted_text"
  | "transcript";

export type SourceIngestionStatus = "queued" | "fetching" | "parsing" | "indexing" | "ready" | "failed";
export type EvidenceBundleStatus = "candidate" | "confirmed" | "consumed" | "archived";
export type EvidencePurpose = "chat" | "board_generation" | "board_edit" | "board_explain" | "board_chat";
export type SourceStructureStatus = "pending" | "building" | "ready" | "linear_only" | "failed";
export type SourceStructureStrategy =
  | "epub_navigation"
  | "epub_heading"
  | "pdf_outline"
  | "pdf_toc"
  | "pdf_merged_toc"
  | "docx_heading"
  | "markdown_heading"
  | "linear_text";

export interface SourceIngestionJob {
  id: string;
  resource_id?: string | null;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  adapter: string;
  status: SourceIngestionStatus;
  progress: number;
  error: string;
  phase_history: string[];
  created_at: string;
  updated_at: string;
}

export interface SourceIngestionRecord {
  id: string;
  owner_user_id: string;
  package_id: string;
  title: string;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  status: SourceIngestionStatus;
  error: string;
  structure_status: SourceStructureStatus;
  structure_strategy?: SourceStructureStrategy | null;
  structure_has_verified_toc: boolean;
  structure_error: string;
  structure_updated_at?: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface RetrievalEvidence {
  id: string;
  source_ingestion_id: string;
  source_title: string;
  source_uri?: string | null;
  chapter_id: string;
  section_path: string[];
  page_range: string;
  chunk_ids: string[];
  excerpt: string;
  expanded_text: string;
  relevance_score: number;
  reason: string;
  token_count: number;
  metadata: Record<string, unknown>;
}

export interface SourceStructure {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  status: SourceStructureStatus;
  strategy: SourceStructureStrategy;
  has_verified_toc: boolean;
  chapter_count: number;
  chunk_count: number;
  confidence: number;
  error: string;
  warnings: string[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface SourceChapter {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  parent_id?: string | null;
  number: string;
  normalized_number: string;
  title: string;
  level: number;
  path: string[];
  order_index: number;
  source_locator: string;
  body_start_offset?: number | null;
  body_end_offset?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  anchor_status: "verified" | "unverified";
  confidence: number;
  excerpt: string;
  metadata: Record<string, unknown>;
}

export interface SourceChunk {
  id: string;
  owner_user_id: string;
  package_id: string;
  source_ingestion_id: string;
  chapter_id?: string | null;
  order_index: number;
  source_locator: string;
  text: string;
  start_offset: number;
  end_offset: number;
  page_start?: number | null;
  page_end?: number | null;
  token_count: number;
  metadata: Record<string, unknown>;
}

export interface SourceStructureView {
  source: SourceIngestionRecord;
  structure?: SourceStructure | null;
  chapters: SourceChapter[];
  chunks: SourceChunk[];
}

export interface SourceContentView {
  source: SourceIngestionRecord;
  content: string;
}

export interface EvidenceBundle {
  id: string;
  owner_user_id: string;
  package_id: string;
  lesson_id?: string | null;
  requirement_run_id?: string | null;
  board_task_run_id?: string | null;
  purpose: EvidencePurpose;
  status: EvidenceBundleStatus;
  query: string;
  evidence_items: RetrievalEvidence[];
  context_text: string;
  token_count: number;
  confirmed_by_user: boolean;
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  metadata: Record<string, unknown>;
}

export interface EvidenceConfirmationResult {
  evidence_bundle: EvidenceBundle;
  active_requirement_sheet?: LearningRequirementSheet | null;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
}

export type ResearchSearchMode = "text" | "semantic" | "hybrid";
export type ResearchContextMode = "retrieval" | "full" | "notes" | "off";
export type ResearchMessageRole = "user" | "assistant" | "system";
export type ResearchArtifactStatus = "queued" | "generating" | "ready" | "failed";
export type ResearchArtifactKind = "insight" | "summary" | "study_guide" | "faq" | "timeline" | "custom" | "podcast";
export type ResearchArtifactLength = "short" | "medium" | "long";

export interface ResearchCitation {
  source_ingestion_id: string;
  source_title: string;
  source_uri?: string | null;
  chapter_id: string;
  section_path: string[];
  page_range: string;
  chunk_ids: string[];
  excerpt: string;
}

export interface ResearchNote {
  id: string;
  owner_user_id: string;
  package_id: string;
  title: string;
  content: string;
  tags: string[];
  citations: ResearchCitation[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ResearchNoteCreate {
  title?: string;
  content: string;
  tags?: string[];
  citations?: ResearchCitation[];
  metadata?: Record<string, unknown>;
}

export interface ResearchNoteUpdate {
  title?: string;
  content?: string;
  tags?: string[];
  citations?: ResearchCitation[];
  metadata?: Record<string, unknown>;
}

export interface ResearchSearchRequest {
  query: string;
  mode?: ResearchSearchMode;
  source_ingestion_ids?: string[];
  include_notes?: boolean;
  limit?: number;
  token_budget?: number;
}

export interface ResearchSearchResult {
  kind: "source" | "note";
  score: number;
  evidence?: RetrievalEvidence | null;
  note?: ResearchNote | null;
}

export interface ResearchSearchResponse {
  query: string;
  mode: ResearchSearchMode;
  results: ResearchSearchResult[];
}

export interface ResearchChatMessage {
  id: string;
  thread_id: string;
  role: ResearchMessageRole;
  content: string;
  citations: ResearchCitation[];
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ResearchChatThread {
  id: string;
  owner_user_id: string;
  package_id: string;
  title: string;
  context_mode: ResearchContextMode;
  source_ingestion_ids: string[];
  note_ids: string[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ResearchChatThreadCreate {
  title?: string;
  context_mode?: ResearchContextMode;
  source_ingestion_ids?: string[];
  note_ids?: string[];
}

export interface ResearchChatThreadUpdate {
  title?: string;
  context_mode?: ResearchContextMode;
  source_ingestion_ids?: string[];
  note_ids?: string[];
}

export interface ResearchChatRequest {
  message: string;
  text_model?: AIModelSelection | null;
  context_mode?: ResearchContextMode;
  source_ingestion_ids?: string[];
  note_ids?: string[];
}

export interface ResearchChatResponse {
  thread: ResearchChatThread;
  message: ResearchChatMessage;
}

export interface ResearchSpeaker {
  name: string;
  role?: string;
  voice?: string;
  instructions?: string;
}

export interface ResearchArtifact {
  id: string;
  owner_user_id: string;
  package_id: string;
  kind: ResearchArtifactKind;
  status: ResearchArtifactStatus;
  title: string;
  content: string;
  transcript: string;
  audio_url?: string | null;
  source_ingestion_ids: string[];
  note_ids: string[];
  citations: ResearchCitation[];
  error: string;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ResearchArtifactCreate {
  kind: ResearchArtifactKind;
  title?: string;
  instructions?: string;
  language?: string;
  tone?: string;
  length?: ResearchArtifactLength;
  segment_count?: number | null;
  source_ingestion_ids?: string[];
  note_ids?: string[];
  speakers?: ResearchSpeaker[];
  text_model?: AIModelSelection | null;
  synthesize_audio?: boolean;
}

export interface ResearchAskRequest {
  question: string;
  source_ingestion_ids?: string[];
  note_ids?: string[];
  include_notes?: boolean;
  text_model?: AIModelSelection | null;
  max_queries?: number;
}

export interface ResearchAskResponse {
  question: string;
  search_queries: string[];
  answer: string;
  citations: ResearchCitation[];
}

export interface ResearchCapabilities {
  native_ingestion: boolean;
  text_search: boolean;
  semantic_search: boolean;
  notes: boolean;
  persisted_chat: boolean;
  transformations: boolean;
  podcast_script: boolean;
  podcast_audio: boolean;
  supported_source_types: string[];
}

export interface ResearchTransformation {
  id: string;
  owner_user_id: string;
  package_id: string;
  name: string;
  instructions: string;
  output_kind: ResearchArtifactKind;
  run_on_import: boolean;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ResearchTransformationCreate {
  name: string;
  instructions: string;
  output_kind?: ResearchArtifactKind;
  run_on_import?: boolean;
  metadata?: Record<string, unknown>;
}

export interface ResearchTransformationUpdate {
  name?: string;
  instructions?: string;
  output_kind?: ResearchArtifactKind;
  run_on_import?: boolean;
  metadata?: Record<string, unknown>;
}

export interface ResearchTransformationRun {
  title?: string;
  source_ingestion_ids?: string[];
  note_ids?: string[];
  text_model?: AIModelSelection | null;
}

export interface ResearchSpeakerProfile {
  id: string;
  owner_user_id: string;
  package_id: string;
  name: string;
  speakers: ResearchSpeaker[];
  created_at: string;
  updated_at: string;
}

export interface ResearchSpeakerProfileCreate {
  name: string;
  speakers: ResearchSpeaker[];
}

export interface ResearchSpeakerProfileUpdate {
  name?: string;
  speakers?: ResearchSpeaker[];
}

export interface ResearchEpisodeProfile {
  id: string;
  owner_user_id: string;
  package_id: string;
  name: string;
  language: string;
  tone: string;
  length: ResearchArtifactLength;
  segment_count: number;
  instructions: string;
  created_at: string;
  updated_at: string;
}

export interface ResearchEpisodeProfileCreate {
  name: string;
  language?: string;
  tone?: string;
  length?: ResearchArtifactLength;
  segment_count?: number;
  instructions?: string;
}

export interface ResearchEpisodeProfileUpdate {
  name?: string;
  language?: string;
  tone?: string;
  length?: ResearchArtifactLength;
  segment_count?: number;
  instructions?: string;
}

export type ResourcePageRole =
  | "cover"
  | "copyright"
  | "toc"
  | "preface"
  | "body"
  | "appendix"
  | "back_matter"
  | "unknown";

export interface ResourcePageSection {
  role: ResourcePageRole;
  page_idx_start?: number | null;
  page_idx_end?: number | null;
  page_no_start?: number | null;
  page_no_end?: number | null;
  title: string;
  confidence: number;
  evidence_excerpt: string;
}

export interface ResourcePageMapEntry {
  page_idx: number;
  page_no: number;
  role: ResourcePageRole;
  printed_page?: number | null;
  body_offset?: number | null;
  confidence: number;
  evidence_excerpt: string;
}

export interface ResourcePageStructure {
  page_count: number;
  body_start_page_idx?: number | null;
  body_start_page_no?: number | null;
  toc_page_indices: number[];
  sections: ResourcePageSection[];
  page_map: ResourcePageMapEntry[];
  diagnostics: string[];
  confidence: number;
}

export interface ResourceLibraryItem {
  id: string;
  name: string;
  mime_type: string;
  resource_type: string;
  size_bytes: number;
  uploaded_at: string;
  scope_lesson_id?: string | null;
  outline: LibraryChapter[];
  concept_index: Record<string, string[]>;
  extracted_text_available: boolean;
  source_type: ResourceSourceType;
  source_uri?: string | null;
  ingestion_status: SourceIngestionStatus;
  ingestion_error: string;
  ingestion_progress: number;
  ingestion_adapter: string;
  ingestion_job?: SourceIngestionJob | null;
  parser_provider: string;
  parser_artifacts_path?: string | null;
  parser_message: string;
  parse_warnings: string[];
  source_units: ResourceSourceUnit[];
  page_structure?: ResourcePageStructure | null;
}

export interface CoursePackage {
  id: string;
  title: string;
  summary: string;
  is_standalone: boolean;
  lessons: Lesson[];
  course_graph: CourseGraphEdge[];
  resources: ResourceLibraryItem[];
  open_lesson_ids: string[];
  active_lesson_id?: string | null;
  workspace_tab_order: string[];
}

export interface WorkspaceState {
  packages: CoursePackage[];
  active_package_id?: string | null;
}

export interface UserView {
  id: string;
  email: string;
  phone?: string | null;
  role: "user" | "admin" | "guest";
  display_name?: string | null;
  avatar_url?: string | null;
  created_at: string;
  last_login_at?: string | null;
  auth_identities: AuthIdentityView[];
}

export interface AuthSessionResponse {
  token: string;
  user: UserView;
}

export interface AuthIdentityView {
  provider: string;
  provider_label: string;
  email?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
  created_at: string;
  last_login_at?: string | null;
}

export interface AuthProviderView {
  id: string;
  label: string;
  description: string;
  configured: boolean;
  kind: "password" | "oauth";
}

export interface AdminOverview {
  stats: {
    users: number;
    admins: number;
    packages: number;
    lessons: number;
    resources: number;
  };
  users: UserView[];
}

export interface AIModelSelection {
  provider: AIProvider;
  model: string;
}

export interface AIModelOption {
  provider: AIProvider;
  model: string;
  label: string;
  capability: AIModelCapability;
  enabled: boolean;
  configured: boolean;
  default: boolean;
  transport?: AIRealtimeTransport | null;
}

export interface AIModelCatalog {
  text: AIModelOption[];
  realtime: AIModelOption[];
  defaults: {
    text: AIModelSelection;
    realtime: AIModelSelection;
  };
}

export interface CodexAccountView {
  type?: string | null;
  email?: string | null;
  plan_type?: string | null;
}

export interface CodexProviderStatus {
  enabled: boolean;
  available: boolean;
  configured: boolean;
  account?: CodexAccountView | null;
  rate_limits?: Record<string, unknown> | null;
  message: string;
}

export interface CodexLoginStartResponse {
  login_id: string;
  verification_url: string;
  user_code: string;
  expires_at?: string | null;
}

export interface CodexLoginStatusResponse {
  login_id: string;
  status: "pending" | "succeeded" | "failed" | "cancelled" | "expired";
  error?: string | null;
  account?: CodexAccountView | null;
}

export interface SelectionRef {
  kind: "chat" | "board" | "source";
  excerpt: string;
  location_kind?: BoardTaskLocationKind | null;
  lesson_id?: string | null;
  block_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  heading_path?: string[];
  before_text?: string;
  after_text?: string;
  text_hash?: string | null;
  source_ingestion_id?: string | null;
  source_title?: string;
  source_uri?: string | null;
  source_chapter_id?: string | null;
  source_chapter_number?: string;
  source_chapter_title?: string;
  source_excerpt?: string;
  source_page_range?: string;
  source_locator?: string;
  source_page_start?: number | null;
  source_page_end?: number | null;
}

export type FormulaInkAction = "reference" | "replace";

export interface FormulaInkPayload {
  image_data_url: string;
  source_latex?: string | null;
  action: FormulaInkAction;
}

export interface BoardFocusRef {
  source: "board" | "chat";
  lesson_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  kind?: "heading" | "paragraph" | "list" | "table" | "code" | "image" | "other" | null;
  heading_path: string[];
  excerpt: string;
  before_text: string;
  after_text: string;
  text_hash?: string | null;
  excerpt_hash?: string | null;
  confidence: number;
  reason: string;
  display_label?: string;
  match_id?: string | null;
  source_segment_ids?: string[];
  order_start?: number | null;
  order_end?: number | null;
  score_breakdown?: Record<string, number>;
}

export interface BoardReadContext {
  target_focus: BoardFocusRef;
  target_excerpt: string;
  surrounding_context: string;
  before_text: string;
  after_text: string;
  range_label: string;
  source_segment_ids: string[];
  order_start?: number | null;
  order_end?: number | null;
  confidence: number;
}

export interface BoardSearchQueryPlan {
  query_text: string;
  search_terms: string[];
  structured_target: string;
  scope_hint: string;
  action_type?: BoardTaskAction | null;
}

export interface BoardSearchCandidate {
  match_id: string;
  source: string;
  chunk_id?: string | null;
  source_segment_ids: string[];
  focus: BoardFocusRef;
  score: number;
  score_breakdown: Record<string, number>;
  reason: string;
}

export interface BoardSearchEvidence {
  status: "selected" | "found" | "ambiguous" | "missing" | "content_absent";
  query_plan: BoardSearchQueryPlan;
  candidates: BoardSearchCandidate[];
  selected_match_id?: string | null;
  source: string;
  confidence: number;
  range_label: string;
  order_start?: number | null;
  order_end?: number | null;
  candidate_count: number;
  failure_reason_code: string;
  read_context?: BoardReadContext | null;
  reason: string;
}

export interface InteractionSession {
  id: string;
  status: "active" | "paused";
  rule_text: string;
  interaction_goal: string;
  target_focus?: BoardFocusRef | null;
  reference_context: string;
  compliant_input_rule?: string;
  expected_user_behavior: string;
  assistant_behavior: string;
  progress_note: string;
  pause_reason: string;
  turn_count: number;
  source_board_task_run_id?: string | null;
  source_board_task_version_id?: string | null;
  source_board_task_route?: string | null;
  rule_steps?: InteractionRuleStep[];
  current_step_index?: number;
  last_violation_reason?: string;
  sequence_items?: BoardFocusRef[];
  sequence_index?: number;
  sequence_mode?: string;
}

export interface InteractionTurnDecision {
  route:
    | "continue_rule"
    | "rule_violation"
    | "side_learning_request"
    | "resume_rule"
    | "exit_rule"
    | "new_task";
  reason: string;
  progress_note: string;
  user_intent: string;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ScopeOption {
  action: ScopeAction;
  label: string;
  description: string;
}

export interface BoardEditPrompt {
  topic: string;
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
}

export interface BoardDecision {
  action: BoardAction;
  reason: string;
}

export interface PatchProposal {
  id: string;
  rationale: string;
  commit_label: string;
  operations: Array<Record<string, unknown>>;
  diff_preview: Array<Record<string, unknown>>;
  target_action: ScopeAction;
  suggested_title?: string | null;
}

export interface ChatRequestPayload {
  message: string;
  text_model?: AIModelSelection | null;
  board_model?: AIModelSelection | null;
  selection?: SelectionRef | null;
  formula_ink?: FormulaInkPayload | null;
  interaction_mode?: ChatInteractionMode;
  scope_action?: ScopeAction | null;
  board_edit_action?: BoardEditConfirmationAction | null;
  board_edit_topic?: string | null;
  board_generation_action?: "start" | null;
  board_task_execution_action?: "resume_confirmed" | null;
  teaching_action?: "continue" | "restart" | null;
  chat_edit_source_commit_id?: string | null;
  chat_edit_base_commit_id?: string | null;
  chat_edit_original_message?: string | null;
  conversation?: ConversationTurn[];
}

export interface SectionTeachingProgress {
  section_index: number;
  section_count: number;
  current_section_title: string;
  has_next_section: boolean;
  waiting_for_continue: boolean;
}

export type AgentTurnRoute =
  | "ordinary_chat"
  | "blank_requirement_refine"
  | "blank_board_generate"
  | "post_generation_teaching_start"
  | "board_teaching_continue"
  | "board_task_refine_or_execute"
  | "interaction_session_turn";

export type AgentActivityStage =
  | "turn_decision"
  | "resolve_target"
  | "build_context"
  | "execute_role"
  | "verify"
  | "persist_history"
  | "final";

export type AgentActivityStatus = "pending" | "running" | "completed" | "blocked" | "failed" | "skipped";

export interface AgentTurnDecision {
  route: AgentTurnRoute;
  reason: string;
  required_role: string;
  blockers: string[];
  next_step: string;
  needs_user_confirmation: boolean;
}

export interface AgentActivityEvent {
  id: string;
  turn_id: string;
  stage: AgentActivityStage;
  label: string;
  status: AgentActivityStatus;
  role: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ChatResponse {
  chatbot_message: string;
  agent_turn_decision?: AgentTurnDecision | null;
  agent_activity?: AgentActivityEvent[];
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  active_interaction_session?: InteractionSession | null;
  interaction_decision?: InteractionTurnDecision | null;
  learning_clarification: LearningClarificationStatus;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
  learning_requirement_operation_status?: "none" | "succeeded" | "failed";
  learning_requirement_operation_failure_reason?: string | null;
  board_task_sheet?: BoardTaskRequirementSheet | null;
  active_board_task_sheet?: BoardTaskRequirementSheet | null;
  board_task_run_id?: string | null;
  board_task_version_id?: string | null;
  board_task_phase?: BoardTaskRunStatus | null;
  board_task_questions?: string[];
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  patch_proposal?: PatchProposal | null;
  scope_options: ScopeOption[];
  board_edit_prompt?: BoardEditPrompt | null;
  resolved_focus?: BoardFocusRef | null;
  focus_candidates?: BoardFocusRef[];
  board_search_evidence?: BoardSearchEvidence | null;
  evidence_bundle?: EvidenceBundle | null;
  candidate_evidence_bundle?: EvidenceBundle | null;
  requirement_cleared?: boolean;
  board_document_operation_status?: BoardDocumentOperationStatus;
  board_document_operation_failure_reason?: string | null;
  board_patch_diff?: DiffPreviewItem[];
  created_lesson?: Lesson | null;
  teaching_progress?: SectionTeachingProgress | null;
  course_package: CoursePackage;
}

export interface RequirementUpdateStreamPayload {
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  learning_clarification: LearningClarificationStatus;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
  clarification_questions: string[];
}

export interface BoardTaskUpdateStreamPayload {
  board_task_sheet: BoardTaskRequirementSheet;
  active_board_task_sheet?: BoardTaskRequirementSheet | null;
  board_task_run_id?: string | null;
  board_task_version_id?: string | null;
  board_task_phase?: BoardTaskRunStatus | null;
  board_task_questions: string[];
}

export interface RealtimeConnectPayload {
  offer_sdp: string;
  latest_assistant_message?: string | null;
  client_session_id?: string | null;
  realtime_model?: AIModelSelection | null;
}

export interface RealtimeConnectResponse {
  answer_sdp: string;
  provider: AIProvider;
  model: string;
  voice: string;
  call_id?: string | null;
  tools_enabled?: boolean;
  client_session_id?: string | null;
}

export interface GoogleRealtimeSessionPayload {
  latest_assistant_message?: string | null;
  client_session_id?: string | null;
  realtime_model?: AIModelSelection | null;
}

export interface GoogleRealtimeSessionResponse {
  websocket_url: string;
  setup: Record<string, unknown>;
  provider: "google";
  model: string;
  voice: string;
}

export interface RealtimeEventLogPayload {
  client_session_id?: string | null;
  lesson_title?: string | null;
  role: "user" | "assistant" | "tool";
  transport_event_type: string;
  transcript: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_status?: string | null;
}

export interface DocumentSavePayload {
  document: BoardDocument;
  label?: string;
  message?: string;
  metadata?: Record<string, unknown>;
  base_commit_id?: string | null;
}

export interface DocumentAIEditPayload {
  instruction: string;
  selection_text?: string | null;
  replace_whole?: boolean;
  conversation?: ConversationTurn[];
}
