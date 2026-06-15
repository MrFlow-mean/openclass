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

export type BoardTaskRunStatus = "collecting" | "ready" | "awaiting_confirmation" | "consumed" | "not_executed" | "archived";
export type BoardDocumentOperationStatus = "none" | "succeeded" | "failed";
export type BoardTaskRequestedAction = "write" | "edit" | "explain" | "chat";
export type BoardTaskConfirmationStatus = "none" | "awaiting" | "confirmed" | "declined";
export type BoardTaskLocationStatus = "missing" | "selected" | "resolved" | "ambiguous" | "content_absent";

export interface BoardTaskRequirementSheet {
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
  resource_chapter_id?: string | null;
}

export interface ResourceMatch {
  resource_id: string;
  chapter_id: string;
  resource_name: string;
  chapter_title: string;
  reason: string;
  score: number;
  is_high_overlap: boolean;
}

export interface ResourceReferencePrompt {
  resource_id: string;
  chapter_id: string;
  resource_name: string;
  chapter_title: string;
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
  score: number;
}

export interface BoardEditPrompt {
  topic: string;
  question: string;
  reason: string;
  confirm_label: string;
  skip_label: string;
}

export interface ResourceContextChunk {
  title: string;
  excerpt: string;
  teaching_hint: string;
}

export interface ResourceReferenceContext {
  resource_id: string;
  chapter_id: string;
  resource_name: string;
  chapter_title: string;
  summary: string;
  teaching_points: string[];
  chunks: ResourceContextChunk[];
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
  board_edit_action?: BoardEditConfirmationAction | null;
  board_edit_topic?: string | null;
  board_generation_action?: "start" | null;
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

export interface ChatResponse {
  chatbot_message: string;
  learning_requirement_sheet: LearningRequirementSheet;
  active_requirement_sheet?: LearningRequirementSheet | null;
  active_interaction_session?: InteractionSession | null;
  interaction_decision?: InteractionTurnDecision | null;
  learning_clarification: LearningClarificationStatus;
  requirement_run_id?: string | null;
  requirement_version_id?: string | null;
  requirement_phase?: LearningRequirementRunStatus | null;
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
  resource_matches: ResourceMatch[];
  reference_prompt?: ResourceReferencePrompt | null;
  board_edit_prompt?: BoardEditPrompt | null;
  selected_reference?: ResourceReferenceContext | null;
  resolved_focus?: BoardFocusRef | null;
  focus_candidates?: BoardFocusRef[];
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
