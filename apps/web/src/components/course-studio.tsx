"use client";

import { Extension, type Editor as TiptapEditor } from "@tiptap/core";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import ImageExtension from "@tiptap/extension-image";
import LinkExtension from "@tiptap/extension-link";
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
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Bold,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  Cpu,
  Download,
  FileText,
  GitBranch,
  Highlighter,
  Italic,
  List,
  ListOrdered,
  LoaderCircle,
  MessageSquare,
  PanelRight,
  PencilLine,
  Plus,
  Quote,
  Radio,
  Redo2,
  Save,
  Send,
  Sparkles,
  Table2,
  Target,
  TextQuote,
  Type,
  Underline,
  Undo2,
  Upload,
  Volume2,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  BoardDecision,
  BoardDocument,
  ChatInteractionMode,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  DocumentPageSettings,
  LearningClarificationStatus,
  Lesson,
  RealtimeEventLogPayload,
  ResourceMatch,
  ResourceReferenceContext,
  ResourceReferencePrompt,
  ScopeOption,
  SelectionRef,
} from "@/types";

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

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "ready" | "pending" | "error";
};

type LessonMessageMap = Record<string, ChatMessage[]>;
type LessonComposerState = {
  chatInput: string;
  composerMode: ChatInteractionMode;
  includeSelectionInPrompt: boolean;
};
type LessonComposerStateMap = Record<string, LessonComposerState>;
type SidebarTab = "history" | "branch" | "library";
type WordRibbonTab = "home" | "insert" | "page";
type SelectionPopoverPosition = {
  top: number;
  left: number;
};
type QueuedRealtimeLogEvent = {
  lessonId: string;
  payload: RealtimeEventLogPayload;
};

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

const WORD_EDITOR_EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
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
  Table.configure({ resizable: true }),
  TableRow,
  TableHeader,
  TableCell,
  FontSize,
  FontFamily,
];

const WORD_EDITOR_PROPS = {
  attributes: {
    class: "word-editor__content",
  },
};

const DEFAULT_LESSON_COMPOSER_STATE: LessonComposerState = {
  chatInput: "",
  composerMode: "ask",
  includeSelectionInPrompt: true,
};

const DEFAULT_PAGE_SETTINGS: DocumentPageSettings = {
  margin_preset: "normal",
  orientation: "portrait",
  page_size: "a4",
  columns: 1,
  page_border: true,
  background_style: "plain",
  watermark_text: "",
  line_numbers: false,
  show_page_number: false,
  header_text: "",
  footer_text: "",
};

const PAGE_SIZE_OPTIONS = [
  { value: "a4", label: "A4", width: 860, height: 1216 },
  { value: "letter", label: "Letter", width: 884, height: 1142 },
  { value: "a3", label: "A3", width: 980, height: 1386 },
] as const;

const PAGE_MARGIN_OPTIONS = [
  { value: "narrow", label: "窄", paddingX: 42, paddingY: 54 },
  { value: "normal", label: "普通", paddingX: 56, paddingY: 68 },
  { value: "wide", label: "宽", paddingX: 74, paddingY: 86 },
] as const;

const PAGE_BACKGROUND_OPTIONS = [
  { value: "plain", label: "纯白" },
  { value: "warm", label: "暖白" },
  { value: "grid", label: "网格纸" },
] as const;

function normalizePageSettings(settings?: Partial<DocumentPageSettings> | null): DocumentPageSettings {
  return {
    ...DEFAULT_PAGE_SETTINGS,
    ...(settings ?? {}),
  };
}

function pagePreviewMetrics(settings: DocumentPageSettings) {
  const baseSize = PAGE_SIZE_OPTIONS.find((option) => option.value === settings.page_size) ?? PAGE_SIZE_OPTIONS[0];
  const margin = PAGE_MARGIN_OPTIONS.find((option) => option.value === settings.margin_preset) ?? PAGE_MARGIN_OPTIONS[1];
  return {
    width: settings.orientation === "landscape" ? baseSize.height : baseSize.width,
    height: settings.orientation === "landscape" ? baseSize.width : baseSize.height,
    paddingX: margin.paddingX,
    paddingY: margin.paddingY,
  };
}

function createChatMessage(
  role: ChatMessage["role"],
  content: string,
  status: ChatMessage["status"] = "ready"
): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    status,
  };
}

function createClientSessionId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

function createLessonComposerState(): LessonComposerState {
  return { ...DEFAULT_LESSON_COMPOSER_STATE };
}

function buildWelcomeMessages(): ChatMessage[] {
  return [
    createChatMessage(
      "assistant",
      "你好！你可以从学习目标出发提问，我会先给出一版可讲的主线内容，再按你的追问继续扩写、改写和补练习。"
    ),
    createChatMessage(
      "assistant",
      "右侧现在是连续 Word-like 板书文档。你可以选中一段文字让我只改这一段，也可以让我先起草一版讲义，再逐段讲透。"
    ),
  ];
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function metadataText(commit: CommitRecord, key: string): string | null {
  const value = commit.metadata?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function metadataBool(commit: CommitRecord, key: string): boolean {
  return commit.metadata?.[key] === true;
}

function compactText(value: string, limit = 120) {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= limit) {
    return compact;
  }
  return `${compact.slice(0, limit - 1)}…`;
}

function nextBranchName(lesson: Lesson) {
  let index = Object.keys(lesson.history_graph.branches).length + 1;
  let name = `branch-${index}`;
  while (lesson.history_graph.branches[name]) {
    index += 1;
    name = `branch-${index}`;
  }
  return name;
}

function documentsEqual(left: BoardDocument | null | undefined, right: BoardDocument | null | undefined) {
  if (!left || !right) {
    return false;
  }
  return (
    left.title === right.title &&
    left.content_html === right.content_html &&
    left.content_text === right.content_text &&
    JSON.stringify(normalizePageSettings(left.page_settings)) ===
      JSON.stringify(normalizePageSettings(right.page_settings))
  );
}

function sameSelection(
  left: SelectionRef | null,
  right: SelectionRef | null
) {
  if (!left || !right) {
    return left === right;
  }
  return (
    left.kind === right.kind &&
    left.lesson_id === right.lesson_id &&
    left.block_id === right.block_id &&
    left.excerpt === right.excerpt
  );
}

function samePopoverPosition(
  left: SelectionPopoverPosition | null,
  right: SelectionPopoverPosition | null
) {
  if (!left || !right) {
    return left === right;
  }
  return Math.abs(left.left - right.left) < 1 && Math.abs(left.top - right.top) < 1;
}

function clampSelectionPopover(left: number, top: number): SelectionPopoverPosition {
  if (typeof window === "undefined") {
    return { left, top };
  }
  return {
    left: Math.max(88, Math.min(left, window.innerWidth - 88)),
    top: Math.max(12, Math.min(top, window.innerHeight - 80)),
  };
}

function popoverPositionFromDomSelection(): SelectionPopoverPosition | null {
  if (typeof window === "undefined") {
    return null;
  }
  const activeSelection = window.getSelection();
  if (!activeSelection || activeSelection.rangeCount === 0) {
    return null;
  }
  const rect = activeSelection.getRangeAt(0).getBoundingClientRect();
  if (!rect.width && !rect.height) {
    return null;
  }
  return clampSelectionPopover(rect.left + rect.width / 2, rect.top - 44);
}

function ToolbarButton({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "flex h-9 w-9 items-center justify-center rounded-lg border text-gray-600 transition",
        active
          ? "border-black bg-black text-white"
          : "border-transparent hover:border-gray-200 hover:bg-white",
        disabled && "cursor-not-allowed opacity-40"
      )}
    >
      {children}
    </button>
  );
}

function RibbonTabButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "mr-4 h-full border-b-2 px-2 text-[10px] font-bold uppercase tracking-widest transition-colors",
        active ? "border-black text-black" : "border-transparent text-gray-400 hover:text-black"
      )}
    >
      {children}
    </button>
  );
}

function RibbonActionButton({
  title,
  label,
  hint,
  active,
  disabled,
  onClick,
}: {
  title: string;
  label: string;
  hint?: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "flex min-w-[74px] flex-col items-start rounded-xl border px-3 py-2 text-left transition",
        active
          ? "border-black bg-black text-white shadow-sm"
          : "border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50",
        disabled && "cursor-not-allowed opacity-40"
      )}
    >
      <span className="text-[12px] font-semibold">{label}</span>
      {hint ? <span className={clsx("mt-1 text-[10px]", active ? "text-white/70" : "text-gray-400")}>{hint}</span> : null}
    </button>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const isAssistant = message.role === "assistant";
  const isPending = message.status === "pending";
  const isError = message.status === "error";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {isPending ? (
          <LoaderCircle className="h-3.5 w-3.5 animate-spin text-blue-600" />
        ) : isAssistant ? (
          <Sparkles className="h-3.5 w-3.5 text-gray-800" />
        ) : (
          <MessageSquare className="h-3.5 w-3.5 text-gray-800" />
        )}
        <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
          {isPending ? "AI 讲师处理中" : isAssistant ? "AI 讲师" : "用户"}
        </span>
      </div>
      <div
        className={clsx(
          "max-w-[94%] rounded-2xl p-4 text-[13px] leading-relaxed shadow-sm",
          isPending
            ? "rounded-tl-sm border border-blue-200 bg-blue-50 text-blue-950"
            : isError
              ? "rounded-tl-sm border border-rose-200 bg-rose-50 text-rose-800"
              : isAssistant
            ? "rounded-tl-sm border border-gray-100 bg-gray-50 text-gray-800"
            : "ml-auto rounded-tr-sm bg-[#1a1a1a] text-white"
        )}
      >
        {message.content}
        {isPending ? (
          <p className="mt-3 text-[11px] font-medium text-blue-700">
            请求已发出，生成讲义可能需要几十秒到几分钟。
          </p>
        ) : null}
      </div>
    </div>
  );
}

function CommitTimelineItem({
  commit,
  active,
  latest,
  onPreview,
  onRestore,
  onBranch,
}: {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
  onPreview: () => void;
  onRestore: () => void;
  onBranch: () => void;
}) {
  const isChatFlow = commit.metadata?.kind === "chat_flow";
  const userMessage = metadataText(commit, "user_message");
  const assistantMessage = metadataText(commit, "assistant_message");
  const boardAction = metadataText(commit, "board_action");
  const autoApplied = metadataBool(commit, "auto_applied");

  return (
    <div className="relative flex gap-4 pl-3">
      <div className={clsx("absolute left-0 top-1.5 h-full w-px", latest ? "bg-black" : "bg-gray-200")} />
      <div
        className={clsx(
          "absolute -left-[4px] top-1.5 h-2 w-2 rounded-full",
          latest ? "bg-black" : active ? "bg-gray-500" : "bg-gray-300"
        )}
      />
      <div className="flex-1 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className={clsx("text-xs font-bold", latest ? "text-black" : "text-gray-800")}>{commit.label}</p>
          {isChatFlow ? (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-blue-700">
              Flow
            </span>
          ) : null}
          {autoApplied ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-emerald-700">
              Applied
            </span>
          ) : null}
        </div>
        {isChatFlow && userMessage ? (
          <div className="mt-2 rounded-xl border border-gray-100 bg-white p-3 text-[11px] leading-5 text-gray-600 shadow-sm">
            <p className="font-bold text-gray-400">用户输入</p>
            <p className="mt-1 text-gray-700">{compactText(userMessage)}</p>
            {assistantMessage ? (
              <>
                <p className="mt-3 font-bold text-gray-400">AI 讲解</p>
                <p className="mt-1 text-gray-700">{compactText(assistantMessage)}</p>
              </>
            ) : null}
            {boardAction ? (
              <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                Action: {boardAction}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-[11px] text-gray-500">{commit.message}</p>
        )}
        <p className="mt-1 text-[11px] text-gray-400">{formatDate(commit.created_at)}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onPreview}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Preview
          </button>
          <button
            type="button"
            onClick={onRestore}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Restore
          </button>
          <button
            type="button"
            onClick={onBranch}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <GitBranch className="mr-1 inline h-3 w-3" />
            Branch
          </button>
        </div>
      </div>
    </div>
  );
}

function WordBoardEditor({
  document,
  readOnly,
  isDirty,
  busyAction,
  toolbarCollapsed,
  onDocumentChange,
  onSelectionChange,
  onSave,
  onImportDocx,
  onExportDocx,
}: {
  document: BoardDocument;
  readOnly: boolean;
  isDirty: boolean;
  busyAction: string | null;
  toolbarCollapsed: boolean;
  onDocumentChange: (document: BoardDocument) => void;
  onSelectionChange: (selection: { excerpt: string; position: SelectionPopoverPosition | null } | null) => void;
  onSave: () => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
}) {
  const importRef = useRef<HTMLInputElement | null>(null);
  const imageUploadRef = useRef<HTMLInputElement | null>(null);
  const [activeRibbonTab, setActiveRibbonTab] = useState<WordRibbonTab>("home");
  const editorContent =
    document.content_html.trim() ||
    (document.content_json && Object.keys(document.content_json).length ? document.content_json : "<p></p>");
  const latestDocumentRef = useRef(document);
  const latestReadOnlyRef = useRef(readOnly);
  const latestOnDocumentChangeRef = useRef(onDocumentChange);
  const latestOnSelectionChangeRef = useRef(onSelectionChange);
  const pageSettings = normalizePageSettings(document.page_settings);
  const pageMetrics = pagePreviewMetrics(pageSettings);
  const pageStyle = {
    "--page-padding-x": `${pageMetrics.paddingX}px`,
    "--page-padding-y": `${pageMetrics.paddingY}px`,
    maxWidth: `${pageMetrics.width}px`,
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
    latestOnDocumentChangeRef.current({
      ...latestDocumentRef.current,
      content_json: currentEditor.getJSON() as Record<string, unknown>,
      content_html: currentEditor.getHTML(),
      content_text: currentEditor.getText(),
    });
  }, []);

  const handleEditorSelectionUpdate = useCallback(({ editor: currentEditor }: { editor: TiptapEditor }) => {
    if (latestReadOnlyRef.current) {
      latestOnSelectionChangeRef.current(null);
      return;
    }
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
    latestOnSelectionChangeRef.current({
      excerpt,
      position: popoverPositionFromDomSelection(),
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
    const matchesIncomingDocument = incomingHtml
      ? editor.getHTML() === incomingHtml
      : JSON.stringify(editor.getJSON()) === JSON.stringify(document.content_json ?? {});
    if (!matchesIncomingDocument) {
      editor.commands.setContent(editorContent, { emitUpdate: false });
    }
  }, [document.id, document.content_html, document.content_json, editor, editorContent, readOnly]);

  const currentFontSize =
    ((editor?.getAttributes("textStyle").fontSize as string | null) ?? "14px").replace("px", "");
  const currentFontFamily = (editor?.getAttributes("textStyle").fontFamily as string | null) ?? FONT_FAMILY_OPTIONS[0].value;
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

  const handleInsertBlankPage = useCallback(() => {
    if (!editor || readOnly) {
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent([
        { type: "paragraph" },
        { type: "horizontalRule" },
        { type: "paragraph" },
        { type: "paragraph" },
      ])
      .run();
  }, [editor, readOnly]);

  const handleInsertCoverPage = useCallback(() => {
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
        { type: "horizontalRule" },
        { type: "paragraph" },
      ])
      .run();
  }, [document.title, editor, readOnly]);

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
            content: [{ type: "text", text: "重点说明：在这里补充定义、提醒或课堂旁注。" }],
          },
        ],
      })
      .run();
  }, [editor, readOnly]);

  const handleInsertLink = useCallback(() => {
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

  const handleInsertHeaderFooter = useCallback(() => {
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

  const handleInsertWatermark = useCallback(() => {
    if (readOnly) {
      return;
    }
    const nextWatermark = window.prompt("请输入水印文字，留空则清除", pageSettings.watermark_text || "内部讲义");
    if (nextWatermark === null) {
      return;
    }
    updatePageSettings({ watermark_text: nextWatermark.trim() });
  }, [pageSettings.watermark_text, readOnly, updatePageSettings]);

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
        <ToolbarButton
          title="插入表格"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        >
          <Table2 className="h-4 w-4" />
        </ToolbarButton>
      </div>

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

  const renderInsertRibbon = () => (
    <>
      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入空白页"
          label="空白页"
          hint="分页占位"
          disabled={!editor || readOnly}
          onClick={handleInsertBlankPage}
        />
        <RibbonActionButton
          title="插入封面"
          label="封面"
          hint="置顶模板"
          disabled={!editor || readOnly}
          onClick={handleInsertCoverPage}
        />
        <RibbonActionButton
          title="插入目录页"
          label="目录页"
          hint="按标题生成"
          disabled={!editor || readOnly}
          onClick={handleInsertTableOfContents}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="切换页码"
          label="页码"
          hint={pageSettings.show_page_number ? "已显示" : "点击显示"}
          active={pageSettings.show_page_number}
          disabled={readOnly}
          onClick={() => updatePageSettings({ show_page_number: !pageSettings.show_page_number })}
        />
        <RibbonActionButton
          title="设置页眉页脚"
          label="页眉页脚"
          hint="编辑文案"
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
          title="插入图片"
          label="图片"
          hint="上传到讲义"
          disabled={!editor || readOnly}
          onClick={() => imageUploadRef.current?.click()}
        />
        <RibbonActionButton
          title="插入表格"
          label="表格"
          hint="3 x 3"
          disabled={!editor || readOnly}
          onClick={() => editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        />
        <RibbonActionButton
          title="插入文本框"
          label="文本框"
          hint="重点旁注"
          disabled={!editor || readOnly}
          onClick={handleInsertTextBox}
        />
      </div>

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入超链接"
          label="超链接"
          hint="外部资料"
          disabled={!editor || readOnly}
          onClick={handleInsertLink}
        />
        <RibbonActionButton
          title="插入水印"
          label="水印"
          hint="页面标识"
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
        {PAGE_MARGIN_OPTIONS.map((option) => (
          <RibbonActionButton
            key={option.value}
            title={`页边距：${option.label}`}
            label={option.label}
            hint="页边距"
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
          active={pageSettings.orientation === "portrait"}
          disabled={readOnly}
          onClick={() => updatePageSettings({ orientation: "portrait" })}
        />
        <RibbonActionButton
          title="横向排版"
          label="横向"
          hint="纸张方向"
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
          active={pageSettings.columns === 1}
          disabled={readOnly}
          onClick={() => updatePageSettings({ columns: 1 })}
        />
        <RibbonActionButton
          title="双栏排版"
          label="双栏"
          hint="分栏"
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
          active={pageSettings.page_border}
          disabled={readOnly}
          onClick={() => updatePageSettings({ page_border: !pageSettings.page_border })}
        />
        <RibbonActionButton
          title="行号"
          label="行号"
          hint={pageSettings.line_numbers ? "已显示" : "点击显示"}
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
            active={pageSettings.background_style === option.value}
            disabled={readOnly}
            onClick={() => updatePageSettings({ background_style: option.value })}
          />
        ))}
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
              开始 (HOME)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "insert"} onClick={() => setActiveRibbonTab("insert")}>
              插入 (INSERT)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "page"} onClick={() => setActiveRibbonTab("page")}>
              页面 (PAGE)
            </RibbonTabButton>
          </div>
          <div className="custom-scrollbar flex items-center gap-3 overflow-x-auto px-5 py-3 whitespace-nowrap">
            {activeRibbonTab === "home" ? renderHomeRibbon() : null}
            {activeRibbonTab === "insert" ? renderInsertRibbon() : null}
            {activeRibbonTab === "page" ? renderPageRibbon() : null}

            <div className="ml-auto flex items-center gap-2">
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
              <button
                type="button"
                onClick={onSave}
                disabled={readOnly || !isDirty || busyAction === "save"}
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-[#1a1a1a] px-4 text-[11px] font-bold uppercase tracking-wider text-white transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busyAction === "save" ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
                保存
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_top,#f7f5ef,transparent_28%),linear-gradient(180deg,#f3f0e7_0%,#eef2f8_100%)]">
        <div className="mx-auto flex w-full justify-center px-6 py-10 md:px-10">
          <div
            className={clsx(
              "word-editor__page relative flex w-full flex-col overflow-hidden",
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
                placeholder="未命名讲义"
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

export function CourseStudio() {
  const router = useRouter();
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const chatRequestInFlightRef = useRef(false);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const realtimePeerRef = useRef<RTCPeerConnection | null>(null);
  const realtimeChannelRef = useRef<RTCDataChannel | null>(null);
  const realtimeStreamRef = useRef<MediaStream | null>(null);
  const realtimeLessonIdRef = useRef<string | null>(null);
  const realtimeClientSessionIdRef = useRef<string | null>(null);
  const realtimeLessonTitleRef = useRef<string | null>(null);
  const realtimeLogQueueRef = useRef<QueuedRealtimeLogEvent[]>([]);
  const realtimeLogFlushInFlightRef = useRef(false);

  const [coursePackage, setCoursePackage] = useState<CoursePackage | null>(null);
  const [draftDocument, setDraftDocument] = useState<BoardDocument | null>(null);
  const [isDocumentDirty, setIsDocumentDirty] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lessonComposerStates, setLessonComposerStates] = useState<LessonComposerStateMap>({});
  const [newBranchName, setNewBranchName] = useState("");
  const [selection, setSelection] = useState<SelectionRef | null>(null);
  const [selectionPopover, setSelectionPopover] = useState<SelectionPopoverPosition | null>(null);
  const [scopeOptions, setScopeOptions] = useState<ScopeOption[]>([]);
  const [, setResourceMatches] = useState<ResourceMatch[]>([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const [referencePrompt, setReferencePrompt] = useState<ResourceReferencePrompt | null>(null);
  const [selectedReference, setSelectedReference] = useState<ResourceReferenceContext | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [lastReferenceRequest, setLastReferenceRequest] = useState<ChatRequestPayload | null>(null);
  const [previewCommitId, setPreviewCommitId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [lessonMessages, setLessonMessages] = useState<LessonMessageMap>({});
  const [topCollapsed, setTopCollapsed] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("history");
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceStatusText, setVoiceStatusText] = useState("点击麦克风，连接 GPT-4o Realtime 语音讲师");

  const handleReturnHome = useCallback(() => {
    if (
      isDocumentDirty &&
      typeof window !== "undefined" &&
      !window.confirm("当前讲义还有未保存修改，确定返回主页吗？")
    ) {
      return;
    }
    router.push("/");
  }, [isDocumentDirty, router]);

  useEffect(() => {
    async function load() {
      try {
        const payload = await api.getCoursePackage();
        const initialLesson =
          payload.lessons.find((lesson) => lesson.id === payload.active_lesson_id) ?? payload.lessons[0] ?? null;
        setCoursePackage(payload);
        setDraftDocument(initialLesson?.board_document ?? null);
        setLessonMessages((current) => {
          const next: LessonMessageMap = {};
          payload.lessons.forEach((lesson) => {
            next[lesson.id] = current[lesson.id] ?? buildWelcomeMessages();
          });
          return next;
        });
        setLessonComposerStates((current) => {
          const next: LessonComposerStateMap = {};
          payload.lessons.forEach((lesson) => {
            next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
          });
          return next;
        });
        setError(null);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "加载失败");
      } finally {
        setIsLoading(false);
      }
    }
    void load();
  }, []);

  const lessonMap = new Map<string, Lesson>();
  coursePackage?.lessons.forEach((lesson) => lessonMap.set(lesson.id, lesson));

  const activeLesson = coursePackage?.active_lesson_id
    ? lessonMap.get(coursePackage.active_lesson_id) ?? coursePackage.lessons[0] ?? null
    : coursePackage?.lessons[0] ?? null;

  const previewCommit =
    previewCommitId && activeLesson
      ? activeLesson.history_graph.commits.find((commit) => commit.id === previewCommitId) ?? null
      : null;

  const displayedDocument = previewCommit?.snapshot ?? draftDocument ?? activeLesson?.board_document ?? null;
  const openLessons = (coursePackage?.workspace_tab_order
    .map((lessonId) => lessonMap.get(lessonId))
    .filter(Boolean) as Lesson[]) ?? [];
  const activeMessages = activeLesson ? lessonMessages[activeLesson.id] ?? [] : [];
  const activeComposerState = activeLesson
    ? lessonComposerStates[activeLesson.id] ?? DEFAULT_LESSON_COMPOSER_STATE
    : DEFAULT_LESSON_COMPOSER_STATE;
  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit";
  const activeRequirements = activeLesson?.learning_requirements ?? null;
  const isPreviewMode = Boolean(previewCommit);
  const latestAssistantMessage = [...activeMessages].reverse().find((message) => message.role === "assistant");
  const relatedEdges =
    activeLesson && coursePackage
      ? coursePackage.course_graph.filter(
          (edge) =>
            edge.source_lesson_id === activeLesson.id || edge.target_lesson_id === activeLesson.id
        )
      : [];
  const composerSelection = selection && !selectionPopover ? selection : null;

  const learningGoalItems = [
    activeRequirements?.learning_goal ?? activeLesson?.summary ?? "围绕当前课程主线推进学习",
    activeRequirements?.success_criteria ?? "先建立概念，再进入例题与练习",
  ];
  const clarityStatus: LearningClarificationStatus =
    learningClarity ?? {
      progress: 0,
      label: "等待学习目的",
      reason: "先告诉我你想学什么，我会逐步确认目标、水平、场景、输出形式和重点约束。",
      missing_items: ["想学的主题", "当前水平或背景", "具体使用场景或知识点"],
      can_start: false,
      forced_start: false,
    };
  const clarityBarTone =
    clarityStatus.progress >= 90
      ? "bg-emerald-500"
      : clarityStatus.can_start
        ? "bg-blue-500"
        : "bg-amber-500";

  function updateLessonComposerState(
    lessonId: string,
    updater: (current: LessonComposerState) => LessonComposerState
  ) {
    setLessonComposerStates((current) => ({
      ...current,
      [lessonId]: updater(current[lessonId] ?? createLessonComposerState()),
    }));
  }

  function updateActiveLessonComposerState(
    updater: (current: LessonComposerState) => LessonComposerState
  ) {
    if (!activeLesson) {
      return;
    }
    updateLessonComposerState(activeLesson.id, updater);
  }

  function syncLessonComposerStates(lessons: Lesson[]) {
    setLessonComposerStates((current) => {
      const next: LessonComposerStateMap = {};
      lessons.forEach((lesson) => {
        next[lesson.id] = current[lesson.id] ?? createLessonComposerState();
      });
      return next;
    });
  }

  function applySelection(nextSelection: SelectionRef, popoverPosition?: SelectionPopoverPosition | null) {
    setSelection((current) => (sameSelection(current, nextSelection) ? current : nextSelection));
    setSelectionPopover((current) => {
      const nextPosition = popoverPosition ?? null;
      return samePopoverPosition(current, nextPosition) ? current : nextPosition;
    });
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
    }));
  }

  function clearSelection() {
    setSelection((current) => (current ? null : current));
    setSelectionPopover((current) => (current ? null : current));
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
    }));
  }

  function focusComposerWithSelection(nextMode: ChatInteractionMode) {
    if (!selection) {
      return;
    }
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: nextMode,
      includeSelectionInPrompt: true,
    }));
    setSelectionPopover(null);
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus();
    });
  }

  function adjustComposerHeight() {
    const input = chatInputRef.current;
    if (!input) {
      return;
    }
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }

  const adjustComposerHeightEffectEvent = useEffectEvent(() => {
    adjustComposerHeight();
  });

  useEffect(() => {
    adjustComposerHeightEffectEvent();
  }, [chatInput, composerSelection?.excerpt]);

  function resetTransientUi() {
    setPreviewCommitId(null);
    setScopeOptions([]);
    setResourceMatches([]);
    setClarificationQuestions([]);
    setLearningClarity(null);
    setLatestBoardDecision(null);
    setReferencePrompt(null);
    setSelectedReference(null);
    setLastScopedRequest(null);
    setLastReferenceRequest(null);
    clearSelection();
  }

  function syncLessonMessages(nextPackage: CoursePackage, options?: { blankLessonIds?: string[] }) {
    const blankLessonIds = new Set(options?.blankLessonIds ?? []);
    setLessonMessages((current) => {
      const next: LessonMessageMap = {};
      nextPackage.lessons.forEach((lesson) => {
        next[lesson.id] =
          current[lesson.id] ?? (blankLessonIds.has(lesson.id) ? [] : buildWelcomeMessages());
      });
      return next;
    });
  }

  function updateLessonMessages(
    lessonId: string,
    updater: (messages: ChatMessage[]) => ChatMessage[]
  ) {
    setLessonMessages((current) => ({
      ...current,
      [lessonId]: updater(current[lessonId] ?? []),
    }));
  }

  function updateCoursePackage(
    nextPackage: CoursePackage,
    options?: { blankLessonIds?: string[]; activeLessonId?: string | null }
  ) {
    const requestedActiveLessonId = options?.activeLessonId;
    const effectiveActiveLessonId =
      requestedActiveLessonId && nextPackage.workspace_tab_order.includes(requestedActiveLessonId)
        ? requestedActiveLessonId
        : nextPackage.active_lesson_id;
    const mergedPackage =
      effectiveActiveLessonId === nextPackage.active_lesson_id
        ? nextPackage
        : { ...nextPackage, active_lesson_id: effectiveActiveLessonId };
    const nextActiveLesson =
      mergedPackage.lessons.find((lesson) => lesson.id === mergedPackage.active_lesson_id) ??
      mergedPackage.lessons[0] ??
      null;
    setCoursePackage(mergedPackage);
    setDraftDocument(nextActiveLesson?.board_document ?? null);
    setIsDocumentDirty(false);
    syncLessonMessages(mergedPackage, options);
    syncLessonComposerStates(mergedPackage.lessons);
    resetTransientUi();
    setError(null);
  }

  function handleLocalDocumentChange(nextDocument: BoardDocument) {
    if (isPreviewMode || !activeLesson) {
      return;
    }
    setDraftDocument((current) => {
      if (current && current.id === nextDocument.id && documentsEqual(current, nextDocument)) {
        return current;
      }
      return nextDocument;
    });
    setIsDocumentDirty(!documentsEqual(nextDocument, activeLesson.board_document));
  }

  async function handleSaveDocument() {
    if (!activeLesson || !draftDocument || !isDocumentDirty || isPreviewMode) {
      return;
    }
    setBusyAction("save");
    try {
      const nextPackage = await api.saveDocument(activeLesson.id, {
        document: draftDocument,
        label: "Manual document edit",
        message: "Saved Word-like rich document changes from the editor",
      });
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson.id });
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleImportDocx(file: File) {
    if (!activeLesson) {
      return;
    }
    setBusyAction("import-docx");
    try {
      const nextPackage = await api.importDocx(activeLesson.id, file);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson.id });
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "导入 DOCX 失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleExportDocx() {
    if (!activeLesson) {
      return;
    }
    setBusyAction("export-docx");
    try {
      const blob = await api.exportDocx(activeLesson.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${activeLesson.slug || activeLesson.id}.docx`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "导出 DOCX 失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function saveGeneratedLesson(topic: string) {
    if (!topic.trim() || !activeLesson) {
      return;
    }
    setBusyAction("generate");
    try {
      const nextPackage = await api.generateLesson(topic.trim(), activeLesson.id, true);
      updateCoursePackage(nextPackage, {
        blankLessonIds: nextPackage.active_lesson_id ? [nextPackage.active_lesson_id] : [],
      });
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "生成 lesson 失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateLessonFromPrompt() {
    const topic = window.prompt("请输入新单课主题，例如：法国咖啡厅点餐情景对话");
    if (!topic) {
      return;
    }
    await saveGeneratedLesson(topic);
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload) {
    if (!activeLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    const lessonId = activeLesson.id;
    const submittedInput = chatInput;
    const payload =
      payloadOverride ??
      ({
        message: chatInput.trim(),
        selection: includeSelectionInPrompt && composerSelection ? composerSelection : null,
        interaction_mode: composerMode,
      } satisfies ChatRequestPayload);
    const payloadWithConversation: ChatRequestPayload = {
      ...payload,
      conversation: activeMessages.slice(-8).map(({ role, content }) => ({ role, content })),
    };

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    const isDirectEdit = payloadWithConversation.interaction_mode === "direct_edit";
    const userMessageContent = payloadOverride?.scope_action
      ? `继续执行：${payloadOverride.scope_action}`
      : payloadOverride?.resource_reference_action === "confirm"
        ? "继续执行：参考推荐章节生成讲义"
        : payloadOverride?.resource_reference_action === "skip"
          ? "继续执行：先不参考推荐章节"
          : isDirectEdit
            ? `直接编辑讲义：${payloadWithConversation.message}`
            : payloadWithConversation.message;
    const pendingAssistantMessage = createChatMessage(
      "assistant",
      payloadOverride?.scope_action
        ? "正在继续执行这一步，我会先把主线接上，再继续细化。"
        : payloadOverride?.resource_reference_action === "confirm"
          ? "正在结合你确认的参考章节起草讲义，我会先给出可讲版本。"
          : payloadOverride?.resource_reference_action === "skip"
            ? "正在按当前 lesson 主线继续生成内容，我会先把第一版讲清楚。"
            : isDirectEdit
              ? "正在改写右侧讲义，并同步准备更像真人老师的讲法。"
              : "正在整理内容并更新右侧讲义，我会先返回一版可讲内容。",
      "pending"
    );
    chatRequestInFlightRef.current = true;
    setBusyAction(isDirectEdit ? "agent-edit" : "chat");
    setError(null);
    if (!payloadOverride) {
      updateLessonComposerState(lessonId, (current) => ({
        ...current,
        chatInput: "",
      }));
    }
    updateLessonMessages(lessonId, (current) => [
      ...current,
      createChatMessage("user", userMessageContent),
      pendingAssistantMessage,
    ]);

    try {
      const response = await api.chatOnLesson(lessonId, payloadWithConversation);
      updateCoursePackage(response.course_package, {
        activeLessonId: response.created_lesson ? undefined : lessonId,
      });
      setLatestBoardDecision(response.board_decision);
      setClarificationQuestions(response.clarification_questions);
      setLearningClarity(response.learning_clarification);
      setScopeOptions(response.scope_options);
      setResourceMatches(response.resource_matches);
      setReferencePrompt(response.reference_prompt ?? null);
      setSelectedReference(response.selected_reference ?? null);
      setLastScopedRequest(response.scope_options.length ? payloadWithConversation : null);
      setLastReferenceRequest(response.reference_prompt ? payloadWithConversation : null);
      const assistantMessages = [createChatMessage("assistant", response.teacher_message)];
      if (response.created_lesson) {
        assistantMessages.push(
          createChatMessage(
            "assistant",
            `我已经为这个更大的知识问题新开了一节课：《${response.created_lesson.title}》。`
          )
        );
      }
      updateLessonMessages(lessonId, (current) => [
        ...current.filter((message) => message.id !== pendingAssistantMessage.id),
        ...assistantMessages,
      ]);
      if (!payloadWithConversation.scope_action) {
        clearSelection();
      }
    } catch (chatError) {
      if (!payloadOverride) {
        updateLessonComposerState(lessonId, (current) => ({
          ...current,
          chatInput: submittedInput,
        }));
      }
      updateLessonMessages(lessonId, (current) => [
        ...current.filter((message) => message.id !== pendingAssistantMessage.id),
        createChatMessage(
          "assistant",
          `这次没有顺利完成，我先把你的输入保留好了，可以直接重试。\n${
            chatError instanceof Error ? chatError.message : "聊天失败"
          }`,
          "error"
        ),
      ]);
      setError(chatError instanceof Error ? chatError.message : "聊天失败");
    } finally {
      chatRequestInFlightRef.current = false;
      setBusyAction(null);
    }
  }

  async function handleScopeAction(option: ScopeOption) {
    if (!activeLesson || !lastScopedRequest) {
      return;
    }
    await handleSubmitChat({
      message: lastScopedRequest.message,
      selection: lastScopedRequest.selection,
      interaction_mode: lastScopedRequest.interaction_mode,
      scope_action: option.action,
      resource_chapter_id: option.resource_chapter_id ?? undefined,
    });
    setScopeOptions([]);
    setLastScopedRequest(null);
  }

  async function handleReferenceAction(action: "confirm" | "skip") {
    if (!referencePrompt || !lastReferenceRequest) {
      return;
    }
    await handleSubmitChat({
      message: lastReferenceRequest.message,
      selection: lastReferenceRequest.selection,
      interaction_mode: lastReferenceRequest.interaction_mode,
      scope_action: lastReferenceRequest.scope_action,
      resource_chapter_id: lastReferenceRequest.resource_chapter_id,
      resource_reference_action: action,
      resource_reference_resource_id: referencePrompt.resource_id,
      resource_reference_chapter_id: referencePrompt.chapter_id,
    });
    setReferencePrompt(null);
    setLastReferenceRequest(null);
  }

  async function handleCreateBranch(fromCommitId = previewCommitId, branchNameOverride?: string) {
    if (!activeLesson) {
      return;
    }
    const branchName = (branchNameOverride ?? newBranchName.trim()).trim();
    const finalBranchName = branchName || nextBranchName(activeLesson);
    setBusyAction("branch");
    try {
      const nextPackage = await api.createBranch(activeLesson.id, finalBranchName, fromCommitId);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson.id });
      setNewBranchName("");
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "创建分支失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateBranchFromCommit(commit: CommitRecord) {
    if (!activeLesson) {
      return;
    }
    setPreviewCommitId(commit.id);
    setDraftDocument(commit.snapshot);
    setIsDocumentDirty(false);
    await handleCreateBranch(commit.id, newBranchName.trim() || nextBranchName(activeLesson));
  }

  async function handleSwitchBranch(branchName: string) {
    if (!activeLesson) {
      return;
    }
    setBusyAction("switch-branch");
    try {
      const nextPackage = await api.switchBranch(activeLesson.id, branchName);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson.id });
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "切换分支失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRestoreCommit(commitId: string) {
    if (!activeLesson) {
      return;
    }
    setBusyAction("restore");
    try {
      const nextPackage = await api.restoreCommit(activeLesson.id, commitId);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson.id });
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "恢复版本失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleOpenLesson(lessonId: string) {
    setBusyAction("open-lesson");
    try {
      const nextPackage = await api.openLesson(lessonId);
      updateCoursePackage(nextPackage);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "打开课程失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCloseLesson(lessonId: string) {
    setBusyAction("close-lesson");
    try {
      const nextPackage = await api.closeLesson(lessonId);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson && activeLesson.id !== lessonId ? activeLesson.id : undefined,
      });
    } catch (closeError) {
      setError(closeError instanceof Error ? closeError.message : "关闭课程失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleUploadResource(file: File | null) {
    if (!file) {
      return;
    }
    setBusyAction("upload");
    try {
      const nextPackage = await api.uploadResource(file);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson?.id });
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传资料失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function flushRealtimeLogQueue() {
    if (realtimeLogFlushInFlightRef.current) {
      return;
    }
    realtimeLogFlushInFlightRef.current = true;
    try {
      while (realtimeLogQueueRef.current.length > 0) {
        const nextEvent = realtimeLogQueueRef.current[0];
        await api.logRealtimeEvent(nextEvent.lessonId, nextEvent.payload);
        realtimeLogQueueRef.current.shift();
      }
    } catch {
      // keep queue for retry
    } finally {
      realtimeLogFlushInFlightRef.current = false;
    }
  }

  function scheduleRealtimeLogFlush() {
    void flushRealtimeLogQueue();
  }

  function flushRealtimeLogQueueWithBeacon() {
    if (realtimeLogFlushInFlightRef.current || realtimeLogQueueRef.current.length === 0) {
      return;
    }
    const pending = [...realtimeLogQueueRef.current];
    const failed: QueuedRealtimeLogEvent[] = [];
    pending.forEach((event) => {
      const sent = api.logRealtimeEventBeacon(event.lessonId, event.payload);
      if (!sent) {
        failed.push(event);
      }
    });
    realtimeLogQueueRef.current = failed;
  }

  function enqueueRealtimeLogEvent(
    lessonId: string,
    role: RealtimeEventLogPayload["role"],
    transportEventType: string,
    transcript: string
  ) {
    const normalized = transcript.trim();
    if (!normalized) {
      return;
    }
    realtimeLogQueueRef.current.push({
      lessonId,
      payload: {
        client_session_id: realtimeClientSessionIdRef.current,
        lesson_title: realtimeLessonTitleRef.current,
        role,
        transport_event_type: transportEventType,
        transcript: normalized,
      },
    });
    scheduleRealtimeLogFlush();
  }

  function disposeRealtimeSession() {
    scheduleRealtimeLogFlush();
    realtimeChannelRef.current?.close();
    realtimeChannelRef.current = null;

    if (realtimePeerRef.current) {
      realtimePeerRef.current.ontrack = null;
      realtimePeerRef.current.onconnectionstatechange = null;
      realtimePeerRef.current.close();
      realtimePeerRef.current = null;
    }

    realtimeStreamRef.current?.getTracks().forEach((track) => track.stop());
    realtimeStreamRef.current = null;

    if (remoteAudioRef.current) {
      remoteAudioRef.current.pause();
      remoteAudioRef.current.srcObject = null;
    }

    realtimeLessonIdRef.current = null;
    realtimeClientSessionIdRef.current = null;
    realtimeLessonTitleRef.current = null;
  }

  const scheduleRealtimeLogFlushEffectEvent = useEffectEvent(() => {
    scheduleRealtimeLogFlush();
  });

  const flushRealtimeLogQueueWithBeaconEffectEvent = useEffectEvent(() => {
    flushRealtimeLogQueueWithBeacon();
  });

  const disposeRealtimeSessionEffectEvent = useEffectEvent(() => {
    disposeRealtimeSession();
  });

  function stopRealtimeSession(statusText = "语音讲师已断开") {
    disposeRealtimeSession();
    setVoiceActive(false);
    setVoiceStatusText(statusText);
    setBusyAction((current) => (current === "voice-connect" ? null : current));
  }

  const stopRealtimeSessionEvent = useEffectEvent((statusText: string) => {
    stopRealtimeSession(statusText);
  });

  function appendRealtimeMessage(lessonId: string, role: ChatMessage["role"], content: string) {
    const normalized = content.trim();
    if (!normalized) {
      return;
    }
    updateLessonMessages(lessonId, (current) => {
      const previous = current[current.length - 1];
      if (previous && previous.role === role && previous.content.trim() === normalized) {
        return current;
      }
      return [...current, createChatMessage(role, normalized)];
    });
  }

  useEffect(() => {
    return () => {
      flushRealtimeLogQueueWithBeaconEffectEvent();
      disposeRealtimeSessionEffectEvent();
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      scheduleRealtimeLogFlushEffectEvent();
    }, 2000);

    function handlePageHide() {
      flushRealtimeLogQueueWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("pagehide", handlePageHide);
    };
  }, []);

  useEffect(() => {
    if (!realtimeLessonIdRef.current || realtimeLessonIdRef.current === activeLesson?.id) {
      return;
    }
    stopRealtimeSessionEvent("已切换课程，语音会话已自动断开");
  }, [activeLesson?.id]);

  async function handleVoiceToggle() {
    if (typeof window === "undefined") {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("语音讲师已手动断开");
      return;
    }
    if (!activeLesson) {
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("当前浏览器不支持麦克风实时语音会话。");
      return;
    }

    setBusyAction("voice-connect");
    setVoiceStatusText("正在连接 GPT-4o Realtime…");
    setError(null);

    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      realtimeStreamRef.current = mediaStream;

      const peerConnection = new RTCPeerConnection();
      const clientSessionId = createClientSessionId("realtime");
      realtimePeerRef.current = peerConnection;
      realtimeLessonIdRef.current = activeLesson.id;
      realtimeClientSessionIdRef.current = clientSessionId;
      realtimeLessonTitleRef.current = activeLesson.title;

      mediaStream.getTracks().forEach((track) => {
        peerConnection.addTrack(track, mediaStream);
      });

      peerConnection.ontrack = (event) => {
        const [remoteStream] = event.streams;
        if (remoteAudioRef.current && remoteStream) {
          remoteAudioRef.current.srcObject = remoteStream;
          void remoteAudioRef.current.play().catch(() => undefined);
        }
      };

      peerConnection.onconnectionstatechange = () => {
        if (peerConnection.connectionState === "connected") {
          setVoiceActive(true);
          setVoiceStatusText("GPT-4o Realtime 已连接，直接说话即可");
          setBusyAction((current) => (current === "voice-connect" ? null : current));
          return;
        }
        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "closed" ||
          peerConnection.connectionState === "disconnected"
        ) {
          stopRealtimeSession("语音会话已结束");
        }
      };

      const dataChannel = peerConnection.createDataChannel("oai-events");
      realtimeChannelRef.current = dataChannel;
      dataChannel.onmessage = (messageEvent) => {
        try {
          const payload = JSON.parse(messageEvent.data) as {
            type?: string;
            transcript?: string;
          };
          const lessonId = realtimeLessonIdRef.current;
          if (!lessonId || !payload.type || !payload.transcript) {
            return;
          }
          if (payload.type === "conversation.item.input_audio_transcription.completed") {
            appendRealtimeMessage(lessonId, "user", payload.transcript);
            enqueueRealtimeLogEvent(lessonId, "user", payload.type, payload.transcript);
          }
          if (payload.type === "response.audio_transcript.done") {
            appendRealtimeMessage(lessonId, "assistant", payload.transcript);
            enqueueRealtimeLogEvent(lessonId, "assistant", payload.type, payload.transcript);
          }
        } catch {
          // ignore
        }
      };

      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      const realtimeResponse = await api.connectRealtime(activeLesson.id, {
        offer_sdp: offer.sdp ?? "",
        latest_assistant_message: latestAssistantMessage?.content ?? null,
        client_session_id: clientSessionId,
      });

      await peerConnection.setRemoteDescription({
        type: "answer",
        sdp: realtimeResponse.answer_sdp,
      });

      setVoiceStatusText(`GPT-4o Realtime 已就绪，语音音色：${realtimeResponse.voice}`);
    } catch (voiceError) {
      stopRealtimeSession("语音连接失败");
      setError(voiceError instanceof Error ? voiceError.message : "连接实时语音失败");
    }
  }

  function handleSelectLesson(lessonId: string) {
    resetTransientUi();
    setCoursePackage((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current, active_lesson_id: lessonId };
      const selectedLesson = next.lessons.find((lesson) => lesson.id === lessonId) ?? null;
      setDraftDocument(selectedLesson?.board_document ?? null);
      setIsDocumentDirty(false);
      return next;
    });
  }

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">正在载入课程工作台…</div>;
  }

  if (!coursePackage || !activeLesson || !displayedDocument) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">没有找到可用课程。</div>;
  }

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-[#f8f6f0] text-[#1a1a1a]">
      <div
        className={clsx(
          "relative z-[60] flex shrink-0 flex-col bg-white transition-all duration-300",
          topCollapsed && "-translate-y-full -mb-12"
        )}
      >
        <header className="flex h-12 items-center justify-between border-b border-gray-200 px-4">
          <div className="flex min-w-0 items-center gap-6">
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={handleReturnHome}
                className="flex h-6 w-6 items-center justify-center rounded bg-black text-white shadow-sm transition hover:bg-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/20"
                title="返回主页"
                aria-label="返回主页"
              >
                <Cpu className="h-3.5 w-3.5" />
              </button>
              <span className="text-[13px] font-semibold tracking-tight">{coursePackage.title}</span>
            </div>

            <nav className="flex min-w-0 items-center overflow-x-auto custom-scrollbar">
              {openLessons.map((lesson) => (
                <button
                  key={lesson.id}
                  type="button"
                  onClick={() => handleSelectLesson(lesson.id)}
                  className={clsx(
                    "group flex h-12 items-center gap-2 border-r border-gray-100 px-4 text-left text-[10px] font-bold uppercase tracking-[0.2em] transition-colors",
                    lesson.id === activeLesson.id
                      ? "border-b-2 border-black bg-white text-black"
                      : "bg-white text-gray-400 hover:bg-gray-50 hover:text-black"
                  )}
                >
                  <span className="max-w-[160px] truncate">{lesson.title}</span>
                  <span className="max-w-[52px] truncate text-[9px] font-medium tracking-[0.16em] text-gray-300">
                    {lesson.history_graph.current_branch}
                  </span>
                  <span
                    className="rounded-md p-1 text-gray-300 opacity-0 transition hover:bg-gray-100 hover:text-black group-hover:opacity-100"
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleCloseLesson(lesson.id);
                    }}
                  >
                    <X className="h-3 w-3" />
                  </span>
                </button>
              ))}
              <button
                type="button"
                onClick={() => void handleCreateLessonFromPrompt()}
                className="p-3 text-gray-300 transition-colors hover:text-black"
                title="新建单课"
              >
                <Plus className="h-4 w-4" />
              </button>
            </nav>
          </div>

          <div className="flex shrink-0 items-center gap-4">
            <div className="flex items-center gap-2 rounded-md border border-green-100/50 bg-green-50/60 px-2 py-1">
              <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
              <span className="text-[10px] font-bold uppercase tracking-widest text-green-700">
                Teacher AI Online
              </span>
            </div>
            <div className="ml-2 flex items-center gap-1 border-l border-gray-200 pl-4">
              <button
                type="button"
                onClick={() => setRightSidebarOpen((current) => !current)}
                className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100"
                title={rightSidebarOpen ? "收起右侧栏" : "展开右侧栏"}
              >
                <PanelRight className="h-4.5 w-4.5" />
              </button>
              <button
                type="button"
                onClick={() => setTopCollapsed(true)}
                className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100"
                title="收起顶部与编辑工具栏"
              >
                <ChevronUp className="h-4.5 w-4.5" />
              </button>
            </div>
            <Image
              src="https://api.dicebear.com/7.x/avataaars/svg?seed=Codex"
              alt="User Avatar"
              className="h-7 w-7 rounded-full border border-gray-200"
              width={28}
              height={28}
              unoptimized
            />
          </div>
        </header>
      </div>

      <button
        type="button"
        onClick={() => setTopCollapsed(false)}
        className={clsx(
          "fixed left-1/2 top-0 z-[70] flex h-4 w-16 -translate-x-1/2 items-center justify-center rounded-b-lg border border-t-0 border-gray-200 bg-white shadow-sm transition-all hover:h-5 hover:bg-gray-50",
          topCollapsed ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        )}
        title="展开顶部与编辑工具栏"
      >
        <ChevronDown className="h-3 w-3 text-gray-400" />
      </button>

      {error ? (
        <div className="mx-4 mt-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 md:mx-6">
          {error}
        </div>
      ) : null}

      {selection && selectionPopover ? (
        <div
          className="fixed z-[90] flex -translate-x-1/2 items-center overflow-hidden rounded-xl border border-gray-200 bg-white text-[13px] font-medium text-gray-800 shadow-lg"
          style={{ left: selectionPopover.left, top: selectionPopover.top }}
          onMouseDown={(event) => event.preventDefault()}
        >
          <button
            type="button"
            onClick={() => focusComposerWithSelection("ask")}
            className="inline-flex h-10 items-center gap-2 px-3.5 transition-colors hover:bg-gray-50"
          >
            <TextQuote className="h-4 w-4" />
            询问 PM AI
          </button>
          {selection.kind === "board" && !isPreviewMode ? (
            <>
              <div className="h-5 w-px bg-gray-200" />
              <button
                type="button"
                onClick={() => focusComposerWithSelection("direct_edit")}
                className="inline-flex h-10 items-center gap-2 px-3.5 transition-colors hover:bg-amber-50 hover:text-amber-700"
              >
                <PencilLine className="h-4 w-4" />
                编辑板书
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <div
        ref={mainContainerRef}
        className={clsx(
          "grid min-h-0 flex-1 grid-cols-[380px_minmax(0,1fr)] overflow-hidden transition-[grid-template-columns] duration-300",
          rightSidebarOpen && "xl:grid-cols-[380px_minmax(0,1fr)_360px]"
        )}
      >
        <aside className="flex h-full min-h-0 flex-col border-r border-gray-200 bg-white">
          <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
            <div className="space-y-6">
              <div className="rounded-xl border border-blue-100/50 bg-[#f4f6ff] p-4">
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Target className="h-3.5 w-3.5 text-blue-600" />
                    <h3 className="text-[11px] font-bold uppercase tracking-widest text-blue-900">
                      学习目的澄清
                    </h3>
                  </div>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-bold text-blue-800 shadow-sm">
                    {clarityStatus.progress}%
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white shadow-inner">
                  <div
                    className={clsx("h-full rounded-full transition-all duration-500", clarityBarTone)}
                    style={{ width: `${clarityStatus.progress}%` }}
                  />
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-blue-700">
                    {clarityStatus.label}
                  </span>
                  {clarityStatus.forced_start ? (
                    <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-amber-700">
                      已按当前信息开始
                    </span>
                  ) : null}
                </div>
                {clarityStatus.reason ? (
                  <p className="mt-2 text-xs leading-6 text-blue-900">{clarityStatus.reason}</p>
                ) : null}
                {clarityStatus.missing_items.length ? (
                  <p className="mt-1 text-[11px] leading-5 text-blue-700/75">
                    可补充：{clarityStatus.missing_items.join("、")}
                  </p>
                ) : null}
                <ul className="mt-3 space-y-2">
                  {learningGoalItems.map((item, index) => (
                    <li key={item} className="flex items-start gap-2.5 text-xs leading-relaxed text-blue-800">
                      {index === 0 ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                      ) : (
                        <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-300" />
                      )}
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="space-y-6">
                {activeMessages.map((message) => (
                  <div
                    key={message.id}
                    onMouseUp={() => {
                      const excerpt = window.getSelection()?.toString().trim();
                      if (excerpt) {
                        applySelection(
                          {
                            kind: "chat",
                            lesson_id: activeLesson.id,
                            excerpt,
                          },
                          popoverPositionFromDomSelection()
                        );
                      }
                    }}
                  >
                    <ChatBubble message={message} />
                  </div>
                ))}
              </div>

              {scopeOptions.length ? (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-amber-700">范围升级建议</p>
                  <div className="mt-3 space-y-2">
                    {scopeOptions.map((option) => (
                      <button
                        key={option.action}
                        type="button"
                        onClick={() => void handleScopeAction(option)}
                        className="w-full rounded-xl border border-amber-200 bg-white px-4 py-3 text-left transition hover:border-amber-300"
                      >
                        <span className="block text-sm font-semibold text-gray-900">{option.label}</span>
                        <span className="mt-1 block text-xs leading-6 text-gray-500">{option.description}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {referencePrompt ? (
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-violet-700">章节参考建议</p>
                  <p className="mt-2 text-sm leading-6 text-violet-950">{referencePrompt.question}</p>
                  <p className="mt-2 text-xs leading-6 text-violet-900/80">{referencePrompt.reason}</p>
                  <div className="mt-3 grid gap-2">
                    <button
                      type="button"
                      onClick={() => void handleReferenceAction("confirm")}
                      className="w-full rounded-xl border border-violet-200 bg-white px-4 py-3 text-left transition hover:border-violet-300"
                    >
                      <span className="block text-sm font-semibold text-gray-900">
                        {referencePrompt.confirm_label}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleReferenceAction("skip")}
                      className="w-full rounded-xl border border-violet-200 bg-white px-4 py-3 text-left transition hover:border-violet-300"
                    >
                      <span className="block text-sm font-semibold text-gray-900">
                        {referencePrompt.skip_label}
                      </span>
                    </button>
                  </div>
                </div>
              ) : null}

              {clarificationQuestions.length ? (
                <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">需求澄清</p>
                  <p className="mt-2 text-xs leading-6 text-sky-900">
                    {latestBoardDecision?.reason ?? "AI 还需要再确认一点学习目标，才能决定后面的讲义策略。"}
                  </p>
                  <div className="mt-3 space-y-2">
                    {clarificationQuestions.map((question, index) => (
                      <div key={`${question}-${index}`} className="rounded-lg bg-white px-3 py-2 text-xs leading-6 text-gray-700">
                        {index + 1}. {question}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {selectedReference ? (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">已引用参考资料</p>
                  <p className="mt-2 text-sm font-semibold text-gray-900">
                    {selectedReference.resource_name} / {selectedReference.chapter_title}
                  </p>
                  <p className="mt-2 text-xs leading-6 text-gray-600">{selectedReference.summary}</p>
                  <div className="mt-3 space-y-2">
                    {selectedReference.teaching_points.slice(0, 3).map((point, index) => (
                      <div
                        key={`${point}-${index}`}
                        className="rounded-lg bg-white px-3 py-2 text-xs leading-6 text-gray-700"
                      >
                        {point}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="shrink-0 border-t border-gray-100 bg-white p-5">
            <div className="group relative mb-4 flex justify-center">
              <button
                type="button"
                onClick={() => void handleVoiceToggle()}
                className={clsx(
                  "relative flex h-12 w-12 items-center justify-center rounded-full text-white shadow-md transition-all hover:scale-105 hover:shadow-lg",
                  voiceActive ? "bg-gray-800 ring-4 ring-gray-200" : "bg-[#1a1a1a]"
                )}
              >
                {voiceActive ? <Radio className="h-5 w-5" /> : <Volume2 className="h-5 w-5" />}
              </button>
            </div>
            <p className="mb-4 text-center text-[11px] leading-5 text-gray-500">{voiceStatusText}</p>
            <audio ref={remoteAudioRef} autoPlay className="hidden" />

            <div
              className={clsx(
                "overflow-hidden rounded-[20px] border bg-white shadow-sm transition-colors focus-within:ring-1",
                composerMode === "direct_edit"
                  ? "border-amber-200 focus-within:border-amber-500 focus-within:ring-amber-500"
                  : "border-gray-200 focus-within:border-black focus-within:ring-black"
              )}
            >
              {composerSelection ? (
                <div className="mx-3 mt-3 flex items-center justify-between gap-3 rounded-xl bg-gray-50 px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    {composerMode === "direct_edit" ? (
                      <PencilLine className="h-4 w-4 shrink-0 text-amber-600" />
                    ) : (
                      <TextQuote className="h-4 w-4 shrink-0 text-gray-500" />
                    )}
                    <p className="min-w-0 truncate text-[13px] leading-5 text-gray-700">
                      “{composerSelection.excerpt.replace(/\s+/g, " ").slice(0, 160)}”
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-white hover:text-black"
                    title="移除引用"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : null}

              <textarea
                ref={chatInputRef}
                value={chatInput}
                disabled={isChatBusy}
                rows={1}
                onChange={(event) =>
                  updateActiveLessonComposerState((current) => ({
                    ...current,
                    chatInput: event.target.value,
                  }))
                }
                onInput={adjustComposerHeight}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void handleSubmitChat();
                  }
                }}
                placeholder={
                  isChatBusy
                    ? "正在处理上一条请求..."
                    : composerMode === "direct_edit"
                    ? "描述要怎么改这段板书，或直接说“重写整篇”..."
                    : composerSelection
                      ? "基于选中内容继续追问"
                      : "提问或下达修改指令..."
                }
                className="custom-scrollbar block w-full resize-none border-0 bg-transparent px-4 py-3 text-[13px] leading-relaxed outline-none placeholder:text-gray-400 disabled:cursor-wait disabled:text-gray-400"
              />
              <div className="flex items-center justify-between gap-3 px-3 pb-3">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <div className="flex shrink-0 items-center gap-1 rounded-md border border-gray-200 bg-gray-50 p-0.5">
                    <button
                      type="button"
                      onClick={() =>
                        updateActiveLessonComposerState((current) => ({
                          ...current,
                          composerMode: "ask",
                        }))
                      }
                      className={clsx(
                        "flex h-7 w-7 items-center justify-center rounded text-gray-500 transition-colors hover:bg-white hover:text-black",
                        composerMode === "ask" && "bg-white text-black shadow-sm"
                      )}
                      title="Ask Mode"
                    >
                      <MessageSquare className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        updateActiveLessonComposerState((current) => ({
                          ...current,
                          composerMode: "direct_edit",
                          includeSelectionInPrompt: true,
                        }));
                      }}
                      className={clsx(
                        "flex h-7 w-7 items-center justify-center rounded text-gray-500 transition-colors hover:bg-white hover:text-black",
                        composerMode === "direct_edit" && "bg-white text-amber-700 shadow-sm"
                      )}
                      title="Agent Edit Mode"
                    >
                      <BrainCircuit className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {composerSelection ? (
                    <button
                      type="button"
                      onClick={() =>
                        updateActiveLessonComposerState((current) => ({
                          ...current,
                          includeSelectionInPrompt: !current.includeSelectionInPrompt,
                        }))
                      }
                      className={clsx(
                        "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition-colors",
                        includeSelectionInPrompt
                          ? "border-gray-200 bg-gray-50 text-gray-600"
                          : "border-gray-200 bg-white text-gray-400"
                      )}
                    >
                      <TextQuote className="h-3.5 w-3.5" />
                      {includeSelectionInPrompt ? "包含选区" : "忽略选区"}
                    </button>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => void handleSubmitChat()}
                  disabled={isChatBusy || !chatInput.trim()}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#1a1a1a] text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isChatBusy ? (
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4 -translate-x-[1px]" />
                  )}
                </button>
              </div>
            </div>
          </div>
        </aside>

        <section className="relative z-10 flex min-w-0 flex-col overflow-hidden bg-white shadow-[0_0_20px_rgba(0,0,0,0.02)]">
          {isPreviewMode ? (
            <div className="shrink-0 border-b border-violet-200 bg-violet-50 px-5 py-3 text-sm text-violet-700">
              正在预览历史快照：{previewCommit?.label}
              <button
                type="button"
                className="ml-3 rounded-md border border-violet-200 bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-violet-700"
                onClick={() => {
                  setPreviewCommitId(null);
                  setDraftDocument(activeLesson.board_document);
                  setIsDocumentDirty(false);
                }}
              >
                回到当前版本
              </button>
            </div>
          ) : null}

          <WordBoardEditor
            document={displayedDocument}
            readOnly={isPreviewMode}
            isDirty={isDocumentDirty}
            busyAction={busyAction}
            toolbarCollapsed={topCollapsed}
            onDocumentChange={handleLocalDocumentChange}
            onSelectionChange={(payload) => {
              if (!payload || !activeLesson) {
                setSelectionPopover(null);
                if (selection?.kind === "board") {
                  clearSelection();
                }
                return;
              }
              applySelection(
                {
                  kind: "board",
                  lesson_id: activeLesson.id,
                  excerpt: payload.excerpt,
                },
                payload.position
              );
            }}
            onSave={() => void handleSaveDocument()}
            onImportDocx={(file) => void handleImportDocx(file)}
            onExportDocx={() => void handleExportDocx()}
          />
        </section>

        <aside
          className={clsx(
            "h-full min-h-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
            rightSidebarOpen ? "hidden xl:flex" : "hidden"
          )}
        >
          <div className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-5">
            <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">课程工作台辅助</h4>
            <button
              type="button"
              onClick={() => setRightSidebarOpen(false)}
              className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="flex border-b border-gray-200 bg-white">
            {[
              { value: "history", label: "History" },
              { value: "branch", label: "Branch" },
              { value: "library", label: "Library" },
            ].map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => setSidebarTab(tab.value as SidebarTab)}
                className={clsx(
                  "flex-1 py-3 text-[10px] font-bold uppercase tracking-wider transition-colors",
                  sidebarTab === tab.value
                    ? "border-b-2 border-black text-black"
                    : "text-gray-400 hover:text-black"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-5 custom-scrollbar">
            {sidebarTab === "history" ? (
              <div className="space-y-8">
                <div className="space-y-4">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
                  {[...activeLesson.history_graph.commits].reverse().map((commit, index) => (
                    <CommitTimelineItem
                      key={commit.id}
                      commit={commit}
                      active={commit.id === previewCommitId}
                      latest={index === 0}
                      onPreview={() => {
                        setPreviewCommitId(commit.id);
                        setDraftDocument(commit.snapshot);
                        setIsDocumentDirty(false);
                      }}
                      onRestore={() => void handleRestoreCommit(commit.id)}
                      onBranch={() => void handleCreateBranchFromCommit(commit)}
                    />
                  ))}
                </div>
              </div>
            ) : null}

            {sidebarTab === "branch" ? (
              <div className="space-y-8">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">分支管理</p>
                  <div className="mt-4 flex gap-2">
                    <input
                      value={newBranchName}
                      onChange={(event) => setNewBranchName(event.target.value)}
                      placeholder="新分支名"
                      className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm outline-none focus:border-black"
                    />
                    <button
                      type="button"
                      onClick={() => void handleCreateBranch()}
                      className="rounded-xl bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
                    >
                      <GitBranch className="mr-1.5 inline h-3.5 w-3.5" />
                      开分支
                    </button>
                  </div>
                  <p className="mt-2 text-[11px] leading-5 text-gray-400">
                    {previewCommit
                      ? `当前会从历史节点「${previewCommit.label}」开启分支；未填写名称时会自动生成。`
                      : "先在 History 中 Preview 某个节点，或直接从当前最新节点开启分支。未填写名称时会自动生成。"}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {Object.values(activeLesson.history_graph.branches).map((branch) => (
                      <button
                        key={branch.name}
                        type="button"
                        onClick={() => void handleSwitchBranch(branch.name)}
                        className={clsx(
                          "rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                          activeLesson.history_graph.current_branch === branch.name
                            ? "border-black bg-black text-white"
                            : "border-gray-200 bg-white text-gray-500 hover:text-black"
                        )}
                      >
                        {branch.name}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="border-t border-gray-200 pt-6">
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="h-4 w-4 text-gray-400" />
                    <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">需求清单</p>
                  </div>
                  <p className="mt-4 text-sm leading-7 text-gray-700">
                    {activeRequirements?.learning_goal ?? "围绕当前板书主线推进学习。"}
                  </p>
                  <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                    <p className="text-xs font-semibold text-gray-900">
                      {activeRequirements?.target_depth ?? "先建立概念，再进入例题与练习"}
                    </p>
                    <p className="mt-2 text-[11px] leading-6 text-gray-500">
                      {activeRequirements?.success_criteria ?? "先讲清当前问题，再决定是否要扩展讲义。"}
                    </p>
                  </div>
                  {latestBoardDecision ? (
                    <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
                      <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
                      <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
                      <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {sidebarTab === "library" ? (
              <div className="space-y-8">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">关联资料库</p>
                  <label className="mt-4 block rounded-xl border border-dashed border-gray-300 bg-white px-4 py-5 text-center text-sm text-gray-500 hover:border-gray-400">
                    上传文件或图片
                    <input
                      type="file"
                      className="hidden"
                      onChange={(event) => void handleUploadResource(event.target.files?.[0] ?? null)}
                    />
                  </label>
                  <div className="mt-4 space-y-3">
                    {coursePackage.resources.length ? (
                      coursePackage.resources.map((resource) => (
                        <div
                          key={resource.id}
                          className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-gray-300"
                        >
                          <div className="flex items-start gap-3">
                            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                              <FileText className="h-4 w-4" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-xs font-bold text-gray-900">{resource.name}</p>
                              <p className="mt-1 text-[11px] text-gray-500">
                                {resource.extracted_text_available
                                  ? `已索引 ${resource.outline.length} 个章节入口`
                                  : "当前仅做入口索引"}
                              </p>
                            </div>
                          </div>
                          <div className="mt-3 space-y-2">
                            {resource.outline.slice(0, 3).map((chapter) => (
                              <div key={chapter.id} className="rounded-lg bg-gray-50 px-3 py-2 text-[11px] text-gray-600">
                                <p className="font-semibold text-gray-800">{chapter.title}</p>
                                <p className="mt-1 leading-6">{chapter.summary}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">
                        还没有上传资料。可以先把教材、讲义或图片放进当前课程资料库。
                      </div>
                    )}
                  </div>
                </div>

                <div className="border-t border-gray-200 pt-6">
                  <div className="mb-4 flex items-center gap-2">
                    <BookOpen className="h-4 w-4 text-gray-400" />
                    <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">课程图谱</p>
                  </div>
                  <div className="space-y-3">
                    {relatedEdges.length ? (
                      relatedEdges.map((edge) => {
                        const source = lessonMap.get(edge.source_lesson_id);
                        const target = lessonMap.get(edge.target_lesson_id);
                        if (!source || !target) {
                          return null;
                        }
                        const nextLesson = edge.source_lesson_id === activeLesson.id ? target : source;
                        return (
                          <button
                            key={edge.id}
                            type="button"
                            onClick={() => void handleOpenLesson(nextLesson.id)}
                            className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:border-gray-300"
                          >
                            <p className="text-xs font-bold text-gray-900">
                              {source.title} → {target.title}
                            </p>
                            <p className="mt-1 text-[11px] text-gray-500">关系：{edge.relationship}</p>
                          </button>
                        );
                      })
                    ) : (
                      <div className="rounded-xl border border-gray-200 bg-white px-4 py-6 text-sm text-gray-500">
                        当前 lesson 还没有更多图谱关系。
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </aside>
      </div>
    </main>
  );
}
