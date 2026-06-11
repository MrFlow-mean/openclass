import type { Editor as TiptapEditor } from "@tiptap/core";
import clsx from "clsx";
import {
  AlignCenter,
  AlignHorizontalSpaceAround,
  AlignLeft,
  AlignRight,
  Bold,
  ClipboardList,
  Columns2,
  Download,
  FilePlus,
  Files,
  FileText,
  Frame,
  Hash,
  Highlighter,
  ImagePlus,
  Italic,
  LayoutTemplate,
  Link as LinkIcon,
  List,
  ListOrdered,
  PaintBucket,
  PanelTop,
  PencilLine,
  Quote,
  RectangleHorizontal,
  RectangleVertical,
  Redo2,
  Stamp,
  Table2,
  TextCursorInput,
  Type,
  Underline,
  Undo2,
  Upload,
} from "lucide-react";
import { useRef, type RefObject } from "react";

import {
  PAGE_BACKGROUND_OPTIONS,
  PAGE_MARGIN_OPTIONS,
  PAGE_SIZE_OPTIONS,
} from "@/components/course-studio/page-settings";
import { FONT_FAMILY_OPTIONS, FONT_SIZE_OPTIONS } from "@/components/course-studio/word-editor-extensions";
import {
  WordEditorTableDimensionFields,
  WordEditorTableEditButtons,
} from "@/components/course-studio/word-editor-table-controls";
import {
  RibbonActionButton,
  RibbonTabButton,
  ToolbarButton,
  WordPageZoomControls,
} from "@/components/course-studio/word-editor-toolbar";
import type { WordEditorCommands } from "@/hooks/course-studio/use-word-editor-controller";
import type { DocumentPageSettings } from "@/types";

export type WordRibbonTab = "home" | "insert" | "page";

export function WordEditorRibbons({
  activeRibbonTab,
  toolbarCollapsed,
  readOnly,
  editor,
  imageUploadRef,
  pageSettings,
  pageZoom,
  currentFontSize,
  currentFontFamily,
  tableRows,
  tableCols,
  tableHasHeaderRow,
  tableInsertHint,
  tableInsertDisabled,
  tableEditDisabled,
  commands,
  setTableRows,
  setTableCols,
  setTableHasHeaderRow,
  updatePageSettings,
  updatePageZoom,
  fitPageToWidth,
  onActiveRibbonTabChange,
  onImportDocx,
  onExportDocx,
}: {
  activeRibbonTab: WordRibbonTab;
  toolbarCollapsed: boolean;
  readOnly: boolean;
  editor: TiptapEditor | null;
  imageUploadRef: RefObject<HTMLInputElement | null>;
  pageSettings: DocumentPageSettings;
  pageZoom: number;
  currentFontSize: string;
  currentFontFamily: string;
  tableRows: number;
  tableCols: number;
  tableHasHeaderRow: boolean;
  tableInsertHint: string;
  tableInsertDisabled: boolean;
  tableEditDisabled: boolean;
  commands: WordEditorCommands;
  setTableRows: (value: number) => void;
  setTableCols: (value: number) => void;
  setTableHasHeaderRow: (value: boolean) => void;
  updatePageSettings: (patch: Partial<DocumentPageSettings>) => void;
  updatePageZoom: (value: number) => void;
  fitPageToWidth: () => void;
  onActiveRibbonTabChange: (tab: WordRibbonTab) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
}) {
  const importRef = useRef<HTMLInputElement | null>(null);

  const tableDimensionFields = (compact = true) => (
    <WordEditorTableDimensionFields
      compact={compact}
      rows={tableRows}
      cols={tableCols}
      hasHeaderRow={tableHasHeaderRow}
      disabled={tableInsertDisabled}
      onRowsChange={setTableRows}
      onColsChange={setTableCols}
      onHeaderRowChange={setTableHasHeaderRow}
    />
  );

  const tableEditButtons = (compact = true) => (
    <WordEditorTableEditButtons editor={editor} disabled={tableEditDisabled} compact={compact} />
  );

  const homeRibbon = (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <select
          disabled={!editor || readOnly}
          value={currentFontFamily}
          onChange={(event) => editor?.chain().focus().setFontFamily(event.target.value).run()}
          className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[12px] font-medium outline-none"
        >
          {FONT_FAMILY_OPTIONS.map((option) => (
            <option key={option.label} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          disabled={!editor || readOnly}
          value={currentFontSize}
          onChange={(event) => editor?.chain().focus().setFontSize(`${event.target.value}px`).run()}
          className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[12px] font-medium outline-none"
        >
          {FONT_SIZE_OPTIONS.map((option) => (
            <option key={option.value} value={option.label}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="加粗"
          active={editor?.isActive("bold")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          <Bold className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="斜体"
          active={editor?.isActive("italic")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          <Italic className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="下划线"
          active={editor?.isActive("underline")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleUnderline().run()}
        >
          <Underline className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="高亮"
          active={editor?.isActive("highlight")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fef08a" }).run()}
        >
          <Highlighter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="文字颜色"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setColor("#c2410c").run()}
        >
          <Type className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="左对齐"
          active={editor?.isActive({ textAlign: "left" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("left").run()}
        >
          <AlignLeft className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="居中"
          active={editor?.isActive({ textAlign: "center" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("center").run()}
        >
          <AlignCenter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="右对齐"
          active={editor?.isActive({ textAlign: "right" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("right").run()}
        >
          <AlignRight className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="引用"
          active={editor?.isActive("blockquote")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          <Quote className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="项目符号"
          active={editor?.isActive("bulletList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          <List className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="编号列表"
          active={editor?.isActive("orderedList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleOrderedList().run()}
        >
          <ListOrdered className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {tableDimensionFields()}
        <ToolbarButton
          title={`插入 ${tableInsertHint} 表格`}
          disabled={tableInsertDisabled}
          onClick={commands.insertTable}
        >
          <Table2 className="h-4 w-4" />
        </ToolbarButton>
      </div>

      {tableEditButtons()}

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title="撤销"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().undo().run()}
        >
          <Undo2 className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title="重做"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().redo().run()}
        >
          <Redo2 className="h-4 w-4" />
        </ToolbarButton>
      </div>
    </>
  );

  const insertRibbon = (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入空白页"
          label="空白页"
          hint="分页占位"
          icon={<FilePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={commands.insertBlankPage}
        />
        <RibbonActionButton
          title="插入封面"
          label="封面"
          hint="置顶模板"
          icon={<LayoutTemplate className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={commands.insertCoverPage}
        />
        <RibbonActionButton
          title="插入目录页"
          label="目录页"
          hint="按标题生成"
          icon={<ClipboardList className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={commands.insertTableOfContents}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="切换页码"
          label="页码"
          hint={pageSettings.show_page_number ? "已显示" : "点击显示"}
          icon={<Hash className="h-4 w-4" />}
          active={pageSettings.show_page_number}
          disabled={readOnly}
          onClick={() => updatePageSettings({ show_page_number: !pageSettings.show_page_number })}
        />
        <RibbonActionButton
          title="设置页眉页脚"
          label="页眉页脚"
          hint="编辑文案"
          icon={<PanelTop className="h-4 w-4" />}
          active={Boolean(pageSettings.header_text || pageSettings.footer_text)}
          disabled={readOnly}
          onClick={commands.insertHeaderFooter}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <input
          ref={imageUploadRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              commands.uploadImage(file);
            }
            event.currentTarget.value = "";
          }}
        />
        <RibbonActionButton
          title="插入图片"
          label="图片"
          hint="上传到讲义"
          icon={<ImagePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={() => imageUploadRef.current?.click()}
        />
        {tableDimensionFields(false)}
        <RibbonActionButton
          title={`插入 ${tableInsertHint} 表格`}
          label="表格"
          hint={tableInsertHint}
          icon={<Table2 className="h-4 w-4" />}
          disabled={tableInsertDisabled}
          onClick={commands.insertTable}
        />
        <RibbonActionButton
          title="插入文本框"
          label="文本框"
          hint="重点旁注"
          icon={<TextCursorInput className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={commands.insertTextBox}
        />
      </div>

      {tableEditButtons(false)}

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入超链接"
          label="超链接"
          hint="外部资料"
          icon={<LinkIcon className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={commands.insertLink}
        />
        <RibbonActionButton
          title="插入水印"
          label="水印"
          hint="页面标识"
          icon={<Stamp className="h-4 w-4" />}
          active={Boolean(pageSettings.watermark_text)}
          disabled={readOnly}
          onClick={commands.insertWatermark}
        />
      </div>
    </>
  );

  const pageRibbon = (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_MARGIN_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`页边距：${option.label}`}
            label={option.label}
            hint="页边距"
            icon={<AlignHorizontalSpaceAround className="h-4 w-4" />}
            active={pageSettings.margin_preset === option.value}
            disabled={readOnly}
            onClick={() => updatePageSettings({ margin_preset: option.value })}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="纵向排版"
          label="纵向"
          hint="纸张方向"
          icon={<RectangleVertical className="h-4 w-4" />}
          active={pageSettings.orientation === "portrait"}
          disabled={readOnly}
          onClick={() => updatePageSettings({ orientation: "portrait" })}
        />
        <RibbonActionButton
          title="横向排版"
          label="横向"
          hint="纸张方向"
          icon={<RectangleHorizontal className="h-4 w-4" />}
          active={pageSettings.orientation === "landscape"}
          disabled={readOnly}
          onClick={() => updatePageSettings({ orientation: "landscape" })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_SIZE_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`纸张大小：${option.label}`}
            label={option.label}
            hint="纸张大小"
            icon={<Files className="h-4 w-4" />}
            active={pageSettings.page_size === option.value}
            disabled={readOnly}
            onClick={() => updatePageSettings({ page_size: option.value })}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="单栏排版"
          label="单栏"
          hint="分栏"
          icon={<FileText className="h-4 w-4" />}
          active={pageSettings.columns === 1}
          disabled={readOnly}
          onClick={() => updatePageSettings({ columns: 1 })}
        />
        <RibbonActionButton
          title="双栏排版"
          label="双栏"
          hint="分栏"
          icon={<Columns2 className="h-4 w-4" />}
          active={pageSettings.columns === 2}
          disabled={readOnly}
          onClick={() => updatePageSettings({ columns: 2 })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="页面边框"
          label="页面边框"
          hint={pageSettings.page_border ? "已开启" : "已关闭"}
          icon={<Frame className="h-4 w-4" />}
          active={pageSettings.page_border}
          disabled={readOnly}
          onClick={() => updatePageSettings({ page_border: !pageSettings.page_border })}
        />
        <RibbonActionButton
          title="行号"
          label="行号"
          hint={pageSettings.line_numbers ? "已显示" : "点击显示"}
          icon={<ListOrdered className="h-4 w-4" />}
          active={pageSettings.line_numbers}
          disabled={readOnly}
          onClick={() => updatePageSettings({ line_numbers: !pageSettings.line_numbers })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_BACKGROUND_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`页面背景：${option.label}`}
            label={option.label}
            hint="背景"
            icon={<PaintBucket className="h-4 w-4" />}
            active={pageSettings.background_style === option.value}
            disabled={readOnly}
            onClick={() => updatePageSettings({ background_style: option.value })}
          />
        ))}
      </div>
    </>
  );

  return (
    <div
      className={clsx(
        "shrink-0 overflow-hidden transition-all duration-300",
        toolbarCollapsed ? "max-h-0 opacity-0" : "max-h-52 opacity-100"
      )}
      aria-hidden={toolbarCollapsed}
    >
      <div className={clsx("border-b border-gray-200 bg-white", readOnly && "bg-gray-50")}>
        <div className="flex h-10 items-center border-b border-gray-100 px-6">
          <RibbonTabButton active={activeRibbonTab === "home"} onClick={() => onActiveRibbonTabChange("home")}>
            <PencilLine className="h-3.5 w-3.5" />
            开始 (HOME)
          </RibbonTabButton>
          <RibbonTabButton active={activeRibbonTab === "insert"} onClick={() => onActiveRibbonTabChange("insert")}>
            <FilePlus className="h-3.5 w-3.5" />
            插入 (INSERT)
          </RibbonTabButton>
          <RibbonTabButton active={activeRibbonTab === "page"} onClick={() => onActiveRibbonTabChange("page")}>
            <Files className="h-3.5 w-3.5" />
            页面 (PAGE)
          </RibbonTabButton>
        </div>
        <div className="custom-scrollbar flex items-center gap-3 overflow-x-auto px-5 py-3 whitespace-nowrap">
          {activeRibbonTab === "home" ? homeRibbon : null}
          {activeRibbonTab === "insert" ? insertRibbon : null}
          {activeRibbonTab === "page" ? pageRibbon : null}

          <div className="ml-auto flex items-center gap-2">
            <WordPageZoomControls
              value={pageZoom}
              onChange={updatePageZoom}
              onFitToWidth={fitPageToWidth}
            />
            <input
              ref={importRef}
              type="file"
              accept=".docx"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  onImportDocx(file);
                }
                event.currentTarget.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => importRef.current?.click()}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
            >
              <Upload className="h-4 w-4" />
              导入 DOCX
            </button>
            <button
              type="button"
              onClick={onExportDocx}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
            >
              <Download className="h-4 w-4" />
              导出 DOCX
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
