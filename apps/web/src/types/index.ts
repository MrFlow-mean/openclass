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
  | "await_reference_choice";

export type ResourceReferenceAction = "confirm" | "skip";
export type ChatInteractionMode = "ask" | "direct_edit";

export interface BoardDocument {
  id: string;
  title: string;
  content_json: Record<string, unknown>;
  content_html: string;
  content_text: string;
}

export interface LearningRequirementSheet {
  theme: string;
  learning_goal: string;
  level: string;
  known_background: string;
  current_questions: string[];
  target_depth: string;
  output_preference: string;
  boundary: string;
  board_scope: string[];
  success_criteria: string;
  risk_notes: string[];
}

export interface LearningClarificationStatus {
  progress: number;
  label: string;
  reason: string;
  missing_items: string[];
  can_start: boolean;
  forced_start: boolean;
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

export interface Lesson {
  id: string;
  title: string;
  slug: string;
  summary: string;
  tags: string[];
  board_document: BoardDocument;
  learning_requirements?: LearningRequirementSheet | null;
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
  outline: LibraryChapter[];
  concept_index: Record<string, string[]>;
  extracted_text_available: boolean;
  source_path?: string | null;
}

export interface CoursePackage {
  id: string;
  title: string;
  summary: string;
  lessons: Lesson[];
  course_graph: CourseGraphEdge[];
  resources: ResourceLibraryItem[];
  open_lesson_ids: string[];
  active_lesson_id?: string | null;
  workspace_tab_order: string[];
}

export interface SelectionRef {
  kind: "chat" | "board";
  excerpt: string;
  lesson_id?: string | null;
  block_id?: string | null;
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
  selection?: SelectionRef | null;
  interaction_mode?: ChatInteractionMode;
  scope_action?: ScopeAction | null;
  resource_chapter_id?: string | null;
  resource_reference_action?: ResourceReferenceAction | null;
  resource_reference_resource_id?: string | null;
  resource_reference_chapter_id?: string | null;
  conversation?: ConversationTurn[];
}

export interface ChatResponse {
  teacher_message: string;
  learning_requirement_sheet: LearningRequirementSheet;
  learning_clarification: LearningClarificationStatus;
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  patch_proposal?: PatchProposal | null;
  scope_options: ScopeOption[];
  resource_matches: ResourceMatch[];
  reference_prompt?: ResourceReferencePrompt | null;
  selected_reference?: ResourceReferenceContext | null;
  created_lesson?: Lesson | null;
  course_package: CoursePackage;
}

export interface RealtimeConnectPayload {
  offer_sdp: string;
  latest_assistant_message?: string | null;
  client_session_id?: string | null;
}

export interface RealtimeConnectResponse {
  answer_sdp: string;
  model: string;
  voice: string;
}

export interface RealtimeEventLogPayload {
  client_session_id?: string | null;
  lesson_title?: string | null;
  role: "user" | "assistant";
  transport_event_type: string;
  transcript: string;
}

export interface DocumentSavePayload {
  document: BoardDocument;
  label?: string;
  message?: string;
}

export interface DocumentAIEditPayload {
  instruction: string;
  selection_text?: string | null;
  replace_whole?: boolean;
  conversation?: ConversationTurn[];
}
