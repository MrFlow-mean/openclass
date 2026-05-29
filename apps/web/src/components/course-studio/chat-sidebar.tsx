import clsx from "clsx";
import {
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Radio,
  Send,
  TextQuote,
  Volume2,
  X,
} from "lucide-react";
import type { Dispatch, HTMLAttributes, ReactNode, RefObject, SetStateAction } from "react";

import { CourseChatMessage } from "@/components/chatbot";
import {
  modelButtonLabel,
  modelOptionKey,
  modelSelectionKey,
  PROVIDER_LABELS,
} from "@/components/course-studio/model-catalog";
import { popoverPositionFromDomSelection } from "@/components/course-studio/selection-utils";
import { LearningClarityCard } from "@/components/learning-clarity-card";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardDecision,
  BoardEditPrompt,
  ChatInteractionMode,
  ChatRequestPayload,
  CommitRecord,
  LearningClarificationStatus,
  Lesson,
  ResourceMatch,
  ResourceReferenceContext,
  ResourceReferencePrompt,
  ScopeOption,
  SelectionRef,
  StrongReasoningPrompt,
} from "@/types";
import type { ChatMessage, LessonComposerState } from "@/components/course-studio/history-utils";

type ModelMenu = "text" | "realtime" | null;

type CourseStudioChatSidebarProps = {
  resizeHandleProps: HTMLAttributes<HTMLDivElement>;
  isResizing: boolean;
  clarityBarTone: string;
  clarityStatus: LearningClarificationStatus;
  activeLesson: Lesson;
  targetCommitId: string | null;
  previewCommit: CommitRecord | null;
  displayedMessages: ChatMessage[];
  isPreviewMode: boolean;
  isChatBusy: boolean;
  showReadyForBoardCard: boolean;
  scopeOptions: ScopeOption[];
  referencePrompt: ResourceReferencePrompt | null;
  resourceMatches: ResourceMatch[];
  boardEditPrompt: BoardEditPrompt | null;
  strongReasoningPrompt: StrongReasoningPrompt | null;
  clarificationQuestions: string[];
  latestBoardDecision: BoardDecision | null;
  selectedReference: ResourceReferenceContext | null;
  chatScrollEndRef: RefObject<HTMLDivElement | null>;
  chatInputRef: RefObject<HTMLTextAreaElement | null>;
  remoteAudioRef: RefObject<HTMLAudioElement | null>;
  modelCatalog: AIModelCatalog;
  selectedTextModel: AIModelSelection;
  selectedRealtimeModel: AIModelSelection;
  selectedTextOption: AIModelOption | null;
  selectedRealtimeOption: AIModelOption | null;
  openModelMenu: ModelMenu;
  setOpenModelMenu: Dispatch<SetStateAction<ModelMenu>>;
  voiceActive: boolean;
  voiceStatusText: string;
  chatInput: string;
  composerMode: ChatInteractionMode;
  composerSelection: SelectionRef | null;
  includeSelectionInPrompt: boolean;
  onApplySelection: (selection: SelectionRef, popoverPosition: ReturnType<typeof popoverPositionFromDomSelection>) => void;
  onContinueTeaching: () => void;
  onEditChatTurn: (commitId: string, nextContent: string) => void | Promise<void>;
  onSwitchChatBranch: (branchName: string) => void | Promise<void>;
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
  onScopeAction: (option: ScopeOption) => void | Promise<void>;
  onReferenceAction: (action: "confirm" | "skip") => void | Promise<void>;
  onBoardEditAction: (action: "confirm" | "skip") => void | Promise<void>;
  onStrongReasoningAction: (action: "confirm" | "skip") => void | Promise<void>;
  onSelectTextModel: (option: AIModelOption) => void;
  onSelectRealtimeModel: (option: AIModelOption) => void;
  onVoiceToggle: () => void | Promise<void>;
  onExitPreviewMode: () => void;
  onClearSelection: () => void;
  onUpdateComposerState: (updater: (current: LessonComposerState) => LessonComposerState) => void;
  onAdjustComposerHeight: () => void;
};

export function CourseStudioChatSidebar({
  resizeHandleProps,
  isResizing,
  clarityBarTone,
  clarityStatus,
  activeLesson,
  targetCommitId,
  previewCommit,
  displayedMessages,
  isPreviewMode,
  isChatBusy,
  showReadyForBoardCard,
  scopeOptions,
  referencePrompt,
  resourceMatches,
  boardEditPrompt,
  strongReasoningPrompt,
  clarificationQuestions,
  latestBoardDecision,
  selectedReference,
  chatScrollEndRef,
  chatInputRef,
  remoteAudioRef,
  modelCatalog,
  selectedTextModel,
  selectedRealtimeModel,
  selectedTextOption,
  selectedRealtimeOption,
  openModelMenu,
  setOpenModelMenu,
  voiceActive,
  voiceStatusText,
  chatInput,
  composerMode,
  composerSelection,
  includeSelectionInPrompt,
  onApplySelection,
  onContinueTeaching,
  onEditChatTurn,
  onSwitchChatBranch,
  onSubmitChat,
  onScopeAction,
  onReferenceAction,
  onBoardEditAction,
  onStrongReasoningAction,
  onSelectTextModel,
  onSelectRealtimeModel,
  onVoiceToggle,
  onExitPreviewMode,
  onClearSelection,
  onUpdateComposerState,
  onAdjustComposerHeight,
}: CourseStudioChatSidebarProps) {
  const voiceStartDisabled = !voiceActive && !selectedRealtimeOption?.enabled;
  const referenceEvidenceMatches = referencePrompt
    ? resourceMatches
        .filter(
          (match) =>
            match.resource_id === referencePrompt.resource_id &&
            match.chapter_id === referencePrompt.chapter_id
        )
        .slice(0, 3)
    : [];
  const selectedReferenceTargetChunk =
    selectedReference?.chunks.find((chunk) => chunk.segment_id === selectedReference.segment_id) ??
    selectedReference?.chunks[0] ??
    null;

  return (
    <aside className="relative flex h-full min-h-0 flex-col border-r border-gray-200 bg-white">
      <div
        {...resizeHandleProps}
        className={clsx(
          "group absolute inset-y-0 right-[-6px] z-30 flex w-3 cursor-col-resize items-center justify-center outline-none",
          isResizing && "bg-gray-100/60"
        )}
      >
        <span
          className={clsx(
            "h-14 w-1 rounded-full bg-gray-200 opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100",
            isResizing && "bg-gray-400 opacity-100"
          )}
        />
      </div>
      <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
        <div className="space-y-6">
          <LearningClarityCard
            barTone={clarityBarTone}
            clarityStatus={clarityStatus}
            lesson={activeLesson}
            targetCommitId={targetCommitId}
          />

          <div className="space-y-5">
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
                    onApplySelection(
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
                <CourseChatMessage
                  message={message}
                  isBusy={isChatBusy}
                  onEditMessage={
                    !isPreviewMode && message.commitId
                      ? (_message, nextContent) => onEditChatTurn(message.commitId!, nextContent)
                      : undefined
                  }
                  onSwitchBranch={!isPreviewMode ? onSwitchChatBranch : undefined}
                  onContinueTeaching={
                    !isPreviewMode &&
                    index === displayedMessages.length - 1 &&
                    message.role === "assistant" &&
                    message.teachingProgress?.has_next_section
                      ? onContinueTeaching
                      : undefined
                  }
                />
              </div>
            ))}
            {showReadyForBoardCard ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">学习需求已清晰</p>
                <p className="mt-2 text-sm leading-6 text-emerald-950">
                  {clarityStatus.summary || clarityStatus.reason}
                </p>
                <p className="mt-2 text-xs leading-6 text-emerald-900/80">
                  接下来将基于这份学习需求生成板书，是否开始？
                </p>
                <button
                  type="button"
                  onClick={() =>
                    void onSubmitChat({
                      message: "开始生成板书",
                      interaction_mode: "ask",
                      board_generation_action: "start",
                    })
                  }
                  disabled={isChatBusy}
                  className="mt-3 inline-flex h-9 items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700"
                >
                  开始生成板书
                </button>
              </div>
            ) : null}
            <div ref={chatScrollEndRef} aria-hidden="true" />
          </div>

          {!isPreviewMode && scopeOptions.length ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
              <p className="text-[11px] font-bold uppercase tracking-widest text-amber-700">范围升级建议</p>
              <div className="mt-3 space-y-2">
                {scopeOptions.map((option) => (
                  <button
                    key={option.action}
                    type="button"
                    onClick={() => void onScopeAction(option)}
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
              <p className="text-[11px] font-bold uppercase tracking-widest text-violet-700">资料参考建议</p>
              <p className="mt-2 text-sm leading-6 text-violet-950">{referencePrompt.question}</p>
              <p className="mt-2 text-xs leading-6 text-violet-900/80">{referencePrompt.reason}</p>
              {referenceEvidenceMatches.length ? (
                <div className="mt-3 space-y-3 border-t border-violet-200/80 pt-3">
                  {referenceEvidenceMatches.map((match) => {
                    const headingPath = match.heading_path?.length ? match.heading_path.join(" / ") : match.chapter_title;
                    const excerpt = match.excerpt || match.evidence?.find((item) => item.label.includes("片段"))?.value || "";
                    return (
                      <div key={`${match.resource_id}-${match.segment_id ?? match.chapter_id}`} className="text-xs leading-5 text-violet-950">
                        <div className="flex items-start justify-between gap-3">
                          <p className="min-w-0 font-semibold text-gray-900">
                            <span className="break-words">{match.resource_name} / {headingPath}</span>
                          </p>
                          <span className="shrink-0 font-semibold text-violet-700">{Math.round(match.score * 100)}%</span>
                        </div>
                        {excerpt ? (
                          <p className="mt-2 border-l-2 border-violet-300 pl-3 text-violet-900/90">{excerpt}</p>
                        ) : null}
                        {match.evidence?.length ? (
                          <div className="mt-2 space-y-1 text-[11px] text-violet-900/75">
                            {match.evidence.slice(0, 3).map((item) => (
                              <p key={`${item.label}-${item.value}`} className="break-words">
                                <span className="font-semibold">{item.label}：</span>{item.value}
                              </p>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              <div className="mt-3 grid gap-2">
                <button
                  type="button"
                  onClick={() => void onReferenceAction("confirm")}
                  className="w-full rounded-xl border border-violet-200 bg-white px-4 py-3 text-left transition hover:border-violet-300"
                >
                  <span className="block text-sm font-semibold text-gray-900">
                    {referencePrompt.confirm_label}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => void onReferenceAction("skip")}
                  className="w-full rounded-xl border border-violet-200 bg-white px-4 py-3 text-left transition hover:border-violet-300"
                >
                  <span className="block text-sm font-semibold text-gray-900">
                    {referencePrompt.skip_label}
                  </span>
                </button>
              </div>
            </div>
          ) : null}

          {!isPreviewMode && strongReasoningPrompt ? (
            <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white text-indigo-700 shadow-sm">
                  <BrainCircuit className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-[11px] font-bold uppercase tracking-widest text-indigo-700">深度推理建议</p>
                  <p className="mt-2 text-sm leading-6 text-indigo-950">{strongReasoningPrompt.question}</p>
                  <p className="mt-2 text-xs leading-6 text-indigo-900/80">{strongReasoningPrompt.reason}</p>
                  {strongReasoningPrompt.model_label ? (
                    <p className="mt-2 text-[11px] font-semibold text-indigo-800">
                      模型：{strongReasoningPrompt.model_label}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => void onStrongReasoningAction("confirm")}
                  className="w-full rounded-xl border border-indigo-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-900 transition hover:border-indigo-300"
                >
                  {strongReasoningPrompt.confirm_label}
                </button>
                <button
                  type="button"
                  onClick={() => void onStrongReasoningAction("skip")}
                  className="w-full rounded-xl border border-indigo-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-900 transition hover:border-indigo-300"
                >
                  {strongReasoningPrompt.skip_label}
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
                  onClick={() => void onBoardEditAction("confirm")}
                  className="w-full rounded-xl border border-emerald-200 bg-white px-4 py-3 text-center text-sm font-semibold text-gray-900 transition hover:border-emerald-300"
                >
                  {boardEditPrompt.confirm_label}
                </button>
                <button
                  type="button"
                  onClick={() => void onBoardEditAction("skip")}
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
            <details
              key={`${selectedReference.resource_id}-${selectedReference.chapter_id}-${selectedReference.segment_id ?? "chapter"}`}
              className="group rounded-xl border border-emerald-200 bg-emerald-50 p-3 [&>summary::-webkit-details-marker]:hidden"
            >
              <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="block text-[11px] font-bold uppercase tracking-widest text-emerald-700">
                    已引用参考资料
                  </span>
                  <span className="mt-1 block truncate text-sm font-semibold text-gray-900">
                    {selectedReference.resource_name} / {selectedReference.chapter_title}
                  </span>
                </span>
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-emerald-200 bg-white text-emerald-700 shadow-sm transition-colors group-open:bg-emerald-100">
                  <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
                </span>
              </summary>
              {selectedReferenceTargetChunk?.excerpt ? (
                <p className="mt-3 border-l-2 border-emerald-300 pl-3 text-xs leading-5 text-emerald-900/90">
                  {selectedReferenceTargetChunk.excerpt}
                </p>
              ) : null}
            </details>
          ) : null}
        </div>
      </div>

      <div className="shrink-0 border-t border-gray-100 bg-white px-3 py-3">
        <div className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] items-center gap-2">
          <ModelPicker
            kind="text"
            label="文本生成"
            icon={<BrainCircuit className="h-4 w-4 shrink-0 text-gray-600" />}
            openModelMenu={openModelMenu}
            setOpenModelMenu={setOpenModelMenu}
            selectedModel={selectedTextModel}
            selectedOption={selectedTextOption}
            options={modelCatalog.text}
            onSelect={onSelectTextModel}
          />
          <ModelPicker
            kind="realtime"
            label="语音模型"
            icon={<Volume2 className="h-4 w-4 shrink-0 text-gray-600" />}
            openModelMenu={openModelMenu}
            setOpenModelMenu={setOpenModelMenu}
            selectedModel={selectedRealtimeModel}
            selectedOption={selectedRealtimeOption}
            options={modelCatalog.realtime}
            onSelect={onSelectRealtimeModel}
          />

          <button
            type="button"
            onClick={() => void onVoiceToggle()}
            disabled={voiceStartDisabled}
            title={voiceStatusText}
            className={clsx(
              "flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-sm transition-all",
              voiceStartDisabled
                ? "cursor-not-allowed bg-gray-200 text-gray-500 shadow-none"
                : "hover:scale-105 hover:shadow-md",
              voiceActive ? "bg-gray-800 ring-2 ring-gray-200" : !voiceStartDisabled && "bg-[#1a1a1a]"
            )}
          >
            {voiceActive ? <Radio className="h-4.5 w-4.5" /> : <Volume2 className="h-4.5 w-4.5" />}
          </button>
        </div>
        <p className="mb-2 min-h-4 px-1 text-center text-[10px] leading-4 text-gray-500">{voiceStatusText}</p>
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
                onClick={onClearSelection}
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
                onExitPreviewMode();
              }
            }}
            onChange={(event) =>
              onUpdateComposerState((current) => ({
                ...current,
                chatInput: event.target.value,
              }))
            }
            onInput={() => onAdjustComposerHeight()}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void onSubmitChat();
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
                      : "给 OpenClass 发消息..."
            }
            className="custom-scrollbar block w-full resize-none border-0 bg-transparent px-3.5 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-gray-400 disabled:cursor-wait disabled:text-gray-400"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <div className="flex shrink-0 items-center gap-1 rounded-md border border-gray-200 bg-gray-50 p-0.5">
                <button
                  type="button"
                  onClick={() =>
                    onUpdateComposerState((current) => ({
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
                    onUpdateComposerState((current) => ({
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
                    onUpdateComposerState((current) => ({
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
              onClick={() => void onSubmitChat()}
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
  );
}

function ModelPicker({
  kind,
  label,
  icon,
  openModelMenu,
  setOpenModelMenu,
  selectedModel,
  selectedOption,
  options,
  onSelect,
}: {
  kind: "text" | "realtime";
  label: string;
  icon: ReactNode;
  openModelMenu: ModelMenu;
  setOpenModelMenu: Dispatch<SetStateAction<ModelMenu>>;
  selectedModel: AIModelSelection;
  selectedOption: AIModelOption | null;
  options: AIModelOption[];
  onSelect: (option: AIModelOption) => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={openModelMenu === kind}
        aria-label={`${label}，当前模型 ${modelButtonLabel(selectedOption, selectedModel)}`}
        onClick={() => setOpenModelMenu((current) => (current === kind ? null : kind))}
        className="flex h-10 w-full items-center justify-between gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 text-left transition-colors hover:border-gray-300 hover:bg-white"
      >
        <span className="flex min-w-0 items-center gap-2">
          {icon}
          <span className="truncate text-xs font-semibold text-gray-900">{label}</span>
        </span>
        <ChevronDown
          className={clsx(
            "h-4 w-4 shrink-0 text-gray-500 transition-transform",
            openModelMenu === kind && "rotate-180"
          )}
        />
      </button>

      {openModelMenu === kind ? (
        <div className="absolute bottom-full left-0 z-30 mb-2 max-h-[360px] w-[min(336px,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-gray-200 bg-white p-2 shadow-xl">
          <div className="space-y-1">
            {options.length === 0 ? (
              <p className="px-2 py-2 text-xs leading-5 text-gray-500">
                {kind === "realtime" ? "实时语音未启用" : "暂无可用模型"}
              </p>
            ) : null}
            {options.map((option) => {
              const selected = modelOptionKey(option) === modelSelectionKey(selectedModel);
              return (
                <button
                  key={`${kind}-${modelOptionKey(option)}`}
                  type="button"
                  onClick={() => onSelect(option)}
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
  );
}
