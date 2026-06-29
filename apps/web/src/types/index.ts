export type BoardAction = "no_change";
export type ChatInteractionMode = "ask" | "direct_edit";
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
  work_mode?: InitialLearningWorkMode | null;
  granularity?: InitialLearningGranularity | null;
}

export type InitialLearningWorkMode = "knowledge_board" | "narrow_topic" | "practice_artifact" | "unknown";
export type InitialLearningGranularity = "single_knowledge_point" | "broad_topic" | "practice_artifact" | "unclear";

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

export interface Lesson {
  id: string;
  title: string;
  slug: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
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

export interface ResourceSourceUnit {
  id: string;
  content_type: string;
  text: string;
  page_idx?: number | null;
  page_no?: number | null;
  source_locator?: string | null;
  asset_path?: string | null;
  bbox: number[];
  order_index: number;
  metadata: Record<string, unknown>;
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
  parser_provider: string;
  parser_artifacts_path?: string | null;
  parser_message: string;
  parse_warnings: string[];
  source_units: ResourceSourceUnit[];
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
  excerpt_hash?: string | null;
  confidence: number;
  reason: string;
  display_label?: string;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ResourceContextChunk {
  title: string;
  excerpt: string;
  teaching_hint: string;
}

export interface ResourceVisualEvidence {
  id: string;
  content_type: string;
  caption: string;
  page_no?: number | null;
  page_idx?: number | null;
  bbox: number[];
  source_locator?: string | null;
  relevance_reason: string;
  relevance_score: number;
}

export interface ResourceReferenceContext {
  resource_id: string;
  chapter_id: string;
  resource_name: string;
  chapter_title: string;
  summary: string;
  teaching_points: string[];
  chunks: ResourceContextChunk[];
  visual_evidence: ResourceVisualEvidence[];
}

export interface BoardDecision {
  action: BoardAction;
  reason: string;
}

export interface ChatRequestPayload {
  message: string;
  text_model?: AIModelSelection | null;
  board_model?: AIModelSelection | null;
  selection?: SelectionRef | null;
  interaction_mode?: ChatInteractionMode;
  conversation?: ConversationTurn[];
}

export interface ChatResponse {
  chatbot_message: string;
  learning_requirement_sheet: LearningRequirementSheet;
  learning_clarification: LearningClarificationStatus;
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  resolved_focus?: BoardFocusRef | null;
  requirement_cleared?: boolean;
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
