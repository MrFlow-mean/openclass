import clsx from "clsx";
import {
  AudioLines,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Send,
  Square,
  TextQuote,
  Volume2,
  X,
} from "lucide-react";
import { useState, type Dispatch, type HTMLAttributes, type ReactNode, type RefObject, type SetStateAction } from "react";

import { CourseChatMessage } from "@/components/chatbot";
import { BoardGenerationConfirmationCard } from "@/components/course-studio/board-generation-confirmation-card";
import {
  modelButtonLabel,
  modelOptionKey,
  modelSelectionKey,
  PROVIDER_LABELS,
} from "@/components/course-studio/model-catalog";
import { latestExecutedNeedSnapshot } from "@/components/course-studio/current-need-snapshot";
import { popoverPositionFromDomSelection } from "@/components/course-studio/selection-utils";
import { LearningClarityCard } from "@/components/learning-clarity-card";
import { boardWorkflowLabel } from "@/lib/learning-requirement-display";
import type {
  AIModelCatalog,
  AIModelOption,
  AIModelSelection,
  BoardDecision,
  BoardEditPrompt,
  BoardTaskRequirementSheet,
  ChatInteractionMode,
  ChatRequestPayload,
  CommitRecord,
  EvidenceBundle,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  ScopeOption,
  SelectionRef,
} from "@/types";
import type { ChatMessage, LessonComposerState } from "@/components/course-studio/history-utils";

type ModelMenu = "text" | "realtime" | null;

const BOARD_TASK_ACTION_LABELS: Partial<Record<NonNullable<BoardTaskRequirementSheet["requested_action"]>, string>> = {
  write: "写入",
  edit: "修改",
  explain: "讲解",
  chat: "互动",
};

function boardTaskActionLabel(action: BoardTaskRequirementSheet["requested_action"]) {
  return action ? BOARD_TASK_ACTION_LABELS[action] ?? action : "待确认";
}

function boardTaskLocationLabel(task: BoardTaskRequirementSheet) {
  const kindLabels: Record<NonNullable<BoardTaskRequirementSheet["location_kind"]>, string> = {
    target_range: "目标范围",
    insertion_anchor: "插入位置",
    unspecified: "待确认",
  };
  const kind = kindLabels[task.location_kind ?? "unspecified"];
  const hint = task.target_hint || task.target_location?.display_label || task.location_status;
  return `${kind} · ${hint}`;
}

function composerSelectionLabel(selection: SelectionRef) {
  if (selection.kind === "source") {
    return "资料章节";
  }
  if (selection.kind === "board" && selection.location_kind === "target_range") {
    return "TargetRange";
  }
  if (selection.kind === "board" && selection.location_kind === "insertion_anchor") {
    return "InsertionAnchor";
  }
  return selection.kind === "board" ? "板书选区" : "对话引用";
}

function composerSelectionToggleLabel(selection: SelectionRef, included: boolean) {
  if (selection.kind === "source") {
    return included ? "包含资料" : "忽略资料";
  }
  return included ? "包含选区" : "忽略选区";
}

function sequenceFocusLabel(lesson: Lesson) {
  const session = lesson.active_interaction_session;
  if (!session || session.sequence_mode !== "section_explanation" || !session.sequence_items?.length) {
    return null;
  }
  const total = session.sequence_items.length;
  const index = Math.max(0, Math.min(session.sequence_index ?? 0, total - 1));
  const focus = session.sequence_items[index] ?? session.target_focus;
  const current = index + 1;
  const label = focus?.display_label || focus?.heading_path?.join(" / ") || session.interaction_goal || "当前子节";
  return {
    current,
    label,
    progress: Math.max(0, Math.min(100, Math.round((current / total) * 100))),
    session,
    total,
  };
}

function hasVisibleLearningClarity(
  clarityStatus: LearningClarificationStatus,
  activeRequirementSheet: LearningRequirementSheet | null
) {
  return (
    clarityStatus.progress > 0 ||
    clarityStatus.ready_for_board ||
    Boolean(clarityStatus.summary.trim()) ||
    clarityStatus.key_facts.length > 0 ||
    clarityStatus.checklist.length > 0 ||
    Boolean(activeRequirementSheet?.work_mode && activeRequirementSheet.work_mode !== "unknown")
  );
}

function CurrentNeedCard({
  activeBoardTask,
  activeRequirementSheet,
  barTone,
  clarityStatus,
  currentNeedPending,
  isChatBusy,
  lesson,
  targetCommitId,
}: {
  activeBoardTask: BoardTaskRequirementSheet | null;
  activeRequirementSheet: LearningRequirementSheet | null;
  barTone: string;
  clarityStatus: LearningClarificationStatus;
  currentNeedPending: boolean;
  isChatBusy: boolean;
  lesson: Lesson;
  targetCommitId: string | null;
}) {
  if (currentNeedPending) {
    return (
      <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">当前任务</p>
          <span className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-sky-700">
            <LoaderCircle className="h-3 w-3 animate-spin" />
            识别中
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-1/3 rounded-full bg-sky-500 transition-all" />
        </div>
        <p className="mt-3 text-xs leading-6 text-sky-950">正在把你的新问题整理成位置、动作和怎么做。</p>
      </div>
    );
  }

  const executedNeed =
    !activeBoardTask && !activeRequirementSheet && !currentNeedPending
      ? latestExecutedNeedSnapshot(lesson, targetCommitId)
      : null;

  const sequence = sequenceFocusLabel(lesson);
  if (sequence) {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">当前顺序讲解</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
            {sequence.current}/{sequence.total}
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${sequence.progress}%` }} />
        </div>
        <div className="mt-3 grid gap-2 text-xs leading-5 text-emerald-950">
          <p>当前：{sequence.label}</p>
          <p>目标：{sequence.session.interaction_goal || "按板书子节顺序讲解"}</p>
          <p>状态：{isChatBusy ? "讲解中" : "等待确认是否继续"}</p>
        </div>
      </div>
    );
  }

  if (executedNeed?.kind === "board_task") {
    const task = executedNeed.boardTask;
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
            已被执行
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-full rounded-full bg-emerald-500 transition-all" />
        </div>
        <div className="mt-3 grid gap-2 text-xs leading-5 text-emerald-950">
          <p>链路：{boardWorkflowLabel(task.board_workflow ?? "act_on_existing_board")}</p>
          <p>位置：{boardTaskLocationLabel(task)}</p>
          <p>动作：{boardTaskActionLabel(task.requested_action)}</p>
          <p>内容：{task.question_or_topic || "已执行的板书任务"}</p>
          <p>
            互动：
            {task.interaction_rule_draft?.rule_text || (task.requested_action === "chat" ? "按已启动规则" : "无特殊规则")}
          </p>
        </div>
      </div>
    );
  }

  if (executedNeed?.kind === "learning_requirement") {
    return (
      <LearningClarityCard
        activeRequirementSheet={executedNeed.requirementSheet}
        barTone="bg-emerald-500"
        clarityStatus={executedNeed.clarityStatus}
        lesson={lesson}
        statusLabelOverride="已被执行"
        targetCommitId={executedNeed.commit.id}
      />
    );
  }

  if (activeBoardTask) {
    const progress = Math.max(0, Math.min(100, activeBoardTask.progress));
    const statusLabel = isChatBusy ? "执行中" : progress >= 100 ? "已完成" : "收集中";
    return (
      <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-sky-700">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-sky-700">
            {statusLabel} · {progress}%
          </span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-3 grid gap-2 text-xs leading-5 text-sky-950">
          <p>链路：{boardWorkflowLabel(activeBoardTask.board_workflow ?? "act_on_existing_board")}</p>
          <p>位置：{boardTaskLocationLabel(activeBoardTask)}</p>
          <p>动作：{boardTaskActionLabel(activeBoardTask.requested_action)}</p>
          <p>内容：{activeBoardTask.question_or_topic || "待确认"}</p>
          <p>
            互动：
            {activeBoardTask.interaction_rule_draft?.rule_text ||
              (activeBoardTask.requested_action === "chat" ? "待确认" : "无特殊规则")}
          </p>
        </div>
        {activeBoardTask.confirmation_status === "awaiting" ? (
          <p className="mt-3 text-xs leading-6 text-sky-900">等待你确认是否先扩写板书。</p>
        ) : null}
        {activeBoardTask.missing_items.length ? (
          <p className="mt-3 text-xs leading-6 text-sky-900">待补充：{activeBoardTask.missing_items.join("、")}</p>
        ) : null}
      </div>
    );
  }

  if (hasVisibleLearningClarity(clarityStatus, activeRequirementSheet)) {
    return (
      <LearningClarityCard
        activeRequirementSheet={activeRequirementSheet}
        barTone={barTone}
        clarityStatus={clarityStatus}
        lesson={lesson}
        targetCommitId={targetCommitId}
      />
    );
  }

  if (lesson.board_document.content_text.trim()) {
    return (
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-widest text-gray-600">当前任务</p>
          <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-gray-600">待输入</span>
        </div>
        <div className="mt-3 h-2 rounded-full bg-white">
          <div className="h-full w-0 rounded-full bg-gray-400" />
        </div>
        <p className="mt-3 text-xs leading-6 text-gray-700">等待新的板书任务。</p>
      </div>
    );
  }

  return (
    <LearningClarityCard
      activeRequirementSheet={activeRequirementSheet}
      barTone={barTone}
      clarityStatus={clarityStatus}
      lesson={lesson}
      targetCommitId={targetCommitId}
    />
  );
}

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
  isPendingEvidenceLoading: boolean;
  scopeOptions: ScopeOption[];
  boardEditPrompt: BoardEditPrompt | null;
  candidateEvidenceBundle: EvidenceBundle | null;
  clarificationQuestions: string[];
  activeBoardTask: BoardTaskRequirementSheet | null;
  activeRequirementSheet: LearningRequirementSheet | null;
  currentNeedPending: boolean;
  latestBoardDecision: BoardDecision | null;
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
  onSubmitChat: (payload?: ChatRequestPayload) => void | Promise<void>;
  onStopChat: () => void;
  onEditMessage: (message: ChatMessage, nextContent: string) => void | Promise<void>;
  onScopeAction: (option: ScopeOption) => void | Promise<void>;
  onBoardEditAction: (action: "confirm" | "skip") => void | Promise<void>;
  onEvidenceAction: (bundleId: string, action: "confirm" | "skip") => void | Promise<void>;
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
  isPendingEvidenceLoading,
  scopeOptions,
  boardEditPrompt,
  candidateEvidenceBundle,
  clarificationQuestions,
  activeBoardTask,
  activeRequirementSheet,
  currentNeedPending,
  latestBoardDecision,
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
  onSubmitChat,
  onStopChat,
  onEditMessage,
  onScopeAction,
  onBoardEditAction,
  onEvidenceAction,
  onSelectTextModel,
  onSelectRealtimeModel,
  onVoiceToggle,
  onExitPreviewMode,
  onClearSelection,
  onUpdateComposerState,
  onAdjustComposerHeight,
}: CourseStudioChatSidebarProps) {
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");

  function startEditingMessage(message: ChatMessage) {
    setEditingMessageId(message.id);
    setEditingMessageContent(message.editableContent ?? message.content);
  }

  async function submitEditedMessage(message: ChatMessage) {
    const nextContent = editingMessageContent.trim();
    if (!nextContent || isChatBusy) {
      return;
    }
    await onEditMessage(message, nextContent);
    setEditingMessageId(null);
    setEditingMessageContent("");
  }

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
          <CurrentNeedCard
            activeBoardTask={!isPreviewMode ? activeBoardTask : null}
            activeRequirementSheet={!isPreviewMode ? activeRequirementSheet : null}
            barTone={clarityBarTone}
            clarityStatus={clarityStatus}
            currentNeedPending={!isPreviewMode && currentNeedPending}
            isChatBusy={isChatBusy}
            lesson={activeLesson}
            targetCommitId={targetCommitId}
          />
          {!isPreviewMode &&
          !activeBoardTask &&
          activeLesson?.active_interaction_session &&
          activeLesson.active_interaction_session.sequence_mode !== "section_explanation" ? (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-bold uppercase tracking-widest text-emerald-700">互动规则</p>
                <span className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-emerald-700">
                  {activeLesson.active_interaction_session.turn_count}
                </span>
              </div>
              <div className="mt-3 grid gap-2 text-xs leading-5 text-emerald-950">
                <p>规则：{activeLesson.active_interaction_session.rule_text || "待确认"}</p>
                <p>目标：{activeLesson.active_interaction_session.interaction_goal || "当前板书内容"}</p>
                <p>
                  合规输入：
                  {activeLesson.active_interaction_session.compliant_input_rule ||
                    activeLesson.active_interaction_session.expected_user_behavior ||
                    "按当前规则回应"}
                </p>
                {activeLesson.active_interaction_session.rule_steps?.length ? (
                  <p>
                    进度：
                    {Math.min(
                      (activeLesson.active_interaction_session.current_step_index ?? 0) + 1,
                      activeLesson.active_interaction_session.rule_steps.length
                    )}{" "}
                    / {activeLesson.active_interaction_session.rule_steps.length}
                  </p>
                ) : null}
                {activeLesson.active_interaction_session.progress_note ? (
                  <p>状态：{activeLesson.active_interaction_session.progress_note}</p>
                ) : null}
                {activeLesson.active_interaction_session.last_violation_reason ? (
                  <p>最近纠错：{activeLesson.active_interaction_session.last_violation_reason}</p>
                ) : null}
              </div>
            </div>
          ) : null}

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
                  onStartEdit={
                    !isPreviewMode &&
                    !isChatBusy &&
                    message.role === "user" &&
                    Boolean(message.commitId && message.parentCommitIds?.[0])
                      ? () => startEditingMessage(message)
                      : undefined
                  }
                  isEditing={editingMessageId === message.id}
                  editingContent={editingMessageContent}
                  onEditingContentChange={setEditingMessageContent}
                  onCancelEdit={() => {
                    setEditingMessageId(null);
                    setEditingMessageContent("");
                  }}
                  onSubmitEdit={() => void submitEditedMessage(message)}
                  isEditDisabled={!editingMessageContent.trim() || isChatBusy}
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
            {showReadyForBoardCard || (activeBoardTask?.requested_action && ["write", "edit"].includes(activeBoardTask.requested_action) && activeBoardTask.progress >= 100 && activeBoardTask.missing_items.length === 0 && !activeBoardTask.clarification_question.trim() && candidateEvidenceBundle?.purpose === "board_edit") ? (
              <BoardGenerationConfirmationCard
                clarityStatus={clarityStatus}
                boardTask={candidateEvidenceBundle?.purpose === "board_edit" ? activeBoardTask : null}
                isChatBusy={isChatBusy}
                isPendingEvidenceLoading={isPendingEvidenceLoading}
                candidateEvidenceBundle={
                  candidateEvidenceBundle?.purpose === "board_generation" || candidateEvidenceBundle?.purpose === "board_edit"
                    ? candidateEvidenceBundle
                    : null
                }
                onSubmitChat={onSubmitChat}
                onEvidenceAction={onEvidenceAction}
              />
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
            aria-label={voiceStatusText}
            title={voiceStatusText}
            className={clsx(
              "flex h-10 w-10 items-center justify-center rounded-full text-white shadow-sm transition-all hover:scale-105 hover:shadow-md",
              voiceActive ? "bg-gray-800 ring-2 ring-gray-200" : "bg-[#1a1a1a]"
            )}
          >
            <AudioLines className="h-4.5 w-4.5" />
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
                <span className="shrink-0 rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-bold text-sky-700">
                  {composerSelectionLabel(composerSelection)}
                </span>
                <p className="min-w-0 truncate text-xs leading-5 text-gray-700">
                  “{composerSelection.excerpt.replace(/\s+/g, " ").slice(0, 160)}”
                </p>
              </div>
              <button
                type="button"
                onClick={onClearSelection}
                aria-label="移除引用"
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
                if (isChatBusy) {
                  return;
                }
                event.preventDefault();
                void onSubmitChat();
              }
            }}
            placeholder={
              isPreviewMode
                  ? "点击输入会回到当前版本并继续对话"
                  : composerMode === "direct_edit"
                    ? "描述要怎么改这段板书，或直接说“重写整篇”..."
                    : composerSelection
                      ? composerSelection.kind === "source"
                        ? "基于引用章节继续提问"
                        : "基于选中内容继续追问"
                      : "给 OpenClass 发消息..."
            }
            className="custom-scrollbar block w-full resize-none border-0 bg-transparent px-3.5 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-gray-400"
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <div className="flex shrink-0 items-center gap-1 rounded-md border border-gray-200 bg-gray-50 p-0.5">
                <button
                  type="button"
                  aria-label="Ask Mode"
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
                  aria-label="Agent Edit Mode"
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
                  {composerSelectionToggleLabel(composerSelection, includeSelectionInPrompt)}
                </button>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => {
                if (isChatBusy) {
                  onStopChat();
                  return;
                }
                void onSubmitChat();
              }}
              aria-label={isChatBusy ? "停止回复" : "发送消息"}
              title={isChatBusy ? "停止回复" : "发送消息"}
              disabled={!isChatBusy && !chatInput.trim()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#1a1a1a] text-white shadow-sm transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isChatBusy ? (
                <Square className="h-3.5 w-3.5 fill-current" />
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
