"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronUp,
  PanelRight,
  PencilLine,
  Plus,
  TextQuote,
  X,
} from "lucide-react";

import { api, getApiWebSocketUrl } from "@/lib/api";
import { CourseStudioChatSidebar } from "@/components/course-studio/chat-sidebar";
import {
  AUTO_SAVE_DELAY_MS,
  DEFAULT_LESSON_COMPOSER_STATE,
  buildLessonMessagesFromHistory,
  createChatMessage,
  createLessonComposerState,
  currentHeadCommitId,
  documentsEqual,
  getLessonCommit,
  isBoardDocumentEmpty,
  learningClarityFromCommit,
  nextBranchName,
  type ChatMessage,
  type LessonComposerState,
  type LessonComposerStateMap,
  type LessonMessageMap,
} from "@/components/course-studio/history-utils";
import {
  FALLBACK_MODEL_CATALOG,
  PROVIDER_LABELS,
  REALTIME_MODEL_STORAGE_KEY,
  TEXT_MODEL_STORAGE_KEY,
  findModelOption,
  googleRealtimeErrorMessage,
  modelButtonLabel,
  normalizeCourseStudioModelCatalog,
  optionToSelection,
  persistModelSelection,
  readStoredModelSelection,
  realtimeConnectionErrorMessage,
  resolveModelSelection,
  websocketMessageText,
  type GoogleRealtimeAudioMessage,
} from "@/components/course-studio/model-catalog";
import {
  samePopoverPosition,
  sameSelection,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import { CourseStudioSidePanel, type CourseStudioSidebarTab } from "@/components/course-studio/studio-side-panel";
import { WordBoardEditor } from "@/components/course-studio/word-board-editor";
import { InlineNameForm } from "@/components/inline-name-form";
import { useRealtimeLogQueue } from "@/hooks/use-realtime-log-queue";
import { useResizablePanelWidth } from "@/hooks/use-resizable-panel-width";
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
  LearningClarificationStatus,
  Lesson,
  ResourceMatch,
  ResourceReferenceContext,
  ResourceReferencePrompt,
  ScopeOption,
  SelectionRef,
} from "@/types";

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

const CHAT_PANEL_WIDTH_STORAGE_KEY = "openclass:studio:chat-panel-width";
const CHAT_PANEL_DEFAULT_WIDTH = 380;
const CHAT_PANEL_MIN_WIDTH = 300;
const CHAT_PANEL_MAX_WIDTH = 640;

function createClientSessionId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

export function CourseStudio() {
  const router = useRouter();
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const chatScrollEndRef = useRef<HTMLDivElement | null>(null);
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
  const {
    width: chatPanelWidth,
    isResizing: isChatPanelResizing,
    dragHandleProps: chatPanelResizeHandleProps,
  } = useResizablePanelWidth({
    storageKey: CHAT_PANEL_WIDTH_STORAGE_KEY,
    defaultWidth: CHAT_PANEL_DEFAULT_WIDTH,
    minWidth: CHAT_PANEL_MIN_WIDTH,
    maxWidth: CHAT_PANEL_MAX_WIDTH,
  });
  const [sidebarTab, setSidebarTab] = useState<CourseStudioSidebarTab>("history");
  const [isCreatingLessonInline, setIsCreatingLessonInline] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceStatusText, setVoiceStatusText] = useState("点击麦克风，连接实时语音 Chatbot");

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
  const activeHeadCommit = activeLesson ? getLessonCommit(activeLesson, currentHeadCommitId(activeLesson)) : null;

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

  useEffect(() => {
    chatScrollEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeLesson?.id, displayedMessages.length, isChatBusy]);

  const activeRequirements = activeLesson?.learning_requirements ?? null;
  const isPreviewMode = Boolean(previewCommit);
  const previewLearningClarity = learningClarityFromCommit(previewCommit);
  const persistedLearningClarity = learningClarityFromCommit(activeHeadCommit);
  const currentRequirementCleared =
    !isPreviewMode && !activeRequirements && activeHeadCommit?.metadata?.requirement_cleared === true;
  const latestAssistantMessage = [...activeMessages].reverse().find((message) => message.role === "assistant");
  const relatedEdges =
    activeLesson && coursePackage
      ? coursePackage.course_graph.filter(
          (edge) =>
            edge.source_lesson_id === activeLesson.id || edge.target_lesson_id === activeLesson.id
        )
      : [];
  const composerSelection = selection && !selectionPopover ? selection : null;

  const clarityStatus: LearningClarificationStatus =
    previewLearningClarity ??
    (currentRequirementCleared ? null : learningClarity) ??
    (currentRequirementCleared ? null : persistedLearningClarity) ?? {
      progress: 0,
      label: "",
      reason: "",
      missing_items: [],
      can_start: false,
      forced_start: false,
      summary: "",
      key_facts: [],
      checklist: [],
      next_question: "",
      ready_for_board: false,
    };
  const showReadyForBoardCard =
    !isPreviewMode && isBoardDocumentEmpty(displayedDocument) && (clarityStatus.ready_for_board || clarityStatus.progress >= 100);
  const clarityBarTone =
    clarityStatus.progress >= 90
      ? "bg-emerald-500"
      : clarityStatus.can_start
        ? "bg-blue-500"
        : "bg-amber-500";
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
    clearAutoSaveTimer();
    autoSaveQueuedRef.current = false;
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

  function buildDocumentSavePayload(document: BoardDocument, reason: AutoSaveReason, baseCommitId: string | null) {
    if (reason === "manual") {
      return {
        document,
        label: "Manual document edit",
        message: "Saved Word-like rich document changes from the editor",
        base_commit_id: baseCommitId,
        metadata: {
          kind: "manual_document_save",
        },
      };
    }
    return {
      document,
      label: "Auto Save",
      message: "Auto-saved Word-like rich document changes from the editor",
      base_commit_id: baseCommitId,
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
    const baseCommitId = currentHeadCommitId(lesson);
    const payload = buildDocumentSavePayload(document, reason, baseCommitId);
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
    const baseCommitId = currentHeadCommitId(lesson);
    const payload = buildDocumentSavePayload(document, reason, baseCommitId);
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
      "",
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
      const chatbotMessage = response.chatbot_message.trim();
      const assistantMessages: ChatMessage[] = [];
      if (chatbotMessage) {
        assistantMessages.push(
          createChatMessage("assistant", chatbotMessage, "ready", undefined, null, response.teaching_progress ?? null)
        );
      }
      updateLessonMessages(lessonId, (current) => [
        ...current.filter((message) => message.id !== pendingAssistantMessage.id),
        ...assistantMessages,
      ]);
      if (options?.speakResponse && chatbotMessage) {
        speakControlledChatbotMessage(chatbotMessage);
        setVoiceStatusText("Chatbot 回复已通过受控工作流播出，可以继续提问");
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

  function stopRealtimeSession(statusText = "语音 Chatbot 已断开") {
    disposeRealtimeSession();
    window.speechSynthesis?.cancel();
    setVoiceActive(false);
    setVoiceStatusText(statusText);
    setBusyAction((current) => (current === "voice-connect" ? null : current));
  }

  const stopRealtimeSessionEvent = useEffectEvent((statusText: string) => {
    stopRealtimeSession(statusText);
  });

  function speakControlledChatbotMessage(content: string) {
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
      stopRealtimeSession("语音 Chatbot 已手动断开");
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
          setVoiceStatusText(`${realtimeLabel} 已连接，说话后会先进入 Chatbot 工作流`);
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
    router.push("/home");
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
                {isCreatingLessonInline && (activeLesson || openLessons.length > 0) ? (
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
              {isCreatingLessonInline ? (
                <InlineNameForm
                  label="第一页名称"
                  placeholder="课程导读 / 第一讲 / 练习讲义"
                  isBusy={busyAction === "generate"}
                  className="w-full max-w-sm"
                  onCancel={() => setIsCreatingLessonInline(false)}
                  onSubmit={handleCreateLessonFromName}
                />
              ) : (
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
              )}
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
            引用到输入框
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
                编辑文档
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <div
        ref={mainContainerRef}
        style={{ "--chat-panel-width": `${chatPanelWidth}px` } as CSSProperties}
        className={clsx(
          "grid min-h-0 flex-1 grid-cols-[var(--chat-panel-width)_minmax(0,1fr)] overflow-hidden transition-[grid-template-columns]",
          isChatPanelResizing ? "duration-0" : "duration-300",
          rightSidebarOpen && "xl:grid-cols-[var(--chat-panel-width)_minmax(0,1fr)_360px]"
        )}
      >
        <CourseStudioChatSidebar
          resizeHandleProps={chatPanelResizeHandleProps}
          isResizing={isChatPanelResizing}
          clarityBarTone={clarityBarTone}
          clarityStatus={clarityStatus}
          activeLesson={activeLesson}
          targetCommitId={currentRequirementCleared ? null : previewCommit?.id ?? activeHeadCommit?.id ?? null}
          previewCommit={previewCommit}
          displayedMessages={displayedMessages}
          isPreviewMode={isPreviewMode}
          isChatBusy={isChatBusy}
          showReadyForBoardCard={showReadyForBoardCard}
          scopeOptions={scopeOptions}
          referencePrompt={referencePrompt}
          boardEditPrompt={boardEditPrompt}
          clarificationQuestions={clarificationQuestions}
          latestBoardDecision={latestBoardDecision}
          selectedReference={selectedReference}
          chatScrollEndRef={chatScrollEndRef}
          chatInputRef={chatInputRef}
          remoteAudioRef={remoteAudioRef}
          modelCatalog={modelCatalog}
          selectedTextModel={selectedTextModel}
          selectedRealtimeModel={selectedRealtimeModel}
          selectedTextOption={selectedTextOption}
          selectedRealtimeOption={selectedRealtimeOption}
          openModelMenu={openModelMenu}
          setOpenModelMenu={setOpenModelMenu}
          voiceActive={voiceActive}
          voiceStatusText={voiceStatusText}
          chatInput={chatInput}
          composerMode={composerMode}
          composerSelection={composerSelection}
          includeSelectionInPrompt={includeSelectionInPrompt}
          onApplySelection={applySelection}
          onContinueTeaching={() => void handleContinueTeaching()}
          onSubmitChat={(payload) => handleSubmitChat(payload)}
          onScopeAction={(option) => handleScopeAction(option)}
          onReferenceAction={(action) => handleReferenceAction(action)}
          onBoardEditAction={(action) => handleBoardEditAction(action)}
          onSelectTextModel={selectTextModel}
          onSelectRealtimeModel={selectRealtimeModel}
          onVoiceToggle={handleVoiceToggle}
          onExitPreviewMode={exitPreviewMode}
          onClearSelection={clearSelection}
          onUpdateComposerState={updateActiveLessonComposerState}
          onAdjustComposerHeight={adjustComposerHeight}
        />

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
                  document_id: payload.documentId,
                  excerpt: payload.excerpt,
                  before_text: payload.beforeText,
                  after_text: payload.afterText,
                },
                payload.position
              );
            }}
            onImportDocx={(file) => void handleImportDocx(file)}
            onExportDocx={() => void handleExportDocx()}
          />
        </section>

        <CourseStudioSidePanel
          open={rightSidebarOpen}
          sidebarTab={sidebarTab}
          onSidebarTabChange={setSidebarTab}
          onClose={() => setRightSidebarOpen(false)}
          activeLesson={activeLesson}
          previewCommit={previewCommit}
          previewCommitId={previewCommitId}
          activeRequirements={activeRequirements}
          latestBoardDecision={latestBoardDecision}
          newBranchName={newBranchName}
          onNewBranchNameChange={setNewBranchName}
          busyAction={busyAction}
          resources={coursePackage.resources}
          relatedEdges={relatedEdges}
          lessonMap={lessonMap}
          onCreateBranch={() => handleCreateBranch()}
          onPreviewCommit={(commit) => handlePreviewCommit(commit)}
          onRestoreCommit={(commitId) => handleRestoreCommit(commitId)}
          onCreateBranchFromCommit={(commit) => handleCreateBranchFromCommit(commit)}
          onSwitchBranch={(branchName) => handleSwitchBranch(branchName)}
          onUploadResource={(file) => handleUploadResource(file)}
          onDeleteResource={(resourceId, resourceName) => handleDeleteResource(resourceId, resourceName)}
          onOpenLesson={(lessonId) => handleOpenLesson(lessonId)}
        />
      </div>
    </main>
  );
}
