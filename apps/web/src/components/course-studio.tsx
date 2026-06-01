"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useRef, useState, type CSSProperties } from "react";
import { BookOpen, Plus } from "lucide-react";

import { BoardEditorPanel } from "@/components/course-studio/board-editor-panel";
import { CourseStudioChatSidebar } from "@/components/course-studio/chat-sidebar";
import { CourseStudioPageShell } from "@/components/course-studio/course-studio-page-shell";
import {
  buildLessonMessagesFromHistory,
  isBoardDocumentEmpty,
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
import { useBoardDraft } from "@/hooks/course-studio/use-board-draft";
import { useCourseWorkspace, type CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import { useLessonChatAgent } from "@/hooks/course-studio/use-lesson-chat-agent";
import { useLessonHistory } from "@/hooks/course-studio/use-lesson-history";
import { useModelCatalog } from "@/hooks/course-studio/use-model-catalog";
import { useRealtimeVoice } from "@/hooks/course-studio/use-realtime-voice";
import { useWorkspaceActions } from "@/hooks/course-studio/use-workspace-actions";
import { InlineNameForm } from "@/components/inline-name-form";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { useResizablePanelWidth } from "@/hooks/use-resizable-panel-width";
import type { AIModelOption, ChatInteractionMode, CoursePackage, LearningClarificationStatus, SelectionRef } from "@/types";

const CHAT_PANEL_WIDTH_STORAGE_KEY = "openclass:studio:chat-panel-width";
const CHAT_PANEL_DEFAULT_WIDTH = 380;
const CHAT_PANEL_MIN_WIDTH = 300;
const CHAT_PANEL_MAX_WIDTH = 640;
const RIGHT_SIDEBAR_WIDTH_STORAGE_KEY = "openclass:studio:right-sidebar-width";
const RIGHT_SIDEBAR_DEFAULT_WIDTH = 360;
const RIGHT_SIDEBAR_MIN_WIDTH = 300;
const RIGHT_SIDEBAR_MAX_WIDTH = 640;

export function CourseStudio() {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const s = txt.studio;
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
    ariaLabel: s.resizeChatbot,
    title: s.resizeChatbot,
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
    resizeDirection: "left",
    ariaLabel: s.resizeSidebar,
    title: s.resizeSidebar,
  });
  const [sidebarTab, setSidebarTab] = useState<CourseStudioSidebarTab>("graph");
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
    handleImportDocx,
    handleExportDocx,
  } = boardDraft;
  const {
    previewCommitId,
    newBranchName,
    mergePreview,
    mergeChoices,
    setNewBranchName,
    handlePreviewCommit,
    exitPreviewMode,
    handleCreateBranchFromCommit,
    handleSwitchBranch,
    handleOpenMergePreview,
    handleMergeChoiceChange,
    handleCancelMerge,
    handleConfirmMerge,
  } = history;

  const previewCommit = history.previewCommit;
  const activeHeadCommit = history.activeHeadCommit;
  const isPreviewMode = history.isPreviewMode;
  const displayedDocument = boardDraft.displayedDocument;
  const displayedMessages =
    activeLesson && previewCommit ? buildLessonMessagesFromHistory(activeLesson, previewCommit.id) : activeMessages;
  const activeRequirements = activeLesson?.learning_requirements ?? null;
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
  const chatAgent = useLessonChatAgent({
    activeLesson,
    activeMessages,
    activeComposerState,
    composerSelection,
    currentBoardDocument: displayedDocument,
    selectedTextModel,
    isPreviewMode,
    chatRequestInFlightRef,
    flushAutoSave,
    exitPreviewMode,
    updateCoursePackage,
    updateLessonMessages,
    updateLessonComposerState,
    clearSelection,
    setStreamingDocumentPreview: boardDraft.setStreamingDocumentPreview,
    setError,
    setBusyAction,
    busyAction,
    onSpeakResponse: speakChatbotResponse,
  });
  const {
    chatInput,
    composerMode,
    includeSelectionInPrompt,
    isChatBusy,
    scopeOptions,
    clarificationQuestions,
    learningClarity,
    latestBoardDecision,
    referencePrompt,
    documentEvidence,
    resourceMatches,
    boardEditPrompt,
    strongReasoningPrompt,
    selectedReference,
    handleSubmitChat,
    handleEditChatTurn,
    handleScopeAction,
    handleReferenceAction,
    handleDocumentEvidenceAction,
    handleBoardEditAction,
    handleStrongReasoningAction,
    handleContinueTeaching,
  } = chatAgent;
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
      void handleSubmitChat(
        {
          message,
          interaction_mode: "ask",
        },
        { speakResponse: true }
      );
    },
  });
  const { remoteAudioRef, voiceActive, voiceStatusText, handleVoiceToggle, stopRealtimeSession } = voice;

  useEffect(() => {
    chatScrollEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeLesson?.id, displayedMessages.length, isChatBusy]);

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

  function speakChatbotResponse(content: string) {
    voice.speakControlledChatbotMessage(content);
    voice.setVoiceStatusText(s.chatbotSpoken);
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

  const workspaceActions = useWorkspaceActions({
    coursePackage,
    activeLesson,
    lessonMap,
    flushAutoSave,
    updateCoursePackage,
    selectLocalLesson: workspace.selectLocalLesson,
    resetDraftToLesson: boardDraft.resetToLesson,
    resetTransientUi,
    setError,
    setBusyAction,
    onLessonCreated: () => setIsCreatingLessonInline(false),
  });
  const {
    handleCreateLessonFromName,
    handleOpenLesson,
    handleCloseLesson,
    handleUploadResource,
    handleDeleteResource,
    handleSelectLesson,
  } = workspaceActions;

  function handleSelectRealtimeModel(option: AIModelOption) {
    if (!option.enabled) {
      return;
    }
    if (voiceActive || busyAction === "voice-connect") {
      stopRealtimeSession(s.voiceModelSwitched);
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
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{s.loading}</div>;
  }

  if (!coursePackage) {
    return <div className="flex min-h-screen items-center justify-center text-gray-500">{s.noCourse}</div>;
  }

  const workspaceTitle = coursePackage.title;
  const lessonTabs = (
    <LessonTabs
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
      isPreviewMode={isPreviewMode}
      onFocusComposerWithSelection={focusComposerWithSelection}
    />
  );

  if (!activeLesson || !displayedDocument) {
    return (
      <CourseStudioPageShell
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
            <h2 className="mt-6 text-2xl font-semibold tracking-tight text-stone-950">{s.emptyPackageTitle}</h2>
            <p className="mt-3 text-sm leading-7 text-stone-500">
              {s.emptyPackageBody}
            </p>
            <div className="mt-8 flex justify-center">
              {isCreatingLessonInline ? (
                <InlineNameForm
                  label={s.firstPageLabel}
                  placeholder={s.firstPagePlaceholder}
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
                  {s.createFirstPage}
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
          documentEvidence={documentEvidence}
          resourceMatches={resourceMatches}
          boardEditPrompt={boardEditPrompt}
          strongReasoningPrompt={strongReasoningPrompt}
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
          onEditChatTurn={(commitId, nextContent) => handleEditChatTurn(commitId, nextContent)}
          onSwitchChatBranch={(branchName) => handleSwitchBranch(branchName)}
          onSubmitChat={(payload) => handleSubmitChat(payload)}
          onScopeAction={(option) => handleScopeAction(option)}
          onReferenceAction={(action) => handleReferenceAction(action)}
          onDocumentEvidenceAction={(evidence, action) => handleDocumentEvidenceAction(evidence, action)}
          onBoardEditAction={(action) => handleBoardEditAction(action)}
          onStrongReasoningAction={(action) => handleStrongReasoningAction(action)}
          onSelectTextModel={selectTextModel}
          onSelectRealtimeModel={handleSelectRealtimeModel}
          onVoiceToggle={handleVoiceToggle}
          onExitPreviewMode={exitPreviewMode}
          onClearSelection={clearSelection}
          onUpdateComposerState={updateActiveLessonComposerState}
          onAdjustComposerHeight={adjustComposerHeight}
        />

        <BoardEditorPanel
          activeLesson={activeLesson}
          document={displayedDocument}
          isPreviewMode={isPreviewMode}
          previewCommit={previewCommit}
          toolbarCollapsed={topCollapsed}
          onExitPreviewMode={exitPreviewMode}
          onDocumentChange={handleLocalDocumentChange}
          onApplySelection={applySelection}
          onClearSelection={clearSelection}
          onImportDocx={(file) => void handleImportDocx(file)}
          onExportDocx={() => void handleExportDocx()}
        />

        <CourseStudioSidePanel
          resizeHandleProps={rightSidebarResizeHandleProps}
          isResizing={isRightSidebarResizing}
          open={rightSidebarOpen}
          sidebarTab={sidebarTab}
          onSidebarTabChange={setSidebarTab}
          onClose={() => setRightSidebarOpen(false)}
          activeLesson={activeLesson}
          previewCommitId={previewCommitId}
          activeRequirements={activeRequirements}
          latestBoardDecision={latestBoardDecision}
          newBranchName={newBranchName}
          mergePreview={mergePreview}
          mergeChoices={mergeChoices}
          onNewBranchNameChange={setNewBranchName}
          busyAction={busyAction}
          resources={coursePackage.resources}
          relatedEdges={relatedEdges}
          lessonMap={lessonMap}
          onPreviewCommit={(commit) => handlePreviewCommit(commit)}
          onCreateBranchFromCommit={(commit) => handleCreateBranchFromCommit(commit)}
          onSwitchBranch={(branchName) => handleSwitchBranch(branchName)}
          onOpenMergePreview={(branchName) => handleOpenMergePreview(branchName)}
          onMergeChoiceChange={(section, choice) => handleMergeChoiceChange(section, choice)}
          onCancelMerge={() => handleCancelMerge()}
          onConfirmMerge={() => handleConfirmMerge()}
          onUploadResource={(file) => handleUploadResource(file)}
          onDeleteResource={(resourceId, resourceName) => handleDeleteResource(resourceId, resourceName)}
          onOpenLesson={(lessonId) => handleOpenLesson(lessonId)}
        />
      </div>
    </CourseStudioPageShell>
  );
}
