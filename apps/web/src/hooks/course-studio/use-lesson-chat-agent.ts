"use client";

import { useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api } from "@/lib/api";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  createChatMessage,
  isBoardDocumentEmpty,
  nextEditBranchName,
  type ChatMessage,
  type LessonComposerState,
} from "@/components/course-studio/history-utils";
import type { AutoSaveReason } from "@/hooks/course-studio/use-board-draft";
import type { CoursePackageApplyOptions } from "@/hooks/course-studio/use-course-workspace";
import type {
  AIModelSelection,
  BoardDocument,
  BoardDecision,
  BoardEditPrompt,
  BoardTaskRequirementSheet,
  ChatRequestPayload,
  CommitRecord,
  CoursePackage,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  ResourceReferenceContext,
  ResourceReferencePrompt,
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
    options?: CoursePackageApplyOptions & { transientLessonId?: string | null }
  ) => { activeLesson: Lesson | null } | void;
  updateLessonMessages: (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  updateLessonComposerState: (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => void;
  setStreamingDocumentPreview: (lessonId: string, document: BoardDocument) => void;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
  onSpeakResponse: (content: string) => void;
};

type LessonAgentState = {
  scopeOptions: ScopeOption[];
  clarificationQuestions: string[];
  learningClarity: LearningClarificationStatus | null;
  streamedRequirementSheet: LearningRequirementSheet | null;
  streamedBoardTaskSheet: BoardTaskRequirementSheet | null;
  currentNeedPending: boolean;
  latestBoardDecision: BoardDecision | null;
  referencePrompt: ResourceReferencePrompt | null;
  boardEditPrompt: BoardEditPrompt | null;
  selectedReference: ResourceReferenceContext | null;
  lastScopedRequest: ChatRequestPayload | null;
  lastReferenceRequest: ChatRequestPayload | null;
  lastBoardEditRequest: ChatRequestPayload | null;
};

function createLessonAgentState(): LessonAgentState {
  return {
    scopeOptions: [],
    clarificationQuestions: [],
    learningClarity: null,
    streamedRequirementSheet: null,
    streamedBoardTaskSheet: null,
    currentNeedPending: false,
    latestBoardDecision: null,
    referencePrompt: null,
    boardEditPrompt: null,
    selectedReference: null,
    lastScopedRequest: null,
    lastReferenceRequest: null,
    lastBoardEditRequest: null,
  };
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
  const [agentStatesByLessonId, setAgentStatesByLessonId] = useState<Record<string, LessonAgentState>>({});
  const activeLessonId = activeLesson?.id ?? null;
  const activeLessonIdRef = useRef(activeLessonId);
  const activeAgentState = activeLesson
    ? agentStatesByLessonId[activeLesson.id] ?? createLessonAgentState()
    : createLessonAgentState();
  const {
    scopeOptions,
    clarificationQuestions,
    learningClarity,
    streamedRequirementSheet,
    streamedBoardTaskSheet,
    currentNeedPending,
    latestBoardDecision,
    referencePrompt,
    boardEditPrompt,
    selectedReference,
    lastScopedRequest,
    lastReferenceRequest,
    lastBoardEditRequest,
  } = activeAgentState;

  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit" || busyAction === "chat-edit";

  useEffect(() => {
    activeLessonIdRef.current = activeLessonId;
  }, [activeLessonId]);

  function updateAgentState(lessonId: string, updater: (current: LessonAgentState) => LessonAgentState) {
    setAgentStatesByLessonId((current) => ({
      ...current,
      [lessonId]: updater(current[lessonId] ?? createLessonAgentState()),
    }));
  }

  function clearAgentState(lessonId: string) {
    setAgentStatesByLessonId((current) => {
      if (!(lessonId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[lessonId];
      return next;
    });
  }

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
    patch: Partial<Pick<ChatMessage, "content" | "statusLabel">>
  ) {
    updateLessonMessages(lessonId, (current) =>
      current.map((message) => (message.id === messageId ? { ...message, ...patch } : message))
    );
  }

  function conversationFromMessages(messages: ChatMessage[]) {
    return messages.slice(-8).map(({ role, content }) => ({ role, content }));
  }

  function displayContentForPayload(payload: ChatRequestPayload) {
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
    if (payload.resource_reference_action === "confirm") {
      return "继续执行：参考推荐章节生成讲义";
    }
    if (payload.resource_reference_action === "skip") {
      return "继续执行：先不参考推荐章节";
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

  function shouldStreamDocumentPreview(payload: ChatRequestPayload, document: BoardDocument | null) {
    if (payload.interaction_mode === "direct_edit" || payload.board_edit_action) {
      return false;
    }
    return (
      payload.board_generation_action === "start" ||
      payload.resource_reference_action === "confirm" ||
      isBoardDocumentEmpty(document)
    );
  }

  function resetAgentState(lessonId?: string | null) {
    const targetLessonId = lessonId ?? activeLessonIdRef.current;
    if (!targetLessonId) {
      return;
    }
    clearAgentState(targetLessonId);
    if (targetLessonId === activeLessonIdRef.current) {
      clearSelection();
    }
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
    let requestLesson = lesson;
    let baseStreamingDocument = currentBoardDocument ?? lesson.board_document;

    chatRequestInFlightRef.current = true;
    setBusyAction(busyActionName);
    setError(null);
    if (!isBoardDocumentEmpty(currentBoardDocument ?? lesson.board_document)) {
      updateAgentState(lessonId, (current) => ({
        ...current,
        learningClarity: null,
        streamedRequirementSheet: null,
        streamedBoardTaskSheet: null,
        currentNeedPending: true,
      }));
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
          updateLessonComposerState(lessonId, (current) => ({
            ...current,
            chatInput: restoreComposerInput,
          }));
        }
        updateAgentState(lessonId, (current) => ({ ...current, currentNeedPending: false }));
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
      const canStreamDocumentPreview = shouldStreamDocumentPreview(payloadWithConversation, baseStreamingDocument);
      requestStarted = true;
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "正在回复" });
      const response = await api.streamChatOnLesson(requestLesson.id, payloadWithConversation, {
        onPhase(label) {
          updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: label });
        },
        onChatDelta(delta) {
          streamedChatContent += delta;
          updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
            content: streamedChatContent,
            statusLabel: "正在回复",
          });
        },
        onDocumentDelta(delta) {
          if (!canStreamDocumentPreview) {
            return;
          }
          streamedDocumentText += delta;
          setStreamingDocumentPreview(lessonId, {
            ...baseStreamingDocument,
            content_json: {},
            content_html: streamingMarkdownToHtml(streamedDocumentText),
            content_text: streamedDocumentText,
          });
        },
        onRequirementUpdate(payload) {
          updateAgentState(lessonId, (current) => ({
            ...current,
            currentNeedPending: false,
            clarificationQuestions: payload.clarification_questions,
            learningClarity: payload.learning_clarification,
            streamedRequirementSheet: payload.active_requirement_sheet ?? payload.learning_requirement_sheet,
          }));
        },
        onBoardTaskUpdate(payload) {
          updateAgentState(lessonId, (current) => ({
            ...current,
            currentNeedPending: false,
            streamedRequirementSheet: null,
            learningClarity: null,
            clarificationQuestions: [],
            streamedBoardTaskSheet: payload.active_board_task_sheet ?? payload.board_task_sheet,
          }));
        },
      });
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
        activeLessonId: response.created_lesson ? undefined : activeLessonIdRef.current ?? requestLesson.id,
        transientLessonId: requestLesson.id,
      });
      if (failedStreamingDocumentPreview) {
        setStreamingDocumentPreview(requestLesson.id, failedStreamingDocumentPreview);
        setError(response.board_document_operation_failure_reason ?? "右侧文档生成失败，已保留未保存的预览草稿。");
      }
      const nextBoardTaskSheet = response.active_board_task_sheet ?? response.board_task_sheet ?? null;
      updateAgentState(requestLesson.id, (current) => ({
        ...current,
        latestBoardDecision: response.board_decision,
        currentNeedPending: false,
        clarificationQuestions: response.clarification_questions,
        learningClarity: response.learning_clarification,
        streamedRequirementSheet:
          response.requirement_cleared || nextBoardTaskSheet
            ? null
            : response.active_requirement_sheet ?? response.learning_requirement_sheet,
        streamedBoardTaskSheet: nextBoardTaskSheet,
        scopeOptions: response.scope_options,
        referencePrompt: response.reference_prompt ?? null,
        boardEditPrompt: response.board_edit_prompt ?? null,
        selectedReference: response.selected_reference ?? null,
        lastScopedRequest: response.scope_options.length ? payloadWithConversation : null,
        lastReferenceRequest: response.reference_prompt ? payloadWithConversation : null,
        lastBoardEditRequest: response.board_edit_prompt ? payloadWithConversation : null,
      }));
      const chatbotMessage = response.chatbot_message.trim();
      const streamedFallbackMessage = streamedChatContent.trim();
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
            responseCommit ? { commitId: responseCommit.id, parentCommitIds: responseCommit.parent_ids } : undefined
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
            responseCommit ? { commitId: responseCommit.id, parentCommitIds: responseCommit.parent_ids } : undefined
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
      if (restoreComposerInput !== undefined) {
        updateLessonComposerState(lessonId, (current) => ({
          ...current,
          chatInput: restoreComposerInput,
        }));
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
      setError(chatError instanceof Error ? chatError.message : "聊天失败");
      updateAgentState(lessonId, (current) => ({ ...current, currentNeedPending: false }));
    } finally {
      chatRequestInFlightRef.current = false;
      setBusyAction(null);
    }
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

    await runChatTurn({
      lesson: activeLesson,
      payload: payloadForTurn,
      conversationMessages: activeMessages,
      userMessageContent: displayContentForPayload(payloadForTurn),
      submittedSelection,
      busyActionName: payloadForTurn.interaction_mode === "direct_edit" ? "agent-edit" : "chat",
      flushReason: "chat",
      clearComposerInput: !payloadOverride,
      restoreComposerInput: payloadOverride ? undefined : submittedInput,
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
      resource_chapter_id: option.resource_chapter_id ?? undefined,
    });
    updateAgentState(activeLesson.id, (current) => ({
      ...current,
      scopeOptions: [],
      lastScopedRequest: null,
    }));
  }

  async function handleReferenceAction(action: "confirm" | "skip") {
    if (!activeLesson || !referencePrompt || !lastReferenceRequest) {
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
    updateAgentState(activeLesson.id, (current) => ({
      ...current,
      referencePrompt: null,
      lastReferenceRequest: null,
    }));
  }

  async function handleBoardEditAction(action: "confirm" | "skip") {
    if (!activeLesson || !boardEditPrompt || !lastBoardEditRequest) {
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
    updateAgentState(activeLesson.id, (current) => ({
      ...current,
      boardEditPrompt: null,
      lastBoardEditRequest: null,
    }));
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
    referencePrompt,
    boardEditPrompt,
    selectedReference,
    resetAgentState,
    handleSubmitChat,
    handleEditMessage,
    handleScopeAction,
    handleReferenceAction,
    handleBoardEditAction,
    handleContinueTeaching,
  };
}
