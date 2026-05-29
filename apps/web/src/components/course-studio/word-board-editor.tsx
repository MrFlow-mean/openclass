"use client";

import { Extension, Node, type Editor as TiptapEditor } from "@tiptap/core";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import ImageExtension from "@tiptap/extension-image";
import LinkExtension from "@tiptap/extension-link";
import { BlockMath, InlineMath } from "@tiptap/extension-mathematics";
import { Table } from "@tiptap/extension-table";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import TableRow from "@tiptap/extension-table-row";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyle } from "@tiptap/extension-text-style";
import UnderlineExtension from "@tiptap/extension-underline";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import clsx from "clsx";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  AlignCenter,
  AlignHorizontalSpaceAround,
  AlignLeft,
  AlignRight,
  ArrowLeft,
  ArrowRight,
  Bold,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Columns2,
  Columns3,
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
  Rows3,
  Stamp,
  Table2,
  TableCellsMerge,
  TableCellsSplit,
  TextCursorInput,
  Trash2,
  Type,
  Underline,
  Undo2,
  Upload,
} from "lucide-react";

import {
  PAGE_BACKGROUND_OPTIONS,
  PAGE_MARGIN_OPTIONS,
  PAGE_SIZE_OPTIONS,
  PAGE_ZOOM_DEFAULT,
  PAGE_ZOOM_WHEEL_SENSITIVITY,
  normalizePageSettings,
  normalizePageZoom,
  pagePreviewMetrics,
} from "@/components/course-studio/page-settings";
import {
  popoverPositionFromDomSelection,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import {
  RibbonActionButton,
  RibbonTabButton,
  ToolbarButton,
  WordPageZoomControls,
} from "@/components/course-studio/word-editor-toolbar";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { MATH_TEXT_SERIALIZERS, normalizeEditorMath } from "@/lib/math-content";
import type { BoardDocument, DocumentPageSettings } from "@/types";

type WordRibbonTab = "home" | "insert" | "page";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    fontSize: {
      setFontSize: (fontSize: string) => ReturnType;
      unsetFontSize: () => ReturnType;
    };
    fontFamily: {
      setFontFamily: (fontFamily: string) => ReturnType;
      unsetFontFamily: () => ReturnType;
    };
  }
}

const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) => {
              if (!attributes.fontSize) {
                return {};
              }
              return { style: `font-size: ${attributes.fontSize}` };
            },
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setFontSize:
        (fontSize: string) =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize }).run(),
      unsetFontSize:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize: null }).removeEmptyTextStyle().run(),
    };
  },
});

const FontFamily = Extension.create({
  name: "fontFamily",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontFamily: {
            default: null,
            parseHTML: (element) => element.style.fontFamily || null,
            renderHTML: (attributes) => {
              if (!attributes.fontFamily) {
                return {};
              }
              return { style: `font-family: ${attributes.fontFamily}` };
            },
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setFontFamily:
        (fontFamily: string) =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontFamily }).run(),
      unsetFontFamily:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontFamily: null }).removeEmptyTextStyle().run(),
    };
  },
});

const PageBreak = Node.create({
  name: "pageBreak",
  group: "block",
  atom: true,
  selectable: true,
  parseHTML() {
    return [{ tag: 'div[data-type="page-break"]' }];
  },
  renderHTML() {
    return ["div", { "data-type": "page-break", class: "word-editor__page-break" }];
  },
});

const FONT_FAMILY_OPTIONS = [
  { label: "Satoshi", value: '"Satoshi","Avenir Next","PingFang SC","Microsoft YaHei",sans-serif' },
  { label: "Serif", value: '"Iowan Old Style","Songti SC","Times New Roman",serif' },
  { label: "Mono", value: '"IBM Plex Mono","SFMono-Regular","Menlo",monospace' },
];

const FONT_SIZE_OPTIONS = [
  { label: "12", value: "12px" },
  { label: "14", value: "14px" },
  { label: "16", value: "16px" },
  { label: "18", value: "18px" },
  { label: "24", value: "24px" },
];

const TABLE_DIMENSION_MIN = 1;
const TABLE_DIMENSION_MAX = 12;

function normalizeTableDimension(value: number) {
  if (!Number.isFinite(value)) {
    return TABLE_DIMENSION_MIN;
  }
  return Math.min(TABLE_DIMENSION_MAX, Math.max(TABLE_DIMENSION_MIN, Math.round(value)));
}

const WORD_EDITOR_EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    link: false,
    underline: false,
  }),
  TextStyle,
  Color,
  Highlight.configure({ multicolor: true }),
  UnderlineExtension,
  LinkExtension.configure({
    autolink: true,
    openOnClick: false,
    defaultProtocol: "https",
  }),
  ImageExtension.configure({
    allowBase64: true,
    HTMLAttributes: {
      class: "word-editor__image",
    },
  }),
  TextAlign.configure({ types: ["heading", "paragraph"] }),
  Table.configure({ resizable: true, cellMinWidth: 72, lastColumnResizable: true }),
  TableRow,
  TableHeader,
  TableCell,
  BlockMath.configure({
    katexOptions: {
      displayMode: true,
      throwOnError: false,
    },
  }),
  InlineMath.configure({
    katexOptions: {
      throwOnError: false,
    },
  }),
  PageBreak,
  FontSize,
  FontFamily,
];

const WORD_EDITOR_PROPS = {
  attributes: {
    class: "word-editor__content",
  },
};

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
  onSelectionChange: (
    selection: {
      excerpt: string;
      position: SelectionPopoverPosition | null;
      documentId: string;
      beforeText: string;
      afterText: string;
    } | null
  ) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
}) {
  const { texts: txt } = useInterfaceLanguage();
  const e = txt.studio.editor;
  const tb = e.toolbar;
  const importRef = useRef<HTMLInputElement | null>(null);
  const imageUploadRef = useRef<HTMLInputElement | null>(null);
  const pageScrollRef = useRef<HTMLDivElement | null>(null);
  const pageZoomRef = useRef(PAGE_ZOOM_DEFAULT);
  const [activeRibbonTab, setActiveRibbonTab] = useState<WordRibbonTab>("home");
  const [tableRows, setTableRows] = useState(3);
  const [tableCols, setTableCols] = useState(3);
  const [tableHasHeaderRow, setTableHasHeaderRow] = useState(true);
  const [isTableActive, setIsTableActive] = useState(false);
  const [pageZoom, setPageZoom] = useState(PAGE_ZOOM_DEFAULT);
  const documentJson =
    document.content_json && Object.keys(document.content_json).length ? document.content_json : null;
  const editorContent = documentJson ?? (document.content_html.trim() || "<p></p>");
  const latestDocumentRef = useRef(document);
  const latestReadOnlyRef = useRef(readOnly);
  const latestOnDocumentChangeRef = useRef(onDocumentChange);
  const latestOnSelectionChangeRef = useRef(onSelectionChange);
  const pageSettings = normalizePageSettings(document.page_settings);
  const pageMetrics = pagePreviewMetrics(pageSettings);
  const pageZoomScale = pageZoom / 100;
  const pageStyle = {
    "--page-padding-x": `${pageMetrics.paddingX}px`,
    "--page-padding-y": `${pageMetrics.paddingY}px`,
    "--page-content-min-height": `${pageMetrics.contentMinHeight}px`,
    "--word-page-preview-height": `${pageMetrics.height}px`,
    "--word-page-zoom": pageZoomScale.toString(),
    width: `${pageMetrics.width}px`,
    minHeight: `${pageMetrics.height}px`,
  } as CSSProperties;
  const pageChromeStyle = {
    paddingLeft: "var(--page-padding-x)",
    paddingRight: "var(--page-padding-x)",
  } as CSSProperties;
  const titleStyle = {
    ...pageChromeStyle,
    paddingTop: "calc(var(--page-padding-y) * 0.72)",
    paddingBottom: "calc(var(--page-padding-y) * 0.56)",
  } as CSSProperties;
  const contentStyle = {
    ...pageChromeStyle,
    paddingTop: "var(--page-padding-y)",
    paddingBottom: "calc(var(--page-padding-y) * 0.9)",
  } as CSSProperties;

  useEffect(() => {
    latestDocumentRef.current = document;
    latestReadOnlyRef.current = readOnly;
    latestOnDocumentChangeRef.current = onDocumentChange;
    latestOnSelectionChangeRef.current = onSelectionChange;
  }, [document, onDocumentChange, onSelectionChange, readOnly]);

  const handleEditorUpdate = useCallback(({ editor: currentEditor }: { editor: TiptapEditor }) => {
    if (latestReadOnlyRef.current) {
      return;
    }
    setIsTableActive(currentEditor.isActive("table"));
    latestOnDocumentChangeRef.current({
      ...latestDocumentRef.current,
      content_json: currentEditor.getJSON() as Record<string, unknown>,
      content_html: currentEditor.getHTML(),
      content_text: currentEditor.getText({ textSerializers: MATH_TEXT_SERIALIZERS }),
    });
  }, []);

  const handleEditorSelectionUpdate = useCallback(({ editor: currentEditor }: { editor: TiptapEditor }) => {
    if (latestReadOnlyRef.current) {
      setIsTableActive(false);
      latestOnSelectionChangeRef.current(null);
      return;
    }
    setIsTableActive(currentEditor.isActive("table"));
    const { from, to } = currentEditor.state.selection;
    if (from === to) {
      latestOnSelectionChangeRef.current(null);
      return;
    }
    const excerpt = currentEditor.state.doc.textBetween(from, to, " ").trim();
    if (!excerpt) {
      latestOnSelectionChangeRef.current(null);
      return;
    }
    const beforeText = currentEditor.state.doc
      .textBetween(Math.max(0, from - 240), from, " ")
      .trim();
    const afterText = currentEditor.state.doc
      .textBetween(to, Math.min(currentEditor.state.doc.content.size, to + 240), " ")
      .trim();
    latestOnSelectionChangeRef.current({
      excerpt,
      position: popoverPositionFromDomSelection(),
      documentId: latestDocumentRef.current.id,
      beforeText,
      afterText,
    });
  }, []);

  const editor = useEditor({
    immediatelyRender: false,
    editable: !readOnly,
    extensions: WORD_EDITOR_EXTENSIONS,
    editorProps: WORD_EDITOR_PROPS,
    onUpdate: handleEditorUpdate,
    onSelectionUpdate: handleEditorSelectionUpdate,
  });

  useEffect(() => {
    if (!editor) {
      return;
    }
    if (editor.isEditable === readOnly) {
      editor.setEditable(!readOnly);
    }
    const incomingHtml = document.content_html.trim();
    const matchesIncomingDocument = documentJson
      ? JSON.stringify(editor.getJSON()) === JSON.stringify(documentJson)
      : incomingHtml
        ? editor.getHTML() === incomingHtml
        : false;
    if (!matchesIncomingDocument) {
      editor.commands.setContent(editorContent, { emitUpdate: false });
      normalizeEditorMath(editor);
    }
  }, [document.id, document.content_html, documentJson, editor, editorContent, readOnly]);

  const currentFontSize =
    ((editor?.getAttributes("textStyle").fontSize as string | null) ?? "14px").replace("px", "");
  const currentFontFamily = (editor?.getAttributes("textStyle").fontFamily as string | null) ?? FONT_FAMILY_OPTIONS[0].value;
  const currentPageNumberLabel = pageSettings.show_page_number ? e.pageNumberLabel : "";

  const updatePageSettings = useCallback(
    (patch: Partial<DocumentPageSettings>) => {
      if (readOnly) {
        return;
      }
      onDocumentChange({
        ...document,
        page_settings: {
          ...pageSettings,
          ...patch,
        },
      });
    },
    [document, onDocumentChange, pageSettings, readOnly]
  );

  const updatePageZoom = useCallback((value: number) => {
    const nextZoom = normalizePageZoom(value);
    pageZoomRef.current = nextZoom;
    setPageZoom(nextZoom);
  }, []);

  const fitPageToWidth = useCallback(() => {
    const viewportWidth = pageScrollRef.current?.clientWidth ?? 0;
    if (!viewportWidth) {
      updatePageZoom(PAGE_ZOOM_DEFAULT);
      return;
    }
    const horizontalBreathingRoom = viewportWidth >= 768 ? 96 : 48;
    updatePageZoom(((viewportWidth - horizontalBreathingRoom) / pageMetrics.width) * 100);
  }, [pageMetrics.width, updatePageZoom]);

  const handlePageWheelZoom = useCallback(
    (event: WheelEvent) => {
      if (!event.ctrlKey) {
        return;
      }
      const pageScroll = pageScrollRef.current;
      if (!pageScroll) {
        return;
      }

      event.preventDefault();
      const currentZoom = pageZoomRef.current;
      const nextZoom = normalizePageZoom(currentZoom - event.deltaY * PAGE_ZOOM_WHEEL_SENSITIVITY);
      if (nextZoom === currentZoom) {
        return;
      }

      const scrollRect = pageScroll.getBoundingClientRect();
      const pointerX = event.clientX - scrollRect.left;
      const pointerY = event.clientY - scrollRect.top;
      const currentScale = currentZoom / 100;
      const nextScale = nextZoom / 100;
      const worldX = (pageScroll.scrollLeft + pointerX) / currentScale;
      const worldY = (pageScroll.scrollTop + pointerY) / currentScale;

      updatePageZoom(nextZoom);
      window.requestAnimationFrame(() => {
        pageScroll.scrollLeft = worldX * nextScale - pointerX;
        pageScroll.scrollTop = worldY * nextScale - pointerY;
      });
    },
    [updatePageZoom]
  );

  useEffect(() => {
    const pageScroll = pageScrollRef.current;
    if (!pageScroll) {
      return;
    }

    pageScroll.addEventListener("wheel", handlePageWheelZoom, { capture: true, passive: false });
    return () => pageScroll.removeEventListener("wheel", handlePageWheelZoom, { capture: true });
  }, [handlePageWheelZoom]);

  const handleInsertBlankPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent([
        { type: "pageBreak" },
        { type: "paragraph" },
        { type: "pageBreak" },
        { type: "paragraph" },
      ])
      .run();
  }, [editor, readOnly]);

  const handleInsertCoverPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const coverTitle = document.title.trim() || e.untitledLesson;
    editor
      .chain()
      .focus("start")
      .insertContent([
        { type: "paragraph" },
        {
          type: "heading",
          attrs: { level: 1, textAlign: "center" },
          content: [{ type: "text", text: coverTitle }],
        },
        {
          type: "paragraph",
          attrs: { textAlign: "center" },
          content: [{ type: "text", text: e.coverSubtitle }],
        },
        {
          type: "paragraph",
          attrs: { textAlign: "center" },
          content: [{ type: "text", text: e.coverContextPlaceholder }],
        },
        { type: "paragraph" },
        { type: "pageBreak" },
        { type: "paragraph" },
      ])
      .run();
  }, [document.title, e.coverContextPlaceholder, e.coverSubtitle, e.untitledLesson, editor, readOnly]);

  const handleInsertTableOfContents = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const headings: string[] = [];
    editor.state.doc.descendants((node) => {
      if (node.type.name === "heading") {
        const text = node.textContent.trim();
        if (text) {
          headings.push(text);
        }
      }
    });

    editor
      .chain()
      .focus("start")
      .insertContent([
        {
          type: "heading",
          attrs: { level: 2 },
          content: [{ type: "text", text: e.tableOfContents }],
        },
        {
          type: "orderedList",
          content: (headings.length ? headings : [e.tocEmpty]).map(
            (heading) => ({
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: heading }],
                },
              ],
            })
          ),
        },
        { type: "paragraph" },
      ])
      .run();
  }, [e.tableOfContents, e.tocEmpty, editor, readOnly]);

  const handleInsertTextBox = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent({
        type: "blockquote",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: e.textBoxPlaceholder }],
          },
        ],
      })
      .run();
  }, [e.textBoxPlaceholder, editor, readOnly]);

  const handleInsertTable = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertTable({ rows: tableRows, cols: tableCols, withHeaderRow: tableHasHeaderRow })
      .run();
    setIsTableActive(true);
  }, [editor, readOnly, tableCols, tableHasHeaderRow, tableRows]);

  const handleInsertLink = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const selectedText = editor.state.doc
      .textBetween(editor.state.selection.from, editor.state.selection.to, " ")
      .trim();
    const hrefInput = window.prompt(e.linkPromptHref, "https://");
    const href = hrefInput?.trim() ?? "";
    if (!href) {
      return;
    }
    if (selectedText) {
      editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
      return;
    }
    const labelInput = window.prompt(e.linkPromptLabel, e.linkDefaultLabel);
    const label = labelInput?.trim() ?? "";
    if (!label) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent({
        type: "text",
        text: label,
        marks: [{ type: "link", attrs: { href } }],
      })
      .run();
  }, [e.linkDefaultLabel, e.linkPromptHref, e.linkPromptLabel, editor, readOnly]);

  const handleInsertHeaderFooter = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextHeader = window.prompt(e.headerPrompt, pageSettings.header_text);
    if (nextHeader === null) {
      return;
    }
    const nextFooter = window.prompt(e.footerPrompt, pageSettings.footer_text);
    if (nextFooter === null) {
      return;
    }
    updatePageSettings({
      header_text: nextHeader.trim(),
      footer_text: nextFooter.trim(),
    });
  }, [e.footerPrompt, e.headerPrompt, pageSettings.footer_text, pageSettings.header_text, readOnly, updatePageSettings]);

  const handleInsertWatermark = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextWatermark = window.prompt(e.watermarkPrompt, pageSettings.watermark_text || e.watermarkDefault);
    if (nextWatermark === null) {
      return;
    }
    updatePageSettings({ watermark_text: nextWatermark.trim() });
  }, [e.watermarkDefault, e.watermarkPrompt, pageSettings.watermark_text, readOnly, updatePageSettings]);

  const handleImageUpload = useCallback(
    (file: File) => {
      if (!editor || readOnly) {
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const src = typeof reader.result === "string" ? reader.result : "";
        if (!src) {
          return;
        }
        editor.chain().focus().setImage({ src, alt: file.name }).run();
      };
      reader.readAsDataURL(file);
    },
    [editor, readOnly]
  );

  const tableInsertHint = `${tableRows} x ${tableCols}${tableHasHeaderRow ? ` - ${e.tableHeaderHint}` : ""}`;
  const tableInsertDisabled = !editor || readOnly;
  const tableEditDisabled = tableInsertDisabled || !isTableActive;

  const renderTableDimensionFields = (compact = true) => (
    <div
      className={clsx(
        "flex items-center gap-1 rounded-lg border border-gray-200 bg-white text-gray-600",
        compact ? "h-9 px-2" : "h-[58px] px-2.5"
      )}
    >
      <Rows3 className="h-3.5 w-3.5 shrink-0" />
      <input
        type="number"
        min={TABLE_DIMENSION_MIN}
        max={TABLE_DIMENSION_MAX}
        value={tableRows}
        aria-label={e.tableRowsAria}
        disabled={tableInsertDisabled}
        onChange={(event) => setTableRows(normalizeTableDimension(Number(event.target.value)))}
        className="w-9 border-0 bg-transparent text-center text-[12px] font-semibold outline-none disabled:cursor-not-allowed"
      />
      <span className="text-[10px] text-gray-300">x</span>
      <Columns3 className="h-3.5 w-3.5 shrink-0" />
      <input
        type="number"
        min={TABLE_DIMENSION_MIN}
        max={TABLE_DIMENSION_MAX}
        value={tableCols}
        aria-label={e.tableColsAria}
        disabled={tableInsertDisabled}
        onChange={(event) => setTableCols(normalizeTableDimension(Number(event.target.value)))}
        className="w-9 border-0 bg-transparent text-center text-[12px] font-semibold outline-none disabled:cursor-not-allowed"
      />
      <label
        title={e.firstRowHeader}
        className={clsx(
          "ml-1 flex items-center gap-1 border-l border-gray-100 pl-2 text-[11px] font-medium",
          tableInsertDisabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
        )}
      >
        <input
          type="checkbox"
          checked={tableHasHeaderRow}
          disabled={tableInsertDisabled}
          onChange={(event) => setTableHasHeaderRow(event.target.checked)}
          className="h-3.5 w-3.5 accent-black"
        />
        {e.tableHeaderHint}
      </label>
    </div>
  );

  const renderTableEditButtons = (compact = true) => {
    if (compact) {
      return (
        <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
          <ToolbarButton
            title={tb.addRowAbove}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addRowBefore().run()}
          >
            <ChevronUp className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.addRowBelow}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addRowAfter().run()}
          >
            <ChevronDown className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.addColLeft}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addColumnBefore().run()}
          >
            <ArrowLeft className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.addColRight}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addColumnAfter().run()}
          >
            <ArrowRight className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.mergeCells}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().mergeCells().run()}
          >
            <TableCellsMerge className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.splitCell}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().splitCell().run()}
          >
            <TableCellsSplit className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title={tb.deleteTable}
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().deleteTable().run()}
          >
            <Trash2 className="h-4 w-4" />
          </ToolbarButton>
        </div>
      );
    }

    return (
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.addRowBelow}
          label={tb.addRow}
          hint={tb.currentTable}
          icon={<Rows3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().addRowAfter().run()}
        />
        <RibbonActionButton
          title={tb.addColRight}
          label={tb.addCol}
          hint={tb.currentTable}
          icon={<Columns3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().addColumnAfter().run()}
        />
        <RibbonActionButton
          title={tb.deleteRow}
          label={tb.deleteRow}
          hint={tb.currentTable}
          icon={<Rows3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().deleteRow().run()}
        />
        <RibbonActionButton
          title={tb.deleteCol}
          label={tb.deleteCol}
          hint={tb.currentTable}
          icon={<Columns3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().deleteColumn().run()}
        />
        <RibbonActionButton
          title={tb.mergeCells}
          label={tb.merge}
          hint={tb.selectedCells}
          icon={<TableCellsMerge className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().mergeCells().run()}
        />
        <RibbonActionButton
          title={tb.splitCell}
          label={tb.split}
          hint={tb.currentCell}
          icon={<TableCellsSplit className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().splitCell().run()}
        />
        <RibbonActionButton
          title={tb.headerRow}
          label={tb.headerRow}
          hint={tb.currentTable}
          icon={<PanelTop className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().toggleHeaderRow().run()}
        />
        <RibbonActionButton
          title={tb.deleteTable}
          label={tb.deleteTableShort}
          hint={tb.currentTable}
          icon={<Trash2 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().deleteTable().run()}
        />
      </div>
    );
  };

  const renderHomeRibbon = () => (
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
          title={tb.bold}
          active={editor?.isActive("bold")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          <Bold className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.italic}
          active={editor?.isActive("italic")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          <Italic className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.underline}
          active={editor?.isActive("underline")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleUnderline().run()}
        >
          <Underline className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.highlight}
          active={editor?.isActive("highlight")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleHighlight({ color: "#fef08a" }).run()}
        >
          <Highlighter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.textColor}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setColor("#c2410c").run()}
        >
          <Type className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title={tb.alignLeft}
          active={editor?.isActive({ textAlign: "left" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("left").run()}
        >
          <AlignLeft className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.alignCenter}
          active={editor?.isActive({ textAlign: "center" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("center").run()}
        >
          <AlignCenter className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.alignRight}
          active={editor?.isActive({ textAlign: "right" })}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().setTextAlign("right").run()}
        >
          <AlignRight className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.quote}
          active={editor?.isActive("blockquote")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          <Quote className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title={tb.bulletList}
          active={editor?.isActive("bulletList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          <List className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.orderedList}
          active={editor?.isActive("orderedList")}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().toggleOrderedList().run()}
        >
          <ListOrdered className="h-4 w-4" />
        </ToolbarButton>
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {renderTableDimensionFields()}
        <ToolbarButton
          title={e.insertTableTitle(tableInsertHint)}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        >
          <Table2 className="h-4 w-4" />
        </ToolbarButton>
      </div>

      {renderTableEditButtons()}

      <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
        <ToolbarButton
          title={tb.undo}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().undo().run()}
        >
          <Undo2 className="h-4 w-4" />
        </ToolbarButton>
        <ToolbarButton
          title={tb.redo}
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().redo().run()}
        >
          <Redo2 className="h-4 w-4" />
        </ToolbarButton>
      </div>
    </>
  );

  const renderInsertRibbon = () => (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.blankPageTitle}
          label={tb.blankPage}
          hint={tb.pageBreakHint}
          icon={<FilePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertBlankPage}
        />
        <RibbonActionButton
          title={tb.coverTitle}
          label={tb.cover}
          hint={tb.topTemplate}
          icon={<LayoutTemplate className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertCoverPage}
        />
        <RibbonActionButton
          title={tb.tocTitle}
          label={tb.toc}
          hint={tb.byHeadings}
          icon={<ClipboardList className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTableOfContents}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.pageNumberTitle}
          label={tb.pageNumber}
          hint={pageSettings.show_page_number ? tb.shown : tb.clickToShow}
          icon={<Hash className="h-4 w-4" />}
          active={pageSettings.show_page_number}
          disabled={readOnly}
          onClick={() => updatePageSettings({ show_page_number: !pageSettings.show_page_number })}
        />
        <RibbonActionButton
          title={tb.headerFooterTitle}
          label={tb.headerFooter}
          hint={tb.editText}
          icon={<PanelTop className="h-4 w-4" />}
          active={Boolean(pageSettings.header_text || pageSettings.footer_text)}
          disabled={readOnly}
          onClick={handleInsertHeaderFooter}
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
              handleImageUpload(file);
            }
            event.currentTarget.value = "";
          }}
        />
        <RibbonActionButton
          title={tb.imageTitle}
          label={tb.image}
          hint={tb.uploadToLesson}
          icon={<ImagePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={() => imageUploadRef.current?.click()}
        />
        {renderTableDimensionFields(false)}
        <RibbonActionButton
          title={e.insertTableTitle(tableInsertHint)}
          label={tb.table}
          hint={tableInsertHint}
          icon={<Table2 className="h-4 w-4" />}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        />
        <RibbonActionButton
          title={tb.textBoxTitle}
          label={tb.textBox}
          hint={tb.keyAside}
          icon={<TextCursorInput className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTextBox}
        />
      </div>

      {renderTableEditButtons(false)}

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.linkTitle}
          label={tb.link}
          hint={tb.externalResource}
          icon={<LinkIcon className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertLink}
        />
        <RibbonActionButton
          title={tb.watermarkTitle}
          label={tb.watermark}
          hint={tb.pageMark}
          icon={<Stamp className="h-4 w-4" />}
          active={Boolean(pageSettings.watermark_text)}
          disabled={readOnly}
          onClick={handleInsertWatermark}
        />
      </div>
    </>
  );

  const renderPageRibbon = () => (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_MARGIN_OPTIONS.map((option) => {
          const label = e.marginLabels[option.value];
          return (
            <RibbonActionButton
              key={option.value}
              title={tb.marginTitle(label)}
              label={label}
              hint={tb.marginHint}
              icon={<AlignHorizontalSpaceAround className="h-4 w-4" />}
              active={pageSettings.margin_preset === option.value}
              disabled={readOnly}
              onClick={() => updatePageSettings({ margin_preset: option.value })}
            />
          );
        })}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.portraitTitle}
          label={tb.portrait}
          hint={tb.orientationHint}
          icon={<RectangleVertical className="h-4 w-4" />}
          active={pageSettings.orientation === "portrait"}
          disabled={readOnly}
          onClick={() => updatePageSettings({ orientation: "portrait" })}
        />
        <RibbonActionButton
          title={tb.landscapeTitle}
          label={tb.landscape}
          hint={tb.orientationHint}
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
            title={tb.pageSizeTitle(option.label)}
            label={option.label}
            hint={tb.pageSizeHint}
            icon={<Files className="h-4 w-4" />}
            active={pageSettings.page_size === option.value}
            disabled={readOnly}
            onClick={() => updatePageSettings({ page_size: option.value })}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.oneColumnTitle}
          label={tb.oneColumn}
          hint={tb.columnsHint}
          icon={<FileText className="h-4 w-4" />}
          active={pageSettings.columns === 1}
          disabled={readOnly}
          onClick={() => updatePageSettings({ columns: 1 })}
        />
        <RibbonActionButton
          title={tb.twoColumnsTitle}
          label={tb.twoColumns}
          hint={tb.columnsHint}
          icon={<Columns2 className="h-4 w-4" />}
          active={pageSettings.columns === 2}
          disabled={readOnly}
          onClick={() => updatePageSettings({ columns: 2 })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title={tb.pageBorder}
          label={tb.pageBorder}
          hint={pageSettings.page_border ? tb.enabled : tb.disabled}
          icon={<Frame className="h-4 w-4" />}
          active={pageSettings.page_border}
          disabled={readOnly}
          onClick={() => updatePageSettings({ page_border: !pageSettings.page_border })}
        />
        <RibbonActionButton
          title={tb.lineNumbers}
          label={tb.lineNumbers}
          hint={pageSettings.line_numbers ? tb.shown : tb.clickToShow}
          icon={<ListOrdered className="h-4 w-4" />}
          active={pageSettings.line_numbers}
          disabled={readOnly}
          onClick={() => updatePageSettings({ line_numbers: !pageSettings.line_numbers })}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        {PAGE_BACKGROUND_OPTIONS.map((option) => {
          const label = e.backgroundLabels[option.value];
          return (
            <RibbonActionButton
              key={option.value}
              title={tb.backgroundTitle(label)}
              label={label}
              hint={tb.backgroundHint}
              icon={<PaintBucket className="h-4 w-4" />}
              active={pageSettings.background_style === option.value}
              disabled={readOnly}
              onClick={() => updatePageSettings({ background_style: option.value })}
            />
          );
        })}
      </div>
    </>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div
        className={clsx(
          "shrink-0 overflow-hidden transition-all duration-300",
          toolbarCollapsed ? "max-h-0 opacity-0" : "max-h-52 opacity-100"
        )}
        aria-hidden={toolbarCollapsed}
      >
        <div className={clsx("border-b border-gray-200 bg-white", readOnly && "bg-gray-50")}>
          <div className="flex h-10 items-center border-b border-gray-100 px-6">
            <RibbonTabButton active={activeRibbonTab === "home"} onClick={() => setActiveRibbonTab("home")}>
              <PencilLine className="h-3.5 w-3.5" />
              {e.tabs.home}
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "insert"} onClick={() => setActiveRibbonTab("insert")}>
              <FilePlus className="h-3.5 w-3.5" />
              {e.tabs.insert}
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "page"} onClick={() => setActiveRibbonTab("page")}>
              <Files className="h-3.5 w-3.5" />
              {e.tabs.page}
            </RibbonTabButton>
          </div>
          <div className="custom-scrollbar flex items-center gap-3 overflow-x-auto px-5 py-3 whitespace-nowrap">
            {activeRibbonTab === "home" ? renderHomeRibbon() : null}
            {activeRibbonTab === "insert" ? renderInsertRibbon() : null}
            {activeRibbonTab === "page" ? renderPageRibbon() : null}

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
                {e.importDocx}
              </button>
              <button
                type="button"
                onClick={onExportDocx}
                className="inline-flex h-10 items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[11px] font-bold uppercase tracking-wider text-gray-600 transition hover:border-gray-300"
              >
                <Download className="h-4 w-4" />
                {e.exportDocx}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        ref={pageScrollRef}
        className="min-h-0 flex-1 overflow-auto bg-[radial-gradient(circle_at_top,#f7f5ef,transparent_28%),linear-gradient(180deg,#f3f0e7_0%,#eef2f8_100%)]"
      >
        <div className="mx-auto flex w-max min-w-full justify-center px-6 py-10 md:px-10">
          <div
            className={clsx(
              "word-editor__page word-editor__page--zoomable relative flex shrink-0 flex-col overflow-hidden",
              !pageSettings.page_border && "word-editor__page--borderless",
              pageSettings.background_style === "warm" && "word-editor__page--warm",
              pageSettings.background_style === "grid" && "word-editor__page--grid",
              pageSettings.columns === 2 && "word-editor__page--columns-2",
              pageSettings.line_numbers && "word-editor__page--line-numbers"
            )}
            style={pageStyle}
          >
            {pageSettings.watermark_text ? (
              <div className="word-editor__watermark pointer-events-none select-none">{pageSettings.watermark_text}</div>
            ) : null}
            {pageSettings.header_text ? (
              <div className="word-editor__chrome word-editor__chrome--header" style={pageChromeStyle}>
                <span>{pageSettings.header_text}</span>
              </div>
            ) : null}
            <div className="border-b border-[#ece4d9]" style={titleStyle}>
              <input
                value={document.title}
                disabled={readOnly}
                onChange={(event) => onDocumentChange({ ...document, title: event.target.value })}
                className="w-full border-0 bg-transparent text-[34px] font-semibold tracking-tight text-[#1a1a1a] outline-none placeholder:text-gray-300"
                placeholder={e.untitledLesson}
              />
            </div>
            <div className="flex-1" style={contentStyle}>
              <EditorContent editor={editor} />
            </div>
            {pageSettings.footer_text || currentPageNumberLabel ? (
              <div className="word-editor__chrome word-editor__chrome--footer" style={pageChromeStyle}>
                <span>{pageSettings.footer_text}</span>
                <span>{currentPageNumberLabel}</span>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
