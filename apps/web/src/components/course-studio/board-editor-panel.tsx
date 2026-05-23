"use client";

import { WordBoardEditor } from "@/components/course-studio/word-board-editor";
import type { SelectionPopoverPosition } from "@/components/course-studio/selection-utils";
import type { BoardDocument, CommitRecord, Lesson, SelectionRef } from "@/types";

type BoardEditorPanelProps = {
  activeLesson: Lesson;
  document: BoardDocument;
  isPreviewMode: boolean;
  previewCommit: CommitRecord | null;
  toolbarCollapsed: boolean;
  onExitPreviewMode: () => void;
  onDocumentChange: (document: BoardDocument) => void;
  onApplySelection: (selection: SelectionRef, position?: SelectionPopoverPosition | null) => void;
  onClearSelection: () => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
};

export function BoardEditorPanel({
  activeLesson,
  document,
  isPreviewMode,
  previewCommit,
  toolbarCollapsed,
  onExitPreviewMode,
  onDocumentChange,
  onApplySelection,
  onClearSelection,
  onImportDocx,
  onExportDocx,
}: BoardEditorPanelProps) {
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

      <WordBoardEditor
        document={document}
        readOnly={isPreviewMode}
        toolbarCollapsed={toolbarCollapsed}
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
        onImportDocx={onImportDocx}
        onExportDocx={onExportDocx}
      />
    </section>
  );
}
