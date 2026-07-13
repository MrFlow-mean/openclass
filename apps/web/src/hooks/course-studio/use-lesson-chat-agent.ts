"use client";

import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api, isMissingChatStreamFinalError } from "@/lib/api";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  createChatMessage,
  isBoardDocumentEmpty,
  learningClarityFromCommit,
  nextEditBranchName,
  type ChatMessage,
  type LessonComposerState,
} from "@/components/course-studio/history-utils";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type {
  AgentActivityEvent,
  AIModelSelection,
  BoardDocument,
  BoardDecision,
  BoardEditPrompt,
  BoardTaskRequirementSheet,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  EvidenceBundle,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  ScopeOption,
  SelectionRef,
} from "@/types";

type UseLessonChatAgentOptions = {
  activeLesson: Lesson | null;
  activeMessages: ChatMessage[];
  activeComposerState: LessonComposerState;
  composerSelection: SelectionRef | null;
  currentBoardDocument: BoardDocument | null;
  selectedTextModel: AIModelSelection;
  selectedBoardModel: AIModelSelection;
  isPreviewMode: boolean;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  exitPreviewMode: () => void;
  updateCoursePackage: (
    nextPackage: CoursePackage,
    options?: CoursePackageApplyOptions
  ) => { activeLesson: Lesson | null } | void;
  updateLessonMessages: (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  updateLessonComposerState: (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => void;
  setStreamingDocumentPreview: (lessonId: string, document: BoardDocument) => boolean;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
  onSpeakResponse: (content: string) => void;
};

type CandidateEvidenceState = {
  lessonId: string;
  bundle: EvidenceBundle | null;
};

const DEFAULT_LEARNING_REQUIREMENT_FAILURE_REASON = "本轮学习需求没有成功更新，请重试刚才的输入。";

export function recoveredLearningRequirementFailureReason(commit: CommitRecord | null): string | null {
  const metadata = commit?.metadata;
  if (
    metadata?.learning_requirement_operation_status !== "failed" &&
    metadata?.refinement_route !== "refinement_failed"
  ) {
    return null;
  }
  const failureReason = metadata.learning_requirement_operation_failure_reason;
  return typeof failureReason === "string" && failureReason.trim()
    ? failureReason.trim()
    : DEFAULT_LEARNING_REQUIREMENT_FAILURE_REASON;
}

export function useLessonChatAgent({
  activeLesson,
  activeMessages,
  activeComposerState,
  composerSelection,
  currentBoardDocument,
  selectedTextModel,
  selectedBoardModel,
  isPreviewMode,
  chatRequestInFlightRef,
  flushAutoSave,
  exitPreviewMode,
  updateCoursePackage,
  updateLessonMessages,
  updateLessonComposerState,
  setStreamingDocumentPreview,
  clearSelection,
  setError,
  setBusyAction,
  busyAction,
  onSpeakResponse,
}: UseLessonChatAgentOptions) {
  const [scopeOptions, setScopeOptions] = useState<ScopeOption[]>([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [streamedRequirementSheet, setStreamedRequirementSheet] = useState<LearningRequirementSheet | null>(null);
  const [streamedBoardTaskSheet, setStreamedBoardTaskSheet] = useState<BoardTaskRequirementSheet | null>(null);
  const [currentNeedPending, setCurrentNeedPending] = useState(false);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const [boardEditPrompt, setBoardEditPrompt] = useState<BoardEditPrompt | null>(null);
  const [candidateEvidenceState, setCandidateEvidenceState] = useState<CandidateEvidenceState | null>(null);
  const [lastEvidenceRequest, setLastEvidenceRequest] = useState<ChatRequestPayload | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [lastBoardEditRequest, setLastBoardEditRequest] = useState<ChatRequestPayload | null>(null);
  const activeLessonIdRef = useRef<string | null>(activeLesson?.id ?? null);
  const chatAbortControllerRef = useRef<AbortController | null>(null);
  const chatAbortRequestedRef = useRef(false);

  useEffect(() => {
    activeLessonIdRef.current = activeLesson?.id ?? null;
  }, [activeLesson?.id]);

  useEffect(() => {
    const lessonId = activeLesson?.id;
    let disposed = false;
    if (!lessonId || isPreviewMode) {
      return;
    }

    void api
      .getPendingEvidence(lessonId)
      .then((bundle) => {
        if (!disposed && activeLessonIdRef.current === lessonId) {
          setCandidateEvidenceState({ lessonId, bundle });
        }
      })
      .catch(() => {
        if (!disposed && activeLessonIdRef.current === lessonId) {
          setCandidateEvidenceState({ lessonId, bundle: null });
        }
      });
    return () => {
      disposed = true;
    };
  }, [activeLesson?.id, isPreviewMode]);

  const candidateEvidenceBundle =
    !isPreviewMode && candidateEvidenceState && candidateEvidenceState.lessonId === activeLesson?.id
      ? candidateEvidenceState.bundle
      : null;
  const isPendingEvidenceLoading =
    !isPreviewMode && Boolean(activeLesson?.id) && candidateEvidenceState?.lessonId !== activeLesson?.id;

  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit" || busyAction === "chat-edit";

  type ChatTurnBusyAction = "chat" | "agent-edit" | "chat-edit";
  type ChatTurnBeforeRequestResult = {
    lesson?: Lesson | null;
    document?: BoardDocument | null;
  };
  type ChatTurnBeforeRequestContext = {
    lessonId: string;
    pendingMessageId: string;
  };
  type RunChatTurnOptions = {
    lesson: Lesson;
    payload: ChatRequestPayload;
    conversationMessages: ChatMessage[];
    userMessageContent: string;
    submittedSelection: SelectionRef | null;
    busyActionName: ChatTurnBusyAction;
    flushReason: AutoSaveReason;
    clearComposerInput?: boolean;
    restoreComposerInput?: string;
    rollbackMessages?: ChatMessage[];
    speakResponse?: boolean;
    beforeRequest?: (context: ChatTurnBeforeRequestContext) => Promise<ChatTurnBeforeRequestResult | void>;
    messageListUpdater?: (current: ChatMessage[], userMessage: ChatMessage, pendingAssistant: ChatMessage) => ChatMessage[];
  };

  function updatePendingAssistant(
    lessonId: string,
    messageId: string,
    patch: Partial<Pick<ChatMessage, "agentActivity" | "content" | "statusLabel">>
  ) {
    updateLessonMessages(lessonId, (current) =>
      current.map((message) => (message.id === messageId ? { ...message, ...patch } : message))
    );
  }

  function restoreComposerInputIfUntouched(lessonId: string, value: string) {
    updateLessonComposerState(lessonId, (current) =>
      current.chatInput.length > 0
        ? current
        : {
            ...current,
            chatInput: value,
          }
    );
  }

  function conversationFromMessages(messages: ChatMessage[]) {
    return messages.slice(-8).map(({ role, content }) => ({ role, content }));
  }

  function displayContentForPayload(payload: ChatRequestPayload) {
    if (payload.board_generation_action === "start") {
      return "开始生成板书";
    }
    if (payload.scope_action) {
      return `继续执行：${payload.scope_action}`;
    }
    if (payload.teaching_action === "continue") {
      return "继续讲下一节";
    }
    if (payload.teaching_action === "restart") {
      return "从第一节重新讲";
    }
    if (payload.board_edit_action === "confirm") {
      return `扩选板书：${payload.board_edit_topic ?? payload.message}`;
    }
    if (payload.board_edit_action === "skip") {
      return `暂不扩选板书：${payload.board_edit_topic ?? payload.message}`;
    }
    return payload.interaction_mode === "direct_edit" ? `直接编辑讲义：${payload.message}` : payload.message;
  }

  function latestCommitFromPackage(coursePackage: CoursePackage, lessonId: string): CommitRecord | null {
    const lesson = coursePackage.lessons.find((item) => item.id === lessonId);
    if (!lesson) {
      return null;
    }
    const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
    const commitId = branch?.head_commit_id ?? lesson.history_graph.commits[lesson.history_graph.commits.length - 1]?.id;
    return lesson.history_graph.commits.find((commit) => commit.id === commitId) ?? null;
  }

  function lessonFromPackage(coursePackage: CoursePackage, lessonId: string) {
    return coursePackage.lessons.find((item) => item.id === lessonId) ?? null;
  }

  function activeLessonIdForAsyncPackage(
    coursePackage: CoursePackage,
    requestLessonId: string,
    fallbackActiveLessonId?: string | null
  ) {
    const currentActiveLessonId = activeLessonIdRef.current;
    if (
      currentActiveLessonId &&
      currentActiveLessonId !== requestLessonId &&
      coursePackage.workspace_tab_order.includes(currentActiveLessonId)
    ) {
      return currentActiveLessonId;
    }
    return fallbackActiveLessonId;
  }

  function recoveredCommitForTurn(lesson: Lesson, submittedMessage: string, requestStartedAtMs: number) {
    const earliestCommitMs = requestStartedAtMs - 5000;
    const normalizedMessage = submittedMessage.trim();
    return (
      [...lesson.history_graph.commits]
        .reverse()
        .find((commit) => {
          const userMessage = commit.metadata?.user_message;
          if (typeof userMessage !== "string" || userMessage.trim() !== normalizedMessage) {
            return false;
          }
          return new Date(commit.created_at).getTime() >= earliestCommitMs;
        }) ?? null
    );
  }

  function shouldStreamDocumentPreview(payload: ChatRequestPayload, document: BoardDocument | null) {
    if (payload.interaction_mode === "direct_edit" || payload.board_edit_action) {
      return false;
    }
    return (
      payload.board_generation_action === "start" ||
      isBoardDocumentEmpty(document)
    );
  }

  function resetAgentState() {
    setScopeOptions([]);
    setClarificationQuestions([]);
    setLearningClarity(null);
    setStreamedRequirementSheet(null);
    setStreamedBoardTaskSheet(null);
    setCurrentNeedPending(false);
    setLatestBoardDecision(null);
    setBoardEditPrompt(null);
    setLastScopedRequest(null);
    setLastBoardEditRequest(null);
    clearSelection();
  }

  async function runChatTurn({
    lesson,
    payload,
    conversationMessages,
    userMessageContent,
    submittedSelection,
    busyActionName,
    flushReason,
    clearComposerInput = false,
    restoreComposerInput,
    rollbackMessages,
    speakResponse = false,
    beforeRequest,
    messageListUpdater,
  }: RunChatTurnOptions) {
    const lessonId = lesson.id;
    const payloadWithConversation: ChatRequestPayload = {
      ...payload,
      text_model: payload.text_model ?? selectedTextModel,
      board_model: payload.board_model ?? selectedBoardModel,
      conversation: payload.conversation ?? conversationFromMessages(conversationMessages),
    };

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    const userMessage = createChatMessage("user", userMessageContent, "ready", undefined, submittedSelection);
    const pendingAssistantMessage: ChatMessage = {
      ...createChatMessage("assistant", "", "pending"),
      statusLabel: "正在保存当前文档",
    };
    let requestStarted = false;
    let streamedChatContent = "";
    let streamedDocumentText = "";
    let streamedDocumentPreviewFrame: number | null = null;
    let streamedAgentActivity: AgentActivityEvent[] = [];
    let sawReadyForBoardRequirementUpdate = false;
    let requestLesson = lesson;
    let baseStreamingDocument = currentBoardDocument ?? lesson.board_document;
    let requestStartedAtMs = Date.now();
    const abortController = new AbortController();
    let canStreamDocumentPreview = false;

    function flushStreamingDocumentPreview() {
      if (!canStreamDocumentPreview || !streamedDocumentText) {
        return;
      }
      setStreamingDocumentPreview(requestLesson.id, {
        ...baseStreamingDocument,
        content_json: {},
        content_html: streamingMarkdownToHtml(streamedDocumentText),
        content_text: streamedDocumentText,
      });
    }

    function scheduleStreamingDocumentPreview() {
      if (!canStreamDocumentPreview) {
        return;
      }
      if (streamedDocumentPreviewFrame !== null) {
        return;
      }
      streamedDocumentPreviewFrame = window.requestAnimationFrame(() => {
        streamedDocumentPreviewFrame = null;
        flushStreamingDocumentPreview();
      });
    }

    function clearStreamingDocumentPreviewFrame() {
      if (streamedDocumentPreviewFrame === null) {
        return;
      }
      window.cancelAnimationFrame(streamedDocumentPreviewFrame);
      streamedDocumentPreviewFrame = null;
    }

    function finishCancelledTurn() {
      clearStreamingDocumentPreviewFrame();
      const stoppedContent = streamedChatContent.trim();
      updateLessonMessages(lessonId, (current) =>
        current
          .map((message) =>
            message.id === pendingAssistantMessage.id
              ? {
                  ...message,
                  content: streamedChatContent,
                  agentActivity: streamedAgentActivity,
                  status: "ready" as const,
                  statusLabel: undefined,
                }
              : message
          )
          .filter((message) => message.id !== pendingAssistantMessage.id || Boolean(stoppedContent))
      );
      setCurrentNeedPending(false);
      setError(null);
    }

    chatAbortRequestedRef.current = false;
    chatAbortControllerRef.current = abortController;
    chatRequestInFlightRef.current = true;
    setBusyAction(busyActionName);
    setError(null);
    if (!isBoardDocumentEmpty(currentBoardDocument ?? lesson.board_document)) {
      setLearningClarity(null);
      setStreamedRequirementSheet(null);
      setStreamedBoardTaskSheet(null);
      setCurrentNeedPending(true);
    }
    if (clearComposerInput) {
      updateLessonComposerState(lessonId, (current) => ({
        ...current,
        chatInput: "",
      }));
    }
    updateLessonMessages(lessonId, (current) =>
      messageListUpdater
        ? messageListUpdater(current, userMessage, pendingAssistantMessage)
        : [...current, userMessage, pendingAssistantMessage]
    );

    try {
      if (!(await flushAutoSave(flushReason))) {
        if (rollbackMessages) {
          updateLessonMessages(lessonId, () => rollbackMessages);
        } else {
          updateLessonMessages(lessonId, (current) =>
            current.filter((message) => message.id !== pendingAssistantMessage.id && message.id !== userMessage.id)
          );
        }
        if (restoreComposerInput !== undefined) {
          restoreComposerInputIfUntouched(lessonId, restoreComposerInput);
        }
        setCurrentNeedPending(false);
        return;
      }
      const beforeRequestResult = await beforeRequest?.({
        lessonId,
        pendingMessageId: pendingAssistantMessage.id,
      });
      if (beforeRequestResult?.lesson) {
        requestLesson = beforeRequestResult.lesson;
      }
      if (beforeRequestResult?.document !== undefined) {
        baseStreamingDocument = beforeRequestResult.document ?? baseStreamingDocument;
      } else if (beforeRequestResult?.lesson) {
        baseStreamingDocument = beforeRequestResult.lesson.board_document;
      }
      canStreamDocumentPreview = shouldStreamDocumentPreview(payloadWithConversation, baseStreamingDocument);
      if (abortController.signal.aborted) {
        finishCancelledTurn();
        return;
      }
      requestStarted = true;
      requestStartedAtMs = Date.now();
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "正在回复" });
      const response = await api.streamChatOnLesson(
        requestLesson.id,
        payloadWithConversation,
        {
          onPhase(label) {
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: label });
          },
          onAgentActivity(event) {
            streamedAgentActivity = [...streamedAgentActivity, event];
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
              agentActivity: streamedAgentActivity,
              statusLabel: event.label,
            });
          },
          onChatDelta(delta) {
            streamedChatContent += delta;
            updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
              content: streamedChatContent,
              agentActivity: streamedAgentActivity,
              statusLabel: "正在回复",
            });
          },
          onDocumentDelta(delta) {
            if (!canStreamDocumentPreview) {
              return;
            }
            streamedDocumentText += delta;
            scheduleStreamingDocumentPreview();
          },
          onRequirementUpdate(payload) {
            if (payload.learning_clarification?.ready_for_board) {
              sawReadyForBoardRequirementUpdate = true;
            }
            setCurrentNeedPending(false);
            setClarificationQuestions(payload.clarification_questions);
            setLearningClarity(payload.learning_clarification);
            setStreamedRequirementSheet(payload.active_requirement_sheet ?? payload.learning_requirement_sheet);
          },
          onBoardTaskUpdate(payload) {
            setCurrentNeedPending(false);
            setStreamedRequirementSheet(null);
            setLearningClarity(null);
            setClarificationQuestions([]);
            setStreamedBoardTaskSheet(payload.active_board_task_sheet ?? payload.board_task_sheet);
          },
        },
        { signal: abortController.signal }
      );
      clearStreamingDocumentPreviewFrame();
      flushStreamingDocumentPreview();
      const failedStreamingDocumentPreview =
        canStreamDocumentPreview &&
        streamedDocumentText.trim() &&
        response.board_document_operation_status === "failed"
          ? {
              ...baseStreamingDocument,
              content_json: {},
              content_html: streamingMarkdownToHtml(streamedDocumentText),
              content_text: streamedDocumentText,
            }
          : null;
      const responseCommit = latestCommitFromPackage(response.course_package, requestLesson.id);
      const committedUserMessage: ChatMessage = responseCommit
        ? {
            ...userMessage,
            id: `${responseCommit.id}:user`,
            commitId: responseCommit.id,
            parentCommitIds: responseCommit.parent_ids,
            editableContent: payloadWithConversation.message,
            interactionMode: payloadWithConversation.interaction_mode ?? "ask",
            editedFromCommitId: payloadWithConversation.chat_edit_source_commit_id ?? null,
          }
        : userMessage;
      updateCoursePackage(response.course_package, {
        activeLessonId: activeLessonIdForAsyncPackage(
          response.course_package,
          requestLesson.id,
          response.created_lesson ? undefined : requestLesson.id
        ),
      });
      if (failedStreamingDocumentPreview) {
        setStreamingDocumentPreview(requestLesson.id, failedStreamingDocumentPreview);
      }
      if (response.board_document_operation_status === "failed") {
        setError(response.board_document_operation_failure_reason ?? "右侧文档生成失败，请重试。");
      }
      if (response.learning_requirement_operation_status === "failed") {
        setError(
          response.learning_requirement_operation_failure_reason ??
            "本轮学习需求没有成功更新，请重试。"
        );
      }
      setLatestBoardDecision(response.board_decision);
      setCurrentNeedPending(false);
      setClarificationQuestions(response.clarification_questions);
      setLearningClarity(response.learning_clarification);
      const nextBoardTaskSheet = response.active_board_task_sheet ?? response.board_task_sheet ?? null;
      setStreamedRequirementSheet(
        response.requirement_cleared || nextBoardTaskSheet
          ? null
          : response.active_requirement_sheet ?? response.learning_requirement_sheet
      );
      setStreamedBoardTaskSheet(nextBoardTaskSheet);
      setScopeOptions(response.scope_options);
      setBoardEditPrompt(response.board_edit_prompt ?? null);
      setCandidateEvidenceState({
        lessonId: requestLesson.id,
        bundle: response.candidate_evidence_bundle ?? null,
      });
      setLastEvidenceRequest(response.candidate_evidence_bundle ? payloadWithConversation : null);
      setLastScopedRequest(response.scope_options.length ? payloadWithConversation : null);
      setLastBoardEditRequest(response.board_edit_prompt ? payloadWithConversation : null);
      const chatbotMessage = response.chatbot_message.trim();
      const streamedFallbackMessage = streamedChatContent.trim();
      const finalAgentActivity = response.agent_activity?.length ? response.agent_activity : streamedAgentActivity;
      const assistantMessages: ChatMessage[] = [];
      if (chatbotMessage) {
        assistantMessages.push(
          createChatMessage(
            "assistant",
            chatbotMessage,
            "ready",
            responseCommit ? `${responseCommit.id}:assistant` : undefined,
            null,
            response.teaching_progress ?? null,
            responseCommit
              ? {
                  agentActivity: finalAgentActivity,
                  boardSearchEvidence: response.board_search_evidence ?? null,
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  boardSearchEvidence: response.board_search_evidence ?? null,
                }
          )
        );
      } else if (streamedFallbackMessage) {
        assistantMessages.push(
          createChatMessage(
            "assistant",
            streamedFallbackMessage,
            "ready",
            responseCommit ? `${responseCommit.id}:assistant` : undefined,
            null,
            response.teaching_progress ?? null,
            responseCommit
              ? {
                  agentActivity: finalAgentActivity,
                  boardSearchEvidence: response.board_search_evidence ?? null,
                  commitId: responseCommit.id,
                  parentCommitIds: responseCommit.parent_ids,
                }
              : {
                  agentActivity: finalAgentActivity,
                  boardSearchEvidence: response.board_search_evidence ?? null,
                }
          )
        );
      }
      updateLessonMessages(lessonId, (current) => [
        ...current
          .map((message) => (message.id === userMessage.id ? committedUserMessage : message))
          .filter((message) => message.id !== pendingAssistantMessage.id),
        ...assistantMessages,
      ]);
      if (speakResponse && chatbotMessage) {
        onSpeakResponse(chatbotMessage);
      }
      if (!payloadWithConversation.scope_action) {
        clearSelection();
      }
    } catch (chatError) {
      if (abortController.signal.aborted && chatAbortRequestedRef.current) {
        finishCancelledTurn();
        return;
      }
      const rawErrorMessage = chatError instanceof Error ? chatError.message : "聊天失败";
      const isTransientNetworkError =
        rawErrorMessage.toLowerCase().includes("network error") ||
        rawErrorMessage.toLowerCase().includes("failed to fetch");
      const userFacingError =
        payloadWithConversation.board_generation_action === "start" && isTransientNetworkError
          ? "板书生成连接中断，可以再次点击“开始生成板书”重试；已确认的学习需求会保留。"
          : sawReadyForBoardRequirementUpdate && isTransientNetworkError
            ? "学习需求已确认，但板书生成连接中断；可以点击“开始生成板书”继续。"
            : rawErrorMessage;
      if (isMissingChatStreamFinalError(chatError)) {
        try {
          const refreshedPackage = await api.getCoursePackage();
          const refreshedLesson = lessonFromPackage(refreshedPackage, requestLesson.id);
          const recoveredCommit =
            refreshedLesson !== null
              ? recoveredCommitForTurn(refreshedLesson, payloadWithConversation.message, requestStartedAtMs)
              : null;
          updateCoursePackage(refreshedPackage, {
            activeLessonId: activeLessonIdForAsyncPackage(refreshedPackage, requestLesson.id, requestLesson.id),
            rebuildMessageLessonIds: recoveredCommit ? [requestLesson.id] : undefined,
          });
          if (refreshedLesson) {
            setStreamedRequirementSheet(
              recoveredCommit || refreshedLesson.board_task_requirements
                ? null
                : refreshedLesson.learning_requirements ?? null
            );
            setStreamedBoardTaskSheet(refreshedLesson.board_task_requirements ?? null);
            setLearningClarity(recoveredCommit ? learningClarityFromCommit(recoveredCommit) : null);
            setClarificationQuestions([]);
          }
          setCurrentNeedPending(false);
          if (recoveredCommit) {
            setError(recoveredLearningRequirementFailureReason(recoveredCommit));
            return;
          }
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          setError("聊天连接在最终结果返回前中断，本轮没有写入历史；可以重试。");
          return;
        } catch (refreshError) {
          const refreshMessage = refreshError instanceof Error ? refreshError.message : "刷新失败";
          updateLessonMessages(lessonId, (current) =>
            current.filter(
              (message) =>
                message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
            )
          );
          setError(`${rawErrorMessage}；刷新最新历史失败：${refreshMessage}`);
          setCurrentNeedPending(false);
          return;
        }
      }
      if (restoreComposerInput !== undefined && !sawReadyForBoardRequirementUpdate) {
        restoreComposerInputIfUntouched(lessonId, restoreComposerInput);
      }
      if (!requestStarted && rollbackMessages) {
        updateLessonMessages(lessonId, () => rollbackMessages);
      } else {
        updateLessonMessages(lessonId, (current) =>
          current.filter(
            (message) =>
              message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
          )
        );
      }
      setError(userFacingError);
      setCurrentNeedPending(false);
    } finally {
      clearStreamingDocumentPreviewFrame();
      if (chatAbortControllerRef.current === abortController) {
        chatAbortControllerRef.current = null;
      }
      chatAbortRequestedRef.current = false;
      chatRequestInFlightRef.current = false;
      setBusyAction(null);
    }
  }

  function handleStopChat() {
    if (!chatRequestInFlightRef.current || !chatAbortControllerRef.current) {
      return;
    }
    chatAbortRequestedRef.current = true;
    chatAbortControllerRef.current.abort();
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload, options?: { speakResponse?: boolean }) {
    if (!activeLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    if (isPreviewMode) {
      exitPreviewMode();
    }
    const submittedInput = chatInput;
    const payload =
      payloadOverride ??
      ({
        message: chatInput.trim(),
        selection: includeSelectionInPrompt && composerSelection ? composerSelection : null,
        interaction_mode: composerMode,
      } satisfies ChatRequestPayload);
    const submittedSelection = payload.selection ?? null;
    const payloadMessage = payload.message.trim();
    if (!payloadMessage) {
      return;
    }
    const payloadForTurn = { ...payload, message: payloadMessage };
    const isBoardGenerationControl = payloadForTurn.board_generation_action === "start";

    await runChatTurn({
      lesson: activeLesson,
      payload: payloadForTurn,
      conversationMessages: activeMessages,
      userMessageContent: displayContentForPayload(payloadForTurn),
      submittedSelection,
      busyActionName: payloadForTurn.interaction_mode === "direct_edit" ? "agent-edit" : "chat",
      flushReason: "chat",
      clearComposerInput: !payloadOverride || isBoardGenerationControl,
      restoreComposerInput: payloadOverride || isBoardGenerationControl ? undefined : submittedInput,
      speakResponse: options?.speakResponse ?? false,
    });
  }

  async function handleEditMessage(sourceMessage: ChatMessage, nextContent: string) {
    if (!activeLesson || chatRequestInFlightRef.current || isChatBusy || isPreviewMode) {
      return;
    }
    const editedMessage = nextContent.trim();
    const sourceCommitId = sourceMessage.commitId;
    const baseCommitId = sourceMessage.parentCommitIds?.[0];
    if (!sourceCommitId || !baseCommitId || !editedMessage) {
      setError("这条消息缺少可分叉的历史版本");
      return;
    }
    const sourceIndex = activeMessages.findIndex((message) => message.id === sourceMessage.id);
    if (sourceIndex < 0) {
      setError("没有找到要编辑的历史消息");
      return;
    }
    const originalMessage = sourceMessage.editableContent ?? sourceMessage.content;
    if (editedMessage === originalMessage.trim()) {
      return;
    }
    const prefixMessages = activeMessages.slice(0, sourceIndex);
    const rollbackMessages = activeMessages;
    const payload: ChatRequestPayload = {
      message: editedMessage,
      selection: sourceMessage.selection ?? null,
      interaction_mode: sourceMessage.interactionMode ?? "ask",
      chat_edit_source_commit_id: sourceCommitId,
      chat_edit_base_commit_id: baseCommitId,
      chat_edit_original_message: originalMessage,
    };

    await runChatTurn({
      lesson: activeLesson,
      payload,
      conversationMessages: prefixMessages,
      userMessageContent: editedMessage,
      submittedSelection: sourceMessage.selection ?? null,
      busyActionName: "chat-edit",
      flushReason: "chat",
      rollbackMessages,
      messageListUpdater: (_current, userMessage, pendingAssistant) => [
        ...prefixMessages,
        userMessage,
        pendingAssistant,
      ],
      beforeRequest: async ({ lessonId, pendingMessageId }) => {
        updatePendingAssistant(lessonId, pendingMessageId, { statusLabel: "正在创建新链路" });
        const branchName = nextEditBranchName(activeLesson);
        const branchedPackage = await api.createBranch(activeLesson.id, branchName, baseCommitId);
        const applied = updateCoursePackage(branchedPackage, {
          activeLessonId: activeLesson.id,
        });
        const branchedLesson =
          applied?.activeLesson ?? branchedPackage.lessons.find((lesson) => lesson.id === activeLesson.id) ?? null;
        return {
          lesson: branchedLesson,
          document: branchedLesson?.board_document ?? null,
        };
      },
    });
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
    });
    setScopeOptions([]);
    setLastScopedRequest(null);
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
      board_edit_action: action,
      board_edit_topic: boardEditPrompt.topic,
    });
    setBoardEditPrompt(null);
    setLastBoardEditRequest(null);
  }

  async function handleEvidenceAction(bundleId: string, action: "confirm" | "skip") {
    if (!activeLesson || isChatBusy) {
      return;
    }
    setBusyAction("chat");
    try {
      const result = await api.confirmEvidence(activeLesson.id, bundleId, action);
      const shouldResumeBoardTask =
        action === "confirm" && candidateEvidenceBundle?.id === bundleId && candidateEvidenceBundle.purpose === "board_edit";
      const resumePayload = shouldResumeBoardTask
        ? lastEvidenceRequest ?? {
            message: activeLesson.board_task_requirements?.question_or_topic ?? "继续执行当前板书写入任务",
            interaction_mode: "ask" as const,
          }
        : null;
      setCandidateEvidenceState({
        lessonId: activeLesson.id,
        bundle: resumePayload ? null : action === "confirm" ? result.evidence_bundle : null,
      });
      setLastEvidenceRequest(null);
      if (result.active_requirement_sheet) {
        setStreamedRequirementSheet(result.active_requirement_sheet);
      }
      if (resumePayload) {
        setBusyAction(null);
        await handleSubmitChat(resumePayload);
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "资料证据确认失败");
    } finally {
      setBusyAction(null);
    }
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

  return {
    chatInput,
    composerMode,
    includeSelectionInPrompt,
    isChatBusy,
    scopeOptions,
    clarificationQuestions,
    learningClarity,
    streamedRequirementSheet,
    streamedBoardTaskSheet,
    currentNeedPending,
    latestBoardDecision,
    boardEditPrompt,
    candidateEvidenceBundle,
    isPendingEvidenceLoading,
    resetAgentState,
    handleSubmitChat,
    handleStopChat,
    handleEditMessage,
    handleScopeAction,
    handleBoardEditAction,
    handleEvidenceAction,
    handleContinueTeaching,
  };
}
