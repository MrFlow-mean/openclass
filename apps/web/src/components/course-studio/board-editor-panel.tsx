"use client";

import { WordBoardEditor } from "@/components/course-studio/word-board-editor";
import type { SelectionPopoverPosition } from "@/components/course-studio/selection-utils";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardDocument,
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
};

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
      />
    </section>
  );
}
