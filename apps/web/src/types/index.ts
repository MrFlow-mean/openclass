export type BlockType =
  | "heading"
  | "paragraph"
  | "formula"
  | "table"
  | "image"
  | "note"
  | "exercise"
  | "dialogue";

export type PatchOperationType =
  | "insert_block"
  | "delete_block"
  | "update_block_content"
  | "replace_range_in_block"
  | "move_block"
  | "update_block_style"
  | "attach_asset";

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
  | "await_scope_choice";

export interface BlockStyle {
  font_family: string;
  font_size: "sm" | "md" | "lg" | "xl";
  alignment: "left" | "center" | "right";
  emphasis: "plain" | "accent" | "callout";
  width: "normal" | "wide" | "full";
}

export interface BoardBlock {
  id: string;
  type: BlockType;
  title: string;
  content: string;
  style: BlockStyle;
  metadata: Record<string, unknown>;
}

export interface BoardDocument {
  id: string;
  title: string;
  blocks: BoardBlock[];
}

export interface PatchOperation {
  op: PatchOperationType;
  block_id?: string | null;
  after_block_id?: string | null;
  title?: string | null;
  content?: string | null;
  block?: BoardBlock | null;
  search?: string | null;
  replacement?: string | null;
  style?: BlockStyle | null;
  asset_url?: string | null;
  note?: string | null;
}

export interface DiffPreviewItem {
  op: PatchOperationType;
  block_id?: string | null;
  before?: BoardBlock | null;
  after?: BoardBlock | null;
  summary: string;
}

export interface PatchProposal {
  id: string;
  rationale: string;
  commit_label: string;
  operations: PatchOperation[];
  diff_preview: DiffPreviewItem[];
  target_action: ScopeAction;
  suggested_title?: string | null;
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
  operations: PatchOperation[];
  snapshot: BoardDocument;
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
  summary: string;
  keywords: string[];
  prerequisites: string[];
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
}

export interface BoardDecision {
  action: BoardAction;
  reason: string;
}

export interface ChatRequestPayload {
  message: string;
  selection?: SelectionRef | null;
  scope_action?: ScopeAction | null;
  resource_chapter_id?: string | null;
  conversation?: ConversationTurn[];
}

export interface ChatResponse {
  teacher_message: string;
  learning_requirement_sheet: LearningRequirementSheet;
  board_decision: BoardDecision;
  needs_clarification: boolean;
  clarification_questions: string[];
  patch_proposal?: PatchProposal | null;
  scope_options: ScopeOption[];
  resource_matches: ResourceMatch[];
  created_lesson?: Lesson | null;
  course_package: CoursePackage;
}
