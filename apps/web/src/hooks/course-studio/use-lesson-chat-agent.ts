"use client";

import { useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { api } from "@/lib/api";
import { streamingMarkdownToHtml } from "@/lib/streaming-rich-document";
import {
  createChatMessage,
  isBoardDocumentEmpty,
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
  CoursePackage,
  LearningClarificationStatus,
  LearningRequirementSheet,
  Lesson,
  ResourceMatch,
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
  isPreviewMode: boolean;
  chatRequestInFlightRef: MutableRefObject<boolean>;
  flushAutoSave: (reason: AutoSaveReason) => Promise<boolean>;
  exitPreviewMode: () => void;
  updateCoursePackage: (nextPackage: CoursePackage, options?: CoursePackageApplyOptions) => void;
  updateLessonMessages: (lessonId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) => void;
  updateLessonComposerState: (lessonId: string, updater: (current: LessonComposerState) => LessonComposerState) => void;
  setStreamingDocumentPreview: (document: BoardDocument) => void;
  clearSelection: () => void;
  setError: Dispatch<SetStateAction<string | null>>;
  setBusyAction: Dispatch<SetStateAction<string | null>>;
  busyAction: string | null;
  onSpeakResponse: (content: string) => void;
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
  const [, setResourceMatches] = useState<ResourceMatch[]>([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<string[]>([]);
  const [learningClarity, setLearningClarity] = useState<LearningClarificationStatus | null>(null);
  const [streamedRequirementSheet, setStreamedRequirementSheet] = useState<LearningRequirementSheet | null>(null);
  const [streamedBoardTaskSheet, setStreamedBoardTaskSheet] = useState<BoardTaskRequirementSheet | null>(null);
  const [latestBoardDecision, setLatestBoardDecision] = useState<BoardDecision | null>(null);
  const [referencePrompt, setReferencePrompt] = useState<ResourceReferencePrompt | null>(null);
  const [boardEditPrompt, setBoardEditPrompt] = useState<BoardEditPrompt | null>(null);
  const [selectedReference, setSelectedReference] = useState<ResourceReferenceContext | null>(null);
  const [lastScopedRequest, setLastScopedRequest] = useState<ChatRequestPayload | null>(null);
  const [lastReferenceRequest, setLastReferenceRequest] = useState<ChatRequestPayload | null>(null);
  const [lastBoardEditRequest, setLastBoardEditRequest] = useState<ChatRequestPayload | null>(null);

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
      payload.resource_reference_action === "confirm" ||
      isBoardDocumentEmpty(document)
    );
  }

  function resetAgentState() {
    setScopeOptions([]);
    setResourceMatches([]);
    setClarificationQuestions([]);
    setLearningClarity(null);
    setStreamedRequirementSheet(null);
    setStreamedBoardTaskSheet(null);
    setLatestBoardDecision(null);
    setReferencePrompt(null);
    setBoardEditPrompt(null);
    setSelectedReference(null);
    setLastScopedRequest(null);
    setLastReferenceRequest(null);
    setLastBoardEditRequest(null);
    clearSelection();
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
    const userMessage = createChatMessage("user", userMessageContent, "ready", undefined, submittedSelection);
    const pendingAssistantMessage: ChatMessage = {
      ...createChatMessage("assistant", "", "pending"),
      statusLabel: "正在保存当前文档",
    };
    const baseStreamingDocument = currentBoardDocument ?? activeLesson.board_document;
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
      ...current,
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
      updatePendingAssistant(lessonId, pendingAssistantMessage.id, { statusLabel: "正在回复" });
      const response = await api.streamChatOnLesson(lessonId, payloadWithConversation, {
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
          setStreamingDocumentPreview({
            ...baseStreamingDocument,
            content_json: {},
            content_html: streamingMarkdownToHtml(streamedDocumentText),
            content_text: streamedDocumentText,
          });
        },
        onRequirementUpdate(payload) {
          setClarificationQuestions(payload.clarification_questions);
          setLearningClarity(payload.learning_clarification);
          setStreamedRequirementSheet(payload.active_requirement_sheet ?? payload.learning_requirement_sheet);
        },
        onBoardTaskUpdate(payload) {
          setStreamedBoardTaskSheet(payload.active_board_task_sheet ?? payload.board_task_sheet);
        },
      });
      updateCoursePackage(response.course_package, {
        activeLessonId: response.created_lesson ? undefined : lessonId,
      });
      setLatestBoardDecision(response.board_decision);
      setClarificationQuestions(response.clarification_questions);
      setLearningClarity(response.learning_clarification);
      setStreamedRequirementSheet(
        response.requirement_cleared ? null : response.active_requirement_sheet ?? response.learning_requirement_sheet
      );
      setStreamedBoardTaskSheet(response.active_board_task_sheet ?? response.board_task_sheet ?? null);
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
    latestBoardDecision,
    referencePrompt,
    boardEditPrompt,
    selectedReference,
    resetAgentState,
    handleSubmitChat,
    handleScopeAction,
    handleReferenceAction,
    handleBoardEditAction,
    handleContinueTeaching,
  };
}
