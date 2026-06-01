"use client";

import { useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api } from "@/lib/api";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  buildConversationBeforeChatCommit,
  buildLessonMessagesFromHistory,
  chatEditBaseCommitId,
  chatInteractionModeForCommit,
  chatSelectionForCommit,
  createChatMessage,
  isBoardDocumentEmpty,
  nextBranchName,
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
  ChatRequestPayload,
  ConversationTurn,
  CoursePackage,
  DocumentEvidence,
  DocumentEvidenceAction,
  LearningClarificationStatus,
  Lesson,
  ResourceMatch,
  ResourceReferenceContext,
  ResourceReferencePrompt,
  ScopeOption,
  SelectionRef,
  StrongReasoningPrompt,
} from "@/types";

type UseLessonChatAgentOptions = {
  activeLesson: Lesson | null;
  activeMessages: ChatMessage[];
  activeComposerState: LessonComposerState;
  composerSelection: SelectionRef | null;
  currentBoardDocument: BoardDocument | null;
  selectedTextModel: AIModelSelection;
  isPreviewMode: boolean;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  exitPreviewMode: () => void;
  updateCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => CoursePackageApplyResult;
  updateLessonMessages: (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  updateLessonComposerState: (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => void;
  setStreamingDocumentPreview: (document: BoardDocument) => void;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
  onSpeakResponse: (content: string) => void;
};

type SubmitChatOptions = {
  speakResponse?: boolean;
  conversationOverride?: ConversationTurn[];
  lessonOverride?: Lesson;
  messagesOverride?: ChatMessage[];
  boardDocumentOverride?: BoardDocument | null;
  rebuildMessagesAfterResponse?: boolean;
};

type CoursePackageApplyResult = {
  coursePackage: CoursePackage;
  activeLesson: Lesson | null;
};

export function useLessonChatAgent({
  activeLesson,
  activeMessages,
  activeComposerState,
  composerSelection,
  currentBoardDocument,
  selectedTextModel,
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
  const [resourceMatches, setResourceMatches] = useState<ResourceMatch[]>([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const [referencePrompt, setReferencePrompt] = useState<ResourceReferencePrompt | null>(null);
  const [documentEvidence, setDocumentEvidence] = useState<DocumentEvidence[]>([]);
  const [boardEditPrompt, setBoardEditPrompt] = useState<BoardEditPrompt | null>(null);
  const [strongReasoningPrompt, setStrongReasoningPrompt] = useState<StrongReasoningPrompt | null>(null);
  const [selectedReference, setSelectedReference] = useState<ResourceReferenceContext | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [lastReferenceRequest, setLastReferenceRequest] = useState<ChatRequestPayload | null>(null);
  const [lastBoardEditRequest, setLastBoardEditRequest] = useState<ChatRequestPayload | null>(null);
  const [lastStrongReasoningRequest, setLastStrongReasoningRequest] = useState<ChatRequestPayload | null>(null);

  const chatInput = activeComposerState.chatInput;
  const composerMode = activeComposerState.composerMode;
  const includeSelectionInPrompt = activeComposerState.includeSelectionInPrompt;
  const isChatBusy = busyAction === "chat" || busyAction === "agent-edit";

  function updatePendingAssistant(
    lessonId: string,
    messageId: string,
    patch: Partial<Pick<ChatMessage, "content" | "statusLabel">>
  ) {
    updateLessonMessages(lessonId, (current) =>
      current.map((message) => (message.id === messageId ? { ...message, ...patch } : message))
    );
  }

  function shouldStreamDocumentPreview(payload: ChatRequestPayload, document: BoardDocument | null) {
    if (payload.interaction_mode === "direct_edit" || payload.board_edit_action) {
      return false;
    }
    return (
      payload.board_generation_action === "start" ||
      payload.document_evidence_action === "reference_generate" ||
      payload.resource_reference_action === "confirm" ||
      isBoardDocumentEmpty(document)
    );
  }

  function resetAgentState() {
    setScopeOptions([]);
    setResourceMatches([]);
    setClarificationQuestions([]);
    setLearningClarity(null);
    setLatestBoardDecision(null);
    setReferencePrompt(null);
    setDocumentEvidence([]);
    setBoardEditPrompt(null);
    setStrongReasoningPrompt(null);
    setSelectedReference(null);
    setLastScopedRequest(null);
    setLastReferenceRequest(null);
    setLastBoardEditRequest(null);
    setLastStrongReasoningRequest(null);
    clearSelection();
  }

  async function handleSubmitChat(payloadOverride?: ChatRequestPayload, options?: SubmitChatOptions) {
    const requestLesson = options?.lessonOverride ?? activeLesson;
    if (!requestLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    if (isPreviewMode) {
      exitPreviewMode();
    }
    const lessonId = requestLesson.id;
    const requestMessages = options?.messagesOverride ?? activeMessages;
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
      conversation:
        payload.conversation ??
        options?.conversationOverride ??
        requestMessages.slice(-8).map(({ role, content }) => ({ role, content })),
    };
    const submittedSelection = payloadWithConversation.selection ?? null;

    if (!payloadWithConversation.message.trim()) {
      return;
    }

    const isDirectEdit = payloadWithConversation.interaction_mode === "direct_edit";
    const userMessageContent = payloadOverride?.scope_action
      ? `Continue: ${payloadOverride.scope_action}`
      : payloadOverride?.teaching_action === "continue"
        ? "Continue to the next section"
          : payloadOverride?.teaching_action === "restart"
            ? "Restart from the first section"
          : payloadOverride?.document_evidence_action === "insert_original"
            ? payloadWithConversation.message
          : payloadOverride?.document_evidence_action === "reference_generate"
            ? payloadWithConversation.message
          : payloadOverride?.board_edit_action === "confirm"
            ? `Expand board selection: ${payloadOverride.board_edit_topic ?? payloadWithConversation.message}`
          : payloadOverride?.board_edit_action === "skip"
            ? `Skip board expansion: ${payloadOverride.board_edit_topic ?? payloadWithConversation.message}`
            : payloadOverride?.resource_reference_action === "confirm"
              ? "Continue: generate notes from the recommended section"
              : payloadOverride?.resource_reference_action === "skip"
                ? "Continue: skip the recommended section"
                : payloadOverride?.strong_reasoning_action === "confirm"
                  ? "Continue: confirm deep reasoning"
                  : payloadOverride?.strong_reasoning_action === "skip"
                    ? "Continue: skip deep reasoning"
                    : isDirectEdit
                      ? `Directly edit notes: ${payloadWithConversation.message}`
                      : payloadWithConversation.message;
    const userMessage = createChatMessage("user", userMessageContent, "ready", undefined, submittedSelection);
    const pendingAssistantMessage: ChatMessage = {
      ...createChatMessage("assistant", "", "pending"),
      statusLabel: "Saving current document",
    };
    const baseStreamingDocument = options?.boardDocumentOverride ?? currentBoardDocument ?? requestLesson.board_document;
    const canStreamDocumentPreview = shouldStreamDocumentPreview(payloadWithConversation, baseStreamingDocument);
    let requestStarted = false;
    let streamedChatContent = "";
    let streamedDocumentText = "";

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
      ...(options?.messagesOverride ?? current),
      userMessage,
      pendingAssistantMessage,
    ]);

    try {
      if (!(await flushAutoSave("chat"))) {
        updateLessonMessages(lessonId, (current) =>
          current.filter((message) => message.id !== pendingAssistantMessage.id && message.id !== userMessage.id)
        );
        if (!payloadOverride) {
          updateLessonComposerState(lessonId, (current) => ({
            ...current,
            chatInput: submittedInput,
          }));
        }
        return;
      }
      requestStarted = true;
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "Replying" });
      const response = await api.streamChatOnLesson(lessonId, payloadWithConversation, {
        onPhase(label) {
          updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: label });
        },
        onChatDelta(delta) {
          streamedChatContent += delta;
          updatePendingAssistant(lessonId, pendingAssistantMessage.id, {
            content: streamedChatContent,
            statusLabel: "Replying",
          });
        },
        onDocumentDelta(delta) {
          if (!canStreamDocumentPreview) {
            return;
          }
          streamedDocumentText += delta;
          setStreamingDocumentPreview({
            ...baseStreamingDocument,
            content_json: {},
            content_html: streamingMarkdownToHtml(streamedDocumentText),
            content_text: streamedDocumentText,
          });
        },
      });
      updateCoursePackage(response.course_package, {
        activeLessonId: response.created_lesson ? undefined : lessonId,
        rebuildMessageLessonIds: options?.rebuildMessagesAfterResponse ? [lessonId] : undefined,
      });
      setLatestBoardDecision(response.board_decision);
      setClarificationQuestions(response.clarification_questions);
      setLearningClarity(response.learning_clarification);
      setScopeOptions(response.scope_options);
      setResourceMatches(response.resource_matches);
      setDocumentEvidence(response.document_evidence ?? []);
      setReferencePrompt(response.reference_prompt ?? null);
      setBoardEditPrompt(response.board_edit_prompt ?? null);
      setStrongReasoningPrompt(response.strong_reasoning_prompt ?? null);
      setSelectedReference(response.selected_reference ?? null);
      setLastScopedRequest(response.scope_options.length ? payloadWithConversation : null);
      setLastReferenceRequest(response.reference_prompt ? payloadWithConversation : null);
      setLastBoardEditRequest(response.board_edit_prompt ? payloadWithConversation : null);
      setLastStrongReasoningRequest(response.strong_reasoning_prompt ? payloadWithConversation : null);
      const chatbotMessage = response.chatbot_message.trim();
      const assistantMessages: ChatMessage[] = [];
      if (chatbotMessage) {
        assistantMessages.push(
          createChatMessage("assistant", chatbotMessage, "ready", undefined, null, response.teaching_progress ?? null)
        );
      }
      if (options?.rebuildMessagesAfterResponse) {
        const nextLesson = response.course_package.lessons.find((lesson) => lesson.id === lessonId) ?? null;
        updateLessonMessages(lessonId, () => (nextLesson ? buildLessonMessagesFromHistory(nextLesson) : assistantMessages));
      } else {
        updateLessonMessages(lessonId, (current) => [
          ...current.filter((message) => message.id !== pendingAssistantMessage.id),
          ...assistantMessages,
        ]);
      }
      if (options?.speakResponse && chatbotMessage) {
        onSpeakResponse(chatbotMessage);
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
      updateLessonMessages(lessonId, (current) =>
        current.filter(
          (message) =>
            message.id !== pendingAssistantMessage.id && (requestStarted || message.id !== userMessage.id)
        )
      );
      setError(chatError instanceof Error ? chatError.message : "Chat failed");
    } finally {
      chatRequestInFlightRef.current = false;
      setBusyAction(null);
    }
  }

  async function handleEditChatTurn(commitId: string, nextContent: string) {
    if (!activeLesson || chatRequestInFlightRef.current || isChatBusy) {
      return;
    }
    const message = nextContent.trim();
    const baseCommitId = chatEditBaseCommitId(activeLesson, commitId);
    if (!message || !baseCommitId) {
      return;
    }
    if (!(await flushAutoSave("branch"))) {
      return;
    }
    if (isPreviewMode) {
      exitPreviewMode();
    }

    const sourceLesson = activeLesson;
    const conversation = buildConversationBeforeChatCommit(sourceLesson, commitId);
    const interactionMode = chatInteractionModeForCommit(sourceLesson, commitId);
    const selection = chatSelectionForCommit(sourceLesson, commitId);
    const branchName = nextBranchName(sourceLesson);
    setBusyAction("branch");
    let branchedLesson: Lesson | null = null;
    try {
      const branchedPackage = await api.createBranch(sourceLesson.id, branchName, baseCommitId);
      const applied = updateCoursePackage(branchedPackage, {
        activeLessonId: sourceLesson.id,
        rebuildMessageLessonIds: [sourceLesson.id],
      });
      branchedLesson = applied.activeLesson;
    } catch (branchError) {
      setError(branchError instanceof Error ? branchError.message : "Could not create chat branch");
      return;
    } finally {
      setBusyAction(null);
    }

    const messagesBeforeEdit = branchedLesson ? buildLessonMessagesFromHistory(branchedLesson) : [];
    await handleSubmitChat(
      {
        message,
        interaction_mode: interactionMode,
        selection,
        conversation,
      },
      {
        conversationOverride: conversation,
        lessonOverride: branchedLesson ?? sourceLesson,
        messagesOverride: messagesBeforeEdit,
        boardDocumentOverride: branchedLesson?.board_document ?? sourceLesson.board_document,
        rebuildMessagesAfterResponse: true,
      }
    );
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
      resource_reference_segment_id: referencePrompt.segment_id ?? null,
    });
    setReferencePrompt(null);
    setLastReferenceRequest(null);
  }

  async function handleDocumentEvidenceAction(evidence: DocumentEvidence, action: DocumentEvidenceAction) {
    await handleSubmitChat({
      message:
        action === "insert_original"
          ? `把资料“${evidence.resource_name}”中定位到的原文插入板书`
          : `参考资料“${evidence.resource_name}”中定位到的证据生成板书`,
      interaction_mode: "ask",
      document_evidence_action: action,
      document_evidence_id: evidence.evidence_id,
    });
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
      resource_reference_segment_id: lastBoardEditRequest.resource_reference_segment_id,
      board_edit_action: action,
      board_edit_topic: boardEditPrompt.topic,
    });
    setBoardEditPrompt(null);
    setLastBoardEditRequest(null);
  }

  async function handleStrongReasoningAction(action: "confirm" | "skip") {
    if (!strongReasoningPrompt || !lastStrongReasoningRequest) {
      return;
    }
    await handleSubmitChat({
      ...lastStrongReasoningRequest,
      strong_reasoning_action: action,
    });
    setStrongReasoningPrompt(null);
    setLastStrongReasoningRequest(null);
  }

  async function handleContinueTeaching() {
    if (!activeLesson) {
      return;
    }
    await handleSubmitChat({
      message: "Continue to the next section",
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
    latestBoardDecision,
    referencePrompt,
    documentEvidence,
    resourceMatches,
    boardEditPrompt,
    strongReasoningPrompt,
    selectedReference,
    resetAgentState,
    handleSubmitChat,
    handleEditChatTurn,
    handleScopeAction,
    handleReferenceAction,
    handleDocumentEvidenceAction,
    handleBoardEditAction,
    handleStrongReasoningAction,
    handleContinueTeaching,
  };
}
