import { useState } from "react";

import { WordEditorPage } from "@/components/course-studio/word-editor-page";
import { WordEditorRibbons, type WordRibbonTab } from "@/components/course-studio/word-editor-ribbons";
import {
  useWordEditorController,
  type WordEditorSelection,
} from "@/hooks/course-studio/use-word-editor-controller";
import type { BoardDocument } from "@/types";

export function WordBoardEditor({
  document,
  readOnly,
  toolbarCollapsed,
  onDocumentChange,
  onSelectionChange,
  onImportDocx,
  onExportDocx,
}: {
  document: BoardDocument;
  readOnly: boolean;
  toolbarCollapsed: boolean;
  onDocumentChange: (document: BoardDocument) => void;
  onSelectionChange: (selection: WordEditorSelection | null) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
}) {
  const [activeRibbonTab, setActiveRibbonTab] = useState<WordRibbonTab>("home");
  const {
    editor,
    imageUploadRef,
    pageScrollRef,
    pageZoom,
    pageSettings,
    pageStyle,
    pageChromeStyle,
    titleStyle,
    contentStyle,
    currentFontSize,
    currentFontFamily,
    currentPageNumberLabel,
    tableRows,
    tableCols,
    tableHasHeaderRow,
    tableInsertHint,
    tableInsertDisabled,
    tableEditDisabled,
    setTableRows,
    setTableCols,
    setTableHasHeaderRow,
    updatePageSettings,
    updatePageZoom,
    fitPageToWidth,
    commands,
  } = useWordEditorController({
    document,
    readOnly,
    onDocumentChange,
    onSelectionChange,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <WordEditorRibbons
        activeRibbonTab={activeRibbonTab}
        toolbarCollapsed={toolbarCollapsed}
        readOnly={readOnly}
        editor={editor}
        imageUploadRef={imageUploadRef}
        pageSettings={pageSettings}
        pageZoom={pageZoom}
        currentFontSize={currentFontSize}
        currentFontFamily={currentFontFamily}
        tableRows={tableRows}
        tableCols={tableCols}
        tableHasHeaderRow={tableHasHeaderRow}
        tableInsertHint={tableInsertHint}
        tableInsertDisabled={tableInsertDisabled}
        tableEditDisabled={tableEditDisabled}
        commands={commands}
        setTableRows={setTableRows}
        setTableCols={setTableCols}
        setTableHasHeaderRow={setTableHasHeaderRow}
        updatePageSettings={updatePageSettings}
        updatePageZoom={updatePageZoom}
        fitPageToWidth={fitPageToWidth}
        onActiveRibbonTabChange={setActiveRibbonTab}
        onImportDocx={onImportDocx}
        onExportDocx={onExportDocx}
      />
      <WordEditorPage
        editor={editor}
        pageScrollRef={pageScrollRef}
        pageSettings={pageSettings}
        pageStyle={pageStyle}
        pageChromeStyle={pageChromeStyle}
        titleStyle={titleStyle}
        contentStyle={contentStyle}
        currentPageNumberLabel={currentPageNumberLabel}
        document={document}
        readOnly={readOnly}
        onDocumentChange={onDocumentChange}
      />
    </div>
  );
}
