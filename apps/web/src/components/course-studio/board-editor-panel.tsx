"use client";

import { WordBoardEditor } from "@/components/course-studio/word-board-editor";
import type { SelectionPopoverPosition } from "@/components/course-studio/selection-utils";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardDocument,
  BoardFocusRef,
  CommitRecord,
  Lesson,
  SelectionRef,
} from "@/types";

type BoardEditorPanelProps = {
  activeLesson: Lesson;
  document: BoardDocument;
  isPreviewMode: boolean;
  isDraftPreviewMode: boolean;
  previewCommit: CommitRecord | null;
  toolbarCollapsed: boolean;
  modelCatalog: AIModelCatalog;
  selectedBoardModel: AIModelSelection;
  selectedBoardOption: AIModelOption | null;
  onExitPreviewMode: () => void;
  onDocumentChange: (document: BoardDocument) => void;
  onApplySelection: (selection: SelectionRef, position?: SelectionPopoverPosition | null) => void;
  onClearSelection: () => void;
  onSelectBoardModel: (option: AIModelOption) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
  onExportHtml: () => void;
};

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function stringArrayValue(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function boardFocusFromMetadata(value: unknown): BoardFocusRef | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const raw = value as Record<string, unknown>;
  if (raw.source !== "board") {
    return null;
  }
  const headingPath = stringArrayValue(raw.heading_path);
  const excerpt = stringValue(raw.excerpt).trim();
  const displayLabel = stringValue(raw.display_label).trim();
  if (!excerpt && !displayLabel && !headingPath.length) {
    return null;
  }
  return {
    source: "board",
    lesson_id: stringValue(raw.lesson_id) || null,
    document_id: stringValue(raw.document_id) || null,
    segment_id: stringValue(raw.segment_id) || null,
    kind: typeof raw.kind === "string" ? (raw.kind as BoardFocusRef["kind"]) : null,
    heading_path: headingPath,
    excerpt,
    before_text: stringValue(raw.before_text),
    after_text: stringValue(raw.after_text),
    text_hash: stringValue(raw.text_hash) || null,
    excerpt_hash: stringValue(raw.excerpt_hash) || null,
    confidence: typeof raw.confidence === "number" ? raw.confidence : 0,
    reason: stringValue(raw.reason),
    display_label: displayLabel,
  };
}

function currentHeadCommit(lesson: Lesson) {
  const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
  const commitId = branch?.head_commit_id ?? lesson.history_graph.commits[lesson.history_graph.commits.length - 1]?.id;
  return lesson.history_graph.commits.find((commit) => commit.id === commitId) ?? null;
}

function activeSessionFocus(lesson: Lesson) {
  const session = lesson.active_interaction_session;
  if (!session || session.status !== "active") {
    return null;
  }
  if (session.sequence_items?.length) {
    const index = Math.max(0, Math.min(session.sequence_index ?? 0, session.sequence_items.length - 1));
    return session.sequence_items[index] ?? session.target_focus ?? null;
  }
  return session.target_focus ?? null;
}

function commitTeachingFocus(commit: CommitRecord | null) {
  if (!commit?.metadata || typeof commit.metadata !== "object") {
    return null;
  }
  const metadata = commit.metadata;
  const kind = stringValue(metadata.kind);
  const assistantMessage = stringValue(metadata.assistant_message).trim();
  const boardTaskRoute = stringValue(metadata.board_task_route);
  const hasDirective = Boolean(metadata.board_explanation_directive && typeof metadata.board_explanation_directive === "object");
  const isTeachingCommit =
    assistantMessage &&
    (kind === "interaction_flow" || (kind === "chat_flow" && (boardTaskRoute === "explain" || hasDirective)));
  return isTeachingCommit ? boardFocusFromMetadata(metadata.resolved_focus) : null;
}

function currentTeachingFocus(lesson: Lesson, previewCommit: CommitRecord | null) {
  return activeSessionFocus(lesson) ?? commitTeachingFocus(previewCommit ?? currentHeadCommit(lesson));
}

export function BoardEditorPanel({
  activeLesson,
  document,
  isPreviewMode,
  isDraftPreviewMode,
  previewCommit,
  toolbarCollapsed,
  modelCatalog,
  selectedBoardModel,
  selectedBoardOption,
  onExitPreviewMode,
  onDocumentChange,
  onApplySelection,
  onClearSelection,
  onSelectBoardModel,
  onImportDocx,
  onExportDocx,
  onExportHtml,
}: BoardEditorPanelProps) {
  const teachingFocus = currentTeachingFocus(activeLesson, previewCommit);

  return (
    <section className="relative z-10 flex min-w-0 flex-col overflow-hidden bg-white shadow-[0_0_20px_rgba(0,0,0,0.02)]">
      {isPreviewMode ? (
        <div className="shrink-0 border-b border-violet-200 bg-violet-50 px-5 py-3 text-sm text-violet-700">
          正在预览历史快照：{previewCommit?.label}
          <button
            type="button"
            className="ml-3 rounded-md border border-violet-200 bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-violet-700"
            onClick={onExitPreviewMode}
          >
            回到当前版本
          </button>
        </div>
      ) : null}
      {!isPreviewMode && isDraftPreviewMode ? (
        <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm text-amber-800">
          正在预览未保存的生成草稿。生成未成功落库，当前内容不会自动保存。
          <button
            type="button"
            className="ml-3 rounded-md border border-amber-200 bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-amber-800"
            onClick={onExitPreviewMode}
          >
            回到当前版本
          </button>
        </div>
      ) : null}

      <WordBoardEditor
        document={document}
        readOnly={isPreviewMode || isDraftPreviewMode}
        teachingFocus={teachingFocus}
        toolbarCollapsed={toolbarCollapsed}
        modelOptions={modelCatalog.text}
        selectedBoardModel={selectedBoardModel}
        selectedBoardOption={selectedBoardOption}
        onDocumentChange={onDocumentChange}
        onSelectionChange={(payload) => {
          if (!payload) {
            onClearSelection();
            return;
          }
          onApplySelection(
            {
              kind: "board",
              lesson_id: activeLesson.id,
              document_id: payload.documentId,
              excerpt: payload.excerpt,
              before_text: payload.beforeText,
              after_text: payload.afterText,
            },
            payload.position
          );
        }}
        onSelectBoardModel={onSelectBoardModel}
        onImportDocx={onImportDocx}
        onExportDocx={onExportDocx}
        onExportHtml={onExportHtml}
      />
    </section>
  );
}
