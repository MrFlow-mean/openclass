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
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  AlignCenter,
  AlignHorizontalSpaceAround,
  AlignLeft,
  AlignRight,
  ArrowLeft,
  ArrowRight,
  Bold,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  Columns3,
  Download,
  FilePlus,
  FileText,
  Files,
  Frame,
  GitBranch,
  Highlighter,
  Hash,
  Italic,
  List,
  ListOrdered,
  LoaderCircle,
  Maximize2,
  MessageSquare,
  Minus,
  PanelRight,
  PanelTop,
  PaintBucket,
  PencilLine,
  Plus,
  Quote,
  Radio,
  Redo2,
  RectangleHorizontal,
  RectangleVertical,
  Rows3,
  Send,
  Sparkles,
  Stamp,
  Table2,
  TableCellsMerge,
  TableCellsSplit,
  Target,
  TextQuote,
  TextCursorInput,
  Trash2,
  Type,
  Underline,
  Undo2,
  Upload,
  Volume2,
  X,
  ClipboardList,
  Columns2,
  ImagePlus,
  LayoutTemplate,
  Link as LinkIcon,
} from "lucide-react";

import { api, getApiWebSocketUrl } from "@/lib/api";
import { BranchSequenceSelector, type BranchSequenceOption } from "@/components/branch-sequence-selector";
import { InlineNameForm } from "@/components/inline-name-form";
import { ResourceUploadDropzone } from "@/components/resource-upload-dropzone";
import { MATH_TEXT_SERIALIZERS, normalizeEditorMath } from "@/lib/math-content";
import { useRealtimeLogQueue } from "@/hooks/use-realtime-log-queue";
import { pcmFloatToBase64, playPcmBase64, resampleLinear } from "@/lib/realtime-audio";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardEditPrompt,
  BoardDecision,
  BoardDocument,
  ChatInteractionMode,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  DocumentPageSettings,
  LearningClarificationStatus,
  Lesson,
  ResourceMatch,
  ResourceReferenceContext,
  ResourceReferencePrompt,
  ScopeOption,
  SelectionRef,
  SectionTeachingProgress,
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
  selection?: SelectionRef | null;
  teachingProgress?: SectionTeachingProgress | null;
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
type GoogleRealtimeAudioMessage = {
  setupComplete?: Record<string, unknown>;
  error?: {
    code?: number;
    message?: string;
    status?: string;
  };
  serverContent?: {
    modelTurn?: {
      parts?: Array<{
        inlineData?: {
          mimeType?: string;
          data?: string;
        };
        text?: string;
      }>;
    };
    inputTranscription?: {
      text?: string;
    };
    outputTranscription?: {
      text?: string;
    };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
};
type AutoSaveStatus = "idle" | "pending" | "saving" | "saved" | "error";
type AutoSaveReason =
  | "debounce"
  | "queued"
  | "manual"
  | "return-home"
  | "select-lesson"
  | "open-lesson"
  | "close-lesson"
  | "create-lesson"
  | "chat"
  | "branch"
  | "preview"
  | "switch-branch"
  | "restore"
  | "import"
  | "export"
  | "upload-resource"
  | "delete-resource"
  | "voice"
  | "pagehide";

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

const DEFAULT_LESSON_COMPOSER_STATE: LessonComposerState = {
  chatInput: "",
  composerMode: "ask",
  includeSelectionInPrompt: true,
};
const AUTO_SAVE_DELAY_MS = 1600;
const HIDDEN_LEARNING_PURPOSE_ITEMS = new Set([
  "理解概念、能跟着连续讲义讲清楚并完成基础练习",
  "用户能复述核心概念并完成一题相关练习",
]);

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

const FALLBACK_MODEL_CATALOG: AIModelCatalog = {
  text: [
    {
      provider: "openai",
      model: "gpt-5.5",
      label: "GPT-5.5",
      capability: "text",
      enabled: true,
      configured: true,
      default: false,
    },
    {
      provider: "openai",
      model: "gpt-5.4",
      label: "GPT-5.4",
      capability: "text",
      enabled: true,
      configured: true,
      default: false,
    },
    {
      provider: "openai",
      model: "gpt-5.4-mini",
      label: "GPT-5.4 Mini",
      capability: "text",
      enabled: true,
      configured: true,
      default: false,
    },
    {
      provider: "openai",
      model: "gemini-3.1-pro-preview",
      label: "Gemini 3.1 Pro Preview",
      capability: "text",
      enabled: true,
      configured: true,
      default: false,
    },
    {
      provider: "openai",
      model: "gemini-3-flash-preview",
      label: "Gemini 3 Flash Preview",
      capability: "text",
      enabled: true,
      configured: true,
      default: true,
    },
  ],
  realtime: [
    {
      provider: "google",
      model: "gemini-3.1-flash-live-preview",
      label: "Google Gemini 3.1 Flash Live",
      capability: "realtime",
      enabled: true,
      configured: true,
      default: true,
      transport: "gemini_live_websocket",
    },
  ],
  defaults: {
    text: { provider: "openai", model: "gemini-3-flash-preview" },
    realtime: { provider: "google", model: "gemini-3.1-flash-live-preview" },
  },
};

const PROVIDER_LABELS: Record<AIModelSelection["provider"], string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  deepseek: "DeepSeek",
  kimi: "Kimi",
  minimax: "MiniMax",
  openai_compatible: "OpenAI 兼容",
  anthropic_compatible: "Anthropic 兼容",
};
const TEXT_MODEL_STORAGE_KEY = "blackboard-ai:selected-text-model";
const REALTIME_MODEL_STORAGE_KEY = "blackboard-ai:selected-realtime-model";
const DISABLED_TEXT_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();
const DISABLED_REALTIME_MODEL_PROVIDERS = new Set<AIModelSelection["provider"]>();

function modelSelectionKey(selection: AIModelSelection): string {
  return `${selection.provider}:${selection.model}`;
}

function modelOptionKey(option: AIModelOption): string {
  return `${option.provider}:${option.model}`;
}

function findModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  if (!selection) {
    return null;
  }
  return options.find((option) => modelOptionKey(option) === modelSelectionKey(selection)) ?? null;
}

function findEnabledModelOption(options: AIModelOption[], selection: AIModelSelection | null): AIModelOption | null {
  const option = findModelOption(options, selection);
  return option?.enabled ? option : null;
}

function normalizeCourseStudioModelCatalog(catalog: AIModelCatalog): AIModelCatalog {
  return {
    ...catalog,
    text: catalog.text.map((option) =>
      DISABLED_TEXT_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
    realtime: catalog.realtime.map((option) =>
      DISABLED_REALTIME_MODEL_PROVIDERS.has(option.provider)
        ? { ...option, enabled: false, configured: false, default: false }
        : option
    ),
  };
}

function modelButtonLabel(option: AIModelOption | null, fallback: AIModelSelection | null): string {
  if (option) {
    return option.label;
  }
  if (!fallback) {
    return "未选择";
  }
  return `${PROVIDER_LABELS[fallback.provider]} ${fallback.model}`;
}

function optionToSelection(option: AIModelOption): AIModelSelection {
  return {
    provider: option.provider,
    model: option.model,
  };
}

function isModelSelection(value: unknown): value is AIModelSelection {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<AIModelSelection>;
  return (
    typeof candidate.provider === "string" &&
    candidate.provider in PROVIDER_LABELS &&
    typeof candidate.model === "string" &&
    candidate.model.trim().length > 0
  );
}

function readStoredModelSelection(key: string): AIModelSelection | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as unknown;
    return isModelSelection(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

async function websocketMessageText(data: MessageEvent["data"]): Promise<string> {
  if (typeof data === "string") {
    return data;
  }
  if (data instanceof Blob) {
    return data.text();
  }
  if (data instanceof ArrayBuffer) {
    return new TextDecoder().decode(data);
  }
  if (ArrayBuffer.isView(data)) {
    return new TextDecoder().decode(data);
  }
  return String(data);
}

function googleRealtimeErrorMessage(error: GoogleRealtimeAudioMessage["error"]): string {
  const rawMessage = error?.message?.trim() ?? "";
  const status = error?.status?.trim() ?? "";
  const lowerMessage = rawMessage.toLowerCase();
  const lowerStatus = status.toLowerCase();

  if (error?.code === 401 || lowerStatus.includes("unauthenticated")) {
    return "Google Gemini Live 认证失败。请检查统一模型 API Key 是否正确。";
  }
  if (error?.code === 403 || lowerStatus.includes("permission") || lowerMessage.includes("permission denied")) {
    return "Google Gemini Live 权限被拒绝。请检查 Google API Key 是否启用了 Gemini API，并确认该 key 可使用 Live API。";
  }
  if (error?.code === 429 || lowerStatus.includes("quota") || lowerMessage.includes("quota")) {
    return "Google Gemini Live 配额不足或请求过于频繁，请稍后重试或检查 Google API 配额。";
  }
  if (rawMessage) {
    return `Google Gemini Live 连接失败：${rawMessage}`;
  }
  return "Google Gemini Live 连接失败。";
}

function realtimeConnectionErrorMessage(error: unknown, selection: AIModelSelection): string {
  const errorName = typeof error === "object" && error && "name" in error ? String(error.name) : "";
  const rawMessage = error instanceof Error ? error.message.trim() : "";
  const lowerMessage = rawMessage.toLowerCase();

  if (
    errorName === "NotAllowedError" ||
    errorName === "SecurityError" ||
    lowerMessage === "permission denied" ||
    lowerMessage.includes("permission dismissed")
  ) {
    return "麦克风权限被拒绝。请在浏览器地址栏允许本网站使用麦克风；如果通过本地启动页打开，请重新打开启动页或点“直接打开前端”；如果不是 localhost，请通过 HTTPS 打开页面。";
  }
  if (errorName === "NotFoundError" || lowerMessage.includes("requested device not found")) {
    return "没有找到可用麦克风。请连接或启用麦克风后重试。";
  }
  if (errorName === "NotReadableError" || lowerMessage.includes("could not start audio source")) {
    return "麦克风暂时不可用，可能正被其他应用占用。请关闭占用麦克风的应用后重试。";
  }
  if (rawMessage) {
    return rawMessage;
  }
  return `连接 ${PROVIDER_LABELS[selection.provider]} 实时语音失败`;
}

function persistModelSelection(key: string, selection: AIModelSelection) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(selection));
}

function resolveModelSelection(
  options: AIModelOption[],
  preferred: AIModelSelection | null,
  fallback: AIModelSelection
): AIModelSelection {
  if (preferred && findEnabledModelOption(options, preferred)) {
    return preferred;
  }
  if (findEnabledModelOption(options, fallback)) {
    return fallback;
  }
  const defaultOption =
    options.find((option) => option.default && option.enabled) ?? options.find((option) => option.enabled) ?? options[0];
  return defaultOption ? optionToSelection(defaultOption) : fallback;
}

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

const PAGE_ZOOM_MIN = 50;
const PAGE_ZOOM_MAX = 200;
const PAGE_ZOOM_DEFAULT = 100;
const PAGE_ZOOM_STEP = 10;
const PAGE_ZOOM_SLIDER_STEP = 5;
const PAGE_ZOOM_WHEEL_SENSITIVITY = 0.18;

function normalizePageZoom(value: number) {
  if (!Number.isFinite(value)) {
    return PAGE_ZOOM_DEFAULT;
  }
  return Math.min(PAGE_ZOOM_MAX, Math.max(PAGE_ZOOM_MIN, Math.round(value)));
}

function normalizePageSettings(settings?: Partial<DocumentPageSettings> | null): DocumentPageSettings {
  return {
    ...DEFAULT_PAGE_SETTINGS,
    ...(settings ?? {}),
  };
}

function pagePreviewMetrics(settings: DocumentPageSettings) {
  const baseSize = PAGE_SIZE_OPTIONS.find((option) => option.value === settings.page_size) ?? PAGE_SIZE_OPTIONS[0];
  const margin = PAGE_MARGIN_OPTIONS.find((option) => option.value === settings.margin_preset) ?? PAGE_MARGIN_OPTIONS[1];
  const width = settings.orientation === "landscape" ? baseSize.height : baseSize.width;
  const height = settings.orientation === "landscape" ? baseSize.width : baseSize.height;
  return {
    width,
    height,
    paddingX: margin.paddingX,
    paddingY: margin.paddingY,
    contentMinHeight: Math.max(360, height - margin.paddingY * 2 - 190),
  };
}

function createChatMessage(
  role: ChatMessage["role"],
  content: string,
  status: ChatMessage["status"] = "ready",
  id?: string,
  selection?: SelectionRef | null,
  teachingProgress?: SectionTeachingProgress | null
): ChatMessage {
  return {
    id: id ?? crypto.randomUUID(),
    role,
    content,
    status,
    ...(selection ? { selection } : {}),
    ...(teachingProgress ? { teachingProgress } : {}),
  };
}

function createClientSessionId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

function createLessonComposerState(): LessonComposerState {
  return { ...DEFAULT_LESSON_COMPOSER_STATE };
}

function visibleLearningPurposeItem(value?: string | null): string | null {
  const trimmed = value?.trim();
  if (!trimmed || HIDDEN_LEARNING_PURPOSE_ITEMS.has(trimmed)) {
    return null;
  }
  return trimmed;
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

function selectionFromMetadata(value: unknown): SelectionRef | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const kind = raw.kind === "chat" || raw.kind === "board" ? raw.kind : null;
  const excerpt = typeof raw.excerpt === "string" ? raw.excerpt.trim() : "";
  if (!kind || !excerpt) {
    return null;
  }
  return {
    kind,
    excerpt,
    lesson_id: typeof raw.lesson_id === "string" ? raw.lesson_id : null,
    block_id: typeof raw.block_id === "string" ? raw.block_id : null,
  };
}

function teachingProgressFromMetadata(value: unknown): SectionTeachingProgress | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const sectionIndex = typeof raw.section_index === "number" ? raw.section_index : null;
  const sectionCount = typeof raw.section_count === "number" ? raw.section_count : null;
  if (sectionIndex === null || sectionCount === null || sectionCount <= 0) {
    return null;
  }
  return {
    section_index: sectionIndex,
    section_count: sectionCount,
    current_section_title: typeof raw.current_section_title === "string" ? raw.current_section_title : "",
    has_next_section: raw.has_next_section === true,
    waiting_for_continue: raw.waiting_for_continue === true,
  };
}

function selectionPreviewLabel(selection: SelectionRef): string {
  return selection.kind === "board" ? "选中的讲义" : "引用的对话";
}

function selectionPreviewText(excerpt: string): string {
  return excerpt.replace(/\s+/g, " ").trim();
}

function currentHeadCommitId(lesson: Lesson): string | null {
  const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
  return (
    branch?.head_commit_id ??
    lesson.history_graph.commits[lesson.history_graph.commits.length - 1]?.id ??
    null
  );
}

function getLessonCommit(lesson: Lesson, commitId: string | null | undefined): CommitRecord | null {
  if (!commitId) {
    return null;
  }
  return lesson.history_graph.commits.find((commit) => commit.id === commitId) ?? null;
}

function conversationTargetCommitId(lesson: Lesson, commitId?: string | null): string | null {
  const requestedCommitId = commitId ?? currentHeadCommitId(lesson);
  const commit = getLessonCommit(lesson, requestedCommitId);
  if (!commit) {
    return requestedCommitId;
  }

  const restoredCommitId = metadataText(commit, "restored_commit_id");
  if (commit.metadata?.kind === "restore_snapshot" && restoredCommitId) {
    return restoredCommitId;
  }

  return commit.id;
}

function commitLineageIds(lesson: Lesson, commitId?: string | null): Set<string> {
  const targetCommitId = conversationTargetCommitId(lesson, commitId);
  const commitsById = new Map(lesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const lineage = new Set<string>();
  const stack = targetCommitId ? [targetCommitId] : [];

  while (stack.length) {
    const nextCommitId = stack.pop();
    if (!nextCommitId || lineage.has(nextCommitId)) {
      continue;
    }
    lineage.add(nextCommitId);
    const commit = commitsById.get(nextCommitId);
    commit?.parent_ids.forEach((parentId) => stack.push(parentId));
  }

  return lineage;
}

function chatUserContentFromCommit(commit: CommitRecord): string | null {
  const userMessage = metadataText(commit, "user_message");
  if (!userMessage) {
    return null;
  }

  const scopeAction = metadataText(commit, "scope_action");
  if (scopeAction) {
    return `继续执行：${scopeAction}`;
  }

  const referenceAction = metadataText(commit, "resource_reference_action");
  if (referenceAction === "confirm") {
    return "继续执行：参考推荐章节生成讲义";
  }
  if (referenceAction === "skip") {
    return "继续执行：先不参考推荐章节";
  }

  const boardEditAction = metadataText(commit, "board_edit_action");
  const boardEditTopic = metadataText(commit, "board_edit_topic");
  if (boardEditAction === "confirm") {
    return `扩选板书：${boardEditTopic || userMessage}`;
  }
  if (boardEditAction === "skip") {
    return `暂不扩选板书：${boardEditTopic || userMessage}`;
  }

  return metadataText(commit, "interaction_mode") === "direct_edit" ? `直接编辑讲义：${userMessage}` : userMessage;
}

function buildLessonMessagesFromHistory(lesson: Lesson, commitId?: string | null): ChatMessage[] {
  const targetCommitId = conversationTargetCommitId(lesson, commitId);
  const lineageIds = commitLineageIds(lesson, targetCommitId);
  const messages: ChatMessage[] = [];

  lesson.history_graph.commits.forEach((commit) => {
    if (!lineageIds.has(commit.id) || commit.metadata?.kind !== "chat_flow") {
      return;
    }

    const userContent = chatUserContentFromCommit(commit);
    if (userContent) {
      messages.push(
        createChatMessage(
          "user",
          userContent,
          "ready",
          `${commit.id}:user`,
          selectionFromMetadata(commit.metadata?.selection)
        )
      );
    }

    const assistantMessage = metadataText(commit, "assistant_message");
    if (assistantMessage) {
      messages.push(
        createChatMessage(
          "assistant",
          assistantMessage,
          "ready",
          `${commit.id}:assistant`,
          null,
          teachingProgressFromMetadata(commit.metadata?.teaching_progress)
        )
      );
    }

    const createdLessonTitle = metadataText(commit, "created_lesson_title");
    if (createdLessonTitle) {
      messages.push(
        createChatMessage(
          "assistant",
          `我已经为这个更大的知识问题新开了一节课：《${createdLessonTitle}》。`,
          "ready",
          `${commit.id}:created-lesson`
        )
      );
    }
  });

  return messages;
}

function learningClarityFromCommit(commit: CommitRecord | null): LearningClarificationStatus | null {
  const value = commit?.metadata?.learning_clarification;
  if (!value || typeof value !== "object") {
    return null;
  }

  const record = value as Partial<LearningClarificationStatus>;
  if (typeof record.progress !== "number" || typeof record.label !== "string" || typeof record.reason !== "string") {
    return null;
  }

  return {
    progress: Math.max(0, Math.min(100, record.progress)),
    label: record.label,
    reason: record.reason,
    missing_items: Array.isArray(record.missing_items)
      ? record.missing_items.filter((item): item is string => typeof item === "string")
      : [],
    can_start: record.can_start === true,
    forced_start: record.forced_start === true,
  };
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

function branchSequenceForCommit(lesson: Lesson, commit: CommitRecord): BranchSequenceOption[] {
  const commitsById = new Map(lesson.history_graph.commits.map((item) => [item.id, item]));
  return Object.values(lesson.history_graph.branches)
    .filter((branch) => branch.base_commit_id === commit.id)
    .sort((left, right) => {
      const timeDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
      if (timeDelta !== 0) {
        return timeDelta;
      }
      return left.name.localeCompare(right.name, "zh-CN", { numeric: true });
    })
    .map((branch, index) => {
      const headCommit = commitsById.get(branch.head_commit_id);
      const snapshot = headCommit?.snapshot ?? commit.snapshot;
      return {
        order: index + 1,
        branchName: branch.name,
        documentTitle: snapshot.title || "未命名章节",
        documentOverview: compactText(snapshot.content_text || snapshot.title || "这个分支暂时还没有章节正文。", 220),
        latestLabel: headCommit?.label ?? "分支起点",
        latestMessage: compactText(headCommit?.message || commit.message || "还没有新的章节更新。", 120),
        updatedAt: headCommit?.created_at ?? branch.created_at,
      };
    });
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
        "mr-4 flex h-full items-center gap-1.5 border-b-2 px-2 text-[10px] font-bold uppercase tracking-widest transition-colors",
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
  icon,
  active,
  disabled,
  onClick,
}: {
  title: string;
  label: string;
  hint?: string;
  icon?: ReactNode;
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
        "flex min-w-[86px] flex-col items-start rounded-lg border px-2.5 py-2 text-left transition",
        active
          ? "border-black bg-black text-white shadow-sm"
          : "border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:bg-gray-50",
        disabled && "cursor-not-allowed opacity-40"
      )}
    >
      <span className="flex items-center gap-2">
        {icon ? (
          <span
            className={clsx(
              "flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
              active ? "bg-white/15 text-white" : "bg-gray-100 text-gray-700"
            )}
          >
            {icon}
          </span>
        ) : null}
        <span className="text-[12px] font-semibold">{label}</span>
      </span>
      {hint ? <span className={clsx("mt-1 text-[10px]", active ? "text-white/70" : "text-gray-400")}>{hint}</span> : null}
    </button>
  );
}

function WordPageZoomControls({
  value,
  onChange,
  onFitToWidth,
}: {
  value: number;
  onChange: (value: number) => void;
  onFitToWidth: () => void;
}) {
  const zoomProgress = ((value - PAGE_ZOOM_MIN) / (PAGE_ZOOM_MAX - PAGE_ZOOM_MIN)) * 100;

  return (
    <div className="flex h-10 items-center gap-1 rounded-full border border-gray-200 bg-gradient-to-b from-white to-gray-50 px-1.5 text-gray-600 shadow-[0_1px_3px_rgba(15,23,42,0.08)]">
      <button
        type="button"
        title="适配页面宽度"
        aria-label="适配页面宽度"
        onClick={onFitToWidth}
        className="flex h-7 w-7 items-center justify-center rounded-full transition hover:bg-white hover:text-black hover:shadow-sm"
      >
        <Maximize2 className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        title="重置缩放为 100%"
        onClick={() => onChange(PAGE_ZOOM_DEFAULT)}
        className="mx-0.5 flex h-7 min-w-14 items-center justify-center rounded-full border border-gray-200 bg-white px-2 text-[12px] font-semibold tabular-nums text-gray-800 shadow-[inset_0_1px_0_rgba(255,255,255,0.75)] transition hover:border-gray-300 hover:text-black"
      >
        {value}%
      </button>
      <button
        type="button"
        title="缩小页面"
        aria-label="缩小页面"
        disabled={value <= PAGE_ZOOM_MIN}
        onClick={() => onChange(value - PAGE_ZOOM_STEP)}
        className="flex h-7 w-7 items-center justify-center rounded-full transition hover:bg-white hover:text-black hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:shadow-none"
      >
        <Minus className="h-3.5 w-3.5" />
      </button>
      <input
        type="range"
        min={PAGE_ZOOM_MIN}
        max={PAGE_ZOOM_MAX}
        step={PAGE_ZOOM_SLIDER_STEP}
        value={value}
        aria-label="页面缩放"
        onChange={(event) => onChange(Number(event.target.value))}
        className="word-editor__zoom-range h-5 w-28 sm:w-32"
        style={{ "--word-zoom-progress": `${zoomProgress}%` } as CSSProperties}
      />
      <button
        type="button"
        title="放大页面"
        aria-label="放大页面"
        disabled={value >= PAGE_ZOOM_MAX}
        onClick={() => onChange(value + PAGE_ZOOM_STEP)}
        className="flex h-7 w-7 items-center justify-center rounded-full transition hover:bg-white hover:text-black hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:shadow-none"
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function ChatBubble({
  message,
  onContinueTeaching,
}: {
  message: ChatMessage;
  onContinueTeaching?: () => void;
}) {
  const [isSelectionExpanded, setIsSelectionExpanded] = useState(false);
  const isAssistant = message.role === "assistant";
  const isPending = message.status === "pending";
  const isError = message.status === "error";
  const selectedExcerpt = message.selection?.excerpt ? selectionPreviewText(message.selection.excerpt) : "";
  const teachingProgress = message.teachingProgress;
  return (
    <div className="flex flex-col gap-2">
      <div className={clsx("flex items-center gap-2", !isAssistant && "justify-end")}>
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
      {message.selection && selectedExcerpt ? (
        <div
          className={clsx(
            "max-w-[94%] rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-gray-700 shadow-sm",
            !isAssistant && "ml-auto"
          )}
        >
          <div className="flex items-start gap-2">
            <TextQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400" />
            <div className="min-w-0">
              <p className="text-[10px] font-semibold text-gray-500">{selectionPreviewLabel(message.selection)}</p>
              <p
                className={clsx(
                  "mt-1 whitespace-pre-wrap break-words pr-1 text-[12px] leading-5",
                  isSelectionExpanded ? "custom-scrollbar max-h-40 overflow-y-auto" : "max-h-10 overflow-hidden"
                )}
              >
                “{selectedExcerpt}”
              </p>
              <button
                type="button"
                aria-expanded={isSelectionExpanded}
                onClick={() => setIsSelectionExpanded((current) => !current)}
                className="mt-2 inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-semibold text-gray-500 transition hover:bg-white hover:text-gray-900"
              >
                {isSelectionExpanded ? (
                  <ChevronUp className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
                {isSelectionExpanded ? "收起" : "展开"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
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
        {isAssistant && teachingProgress && !isPending && !isError ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-200 pt-3 text-[11px] text-gray-500">
            <span>
              第 {teachingProgress.section_index + 1}/{teachingProgress.section_count} 节
              {teachingProgress.current_section_title ? `：${teachingProgress.current_section_title}` : ""}
            </span>
            {teachingProgress.has_next_section && onContinueTeaching ? (
              <button
                type="button"
                onClick={onContinueTeaching}
                className="inline-flex h-7 items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 font-semibold text-gray-700 transition hover:border-gray-300 hover:text-gray-950"
              >
                <ArrowRight className="h-3.5 w-3.5" />
                继续下一节
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CommitTimelineItem({
  commit,
  active,
  latest,
  branchSequence,
  currentBranchName,
  onPreview,
  onRestore,
  onBranch,
  onSwitchBranch,
}: {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
  branchSequence: BranchSequenceOption[];
  currentBranchName: string;
  onPreview: () => void;
  onRestore: () => void;
  onBranch: () => void;
  onSwitchBranch: (branchName: string) => void;
}) {
  const isChatFlow = commit.metadata?.kind === "chat_flow";
  const isAutoSave = metadataBool(commit, "autosave") || commit.metadata?.kind === "auto_document_save";
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
          {isAutoSave ? (
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-stone-600">
              Auto Save
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
        <BranchSequenceSelector
          branches={branchSequence}
          currentBranchName={currentBranchName}
          onSelectBranch={onSwitchBranch}
        />
      </div>
    </div>
  );
}

function WordBoardEditor({
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
  onSelectionChange: (selection: { excerpt: string; position: SelectionPopoverPosition | null } | null) => void;
  onImportDocx: (file: File) => void;
  onExportDocx: () => void;
}) {
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
  const editorContent =
    document.content_html.trim() ||
    (document.content_json && Object.keys(document.content_json).length ? document.content_json : "<p></p>");
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
      normalizeEditorMath(editor);
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

  const tableInsertHint = `${tableRows} x ${tableCols}${tableHasHeaderRow ? " · 表头" : ""}`;
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
        aria-label="表格行数"
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
        aria-label="表格列数"
        disabled={tableInsertDisabled}
        onChange={(event) => setTableCols(normalizeTableDimension(Number(event.target.value)))}
        className="w-9 border-0 bg-transparent text-center text-[12px] font-semibold outline-none disabled:cursor-not-allowed"
      />
      <label
        title="首行设为表头"
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
        表头
      </label>
    </div>
  );

  const renderTableEditButtons = (compact = true) => {
    if (compact) {
      return (
        <div className="flex items-center gap-1 border-r border-gray-100 pr-4">
          <ToolbarButton
            title="上方插入行"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addRowBefore().run()}
          >
            <ChevronUp className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="下方插入行"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addRowAfter().run()}
          >
            <ChevronDown className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="左侧插入列"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addColumnBefore().run()}
          >
            <ArrowLeft className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="右侧插入列"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().addColumnAfter().run()}
          >
            <ArrowRight className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="合并单元格"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().mergeCells().run()}
          >
            <TableCellsMerge className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="拆分单元格"
            disabled={tableEditDisabled}
            onClick={() => editor?.chain().focus().splitCell().run()}
          >
            <TableCellsSplit className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton
            title="删除表格"
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
          title="下方插入一行"
          label="加行"
          hint="当前表格"
          icon={<Rows3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().addRowAfter().run()}
        />
        <RibbonActionButton
          title="右侧插入一列"
          label="加列"
          hint="当前表格"
          icon={<Columns3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().addColumnAfter().run()}
        />
        <RibbonActionButton
          title="删除当前行"
          label="删行"
          hint="当前表格"
          icon={<Rows3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().deleteRow().run()}
        />
        <RibbonActionButton
          title="删除当前列"
          label="删列"
          hint="当前表格"
          icon={<Columns3 className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().deleteColumn().run()}
        />
        <RibbonActionButton
          title="合并单元格"
          label="合并"
          hint="选中单元格"
          icon={<TableCellsMerge className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().mergeCells().run()}
        />
        <RibbonActionButton
          title="拆分单元格"
          label="拆分"
          hint="当前单元格"
          icon={<TableCellsSplit className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().splitCell().run()}
        />
        <RibbonActionButton
          title="切换表头行"
          label="表头行"
          hint="当前表格"
          icon={<PanelTop className="h-4 w-4" />}
          disabled={tableEditDisabled}
          onClick={() => editor?.chain().focus().toggleHeaderRow().run()}
        />
        <RibbonActionButton
          title="删除表格"
          label="删表"
          hint="当前表格"
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
        {renderTableDimensionFields()}
        <ToolbarButton
          title={`插入 ${tableInsertHint} 表格`}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        >
          <Table2 className="h-4 w-4" />
        </ToolbarButton>
      </div>

      {renderTableEditButtons()}

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
          icon={<FilePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertBlankPage}
        />
        <RibbonActionButton
          title="插入封面"
          label="封面"
          hint="置顶模板"
          icon={<LayoutTemplate className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertCoverPage}
        />
        <RibbonActionButton
          title="插入目录页"
          label="目录页"
          hint="按标题生成"
          icon={<ClipboardList className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTableOfContents}
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
          icon={<ImagePlus className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={() => imageUploadRef.current?.click()}
        />
        {renderTableDimensionFields(false)}
        <RibbonActionButton
          title={`插入 ${tableInsertHint} 表格`}
          label="表格"
          hint={tableInsertHint}
          icon={<Table2 className="h-4 w-4" />}
          disabled={tableInsertDisabled}
          onClick={handleInsertTable}
        />
        <RibbonActionButton
          title="插入文本框"
          label="文本框"
          hint="重点旁注"
          icon={<TextCursorInput className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertTextBox}
        />
      </div>

      {renderTableEditButtons(false)}

      <div className="flex items-center gap-2 border-r border-gray-100 pr-4">
        <RibbonActionButton
          title="插入超链接"
          label="超链接"
          hint="外部资料"
          icon={<LinkIcon className="h-4 w-4" />}
          disabled={!editor || readOnly}
          onClick={handleInsertLink}
        />
        <RibbonActionButton
          title="插入水印"
          label="水印"
          hint="页面标识"
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
              开始 (HOME)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "insert"} onClick={() => setActiveRibbonTab("insert")}>
              <FilePlus className="h-3.5 w-3.5" />
              插入 (INSERT)
            </RibbonTabButton>
            <RibbonTabButton active={activeRibbonTab === "page"} onClick={() => setActiveRibbonTab("page")}>
              <Files className="h-3.5 w-3.5" />
              页面 (PAGE)
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
  const googleRealtimeSocketRef = useRef<WebSocket | null>(null);
  const googleAudioContextRef = useRef<AudioContext | null>(null);
  const googleAudioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const googleAudioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const googlePlaybackContextRef = useRef<AudioContext | null>(null);
  const googlePlaybackTimeRef = useRef(0);
  const googlePlaybackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const googleInputTranscriptRef = useRef("");
  const googleOutputTranscriptRef = useRef("");
  const openAIResponseInProgressRef = useRef(false);
  const realtimeLessonIdRef = useRef<string | null>(null);
  const realtimeClientSessionIdRef = useRef<string | null>(null);
  const realtimeLessonTitleRef = useRef<string | null>(null);
  const autoSaveTimerRef = useRef<number | null>(null);
  const autoSaveInFlightRef = useRef<Promise<boolean> | null>(null);
  const autoSaveQueuedRef = useRef(false);
  const documentDraftVersionRef = useRef(0);
  const activeLessonRef = useRef<Lesson | null>(null);
  const draftDocumentRef = useRef<BoardDocument | null>(null);
  const isDocumentDirtyRef = useRef(false);
  const isPreviewModeRef = useRef(false);
  const getRealtimeClientSessionId = useCallback(() => realtimeClientSessionIdRef.current, []);
  const getRealtimeLessonTitle = useCallback(() => realtimeLessonTitleRef.current, []);
  const {
    enqueueRealtimeLogEvent,
    flushRealtimeLogQueue,
    flushRealtimeLogQueueWithBeacon,
  } = useRealtimeLogQueue({
    getClientSessionId: getRealtimeClientSessionId,
    getLessonTitle: getRealtimeLessonTitle,
  });

  const [coursePackage, setCoursePackage] = useState<CoursePackage | null>(null);
  const [modelCatalog, setModelCatalog] = useState<AIModelCatalog>(() =>
    normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG)
  );
  const [selectedTextModel, setSelectedTextModel] = useState<AIModelSelection>(FALLBACK_MODEL_CATALOG.defaults.text);
  const [selectedRealtimeModel, setSelectedRealtimeModel] = useState<AIModelSelection>(
    FALLBACK_MODEL_CATALOG.defaults.realtime
  );
  const [openModelMenu, setOpenModelMenu] = useState<"text" | "realtime" | null>(null);
  const [draftDocument, setDraftDocument] = useState<BoardDocument | null>(null);
  const [isDocumentDirty, setIsDocumentDirty] = useState(false);
  const [, setAutoSaveStatus] = useState<AutoSaveStatus>("idle");
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
  const [boardEditPrompt, setBoardEditPrompt] = useState<BoardEditPrompt | null>(null);
  const [selectedReference, setSelectedReference] = useState<ResourceReferenceContext | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [lastReferenceRequest, setLastReferenceRequest] = useState<ChatRequestPayload | null>(null);
  const [lastBoardEditRequest, setLastBoardEditRequest] = useState<ChatRequestPayload | null>(null);
  const [previewCommitId, setPreviewCommitId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [lessonMessages, setLessonMessages] = useState<LessonMessageMap>({});
  const [topCollapsed, setTopCollapsed] = useState(true);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("history");
  const [isCreatingLessonInline, setIsCreatingLessonInline] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceStatusText, setVoiceStatusText] = useState("点击麦克风，连接所选实时语音讲师");

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
            next[lesson.id] = current[lesson.id] ?? buildLessonMessagesFromHistory(lesson);
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

  useEffect(() => {
    async function loadModelCatalog() {
      try {
        const catalog = normalizeCourseStudioModelCatalog(await api.getAIModels());
        setModelCatalog(catalog);
        setSelectedTextModel(
          resolveModelSelection(catalog.text, readStoredModelSelection(TEXT_MODEL_STORAGE_KEY), catalog.defaults.text)
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            catalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            catalog.defaults.realtime
          )
        );
      } catch {
        const fallbackCatalog = normalizeCourseStudioModelCatalog(FALLBACK_MODEL_CATALOG);
        setModelCatalog(fallbackCatalog);
        setSelectedTextModel(
          resolveModelSelection(
            fallbackCatalog.text,
            readStoredModelSelection(TEXT_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.text
          )
        );
        setSelectedRealtimeModel(
          resolveModelSelection(
            fallbackCatalog.realtime,
            readStoredModelSelection(REALTIME_MODEL_STORAGE_KEY),
            fallbackCatalog.defaults.realtime
          )
        );
      }
    }
    void loadModelCatalog();
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
  const activeMessages = activeLesson
    ? lessonMessages[activeLesson.id] ?? buildLessonMessagesFromHistory(activeLesson)
    : [];
  const displayedMessages =
    activeLesson && previewCommit ? buildLessonMessagesFromHistory(activeLesson, previewCommit.id) : activeMessages;
  const activeComposerState = activeLesson
    ? lessonComposerStates[activeLesson.id] ?? DEFAULT_LESSON_COMPOSER_STATE
    : DEFAULT_LESSON_COMPOSER_STATE;
  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const selectedTextOption = findModelOption(modelCatalog.text, selectedTextModel);
  const selectedRealtimeOption = findModelOption(modelCatalog.realtime, selectedRealtimeModel);
  const selectedRealtimeTransport = selectedRealtimeOption?.transport ?? "gemini_live_websocket";
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit";
  const activeRequirements = activeLesson?.learning_requirements ?? null;
  const isPreviewMode = Boolean(previewCommit);
  const previewLearningClarity = learningClarityFromCommit(previewCommit);
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
    visibleLearningPurposeItem(activeRequirements?.learning_goal) ?? visibleLearningPurposeItem(activeLesson?.summary),
    visibleLearningPurposeItem(activeRequirements?.success_criteria),
  ].filter((item): item is string => item !== null);
  const clarityStatus: LearningClarificationStatus =
    previewLearningClarity ??
    learningClarity ?? {
      progress: 0,
      label: "",
      reason: "",
      missing_items: [],
      can_start: false,
      forced_start: false,
    };
  const clarityBarTone =
    clarityStatus.progress >= 90
      ? "bg-emerald-500"
      : clarityStatus.can_start
        ? "bg-blue-500"
        : "bg-amber-500";
  const showClarityBadges = Boolean(clarityStatus.label || clarityStatus.forced_start);

  useEffect(() => {
    activeLessonRef.current = activeLesson;
    draftDocumentRef.current = draftDocument;
    isDocumentDirtyRef.current = isDocumentDirty;
    isPreviewModeRef.current = isPreviewMode;
  }, [activeLesson, draftDocument, isDocumentDirty, isPreviewMode]);

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
    setBoardEditPrompt(null);
    setSelectedReference(null);
    setLastScopedRequest(null);
    setLastReferenceRequest(null);
    setLastBoardEditRequest(null);
    clearSelection();
  }

  function syncLessonMessages(
    nextPackage: CoursePackage,
    options?: { blankLessonIds?: string[]; rebuildLessonIds?: string[] }
  ) {
    const blankLessonIds = new Set(options?.blankLessonIds ?? []);
    const rebuildLessonIds = new Set(options?.rebuildLessonIds ?? []);
    setLessonMessages((current) => {
      const next: LessonMessageMap = {};
      nextPackage.lessons.forEach((lesson) => {
        next[lesson.id] = blankLessonIds.has(lesson.id)
          ? []
          : rebuildLessonIds.has(lesson.id)
            ? buildLessonMessagesFromHistory(lesson)
            : current[lesson.id] ?? buildLessonMessagesFromHistory(lesson);
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
    options?: { blankLessonIds?: string[]; activeLessonId?: string | null; rebuildMessageLessonIds?: string[] }
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
    draftDocumentRef.current = nextActiveLesson?.board_document ?? null;
    isDocumentDirtyRef.current = false;
    setAutoSaveStatus("idle");
    syncLessonMessages(mergedPackage, {
      blankLessonIds: options?.blankLessonIds,
      rebuildLessonIds: options?.rebuildMessageLessonIds,
    });
    syncLessonComposerStates(mergedPackage.lessons);
    resetTransientUi();
    setError(null);
  }

  function clearAutoSaveTimer() {
    if (autoSaveTimerRef.current === null) {
      return;
    }
    window.clearTimeout(autoSaveTimerRef.current);
    autoSaveTimerRef.current = null;
  }

  function buildDocumentSavePayload(document: BoardDocument, reason: AutoSaveReason) {
    if (reason === "manual") {
      return {
        document,
        label: "Manual document edit",
        message: "Saved Word-like rich document changes from the editor",
        metadata: {
          kind: "manual_document_save",
        },
      };
    }
    return {
      document,
      label: "Auto Save",
      message: "Auto-saved Word-like rich document changes from the editor",
      metadata: {
        kind: "auto_document_save",
        autosave: true,
        autosave_reason: reason,
        source: "word_board_editor",
      },
    };
  }

  function applyAutoSavedPackage(nextPackage: CoursePackage, lessonId: string, savedVersion: number) {
    const currentActiveLessonId = activeLessonRef.current?.id ?? null;
    const effectiveActiveLessonId =
      currentActiveLessonId && nextPackage.workspace_tab_order.includes(currentActiveLessonId)
        ? currentActiveLessonId
        : nextPackage.active_lesson_id;
    const mergedPackage =
      effectiveActiveLessonId === nextPackage.active_lesson_id
        ? nextPackage
        : { ...nextPackage, active_lesson_id: effectiveActiveLessonId };
    const savedLesson = mergedPackage.lessons.find((lesson) => lesson.id === lessonId) ?? null;

    setCoursePackage(mergedPackage);
    syncLessonMessages(mergedPackage);
    syncLessonComposerStates(mergedPackage.lessons);

    if (currentActiveLessonId !== lessonId || !savedLesson) {
      setError(null);
      return;
    }

    if (documentDraftVersionRef.current === savedVersion) {
      setDraftDocument(savedLesson.board_document);
      draftDocumentRef.current = savedLesson.board_document;
      setIsDocumentDirty(false);
      isDocumentDirtyRef.current = false;
      setAutoSaveStatus("saved");
      setError(null);
      return;
    }

    const latestDraft = draftDocumentRef.current;
    const stillDirty = Boolean(latestDraft && !documentsEqual(latestDraft, savedLesson.board_document));
    setIsDocumentDirty(stillDirty);
    isDocumentDirtyRef.current = stillDirty;
    setAutoSaveStatus(stillDirty ? "pending" : "saved");
    setError(null);
  }

  async function flushAutoSave(reason: AutoSaveReason): Promise<boolean> {
    clearAutoSaveTimer();
    if (autoSaveInFlightRef.current) {
      autoSaveQueuedRef.current = true;
      await autoSaveInFlightRef.current;
      if (!isDocumentDirtyRef.current) {
        return true;
      }
      return flushAutoSave(reason);
    }

    const lesson = activeLessonRef.current;
    const document = draftDocumentRef.current;
    if (!lesson || !document || !isDocumentDirtyRef.current || isPreviewModeRef.current) {
      return true;
    }
    if (documentsEqual(document, lesson.board_document)) {
      setIsDocumentDirty(false);
      isDocumentDirtyRef.current = false;
      setAutoSaveStatus("idle");
      return true;
    }

    const savedVersion = documentDraftVersionRef.current;
    const isManualSave = reason === "manual";
    const payload = buildDocumentSavePayload(document, reason);
    if (isManualSave) {
      setBusyAction("save");
    }
    setAutoSaveStatus("saving");

    const request = (async () => {
      try {
        const nextPackage = await api.saveDocument(lesson.id, payload);
        applyAutoSavedPackage(nextPackage, lesson.id, savedVersion);
        return true;
      } catch (saveError) {
        setAutoSaveStatus("error");
        setError(saveError instanceof Error ? saveError.message : "自动保存失败");
        return false;
      } finally {
        if (isManualSave) {
          setBusyAction((current) => (current === "save" ? null : current));
        }
      }
    })();

    autoSaveInFlightRef.current = request;
    try {
      return await request;
    } finally {
      autoSaveInFlightRef.current = null;
      if (autoSaveQueuedRef.current) {
        autoSaveQueuedRef.current = false;
        if (isDocumentDirtyRef.current) {
          scheduleAutoSave("queued");
        }
      }
    }
  }

  function scheduleAutoSave(reason: AutoSaveReason = "debounce") {
    clearAutoSaveTimer();
    if (!isDocumentDirtyRef.current || isPreviewModeRef.current) {
      return;
    }
    if (autoSaveInFlightRef.current) {
      autoSaveQueuedRef.current = true;
      return;
    }
    setAutoSaveStatus("pending");
    autoSaveTimerRef.current = window.setTimeout(() => {
      autoSaveTimerRef.current = null;
      void flushAutoSave(reason);
    }, AUTO_SAVE_DELAY_MS);
  }

  function flushAutoSaveWithBeacon(reason: AutoSaveReason = "pagehide") {
    clearAutoSaveTimer();
    const lesson = activeLessonRef.current;
    const document = draftDocumentRef.current;
    if (!lesson || !document || !isDocumentDirtyRef.current || isPreviewModeRef.current) {
      return;
    }
    if (documentsEqual(document, lesson.board_document)) {
      return;
    }
    const payload = buildDocumentSavePayload(document, reason);
    const sent = api.saveDocumentBeacon(lesson.id, payload);
    if (!sent) {
      void api.saveDocumentKeepalive(lesson.id, payload).catch(() => undefined);
    }
  }

  function handleLocalDocumentChange(nextDocument: BoardDocument) {
    if (isPreviewMode || !activeLesson) {
      return;
    }
    const hasChanged = !documentsEqual(draftDocumentRef.current, nextDocument);
    const dirty = !documentsEqual(nextDocument, activeLesson.board_document);
    if (hasChanged) {
      documentDraftVersionRef.current += 1;
    }
    draftDocumentRef.current = nextDocument;
    isDocumentDirtyRef.current = dirty;
    setDraftDocument((current) => {
      if (current && current.id === nextDocument.id && documentsEqual(current, nextDocument)) {
        return current;
      }
      return nextDocument;
    });
    setIsDocumentDirty(dirty);
    setAutoSaveStatus(dirty ? "pending" : "idle");
  }

  const scheduleAutoSaveEffectEvent = useEffectEvent(() => {
    scheduleAutoSave("debounce");
  });

  const clearAutoSaveTimerEffectEvent = useEffectEvent(() => {
    clearAutoSaveTimer();
  });

  useEffect(() => {
    if (!isDocumentDirty || isPreviewMode) {
      clearAutoSaveTimerEffectEvent();
      return;
    }
    scheduleAutoSaveEffectEvent();
    return () => {
      clearAutoSaveTimerEffectEvent();
    };
  }, [activeLesson?.id, draftDocument, isDocumentDirty, isPreviewMode]);

  async function handleImportDocx(file: File) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("import"))) {
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
    if (!(await flushAutoSave("export"))) {
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

  async function saveGeneratedLesson(topic: string): Promise<boolean> {
    if (!topic.trim()) {
      return false;
    }
    setBusyAction("generate");
    try {
      const nextPackage = await api.generateLesson(topic.trim(), {
        branchFromLessonId: coursePackage?.is_standalone ? null : activeLesson?.id,
        startBlank: true,
        targetPackageId: coursePackage?.id,
      });
      updateCoursePackage(nextPackage, {
        blankLessonIds: nextPackage.active_lesson_id ? [nextPackage.active_lesson_id] : [],
      });
      return true;
    } catch (generationError) {
      setError(generationError instanceof Error ? generationError.message : "生成 lesson 失败");
      return false;
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateLessonFromName(topic: string) {
    if (!(await flushAutoSave("create-lesson"))) {
      return false;
    }
    const isCreated = await saveGeneratedLesson(topic);
    if (isCreated) {
      setIsCreatingLessonInline(false);
    }
    return isCreated;
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload, options?: { speakResponse?: boolean }) {
    if (!activeLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    if (isPreviewMode) {
      exitPreviewMode();
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
      text_model: payload.text_model ?? selectedTextModel,
      conversation: activeMessages.slice(-8).map(({ role, content }) => ({ role, content })),
    };
    const submittedSelection = payloadWithConversation.selection ?? null;

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    chatRequestInFlightRef.current = true;
    if (!(await flushAutoSave("chat"))) {
      chatRequestInFlightRef.current = false;
      return;
    }

    const isDirectEdit = payloadWithConversation.interaction_mode === "direct_edit";
    const userMessageContent = payloadOverride?.scope_action
      ? `继续执行：${payloadOverride.scope_action}`
      : payloadOverride?.teaching_action === "continue"
        ? "继续讲下一节"
        : payloadOverride?.teaching_action === "restart"
          ? "从第一节重新讲"
          : payloadOverride?.board_edit_action === "confirm"
            ? `扩选板书：${payloadOverride.board_edit_topic ?? payloadWithConversation.message}`
            : payloadOverride?.board_edit_action === "skip"
              ? `暂不扩选板书：${payloadOverride.board_edit_topic ?? payloadWithConversation.message}`
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
        : payloadOverride?.teaching_action === "continue"
          ? "正在接着讲下一小节。"
          : payloadOverride?.teaching_action === "restart"
            ? "正在从第一小节重新讲。"
            : payloadOverride?.board_edit_action === "confirm"
              ? "正在把这次扩展落成版书内容，并同步准备讲解。"
              : payloadOverride?.board_edit_action === "skip"
                ? "好的，我会只按内部讲义继续讲解，不改右侧版书。"
                : payloadOverride?.resource_reference_action === "confirm"
                  ? "正在结合你确认的参考章节准备讲解。"
                  : payloadOverride?.resource_reference_action === "skip"
                    ? "正在按当前 lesson 主线准备讲解。"
                    : isDirectEdit
                      ? "正在改写右侧讲义，并同步准备更像真人老师的讲法。"
                      : "正在整理学习需求并准备讲解。",
      "pending"
    );
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
      createChatMessage("user", userMessageContent, "ready", undefined, submittedSelection),
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
      setBoardEditPrompt(response.board_edit_prompt ?? null);
      setSelectedReference(response.selected_reference ?? null);
      setLastScopedRequest(response.scope_options.length ? payloadWithConversation : null);
      setLastReferenceRequest(response.reference_prompt ? payloadWithConversation : null);
      setLastBoardEditRequest(response.board_edit_prompt ? payloadWithConversation : null);
      const assistantMessages = [
        createChatMessage("assistant", response.teacher_message, "ready", undefined, null, response.teaching_progress ?? null),
      ];
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
      if (options?.speakResponse) {
        speakControlledTeacherMessage(response.teacher_message);
        setVoiceStatusText("讲师回答已通过受控工作流播出，可以继续提问");
      }
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

  async function handleBoardEditAction(action: "confirm" | "skip") {
    if (!boardEditPrompt || !lastBoardEditRequest) {
      return;
    }
    await handleSubmitChat({
      message: lastBoardEditRequest.message,
      selection: lastBoardEditRequest.selection,
      interaction_mode: lastBoardEditRequest.interaction_mode,
      scope_action: lastBoardEditRequest.scope_action,
      resource_chapter_id: lastBoardEditRequest.resource_chapter_id,
      resource_reference_action: lastBoardEditRequest.resource_reference_action,
      resource_reference_resource_id: lastBoardEditRequest.resource_reference_resource_id,
      resource_reference_chapter_id: lastBoardEditRequest.resource_reference_chapter_id,
      board_edit_action: action,
      board_edit_topic: boardEditPrompt.topic,
    });
    setBoardEditPrompt(null);
    setLastBoardEditRequest(null);
  }

  async function handleContinueTeaching() {
    if (!activeLesson) {
      return;
    }
    await handleSubmitChat({
      message: "继续下一节",
      interaction_mode: "ask",
      teaching_action: "continue",
    });
  }

  async function handleCreateBranch(fromCommitId = previewCommitId, branchNameOverride?: string) {
    if (!activeLesson) {
      return;
    }
    if (!fromCommitId && !(await flushAutoSave("branch"))) {
      return;
    }
    const branchName = (branchNameOverride ?? newBranchName.trim()).trim();
    const finalBranchName = branchName || nextBranchName(activeLesson);
    setBusyAction("branch");
    try {
      const nextPackage = await api.createBranch(activeLesson.id, finalBranchName, fromCommitId);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: fromCommitId ? [activeLesson.id] : undefined,
      });
      setNewBranchName("");
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "创建分支失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handlePreviewCommit(commit: CommitRecord) {
    if (!(await flushAutoSave("preview"))) {
      return;
    }
    setPreviewCommitId(commit.id);
    setDraftDocument(commit.snapshot);
    draftDocumentRef.current = commit.snapshot;
    setIsDocumentDirty(false);
    isDocumentDirtyRef.current = false;
    setAutoSaveStatus("idle");
  }

  function exitPreviewMode() {
    if (!activeLesson || !previewCommitId) {
      return;
    }
    setPreviewCommitId(null);
    setDraftDocument(activeLesson.board_document);
    draftDocumentRef.current = activeLesson.board_document;
    setIsDocumentDirty(false);
    isDocumentDirtyRef.current = false;
    setAutoSaveStatus("idle");
  }

  async function handleCreateBranchFromCommit(commit: CommitRecord) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("branch"))) {
      return;
    }
    setPreviewCommitId(commit.id);
    setDraftDocument(commit.snapshot);
    draftDocumentRef.current = commit.snapshot;
    setIsDocumentDirty(false);
    isDocumentDirtyRef.current = false;
    setAutoSaveStatus("idle");
    await handleCreateBranch(commit.id, newBranchName.trim() || nextBranchName(activeLesson));
  }

  async function handleSwitchBranch(branchName: string) {
    if (!activeLesson) {
      return;
    }
    if (!(await flushAutoSave("switch-branch"))) {
      return;
    }
    setBusyAction("switch-branch");
    try {
      const nextPackage = await api.switchBranch(activeLesson.id, branchName);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: [activeLesson.id],
      });
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
    if (!(await flushAutoSave("restore"))) {
      return;
    }
    setBusyAction("restore");
    try {
      const nextPackage = await api.restoreCommit(activeLesson.id, commitId);
      updateCoursePackage(nextPackage, {
        activeLessonId: activeLesson.id,
        rebuildMessageLessonIds: [activeLesson.id],
      });
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "恢复版本失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleOpenLesson(lessonId: string) {
    if (!(await flushAutoSave("open-lesson"))) {
      return;
    }
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
    if (activeLesson?.id === lessonId && !(await flushAutoSave("close-lesson"))) {
      return;
    }
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
    if (!(await flushAutoSave("upload-resource"))) {
      return;
    }
    setBusyAction("upload");
    try {
      const nextPackage = await api.uploadResource(file, activeLesson?.id);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson?.id });
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传资料失败");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleDeleteResource(resourceId: string, resourceName: string) {
    if (!window.confirm(`删除资料“${resourceName}”？删除后，AI 将不再引用它。`)) {
      return;
    }
    if (!(await flushAutoSave("delete-resource"))) {
      return;
    }
    setBusyAction(`delete-resource:${resourceId}`);
    try {
      const nextPackage = await api.deleteResource(resourceId, activeLesson?.id);
      updateCoursePackage(nextPackage, { activeLessonId: activeLesson?.id });
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除资料失败");
    } finally {
      setBusyAction(null);
    }
  }

  function stopGoogleQueuedPlayback() {
    googlePlaybackSourcesRef.current.forEach((source) => {
      try {
        source.stop();
      } catch {
        // Already ended or never started.
      }
      try {
        source.disconnect();
      } catch {
        // Already disconnected.
      }
    });
    googlePlaybackSourcesRef.current.clear();
    const playbackContext = googlePlaybackContextRef.current;
    googlePlaybackTimeRef.current = playbackContext?.currentTime ?? 0;
  }

  function queueGooglePlayback(base64: string, mimeType?: string) {
    const playbackContext = googlePlaybackContextRef.current;
    if (!playbackContext) {
      return;
    }
    const source = playPcmBase64(base64, mimeType, playbackContext, googlePlaybackTimeRef);
    googlePlaybackSourcesRef.current.add(source);
    source.addEventListener(
      "ended",
      () => {
        googlePlaybackSourcesRef.current.delete(source);
      },
      { once: true }
    );
  }

  function resetOpenAIRemoteAudioPlayback() {
    const remoteAudio = remoteAudioRef.current;
    const remoteStream = remoteAudio?.srcObject;
    if (!remoteAudio || !remoteStream) {
      return;
    }
    remoteAudio.pause();
    remoteAudio.srcObject = null;
    remoteAudio.srcObject = remoteStream;
    void remoteAudio.play().catch(() => undefined);
  }

  function disposeRealtimeSession() {
    void flushRealtimeLogQueue();
    realtimeChannelRef.current?.close();
    realtimeChannelRef.current = null;
    googleRealtimeSocketRef.current?.close();
    googleRealtimeSocketRef.current = null;

    googleAudioProcessorRef.current?.disconnect();
    googleAudioProcessorRef.current = null;
    googleAudioSourceRef.current?.disconnect();
    googleAudioSourceRef.current = null;
    void googleAudioContextRef.current?.close().catch(() => undefined);
    googleAudioContextRef.current = null;
    stopGoogleQueuedPlayback();
    void googlePlaybackContextRef.current?.close().catch(() => undefined);
    googlePlaybackContextRef.current = null;
    googlePlaybackTimeRef.current = 0;
    googleInputTranscriptRef.current = "";
    googleOutputTranscriptRef.current = "";
    openAIResponseInProgressRef.current = false;

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
    void flushRealtimeLogQueue();
  });

  const flushRealtimeLogQueueWithBeaconEffectEvent = useEffectEvent(() => {
    flushRealtimeLogQueueWithBeacon();
  });

  const flushAutoSaveWithBeaconEffectEvent = useEffectEvent(() => {
    flushAutoSaveWithBeacon("pagehide");
  });

  const disposeRealtimeSessionEffectEvent = useEffectEvent(() => {
    disposeRealtimeSession();
  });

  function stopRealtimeSession(statusText = "语音讲师已断开") {
    disposeRealtimeSession();
    window.speechSynthesis?.cancel();
    setVoiceActive(false);
    setVoiceStatusText(statusText);
    setBusyAction((current) => (current === "voice-connect" ? null : current));
  }

  const stopRealtimeSessionEvent = useEffectEvent((statusText: string) => {
    stopRealtimeSession(statusText);
  });

  function speakControlledTeacherMessage(content: string) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      return;
    }
    const text = content.trim();
    if (!text) {
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
  }

  function handleRealtimeUserTranscript(lessonId: string, transcript: string, eventType: string) {
    const normalized = transcript.trim();
    if (!normalized) {
      return;
    }
    enqueueRealtimeLogEvent(lessonId, "user", eventType, normalized);
    if (chatRequestInFlightRef.current) {
      setVoiceStatusText("正在处理上一句语音，请稍等片刻");
      return;
    }
    void handleSubmitChat(
      {
        message: normalized,
        interaction_mode: "ask",
      },
      { speakResponse: true }
    );
  }

  function flushGoogleRealtimeTranscripts(lessonId: string) {
    const userTranscript = googleInputTranscriptRef.current.trim();
    const assistantTranscript = googleOutputTranscriptRef.current.trim();
    if (userTranscript) {
      handleRealtimeUserTranscript(lessonId, userTranscript, "google.input_transcription");
      googleInputTranscriptRef.current = "";
    }
    if (assistantTranscript) {
      enqueueRealtimeLogEvent(lessonId, "assistant", "google.output_transcription", assistantTranscript);
      googleOutputTranscriptRef.current = "";
    }
  }

  function beginGoogleAudioStreaming(socket: WebSocket, mediaStream: MediaStream, audioContext: AudioContext) {
    const source = audioContext.createMediaStreamSource(mediaStream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    source.connect(processor);
    processor.connect(audioContext.destination);
    googleAudioSourceRef.current = source;
    googleAudioProcessorRef.current = processor;
    processor.onaudioprocess = (event) => {
      if (socket.readyState !== WebSocket.OPEN) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const resampled = resampleLinear(input, audioContext.sampleRate, 16000);
      socket.send(
        JSON.stringify({
          realtimeInput: {
            audio: {
              mimeType: "audio/pcm;rate=16000",
              data: pcmFloatToBase64(resampled),
            },
          },
        })
      );
    };
  }

  function handleGoogleRealtimeMessage(message: GoogleRealtimeAudioMessage) {
    const lessonId = realtimeLessonIdRef.current;
    if (!lessonId) {
      return;
    }
    const serverContent = message.serverContent;
    if (!serverContent) {
      return;
    }
    const inputText = serverContent.inputTranscription?.text;
    if (inputText) {
      googleInputTranscriptRef.current += inputText;
    }
    if (serverContent.interrupted) {
      stopGoogleQueuedPlayback();
      googleOutputTranscriptRef.current = "";
      setVoiceStatusText("检测到插话，已停止上一段回答");
    }
    const outputText = serverContent.outputTranscription?.text;
    if (outputText && !serverContent.interrupted) {
      googleOutputTranscriptRef.current += outputText;
    }
    serverContent.modelTurn?.parts?.forEach((part) => {
      const inlineData = part.inlineData;
      if (!inlineData?.data || serverContent.interrupted) {
        return;
      }
      queueGooglePlayback(inlineData.data, inlineData.mimeType);
    });
    if (serverContent.turnComplete) {
      flushGoogleRealtimeTranscripts(lessonId);
    }
  }

  function selectTextModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    const nextSelection = optionToSelection(option);
    setSelectedTextModel(nextSelection);
    persistModelSelection(TEXT_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  function selectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("已切换实时语音模型，当前会话已断开");
    }
    const nextSelection = optionToSelection(option);
    setSelectedRealtimeModel(nextSelection);
    persistModelSelection(REALTIME_MODEL_STORAGE_KEY, nextSelection);
    setOpenModelMenu(null);
  }

  async function startGoogleRealtimeSession(
    lesson: Lesson,
    mediaStream: MediaStream,
    clientSessionId: string
  ) {
    const session = await api.createGoogleRealtimeSession(lesson.id, {
      latest_assistant_message: latestAssistantMessage?.content ?? null,
      client_session_id: clientSessionId,
      realtime_model: selectedRealtimeModel,
    });
    const audioContext = new AudioContext();
    const playbackContext = new AudioContext();
    googleAudioContextRef.current = audioContext;
    googlePlaybackContextRef.current = playbackContext;
    googlePlaybackTimeRef.current = playbackContext.currentTime;
    await audioContext.resume();
    await playbackContext.resume();

    const socket = new WebSocket(getApiWebSocketUrl(session.websocket_url));
    googleRealtimeSocketRef.current = socket;
    await new Promise<void>((resolve, reject) => {
      let streamingStarted = false;
      let settled = false;
      const resolveStart = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve();
      };
      const rejectStart = (message: string) => {
        if (settled) {
          return;
        }
        settled = true;
        reject(new Error(message));
      };
      socket.onopen = () => {
        socket.send(JSON.stringify(session.setup));
      };
      socket.onerror = () => {
        rejectStart("Google Gemini Live WebSocket 连接失败");
      };
      socket.onclose = (event) => {
        if (!streamingStarted) {
          rejectStart(
            `Google Gemini Live WebSocket 在初始化前关闭（${event.code}${event.reason ? `：${event.reason}` : ""}）`
          );
        }
        if (googleRealtimeSocketRef.current === socket) {
          stopRealtimeSession("Google Gemini Live 会话已结束");
        }
      };
      socket.onmessage = (event) => {
        void (async () => {
          try {
            const messageText = await websocketMessageText(event.data);
            const payload = JSON.parse(messageText) as GoogleRealtimeAudioMessage;
            if (payload.error) {
              const message = googleRealtimeErrorMessage(payload.error);
              if (!streamingStarted) {
                rejectStart(message);
                return;
              }
              stopRealtimeSession("Google Gemini Live 会话已结束");
              setError(message);
              return;
            }
            if (payload.setupComplete && !streamingStarted) {
              streamingStarted = true;
              beginGoogleAudioStreaming(socket, mediaStream, audioContext);
              setVoiceActive(true);
              setBusyAction((current) => (current === "voice-connect" ? null : current));
              setVoiceStatusText(`Google Gemini Live 已连接，语音音色：${session.voice}`);
              resolveStart();
              return;
            }
            handleGoogleRealtimeMessage(payload);
          } catch {
            // ignore malformed realtime events
          }
        })();
      };
    });
  }

  useEffect(() => {
    return () => {
      flushAutoSaveWithBeaconEffectEvent();
      flushRealtimeLogQueueWithBeaconEffectEvent();
      disposeRealtimeSessionEffectEvent();
    };
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      scheduleRealtimeLogFlushEffectEvent();
    }, 2000);

    function handlePageHide() {
      flushAutoSaveWithBeaconEffectEvent();
      flushRealtimeLogQueueWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
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
      setError("当前浏览器无法访问麦克风。请使用支持麦克风的浏览器，并通过 localhost 或 HTTPS 打开页面。");
      return;
    }
    if (selectedRealtimeOption && !selectedRealtimeOption.enabled) {
      setError(`当前未配置 ${PROVIDER_LABELS[selectedRealtimeModel.provider]} 的实时语音 API Key。`);
      return;
    }
    if (!(await flushAutoSave("voice"))) {
      return;
    }

    setBusyAction("voice-connect");
    const realtimeLabel = modelButtonLabel(selectedRealtimeOption, selectedRealtimeModel);
    setVoiceStatusText(`正在连接 ${realtimeLabel}…`);
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

      const clientSessionId = createClientSessionId("realtime");
      realtimeLessonIdRef.current = activeLesson.id;
      realtimeClientSessionIdRef.current = clientSessionId;
      realtimeLessonTitleRef.current = activeLesson.title;

      if (selectedRealtimeTransport === "gemini_live_websocket" || selectedRealtimeModel.provider === "google") {
        await startGoogleRealtimeSession(activeLesson, mediaStream, clientSessionId);
        return;
      }

      const peerConnection = new RTCPeerConnection();
      realtimePeerRef.current = peerConnection;

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
          setVoiceStatusText(`${realtimeLabel} 已连接，说话后会先进入 PM/版书管理/讲师工作流`);
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
          if (payload.type === "response.created") {
            openAIResponseInProgressRef.current = true;
          }
          if (payload.type === "response.done" || payload.type === "response.audio.done") {
            openAIResponseInProgressRef.current = false;
          }
          if (payload.type === "input_audio_buffer.speech_started") {
            if (openAIResponseInProgressRef.current && dataChannel.readyState === "open") {
              dataChannel.send(JSON.stringify({ type: "response.cancel" }));
              openAIResponseInProgressRef.current = false;
            }
            resetOpenAIRemoteAudioPlayback();
          }
          const lessonId = realtimeLessonIdRef.current;
          if (!lessonId || !payload.type || !payload.transcript) {
            return;
          }
          if (
            payload.type === "conversation.item.input_audio_transcription.completed" ||
            payload.type === "conversation.item.input_audio_transcription.done"
          ) {
            handleRealtimeUserTranscript(lessonId, payload.transcript, payload.type);
          }
          if (payload.type === "response.audio_transcript.done") {
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
        realtime_model: selectedRealtimeModel,
      });

      await peerConnection.setRemoteDescription({
        type: "answer",
        sdp: realtimeResponse.answer_sdp,
      });

      setVoiceStatusText(`${PROVIDER_LABELS[realtimeResponse.provider]} ${realtimeResponse.model} 已就绪，正在受控转写`);
    } catch (voiceError) {
      stopRealtimeSession("语音连接失败");
      setError(realtimeConnectionErrorMessage(voiceError, selectedRealtimeModel));
    }
  }

  async function handleSelectLesson(lessonId: string) {
    if (activeLesson?.id !== lessonId && !(await flushAutoSave("select-lesson"))) {
      return;
    }
    resetTransientUi();
    setCoursePackage((current) => {
      if (!current) {
        return current;
      }
      const next = { ...current, active_lesson_id: lessonId };
      const selectedLesson = next.lessons.find((lesson) => lesson.id === lessonId) ?? null;
      setDraftDocument(selectedLesson?.board_document ?? null);
      setIsDocumentDirty(false);
      draftDocumentRef.current = selectedLesson?.board_document ?? null;
      isDocumentDirtyRef.current = false;
      setAutoSaveStatus("idle");
      return next;
    });
  }

  async function handleReturnHome() {
    if (!(await flushAutoSave("return-home"))) {
      return;
    }
    router.push("/");
  }

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">正在载入课程工作台…</div>;
  }

  if (!coursePackage) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">没有找到可用课程。</div>;
  }

  const workspaceTitle = coursePackage.title;

  function renderWorkspaceHeader() {
    return (
      <>
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
                  onClick={() => void handleReturnHome()}
                  className="group flex h-8 w-8 items-center justify-center rounded-full text-gray-600 transition-colors duration-150 hover:bg-gray-100 hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-300"
                  title="返回主页"
                  aria-label="返回主页"
                >
                  <ArrowLeft className="h-5 w-5 stroke-[1.8] transition-transform duration-150 group-hover:-translate-x-0.5" />
                </button>
                <span className="text-[13px] font-semibold tracking-tight">{workspaceTitle}</span>
              </div>

              <nav className="flex min-w-0 items-center overflow-x-auto custom-scrollbar">
                {openLessons.map((lesson) => (
                  <button
                    key={lesson.id}
                    type="button"
                    onClick={() => void handleSelectLesson(lesson.id)}
                    className={clsx(
                      "group flex h-12 items-center gap-2 border-r border-gray-100 px-4 text-left text-[10px] font-bold uppercase tracking-[0.2em] transition-colors",
                      lesson.id === activeLesson?.id
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
                {isCreatingLessonInline ? (
                  <InlineNameForm
                    label="新页面名称"
                    placeholder="课程导读 / 第一讲 / 练习讲义"
                    variant="tab"
                    isBusy={busyAction === "generate"}
                    onCancel={() => setIsCreatingLessonInline(false)}
                    onSubmit={handleCreateLessonFromName}
                  />
                ) : null}
                <button
                  type="button"
                  onClick={() => setIsCreatingLessonInline(true)}
                  className="p-3 text-gray-300 transition-colors hover:text-black"
                  title="新建页面"
                >
                  <Plus className="h-4 w-4" />
                </button>
              </nav>
            </div>

            <div className="flex shrink-0 items-center gap-4">
              <div className="ml-2 flex items-center gap-1 border-l border-gray-200 pl-4">
                <button
                  type="button"
                  onClick={() => setRightSidebarOpen((current) => !current)}
                  aria-pressed={rightSidebarOpen}
                  className={clsx(
                    "rounded-md border p-1.5 transition-colors",
                    rightSidebarOpen
                      ? "border-gray-200 bg-gray-100 text-gray-700 shadow-sm"
                      : "border-transparent bg-white text-gray-500 hover:border-gray-200 hover:bg-gray-50"
                  )}
                  title={rightSidebarOpen ? "收起右侧栏" : "展开右侧栏"}
                >
                  <PanelRight className="h-4.5 w-4.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setTopCollapsed(true)}
                  aria-pressed={!topCollapsed}
                  className={clsx(
                    "rounded-md border p-1.5 transition-colors",
                    !topCollapsed
                      ? "border-gray-200 bg-gray-100 text-gray-700 shadow-sm"
                      : "border-transparent bg-white text-gray-500 hover:border-gray-200 hover:bg-gray-50"
                  )}
                  title="收起顶部与编辑工具栏"
                >
                  <ChevronUp className="h-4.5 w-4.5" />
                </button>
              </div>
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
      </>
    );
  }

  function renderErrorBanner() {
    if (!error) {
      return null;
    }
    return (
      <div
        role="alert"
        className="mx-4 mt-3 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 md:mx-6"
      >
        <span className="min-w-0 flex-1">{error}</span>
        <button
          type="button"
          onClick={() => setError(null)}
          aria-label="关闭错误提示"
          title="关闭提示"
          className="-mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-rose-500 transition-colors hover:bg-rose-100 hover:text-rose-700"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  if (!activeLesson || !displayedDocument) {
    return (
      <main className="flex h-screen flex-col overflow-hidden bg-[#f8f6f0] text-[#1a1a1a]">
        {renderWorkspaceHeader()}

        {renderErrorBanner()}

        <section className="flex flex-1 items-center justify-center px-6">
          <div className="w-full max-w-xl rounded-[32px] border border-stone-200 bg-white/90 p-10 text-center shadow-[0_24px_70px_rgba(15,23,42,0.08)]">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[20px] bg-stone-950 text-white">
              <BookOpen className="h-7 w-7" />
            </div>
            <h2 className="mt-6 text-2xl font-semibold tracking-tight text-stone-950">这个课程包还是空的</h2>
            <p className="mt-3 text-sm leading-7 text-stone-500">
              上方这条页签栏已经是当前课程包的页面区了。点右上角的加号，或者直接从下面创建第一张课程页面。
            </p>
            <div className="mt-8 flex justify-center">
              <button
                type="button"
                onClick={() => {
                  setTopCollapsed(false);
                  setIsCreatingLessonInline(true);
                }}
                className="inline-flex items-center gap-2 rounded-full bg-stone-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-stone-800"
              >
                <Plus className="h-4 w-4" />
                新建第一页
              </button>
            </div>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="flex h-screen flex-col overflow-hidden bg-[#f8f6f0] text-[#1a1a1a]">
      {renderWorkspaceHeader()}

      {renderErrorBanner()}

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
                {showClarityBadges ? (
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {clarityStatus.label ? (
                      <span className="rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-blue-700">
                        {clarityStatus.label}
                      </span>
                    ) : null}
                    {clarityStatus.forced_start ? (
                      <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-amber-700">
                        已按当前信息开始
                      </span>
                    ) : null}
                  </div>
                ) : null}
                {clarityStatus.reason ? (
                  <p className="mt-2 text-xs leading-6 text-blue-900">{clarityStatus.reason}</p>
                ) : null}
                {learningGoalItems.length ? (
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
                ) : null}
              </div>

              <div className="space-y-6">
                {previewCommit ? (
                  <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-xs leading-6 text-violet-800">
                    正在查看 {previewCommit.label} 时的交流记录。
                  </div>
                ) : null}

                {displayedMessages.map((message, index) => (
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
                    <ChatBubble
                      message={message}
                      onContinueTeaching={
                        !isPreviewMode &&
                        index === displayedMessages.length - 1 &&
                        message.role === "assistant" &&
                        message.teachingProgress?.has_next_section
                          ? () => void handleContinueTeaching()
                          : undefined
                      }
                    />
                  </div>
                ))}
              </div>

              {!isPreviewMode && scopeOptions.length ? (
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

              {!isPreviewMode && referencePrompt ? (
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

              {!isPreviewMode && boardEditPrompt ? (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">扩选板书</p>
                  <p className="mt-2 text-sm leading-6 text-emerald-950">{boardEditPrompt.question}</p>
                  <p className="mt-2 text-xs leading-6 text-emerald-900/80">{boardEditPrompt.reason}</p>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => void handleBoardEditAction("confirm")}
                      className="w-full rounded-xl border border-emerald-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-900 transition hover:border-emerald-300"
                    >
                      {boardEditPrompt.confirm_label}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleBoardEditAction("skip")}
                      className="w-full rounded-xl border border-emerald-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-900 transition hover:border-emerald-300"
                    >
                      {boardEditPrompt.skip_label}
                    </button>
                  </div>
                </div>
              ) : null}

              {!isPreviewMode && clarificationQuestions.length ? (
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

              {!isPreviewMode && selectedReference ? (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">已引用参考资料</p>
                  <p className="mt-2 text-sm font-semibold text-gray-900">
                    {selectedReference.resource_name} / {selectedReference.chapter_title}
                  </p>
                </div>
              ) : null}
            </div>
          </div>

          <div className="shrink-0 border-t border-gray-100 bg-white px-3 py-3">
            <div className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] items-center gap-2">
              <div className="relative">
                <button
                  type="button"
                  aria-expanded={openModelMenu === "text"}
                  aria-label={`文本生成，当前模型 ${modelButtonLabel(selectedTextOption, selectedTextModel)}`}
                  onClick={() => setOpenModelMenu((current) => (current === "text" ? null : "text"))}
                  className="flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 text-left transition-colors hover:border-gray-300 hover:bg-white"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <BrainCircuit className="h-4 w-4 shrink-0 text-gray-600" />
                    <span className="truncate text-xs font-semibold text-gray-900">文本生成</span>
                  </span>
                  <ChevronDown
                    className={clsx(
                      "h-4 w-4 shrink-0 text-gray-500 transition-transform",
                      openModelMenu === "text" && "rotate-180"
                    )}
                  />
                </button>

                {openModelMenu === "text" ? (
                  <div className="absolute bottom-full left-0 z-30 mb-2 max-h-[360px] w-[min(336px,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-gray-200 bg-white p-2 shadow-xl">
                    <div className="space-y-1">
                      {modelCatalog.text.map((option) => {
                        const selected = modelOptionKey(option) === modelSelectionKey(selectedTextModel);
                        return (
                          <button
                            key={`text-${modelOptionKey(option)}`}
                            type="button"
                            onClick={() => selectTextModel(option)}
                            disabled={!option.enabled}
                            className={clsx(
                              "flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left transition-colors",
                              selected ? "bg-gray-100 text-gray-950" : "text-gray-700 hover:bg-gray-50",
                              !option.enabled && "cursor-not-allowed opacity-50 hover:bg-transparent"
                            )}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-xs font-semibold">{option.label}</span>
                              <span className="block truncate text-[11px] text-gray-400">
                                {PROVIDER_LABELS[option.provider]} / {option.model}
                                {option.configured ? "" : " / 未配置"}
                              </span>
                            </span>
                            {selected ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : null}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="relative">
                <button
                  type="button"
                  aria-expanded={openModelMenu === "realtime"}
                  aria-label={`语音模型，当前模型 ${modelButtonLabel(selectedRealtimeOption, selectedRealtimeModel)}`}
                  onClick={() => setOpenModelMenu((current) => (current === "realtime" ? null : "realtime"))}
                  className="flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 text-left transition-colors hover:border-gray-300 hover:bg-white"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Volume2 className="h-4 w-4 shrink-0 text-gray-600" />
                    <span className="truncate text-xs font-semibold text-gray-900">语音模型</span>
                  </span>
                  <ChevronDown
                    className={clsx(
                      "h-4 w-4 shrink-0 text-gray-500 transition-transform",
                      openModelMenu === "realtime" && "rotate-180"
                    )}
                  />
                </button>

                {openModelMenu === "realtime" ? (
                  <div className="absolute bottom-full right-0 z-30 mb-2 max-h-[280px] w-[min(336px,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-gray-200 bg-white p-2 shadow-xl">
                    <div className="space-y-1">
                      {modelCatalog.realtime.map((option) => {
                        const selected = modelOptionKey(option) === modelSelectionKey(selectedRealtimeModel);
                        return (
                          <button
                            key={`realtime-${modelOptionKey(option)}`}
                            type="button"
                            onClick={() => selectRealtimeModel(option)}
                            disabled={!option.enabled}
                            className={clsx(
                              "flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left transition-colors",
                              selected ? "bg-gray-100 text-gray-950" : "text-gray-700 hover:bg-gray-50",
                              !option.enabled && "cursor-not-allowed opacity-50 hover:bg-transparent"
                            )}
                          >
                            <span className="min-w-0">
                              <span className="block truncate text-xs font-semibold">{option.label}</span>
                              <span className="block truncate text-[11px] text-gray-400">
                                {PROVIDER_LABELS[option.provider]} / {option.model}
                                {option.configured ? "" : " / 未配置"}
                              </span>
                            </span>
                            {selected ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : null}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>

              <button
                type="button"
                onClick={() => void handleVoiceToggle()}
                title={voiceStatusText}
                className={clsx(
                  "flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-sm transition-all hover:scale-105 hover:shadow-md",
                  voiceActive ? "bg-gray-800 ring-2 ring-gray-200" : "bg-[#1a1a1a]"
                )}
              >
                {voiceActive ? <Radio className="h-4.5 w-4.5" /> : <Volume2 className="h-4.5 w-4.5" />}
              </button>
            </div>
            <p className="mb-2 truncate px-1 text-center text-[10px] leading-4 text-gray-500">{voiceStatusText}</p>
            <audio ref={remoteAudioRef} autoPlay className="hidden" />

            <div
              className={clsx(
                "overflow-hidden rounded-2xl border bg-white shadow-sm transition-colors focus-within:ring-1",
                composerMode === "direct_edit"
                  ? "border-amber-200 focus-within:border-amber-500 focus-within:ring-amber-500"
                  : "border-gray-200 focus-within:border-black focus-within:ring-black"
              )}
            >
              {composerSelection ? (
                <div className="mx-2.5 mt-2.5 flex items-center justify-between gap-2 rounded-xl bg-gray-50 px-2.5 py-1.5">
                  <div className="flex min-w-0 items-center gap-2">
                    {composerMode === "direct_edit" ? (
                      <PencilLine className="h-4 w-4 shrink-0 text-amber-600" />
                    ) : (
                      <TextQuote className="h-4 w-4 shrink-0 text-gray-500" />
                    )}
                    <p className="min-w-0 truncate text-xs leading-5 text-gray-700">
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
                onFocus={() => {
                  if (isPreviewMode) {
                    exitPreviewMode();
                  }
                }}
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
                    : isPreviewMode
                      ? "点击输入会回到当前版本并继续对话"
                    : composerMode === "direct_edit"
                    ? "描述要怎么改这段板书，或直接说“重写整篇”..."
                    : composerSelection
                      ? "基于选中内容继续追问"
                      : "提问或下达修改指令..."
                }
                className="custom-scrollbar block w-full resize-none border-0 bg-transparent px-3.5 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-gray-400 disabled:cursor-wait disabled:text-gray-400"
              />
              <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
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
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1a1a1a] text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
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
                onClick={exitPreviewMode}
              >
                回到当前版本
              </button>
            </div>
          ) : null}

          <WordBoardEditor
            document={displayedDocument}
            readOnly={isPreviewMode}
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
                      branchSequence={branchSequenceForCommit(activeLesson, commit)}
                      currentBranchName={activeLesson.history_graph.current_branch}
                      onPreview={() => void handlePreviewCommit(commit)}
                      onRestore={() => void handleRestoreCommit(commit.id)}
                      onBranch={() => void handleCreateBranchFromCommit(commit)}
                      onSwitchBranch={(branchName) => void handleSwitchBranch(branchName)}
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
                  <ResourceUploadDropzone
                    disabled={Boolean(busyAction)}
                    uploading={busyAction === "upload"}
                    onUpload={(file) => void handleUploadResource(file)}
                  />
                  <div className="mt-4 space-y-3">
                    {coursePackage.resources.length ? (
                      coursePackage.resources.map((resource) => {
                        const isDeletingResource = busyAction === `delete-resource:${resource.id}`;
                        return (
                          <div
                            key={resource.id}
                            className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-gray-300"
                          >
                            <div className="flex items-start gap-3">
                              <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                                {resource.resource_type === "image" || resource.mime_type.startsWith("image/") ? (
                                  <ImagePlus className="h-4 w-4" />
                                ) : (
                                  <FileText className="h-4 w-4" />
                                )}
                              </div>
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-xs font-bold text-gray-900">{resource.name}</p>
                                <p className="mt-1 text-[11px] text-gray-500">
                                  {resource.extracted_text_available
                                    ? `已索引 ${resource.outline.length} 个章节入口`
                                    : "当前仅做入口索引"}
                                </p>
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleDeleteResource(resource.id, resource.name)}
                                disabled={Boolean(busyAction)}
                                title={`删除 ${resource.name}`}
                                aria-label={`删除资料 ${resource.name}`}
                                className={clsx(
                                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600",
                                  busyAction && "cursor-not-allowed opacity-50 hover:bg-transparent hover:text-gray-400"
                                )}
                              >
                                {isDeletingResource ? (
                                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <Trash2 className="h-3.5 w-3.5" />
                                )}
                              </button>
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
                        );
                      })
                    ) : null}
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
