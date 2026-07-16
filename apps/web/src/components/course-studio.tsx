"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { BookOpen, Plus } from "lucide-react";

import { BoardEditorPanel } from "@/components/course-studio/board-editor-panel";
import { CourseStudioChatSidebar } from "@/components/course-studio/chat-sidebar";
import { CourseStudioPageShell } from "@/components/course-studio/course-studio-page-shell";
import type { FormulaInkEditorSubmitPayload } from "@/components/course-studio/word-board-editor";
import {
  buildLessonMessagesFromHistory,
  learningClarityFromCommit,
} from "@/components/course-studio/history-utils";
import {
  samePopoverPosition,
  sameSelection,
  type SelectionPopoverPosition,
} from "@/components/course-studio/selection-utils";
import { LessonTabs } from "@/components/course-studio/lesson-tabs";
import { SelectionPopover } from "@/components/course-studio/selection-popover";
import { CourseStudioSidePanel, type CourseStudioSidebarTab } from "@/components/course-studio/studio-side-panel";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { useBoardDraft } from "@/hooks/course-studio/use-board-draft";
import { useChatSpeech } from "@/hooks/course-studio/use-chat-speech";
import { useCourseWorkspace, type CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import { useLessonChatAgent } from "@/hooks/course-studio/use-lesson-chat-agent";
import { useLessonHistory } from "@/hooks/course-studio/use-lesson-history";
import { useModelCatalog } from "@/hooks/course-studio/use-model-catalog";
import { useRealtimeVoice } from "@/hooks/course-studio/use-realtime-voice";
import { useWorkspaceActions } from "@/hooks/course-studio/use-workspace-actions";
import { InlineNameForm } from "@/components/inline-name-form";
import { useResizablePanelWidth } from "@/hooks/use-resizable-panel-width";
import type { AIModelOption, ChatInteractionMode, CoursePackage, LearningClarificationStatus, SelectionRef } from "@/types";

const CHAT_PANEL_WIDTH_STORAGE_KEY = "openclass:studio:chat-panel-width";
const CHAT_PANEL_DEFAULT_WIDTH = 380;
const CHAT_PANEL_MIN_WIDTH = 300;
const CHAT_PANEL_MAX_WIDTH = 640;
const RIGHT_SIDEBAR_WIDTH_STORAGE_KEY = "openclass:studio:right-sidebar-width";
const RIGHT_SIDEBAR_DEFAULT_WIDTH = 360;
const RIGHT_SIDEBAR_MIN_WIDTH = 280;
const RIGHT_SIDEBAR_MAX_WIDTH = 640;

export function CourseStudio() {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const studioTexts = txt.studio;
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const chatScrollEndRef = useRef<HTMLDivElement | null>(null);
  const chatRequestInFlightRef = useRef(false);

  const workspace = useCourseWorkspace();
  const {
    coursePackage,
    isLoading,
    error,
    setError,
    lessonMap,
    activeLesson,
    openLessons,
    activeMessages,
    activeComposerState,
    updateLessonMessages,
    updateLessonComposerState,
    updateActiveLessonComposerState,
    applyCoursePackage: applyWorkspaceCoursePackage,
  } = workspace;
  const modelSelection = useModelCatalog();
  const {
    modelCatalog,
    selectedTextModel,
    selectedRealtimeModel,
    selectedTextOption,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    openModelMenu,
    setOpenModelMenu,
    selectTextModel,
    selectRealtimeModel,
  } = modelSelection;
  const textModelReady = modelCatalog.text.some((option) => option.enabled);
  const [selection, setSelection] = useState<SelectionRef | null>(null);
  const [selectionPopover, setSelectionPopover] = useState<SelectionPopoverPosition | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
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
    label: "调整 Chatbot 宽度",
  });
  const {
    width: rightSidebarWidth,
    isResizing: isRightSidebarResizing,
    dragHandleProps: rightSidebarResizeHandleProps,
  } = useResizablePanelWidth({
    storageKey: RIGHT_SIDEBAR_WIDTH_STORAGE_KEY,
    defaultWidth: RIGHT_SIDEBAR_DEFAULT_WIDTH,
    minWidth: RIGHT_SIDEBAR_MIN_WIDTH,
    maxWidth: RIGHT_SIDEBAR_MAX_WIDTH,
    dragDirection: "grow-left",
    label: "调整课程工作台辅助宽度",
  });
  const [sidebarTab, setSidebarTab] = useState<CourseStudioSidebarTab>("history");
  const [isCreatingLessonInline, setIsCreatingLessonInline] = useState(false);

  const boardDraft = useBoardDraft({
    activeLesson,
    setError,
    setBusyAction,
    applyCoursePackage: applyWorkspaceCoursePackage,
    applyAutoSavedCoursePackage: workspace.applyAutoSavedCoursePackage,
    onPackageApplied: resetTransientUi,
  });

  function updateCoursePackage(nextPackage: CoursePackage, options?: CoursePackageApplyOptions) {
    const result = applyWorkspaceCoursePackage(nextPackage, options);
    boardDraft.resetToLesson(result.activeLesson);
    resetTransientUi();
    return result;
  }

  const history = useLessonHistory({
    activeLesson,
    flushAutoSave: boardDraft.flushAutoSave,
    resetDraftToLesson: boardDraft.resetToLesson,
    setPreviewDocument: boardDraft.setPreviewDocument,
    applyCoursePackage: updateCoursePackage,
    setError,
    setBusyAction,
  });
  const {
    flushAutoSave,
    flushAutoSaveWithBeacon,
    handleLocalDocumentChange,
    markStructureRemovalIntent,
    handleImportDocx,
    handleExportDocx,
    handleExportHtml,
  } = boardDraft;
  const {
    previewCommitId,
    newBranchName,
    setNewBranchName,
    handleCreateBranch,
    handlePreviewCommit,
    exitPreviewMode,
    handleCreateBranchFromCommit,
    handleSwitchBranch,
    handleRestoreCommit,
  } = history;

  const previewCommit = history.previewCommit;
  const activeHeadCommit = history.activeHeadCommit;
  const isPreviewMode = history.isPreviewMode;
  const isDraftPreviewMode = !isPreviewMode && boardDraft.isPreviewing;
  const displayedDocument = boardDraft.displayedDocument;
  const displayedMessages =
    activeLesson && previewCommit ? buildLessonMessagesFromHistory(activeLesson, previewCommit.id) : activeMessages;
  const persistedRequirements = activeLesson?.learning_requirements ?? null;
  const persistedBoardTask = activeLesson?.board_task_requirements ?? null;
  const previewLearningClarity = learningClarityFromCommit(previewCommit);
  const persistedLearningClarity = learningClarityFromCommit(activeHeadCommit);
  const currentRequirementCleared =
    !isPreviewMode && !persistedRequirements && activeHeadCommit?.metadata?.requirement_cleared === true;
  const latestAssistantMessage = [...activeMessages].reverse().find((message) => message.role === "assistant");
  const composerSelection = activeComposerState.composerSelection;
  const composerAttachments = activeComposerState.composerAttachments;
  function exitAnyPreviewMode() {
    if (isDraftPreviewMode) {
      boardDraft.resetToLesson(activeLesson);
      return;
    }
    exitPreviewMode();
  }
  const chatAgent = useLessonChatAgent({
    activeLesson,
    activeMessages,
    activeComposerState,
    composerSelection,
    currentBoardDocument: displayedDocument,
    selectedTextModel,
    textModelReady,
    isPreviewMode: isPreviewMode || isDraftPreviewMode,
    chatRequestInFlightRef,
    flushAutoSave,
    exitPreviewMode: exitAnyPreviewMode,
    updateCoursePackage,
    updateLessonMessages,
    updateLessonComposerState,
    clearSelection,
    setStreamingDocumentPreview: boardDraft.setStreamingDocumentPreview,
    setError,
    setBusyAction,
    busyAction,
  });
  const {
    chatInput,
    composerMode,
    includeSelectionInPrompt,
    isChatBusy,
    clarificationQuestions,
    learningClarity,
    streamedRequirementSheet,
    streamedBoardTaskSheet,
    currentNeedPending,
    latestBoardDecision,
    handleSubmitChat,
    handleStopChat,
    handleEditMessage,
    handleContinueTeaching,
  } = chatAgent;
  const activeRequirements = streamedRequirementSheet ?? persistedRequirements;
  const activeBoardTask = streamedBoardTaskSheet ?? persistedBoardTask;
  const voice = useRealtimeVoice({
    activeLesson,
    latestAssistantMessageContent: latestAssistantMessage?.content ?? null,
    selectedRealtimeModel,
    selectedRealtimeOption,
    selectedRealtimeTransport,
    busyAction,
    setBusyAction,
    setError,
    flushAutoSave,
    chatRequestInFlightRef,
    onSubmitTranscript: (message) => {
      void handleSubmitChat({
        message,
        interaction_mode: "ask",
      });
    },
  });
  const { remoteAudioRef, voiceActive, voiceStatusText, handleVoiceToggle, stopRealtimeSession } = voice;
  const chatSpeech = useChatSpeech({
    lessonId: activeLesson?.id ?? null,
    messages: activeMessages,
  });

  useEffect(() => {
    chatScrollEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeLesson?.id, displayedMessages.length, isChatBusy]);

  const clarityStatus: LearningClarificationStatus =
    previewLearningClarity ??
    learningClarity ??
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
  const clarityBarTone =
    clarityStatus.progress >= 90
      ? "bg-emerald-500"
      : clarityStatus.can_start
      ? "bg-blue-500"
      : "bg-amber-500";

  function applySelection(nextSelection: SelectionRef, popoverPosition?: SelectionPopoverPosition | null) {
    setSelection((current) => (sameSelection(current, nextSelection) ? current : nextSelection));
    setSelectionPopover((current) => {
      const nextPosition = popoverPosition ?? null;
      return samePopoverPosition(current, nextPosition) ? current : nextPosition;
    });
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      composerSelection: null,
    }));
  }

  function clearTransientSelection() {
    setSelection((current) => (current ? null : current));
    setSelectionPopover((current) => (current ? null : current));
  }

  function clearSelection() {
    clearTransientSelection();
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: null,
    }));
  }

  function focusComposerWithSelection(nextMode: ChatInteractionMode, explicitSelection?: SelectionRef) {
    const selectionToFocus = explicitSelection ?? selection;
    if (!selectionToFocus) {
      return;
    }
    const normalizedSelection: SelectionRef =
      selectionToFocus.kind === "board"
        ? {
            ...selectionToFocus,
            location_kind: selectionToFocus.location_kind === "insertion_anchor" ? "insertion_anchor" : "target_range",
          }
        : selectionToFocus;
    setSelection(normalizedSelection);
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: nextMode,
      includeSelectionInPrompt: true,
      composerSelection: normalizedSelection,
    }));
    setSelectionPopover(null);
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus();
    });
  }

  function applySourceReference(sourceReference: SelectionRef) {
    if (sourceReference.kind !== "source" || !sourceReference.source_chapter_id) {
      return;
    }
    setSelection(sourceReference);
    setSelectionPopover(null);
    updateActiveLessonComposerState((current) => ({
      ...current,
      composerMode: "ask",
      includeSelectionInPrompt: true,
      composerSelection: sourceReference,
    }));
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus();
    });
  }

  function handleFormulaInkSubmit(payload: FormulaInkEditorSubmitPayload) {
    if (!activeLesson || !textModelReady || isChatBusy || chatRequestInFlightRef.current) {
      return false;
    }
    const formulaSelection: SelectionRef = {
      kind: "board",
      location_kind: payload.selection.locationKind,
      lesson_id: activeLesson.id,
      document_id: payload.selection.documentId,
      excerpt: payload.selection.excerpt,
      before_text: payload.selection.beforeText,
      after_text: payload.selection.afterText,
    };
    setSelection(formulaSelection);
    setSelectionPopover(null);
    const isReplaceAction = payload.action === "replace";
    void handleSubmitChat({
      message: isReplaceAction
        ? "请识别我手写的公式，并把当前选中的公式更改为识别结果。"
        : "请识别我手写的公式，并结合当前选中的公式回答。",
      selection: formulaSelection,
      interaction_mode: isReplaceAction ? "direct_edit" : "ask",
      formula_ink: {
        action: payload.action,
        image_data_url: payload.imageDataUrl,
        source_latex: payload.sourceLatex,
      },
    });
    return true;
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
    history.setPreviewCommitId(null);
    chatAgent.resetAgentState();
  }

  function resetTransientUiForLessonSwitch() {
    history.setPreviewCommitId(null);
    chatAgent.resetAgentState({ clearComposerSelection: false });
    clearTransientSelection();
  }

  const workspaceActions = useWorkspaceActions({
    coursePackage,
    activeLesson,
    lessonMap,
    flushAutoSave,
    updateCoursePackage,
    selectLocalLesson: workspace.selectLocalLesson,
    resetDraftToLesson: boardDraft.resetToLesson,
    resetTransientUi: resetTransientUiForLessonSwitch,
    setError,
    setBusyAction,
    onLessonCreated: () => setIsCreatingLessonInline(false),
  });
  const {
    handleCreateLessonFromName,
    handleCloseLesson,
    handleSelectLesson,
  } = workspaceActions;

  function handleSelectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession("已切换实时语音模型，当前会话已断开");
    }
    selectRealtimeModel(option);
  }

  const flushAutoSaveWithBeaconEffectEvent = useEffectEvent(() => {
    flushAutoSaveWithBeacon("pagehide");
  });

  useEffect(() => {
    function handlePageHide() {
      flushAutoSaveWithBeaconEffectEvent();
    }

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handlePageHide);
    return () => {
      flushAutoSaveWithBeaconEffectEvent();
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handlePageHide);
    };
  }, []);

  async function handleReturnHome() {
    if (!(await flushAutoSave("return-home"))) {
      return;
    }
    router.push("/home");
  }

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{studioTexts.loading}</div>;
  }

  if (!coursePackage) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{studioTexts.packageMissing}</div>;
  }

  const workspaceTitle = coursePackage.title;
  const lessonTabs = (
    <LessonTabs
      texts={studioTexts}
      lessons={openLessons}
      activeLessonId={activeLesson?.id ?? null}
      isCreatingLessonInline={isCreatingLessonInline}
      isBusyCreating={busyAction === "generate"}
      onSelectLesson={(lessonId) => void handleSelectLesson(lessonId)}
      onCloseLesson={(lessonId) => void handleCloseLesson(lessonId)}
      onStartCreateLesson={() => setIsCreatingLessonInline(true)}
      onCancelCreateLesson={() => setIsCreatingLessonInline(false)}
      onCreateLesson={handleCreateLessonFromName}
    />
  );
  const selectionPopoverNode = (
    <SelectionPopover
      selection={selection}
      position={selectionPopover}
      onFocusComposerWithSelection={() => focusComposerWithSelection("ask")}
    />
  );

  if (!activeLesson || !displayedDocument) {
    return (
      <CourseStudioPageShell
        texts={studioTexts}
        workspaceTitle={workspaceTitle}
        topCollapsed={topCollapsed}
        rightSidebarOpen={rightSidebarOpen}
        error={error}
        tabs={lessonTabs}
        selectionPopover={selectionPopoverNode}
        onReturnHome={() => void handleReturnHome()}
        onTopCollapsedChange={setTopCollapsed}
        onRightSidebarOpenChange={setRightSidebarOpen}
        onClearError={() => setError(null)}
      >
        <section className="flex flex-1 items-center justify-center px-6">
          <div className="w-full max-w-xl rounded-[32px] border border-stone-200 bg-white/90 p-10 text-center shadow-[0_24px_70px_rgba(15,23,42,0.08)]">
            <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-[20px] bg-stone-950 text-white">
              <BookOpen className="h-7 w-7" />
            </div>
            <h2 className="mt-6 text-2xl font-semibold tracking-tight text-stone-950">{studioTexts.emptyPackageTitle}</h2>
            <p className="mt-3 text-sm leading-7 text-stone-500">
              {studioTexts.emptyPackageBody}
            </p>
            <div className="mt-8 flex justify-center">
              {isCreatingLessonInline ? (
                <InlineNameForm
                  label={studioTexts.firstPageNameLabel}
                  placeholder={studioTexts.lessonNamePlaceholder}
                  confirmLabel={studioTexts.confirm}
                  cancelLabel={studioTexts.cancel}
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
                  {studioTexts.createFirstPage}
                </button>
              )}
            </div>
          </div>
        </section>
      </CourseStudioPageShell>
    );
  }

  return (
    <CourseStudioPageShell
      texts={studioTexts}
      workspaceTitle={workspaceTitle}
      topCollapsed={topCollapsed}
      rightSidebarOpen={rightSidebarOpen}
      error={error}
      tabs={lessonTabs}
      selectionPopover={selectionPopoverNode}
      onReturnHome={() => void handleReturnHome()}
      onTopCollapsedChange={setTopCollapsed}
      onRightSidebarOpenChange={setRightSidebarOpen}
      onClearError={() => setError(null)}
    >
      <div
        ref={mainContainerRef}
        style={
          {
            "--chat-panel-width": `${chatPanelWidth}px`,
            "--right-sidebar-width": `${rightSidebarWidth}px`,
          } as CSSProperties
        }
        className={clsx(
          "grid min-h-0 flex-1 grid-cols-[var(--chat-panel-width)_minmax(0,1fr)] overflow-hidden transition-[grid-template-columns]",
          isChatPanelResizing || isRightSidebarResizing ? "duration-0" : "duration-300",
          rightSidebarOpen && "xl:grid-cols-[var(--chat-panel-width)_minmax(0,1fr)_var(--right-sidebar-width)]"
        )}
      >
        <CourseStudioChatSidebar
          packageId={coursePackage.id}
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
          clarificationQuestions={clarificationQuestions}
          activeBoardTask={activeBoardTask}
          activeRequirementSheet={activeRequirements}
          currentNeedPending={currentNeedPending}
          latestBoardDecision={latestBoardDecision}
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
          composerAttachments={composerAttachments}
          composerMode={composerMode}
          composerSelection={composerSelection}
          includeSelectionInPrompt={includeSelectionInPrompt}
          onApplySelection={applySelection}
          onContinueTeaching={() => void handleContinueTeaching()}
          onSubmitChat={(payload) => handleSubmitChat(payload)}
          onStopChat={handleStopChat}
          onEditMessage={(message, nextContent) => handleEditMessage(message, nextContent)}
          onSelectTextModel={selectTextModel}
          onSelectRealtimeModel={handleSelectRealtimeModel}
          onVoiceToggle={handleVoiceToggle}
          onSpeakMessage={chatSpeech.speakMessage}
          onExitPreviewMode={exitAnyPreviewMode}
          onClearSelection={clearSelection}
          onUpdateComposerState={updateActiveLessonComposerState}
          onAdjustComposerHeight={adjustComposerHeight}
          onError={setError}
        />

        <BoardEditorPanel
          activeLesson={activeLesson}
          document={displayedDocument}
          isPreviewMode={isPreviewMode}
          isDraftPreviewMode={isDraftPreviewMode}
          previewCommit={previewCommit}
          toolbarCollapsed={topCollapsed}
          onExitPreviewMode={exitAnyPreviewMode}
          onDocumentChange={handleLocalDocumentChange}
          onStructureRemovalIntent={markStructureRemovalIntent}
          onApplySelection={applySelection}
          onClearTransientSelection={clearTransientSelection}
          onImportDocx={(file) => void handleImportDocx(file)}
          onExportDocx={() => void handleExportDocx()}
          onExportHtml={() => void handleExportHtml()}
          onReferenceFormula={(formulaSelection) => focusComposerWithSelection("ask", formulaSelection)}
          onFormulaInkSubmit={handleFormulaInkSubmit}
        />

        <CourseStudioSidePanel
          open={rightSidebarOpen}
          resizeHandleProps={rightSidebarResizeHandleProps}
          isResizing={isRightSidebarResizing}
          sidebarTab={sidebarTab}
          onSidebarTabChange={setSidebarTab}
          onClose={() => setRightSidebarOpen(false)}
          activeLesson={activeLesson}
          packageId={coursePackage.id}
          previewCommit={previewCommit}
          previewCommitId={previewCommitId}
          activeRequirements={activeRequirements}
          activeBoardTask={activeBoardTask}
          latestBoardDecision={latestBoardDecision}
          newBranchName={newBranchName}
          onNewBranchNameChange={setNewBranchName}
          onCreateBranch={() => handleCreateBranch()}
          onPreviewCommit={(commit) => handlePreviewCommit(commit)}
          onRestoreCommit={(commitId) => handleRestoreCommit(commitId)}
          onCreateBranchFromCommit={(commit) => handleCreateBranchFromCommit(commit)}
          onSwitchBranch={(branchName) => handleSwitchBranch(branchName)}
          onError={setError}
          onSourceReference={applySourceReference}
          speechAutoEnabled={chatSpeech.autoSpeakEnabled}
          speechIsLoading={chatSpeech.isSpeechLoading}
          speechIsPlaying={chatSpeech.isSpeechPlaying}
          speechIsPaused={chatSpeech.isSpeechPaused}
          speechStatusText={chatSpeech.speechStatusText}
          speechOptions={chatSpeech.speechOptions}
          speechSelectedVoice={chatSpeech.selectedVoice}
          speechRate={chatSpeech.speechRate}
          speechCurrentModel={chatSpeech.currentModel}
          speechCurrentText={chatSpeech.currentSpeechText}
          speechCurrentTime={chatSpeech.currentTime}
          speechDuration={chatSpeech.duration}
          speechCanSeek={chatSpeech.canSeekSpeech}
          speechCanReplay={chatSpeech.canReplaySpeech}
          onSpeechAutoToggle={chatSpeech.toggleAutoSpeak}
          onSpeechCancel={chatSpeech.stopSpeech}
          onSpeechPause={chatSpeech.pauseSpeech}
          onSpeechResume={chatSpeech.resumeSpeech}
          onSpeechReplay={chatSpeech.replayCurrentSpeech}
          onSpeechSeek={chatSpeech.seekSpeech}
          onSpeechVoiceChange={chatSpeech.selectVoice}
          onSpeechRateChange={chatSpeech.selectSpeechRate}
        />
      </div>
    </CourseStudioPageShell>
  );
}
