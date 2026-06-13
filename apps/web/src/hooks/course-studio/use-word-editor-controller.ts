import type { Editor as TiptapEditor } from "@tiptap/core";
import { useEditor } from "@tiptap/react";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import {
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
  FONT_FAMILY_OPTIONS,
  WORD_EDITOR_EXTENSIONS,
  WORD_EDITOR_PROPS,
} from "@/components/course-studio/word-editor-extensions";
import { MATH_TEXT_SERIALIZERS, normalizeEditorMath } from "@/lib/math-content";
import type { BoardDocument, BoardFocusRef, DocumentPageSettings } from "@/types";

export type WordEditorSelection = {
  excerpt: string;
  position: SelectionPopoverPosition | null;
  documentId: string;
  beforeText: string;
  afterText: string;
};

export type WordEditorCommands = {
  insertBlankPage: () => void;
  insertCoverPage: () => void;
  insertTableOfContents: () => void;
  insertTextBox: () => void;
  insertTable: () => void;
  insertLink: () => void;
  insertHeaderFooter: () => void;
  insertWatermark: () => void;
  uploadImage: (file: File) => void;
};

type FlatEditorChar = {
  text: string;
  pos: number | null;
};

type NormalizedEditorText = {
  text: string;
  map: number[];
};

function normalizeLookupText(value: string) {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function appendBlockBoundary(chars: FlatEditorChar[]) {
  const last = chars[chars.length - 1]?.text ?? "";
  if (chars.length && !/\s/.test(last)) {
    chars.push({ text: " ", pos: null });
  }
}

function flattenEditorText(editor: TiptapEditor) {
  const chars: FlatEditorChar[] = [];
  editor.state.doc.descendants((node, pos) => {
    if (node.isBlock) {
      appendBlockBoundary(chars);
    }
    if (!node.isText || !node.text) {
      return;
    }
    for (let offset = 0; offset < node.text.length; offset += 1) {
      chars.push({ text: node.text[offset] ?? "", pos: pos + offset });
    }
  });
  return chars;
}

function normalizedEditorText(chars: FlatEditorChar[]): NormalizedEditorText {
  let text = "";
  const map: number[] = [];
  let lastWasSpace = true;
  chars.forEach((char, index) => {
    if (/\s/.test(char.text)) {
      if (!lastWasSpace && text.length) {
        text += " ";
        map.push(index);
        lastWasSpace = true;
      }
      return;
    }
    text += char.text.toLocaleLowerCase();
    map.push(index);
    lastWasSpace = false;
  });
  if (text.endsWith(" ")) {
    return { text: text.slice(0, -1), map: map.slice(0, -1) };
  }
  return { text, map };
}

function uniqueNeedles(values: Array<string | undefined | null>) {
  const seen = new Set<string>();
  return values.flatMap((value) => {
    const normalized = normalizeLookupText(value ?? "");
    if (normalized.length < 2 || seen.has(normalized)) {
      return [];
    }
    seen.add(normalized);
    return [normalized];
  });
}

function allMatchIndexes(haystack: string, needle: string) {
  const indexes: number[] = [];
  let fromIndex = 0;
  while (fromIndex < haystack.length) {
    const index = haystack.indexOf(needle, fromIndex);
    if (index < 0) {
      break;
    }
    indexes.push(index);
    fromIndex = index + Math.max(needle.length, 1);
  }
  return indexes;
}

function contextScore(text: string, start: number, end: number, focus: BoardFocusRef) {
  const before = normalizeLookupText(focus.before_text).slice(-96);
  const after = normalizeLookupText(focus.after_text).slice(0, 96);
  const previous = text.slice(Math.max(0, start - 160), start);
  const next = text.slice(end, Math.min(text.length, end + 160));
  let score = 0;
  if (before && previous.endsWith(before)) {
    score += 2;
  } else if (before && previous.includes(before.slice(-48))) {
    score += 1;
  }
  if (after && next.startsWith(after)) {
    score += 2;
  } else if (after && next.includes(after.slice(0, 48))) {
    score += 1;
  }
  return score;
}

function rangeFromNormalizedMatch(
  chars: FlatEditorChar[],
  map: number[],
  start: number,
  length: number
): { from: number; to: number } | null {
  const startFlatIndex = map[start];
  const endFlatIndex = map[start + length - 1];
  if (startFlatIndex === undefined || endFlatIndex === undefined) {
    return null;
  }
  let from: number | null = null;
  let to: number | null = null;
  for (let index = startFlatIndex; index <= endFlatIndex; index += 1) {
    const pos = chars[index]?.pos;
    if (typeof pos !== "number") {
      continue;
    }
    from ??= pos;
    to = pos + 1;
  }
  return from !== null && to !== null && from < to ? { from, to } : null;
}

function findTeachingFocusRange(editor: TiptapEditor, focus: BoardFocusRef): { from: number; to: number } | null {
  const chars = flattenEditorText(editor);
  const normalized = normalizedEditorText(chars);
  const lastHeading = focus.heading_path[focus.heading_path.length - 1];
  const needles = uniqueNeedles([focus.excerpt, focus.display_label, lastHeading]);
  for (const needle of needles) {
    const matches = allMatchIndexes(normalized.text, needle)
      .map((index) => ({
        index,
        score: contextScore(normalized.text, index, index + needle.length, focus),
      }))
      .sort((a, b) => b.score - a.score || a.index - b.index);
    for (const match of matches) {
      const range = rangeFromNormalizedMatch(chars, normalized.map, match.index, needle.length);
      if (range) {
        return range;
      }
    }
  }
  return null;
}

function scrollTeachingFocusIntoView(
  editor: TiptapEditor,
  pageScroll: HTMLDivElement | null,
  range: { from: number; to: number }
) {
  if (!pageScroll) {
    return;
  }
  window.requestAnimationFrame(() => {
    try {
      const coords = editor.view.coordsAtPos(range.from);
      const containerRect = pageScroll.getBoundingClientRect();
      const targetTop = pageScroll.scrollTop + coords.top - containerRect.top - pageScroll.clientHeight * 0.34;
      pageScroll.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    } catch {
      editor.commands.scrollIntoView();
    }
  });
}

function teachingFocusKey(focus: BoardFocusRef | null | undefined) {
  if (!focus) {
    return "";
  }
  return [
    focus.document_id ?? "",
    focus.segment_id ?? "",
    focus.text_hash ?? "",
    focus.excerpt_hash ?? "",
    focus.heading_path.join("/"),
    focus.display_label ?? "",
    focus.excerpt,
  ].join("\u001f");
}

export function useWordEditorController({
  document,
  readOnly,
  teachingFocus,
  onDocumentChange,
  onSelectionChange,
}: {
  document: BoardDocument;
  readOnly: boolean;
  teachingFocus?: BoardFocusRef | null;
  onDocumentChange: (document: BoardDocument) => void;
  onSelectionChange: (selection: WordEditorSelection | null) => void;
}) {
  const imageUploadRef = useRef<HTMLInputElement | null>(null);
  const pageScrollRef = useRef<HTMLDivElement | null>(null);
  const pageZoomRef = useRef(PAGE_ZOOM_DEFAULT);
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
    const beforeText = currentEditor.state.doc.textBetween(Math.max(0, from - 240), from, " ").trim();
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

  const currentTeachingFocusKey = teachingFocusKey(teachingFocus);

  useEffect(() => {
    if (!editor) {
      return;
    }
    if (
      !teachingFocus ||
      teachingFocus.source !== "board" ||
      (teachingFocus.document_id && teachingFocus.document_id !== document.id)
    ) {
      editor.commands.clearTeachingFocusHighlight();
      return;
    }
    const range = findTeachingFocusRange(editor, teachingFocus);
    if (!range) {
      editor.commands.clearTeachingFocusHighlight();
      return;
    }
    editor.commands.setTeachingFocusHighlight(range);
    scrollTeachingFocusIntoView(editor, pageScrollRef.current, range);
  }, [currentTeachingFocusKey, document.content_text, document.id, editor, teachingFocus]);

  const currentFontSize =
    ((editor?.getAttributes("textStyle").fontSize as string | null) ?? "14px").replace("px", "");
  const currentFontFamily =
    (editor?.getAttributes("textStyle").fontFamily as string | null) ?? FONT_FAMILY_OPTIONS[0].value;
  const currentPageNumberLabel = pageSettings.show_page_number ? "第 1 页" : "";

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

  const insertBlankPage = useCallback(() => {
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

  const insertCoverPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const coverTitle = document.title.trim() || "未命名讲义";
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
          content: [{ type: "text", text: "课程讲义 / Lesson Notes" }],
        },
        {
          type: "paragraph",
          attrs: { textAlign: "center" },
          content: [{ type: "text", text: "在这里补充授课对象、目标和使用场景" }],
        },
        { type: "paragraph" },
        { type: "pageBreak" },
        { type: "paragraph" },
      ])
      .run();
  }, [document.title, editor, readOnly]);

  const insertTableOfContents = useCallback(() => {
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
          content: [{ type: "text", text: "目录" }],
        },
        {
          type: "orderedList",
          content: (headings.length ? headings : ["正文里出现标题后，可再次插入目录页自动整理结构"]).map(
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
  }, [editor, readOnly]);

  const insertTextBox = useCallback(() => {
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
            content: [{ type: "text", text: "重点说明：在这里补充定义、提醒或课堂旁注。" }],
          },
        ],
      })
      .run();
  }, [editor, readOnly]);

  const insertTable = useCallback(() => {
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

  const insertLink = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    const selectedText = editor.state.doc
      .textBetween(editor.state.selection.from, editor.state.selection.to, " ")
      .trim();
    const hrefInput = window.prompt("请输入超链接地址", "https://");
    const href = hrefInput?.trim() ?? "";
    if (!href) {
      return;
    }
    if (selectedText) {
      editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
      return;
    }
    const labelInput = window.prompt("请输入显示文本", "查看资料");
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
  }, [editor, readOnly]);

  const insertHeaderFooter = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextHeader = window.prompt("请输入页眉文字，留空则清除", pageSettings.header_text);
    if (nextHeader === null) {
      return;
    }
    const nextFooter = window.prompt("请输入页脚文字，留空则清除", pageSettings.footer_text);
    if (nextFooter === null) {
      return;
    }
    updatePageSettings({
      header_text: nextHeader.trim(),
      footer_text: nextFooter.trim(),
    });
  }, [pageSettings.footer_text, pageSettings.header_text, readOnly, updatePageSettings]);

  const insertWatermark = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextWatermark = window.prompt("请输入水印文字，留空则清除", pageSettings.watermark_text || "内部讲义");
    if (nextWatermark === null) {
      return;
    }
    updatePageSettings({ watermark_text: nextWatermark.trim() });
  }, [pageSettings.watermark_text, readOnly, updatePageSettings]);

  const uploadImage = useCallback(
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

  const tableInsertHint = `${tableRows} x ${tableCols}${tableHasHeaderRow ? " · 表头" : ""}`;
  const tableInsertDisabled = !editor || readOnly;
  const tableEditDisabled = tableInsertDisabled || !isTableActive;

  return {
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
    commands: {
      insertBlankPage,
      insertCoverPage,
      insertTableOfContents,
      insertTextBox,
      insertTable,
      insertLink,
      insertHeaderFooter,
      insertWatermark,
      uploadImage,
    },
  };
}
