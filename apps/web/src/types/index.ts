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
  | "await_reference_choice"
  | "await_focus_choice";

export type ResourceReferenceAction = "confirm" | "skip";
export type DocumentEvidenceAction = "insert_original" | "reference_generate";
export type ResourceIndexStatus = "queued" | "processing" | "ready" | "no_text" | "failed";
export type BoardEditConfirmationAction = "confirm" | "skip";
export type StrongReasoningAction = "confirm" | "skip";
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
}

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
  metadata?: Record<string, unknown>;
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

export type MergeBranchChoice = "target" | "source";
export type MergeBranchSectionStatus = "no_change" | "source_only" | "target_only" | "conflict";
export type MergeBranchSectionKey = "document" | "requirements" | "session";

export interface MergeBranchSectionPreview {
  status: MergeBranchSectionStatus;
  recommended_choice: MergeBranchChoice;
  requires_confirmation: boolean;
  base_summary: string;
  target_summary: string;
  source_summary: string;
}

export interface MergeBranchPreviewResponse {
  source_branch: string;
  target_branch: string;
  base_commit_id: string;
  target_head_commit_id: string;
  source_head_commit_id: string;
  can_merge: boolean;
  already_merged: boolean;
  document: MergeBranchSectionPreview;
  requirements: MergeBranchSectionPreview;
  session: MergeBranchSectionPreview;
}

export type MergeBranchChoices = Record<MergeBranchSectionKey, MergeBranchChoice>;

export interface Lesson {
  id: string;
  title: string;
  slug: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
  learning_requirements?: LearningRequirementSheet | null;
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
  locator_hint?: string | null;
  order_index: number;
  scan_strategy: "outline_only" | "heading_section" | "page_window" | "fulltext_match";
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
  index_status: ResourceIndexStatus;
  index_message: string;
  index_updated_at: string;
  page_count: number;
  indexed_block_count: number;
}

export type ResourceActivityAction = "uploaded" | "deleted";

export interface ResourceActivityEvent {
  id: string;
  action: ResourceActivityAction;
  resource_id: string;
  resource_name: string;
  mime_type: string;
  resource_type: string;
  size_bytes: number;
  occurred_at: string;
  scope_lesson_id?: string | null;
}

export interface CoursePackage {
  id: string;
  title: string;
  summary: string;
  is_standalone: boolean;
  lessons: Lesson[];
  course_graph: CourseGraphEdge[];
  resources: ResourceLibraryItem[];
  resource_events: ResourceActivityEvent[];
  open_lesson_ids: string[];
  active_lesson_id?: string | null;
  workspace_tab_order: string[];
}

export interface WorkspaceState {
  packages: CoursePackage[];
  active_package_id?: string | null;
}

export interface PublicUserView {
  id: string;
  display_name: string;
  avatar_url?: string | null;
}

export interface OpenCourseStats {
  lessons: number;
  resources: number;
  forks: number;
  open_contributions: number;
  contributors: number;
  maintainers: number;
}

export interface OpenCourseSummary {
  id: string;
  package_id: string;
  owner: PublicUserView;
  title: string;
  summary: string;
  topics: string[];
  stats: OpenCourseStats;
  published_at: string;
  updated_at: string;
}

export interface CourseMaintainerView {
  publication_id: string;
  user: PublicUserView;
  role: "owner" | "maintainer";
  added_at: string;
}

export interface CourseForkView {
  id: string;
  publication_id: string;
  fork_package_id: string;
  source_package_id: string;
  created_at: string;
  updated_at: string;
}

export type CourseContributionStatus = "open" | "changes_requested" | "merged" | "closed";
export type CourseContributionReviewAction = "request_changes" | "close" | "merge";
export type CourseChangeStatus = "unchanged" | "edited" | "added" | "deleted";

export interface ContributionLessonChange {
  status: CourseChangeStatus;
  source_lesson_id?: string | null;
  fork_lesson_id?: string | null;
  title: string;
  base_summary: string;
  current_summary: string;
  proposed_summary: string;
  current_changed: boolean;
}

export interface ContributionResourceChange {
  status: CourseChangeStatus;
  source_resource_id?: string | null;
  fork_resource_id?: string | null;
  name: string;
}

export interface CourseContributionEventView {
  id: string;
  actor: PublicUserView;
  event_type: string;
  message: string;
  created_at: string;
}

export interface CourseContributionSummary {
  id: string;
  publication_id: string;
  fork_id: string;
  title: string;
  description: string;
  status: CourseContributionStatus;
  contributor: PublicUserView;
  lesson_changes: ContributionLessonChange[];
  resource_changes: ContributionResourceChange[];
  created_at: string;
  updated_at: string;
  reviewed_by?: PublicUserView | null;
  reviewed_at?: string | null;
}

export interface CourseContributionView extends CourseContributionSummary {
  course: OpenCourseSummary;
  baseline_package?: CoursePackage | null;
  proposed_package?: CoursePackage | null;
  source_package?: CoursePackage | null;
  events: CourseContributionEventView[];
}

export interface OpenCourseDetail {
  course: OpenCourseSummary;
  package: CoursePackage;
  maintainers: CourseMaintainerView[];
  contributions: CourseContributionSummary[];
  viewer_can_review: boolean;
  viewer_is_owner: boolean;
  viewer_fork?: CourseForkView | null;
}

export interface OpenCourseListResponse {
  courses: OpenCourseSummary[];
}

export interface ForkCourseResponse {
  fork: CourseForkView;
  course_package: CoursePackage;
}

// 用户角色与状态：后端 models.UserView 的同名 Literal 是权威来源，这里保持取值一致。
export type UserRole = "user" | "admin" | "guest";
export type UserStatus = "active" | "disabled";

export interface UserView {
  id: string;
  email: string;
  phone?: string | null;
  role: UserRole;
  status: UserStatus;
  display_name?: string | null;
  avatar_url?: string | null;
  created_at: string;
  updated_at?: string | null;
  last_login_at?: string | null;
  email_verified_at?: string | null;
  session_count?: number | null;
  package_count?: number | null;
  auth_identities: AuthIdentityView[];
}

export interface RegisterResponse {
  email: string;
  verification_required: true;
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
    disabled_users: number;
    unverified_users: number;
    active_sessions: number;
  };
  users: UserView[];
  mail_delivery_configured: boolean;
  mail_delivery_mode: string;
}

export interface AdminAuditLogView {
  id: string;
  actor_user_id: string;
  target_user_id?: string | null;
  action: string;
  metadata: Record<string, unknown>;
  created_at: string;
  actor_email?: string | null;
  target_email?: string | null;
}

export interface AdminAuditLogResponse {
  logs: AdminAuditLogView[];
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

export interface SelectionRef {
  kind: "chat" | "board";
  excerpt: string;
  lesson_id?: string | null;
  block_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  heading_path?: string[];
  before_text?: string;
  after_text?: string;
  text_hash?: string | null;
}

export interface BoardFocusRef {
  source: "board" | "resource" | "chat";
  lesson_id?: string | null;
  document_id?: string | null;
  segment_id?: string | null;
  kind?: "heading" | "paragraph" | "list" | "table" | "code" | "image" | "other" | null;
  heading_path: string[];
  excerpt: string;
  before_text: string;
  after_text: string;
  text_hash?: string | null;
  confidence: number;
  reason: string;
}

export interface InteractionSession {
  id: string;
  status: "active" | "paused";
  rule_text: string;
  interaction_goal: string;
  target_focus?: BoardFocusRef | null;
  reference_context: string;
  expected_user_behavior: string;
  assistant_behavior: string;
  progress_note: string;
  pause_reason: string;
  turn_count: number;
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
  resource_chapter_id?: string | null;
}

export interface ResourceMatch {
  resource_id: string;
  chapter_id: string;
  segment_id?: string | null;
  resource_name: string;
  chapter_title: string;
  heading_path?: string[];
  excerpt?: string;
  before_text?: string;
  after_text?: string;
  text_hash?: string | null;
  page_range?: string | null;
  text_source?: string;
  reason: string;
  evidence?: Array<{ label: string; value: string }>;
  score_breakdown?: Record<string, number>;
  score: number;
  is_high_overlap: boolean;
}

export interface ResourceReferencePrompt {
  resource_id: string;
  chapter_id: string;
  segment_id?: string | null;
  resource_name: string;
  chapter_title: string;
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
  score: number;
  text_evidence_available?: boolean;
  requires_text_fallback_confirmation?: boolean;
}

export interface BoardEditPrompt {
  topic: string;
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
}

export interface StrongReasoningPrompt {
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
  model_label?: string | null;
}

export interface ResourceContextChunk {
  title: string;
  excerpt: string;
  teaching_hint: string;
  segment_id?: string | null;
  heading_path?: string[];
  before_text?: string;
  after_text?: string;
  text_hash?: string | null;
  page_range?: string | null;
  text_source?: string;
}

export interface ResourceReferenceContext {
  resource_id: string;
  chapter_id: string;
  segment_id?: string | null;
  resource_name: string;
  chapter_title: string;
  summary: string;
  teaching_points: string[];
  chunks: ResourceContextChunk[];
  text_evidence_available?: boolean;
  text_evidence_status?: string;
}

export interface DocumentEvidence {
  evidence_id: string;
  resource_id: string;
  resource_name: string;
  page_range?: string | null;
  printed_page_range?: string | null;
  heading_path: string[];
  excerpt: string;
  confidence: number;
  trace: string[];
  preview_url?: string | null;
  available_actions: DocumentEvidenceAction[];
  text_source: string;
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
  selection?: SelectionRef | null;
  interaction_mode?: ChatInteractionMode;
  scope_action?: ScopeAction | null;
  resource_chapter_id?: string | null;
  resource_reference_action?: ResourceReferenceAction | null;
  resource_reference_resource_id?: string | null;
  resource_reference_chapter_id?: string | null;
  resource_reference_segment_id?: string | null;
  document_evidence_action?: DocumentEvidenceAction | null;
  document_evidence_id?: string | null;
  board_edit_action?: BoardEditConfirmationAction | null;
  board_edit_topic?: string | null;
  strong_reasoning_action?: StrongReasoningAction | null;
  board_generation_action?: "start" | null;
  teaching_action?: "continue" | "restart" | null;
  conversation?: ConversationTurn[];
}

export interface SectionTeachingProgress {
  section_index: number;
  section_count: number;
  current_section_title: string;
  has_next_section: boolean;
  waiting_for_continue: boolean;
}

export interface ChatResponse {
  chatbot_message: string;
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  active_interaction_session?: InteractionSession | null;
  interaction_decision?: InteractionTurnDecision | null;
  learning_clarification: LearningClarificationStatus;
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  patch_proposal?: PatchProposal | null;
  scope_options: ScopeOption[];
  resource_matches: ResourceMatch[];
  document_evidence: DocumentEvidence[];
  reference_prompt?: ResourceReferencePrompt | null;
  board_edit_prompt?: BoardEditPrompt | null;
  strong_reasoning_prompt?: StrongReasoningPrompt | null;
  selected_reference?: ResourceReferenceContext | null;
  resolved_focus?: BoardFocusRef | null;
  focus_candidates?: BoardFocusRef[];
  requirement_cleared?: boolean;
  created_lesson?: Lesson | null;
  teaching_progress?: SectionTeachingProgress | null;
  course_package: CoursePackage;
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
