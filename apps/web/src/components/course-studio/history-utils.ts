import type { BranchSequenceOption } from "@/components/branch-sequence-selector";
import type { CourseChatMessageView } from "@/components/chatbot";
import { normalizePageSettings } from "@/components/course-studio/page-settings";
import type {
  BoardDocument,
  ChatInteractionMode,
  CommitRecord,
  LearningClarificationStatus,
  LearningRequirementKeyFact,
  Lesson,
  SelectionRef,
  SectionTeachingProgress,
} from "@/types";

export type ChatMessage = CourseChatMessageView;

export type LessonMessageMap = Record<string, ChatMessage[]>;
export type LessonComposerState = {
  chatInput: string;
  composerMode: ChatInteractionMode;
  includeSelectionInPrompt: boolean;
};
export type LessonComposerStateMap = Record<string, LessonComposerState>;

export const DEFAULT_LESSON_COMPOSER_STATE: LessonComposerState = {
  chatInput: "",
  composerMode: "ask",
  includeSelectionInPrompt: true,
};

export const AUTO_SAVE_DELAY_MS = 1600;

export function createChatMessage(
  role: ChatMessage["role"],
  content: string,
  status: ChatMessage["status"] = "ready",
  id?: string,
  selection?: SelectionRef | null,
  teachingProgress?: SectionTeachingProgress | null,
  metadata?: Partial<
    Pick<
      ChatMessage,
      "commitId" | "parentCommitIds" | "editableContent" | "interactionMode" | "editedFromCommitId"
    >
  >
): ChatMessage {
  return {
    id: id ?? crypto.randomUUID(),
    role,
    content,
    status,
    ...(selection ? { selection } : {}),
    ...(teachingProgress ? { teachingProgress } : {}),
    ...metadata,
  };
}

export function createLessonComposerState(): LessonComposerState {
  return { ...DEFAULT_LESSON_COMPOSER_STATE };
}

export function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function metadataText(commit: CommitRecord, key: string): string | null {
  const value = commit.metadata?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export function metadataBool(commit: CommitRecord, key: string): boolean {
  return commit.metadata?.[key] === true;
}

function metadataLearningClarificationForcedStart(commit: CommitRecord): boolean {
  const value = commit.metadata?.learning_clarification;
  return Boolean(value && typeof value === "object" && (value as { forced_start?: unknown }).forced_start === true);
}

const LEGACY_NON_AI_ASSISTANT_PATTERNS = [
  "给我一个关键词",
  "从那里开讲",
  "我们先找一个小入口",
  "这次没有拿到可用的临场讲解内容",
  "没有写入板书",
  "模型没有返回可用",
  "请求已发出，生成讲义可能需要",
] as const;

const DISPLAYABLE_CHAT_COMMIT_KINDS = new Set([
  "chat_flow",
  "board_document_generation",
  "board_document_edit",
  "basic_chat",
  "learning_requirement_refinement",
]);

function isDisplayableAssistantContent(content: string | null, source?: string | null): content is string {
  const text = content?.trim();
  if (!text) {
    return false;
  }
  if (source && !["ai", "chatbot", "board_document_editor_ai", "workflow"].includes(source)) {
    return false;
  }
  return !LEGACY_NON_AI_ASSISTANT_PATTERNS.some((pattern) => text.includes(pattern));
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
    document_id: typeof raw.document_id === "string" ? raw.document_id : null,
    segment_id: typeof raw.segment_id === "string" ? raw.segment_id : null,
    heading_path: Array.isArray(raw.heading_path) ? raw.heading_path.filter((item): item is string => typeof item === "string") : [],
    before_text: typeof raw.before_text === "string" ? raw.before_text : "",
    after_text: typeof raw.after_text === "string" ? raw.after_text : "",
    text_hash: typeof raw.text_hash === "string" ? raw.text_hash : null,
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

export function currentHeadCommitId(lesson: Lesson): string | null {
  const branch = lesson.history_graph.branches[lesson.history_graph.current_branch];
  return (
    branch?.head_commit_id ??
    lesson.history_graph.commits[lesson.history_graph.commits.length - 1]?.id ??
    null
  );
}

export function getLessonCommit(lesson: Lesson, commitId: string | null | undefined): CommitRecord | null {
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

  if (metadataText(commit, "board_generation_action") === "start") {
    return "开始生成板书";
  }

  return metadataText(commit, "interaction_mode") === "direct_edit" ? `直接编辑讲义：${userMessage}` : userMessage;
}

function chatInteractionModeFromCommit(commit: CommitRecord): ChatInteractionMode {
  return metadataText(commit, "interaction_mode") === "direct_edit" ? "direct_edit" : "ask";
}

export function buildLessonMessagesFromHistory(lesson: Lesson, commitId?: string | null): ChatMessage[] {
  const targetCommitId = conversationTargetCommitId(lesson, commitId);
  const lineageIds = commitLineageIds(lesson, targetCommitId);
  const messages: ChatMessage[] = [];

  lesson.history_graph.commits.forEach((commit) => {
    if (!lineageIds.has(commit.id) || !DISPLAYABLE_CHAT_COMMIT_KINDS.has(String(commit.metadata?.kind ?? ""))) {
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
          selectionFromMetadata(commit.metadata?.selection),
          null,
          {
            commitId: commit.id,
            parentCommitIds: commit.parent_ids,
            editableContent: metadataText(commit, "user_message") ?? userContent,
            interactionMode: chatInteractionModeFromCommit(commit),
            editedFromCommitId: metadataText(commit, "chat_edit_source_commit_id"),
          }
        )
      );
    }

    const assistantMessage = metadataText(commit, "assistant_message");
    const assistantMessageSource = metadataText(commit, "assistant_message_source");
    const legacyChatbotGeneratedDuringHandoff =
      metadataLearningClarificationForcedStart(commit) && assistantMessageSource === "ai";
    if (!legacyChatbotGeneratedDuringHandoff && isDisplayableAssistantContent(assistantMessage, assistantMessageSource)) {
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
  });

  return messages;
}

export function learningClarityFromCommit(commit: CommitRecord | null): LearningClarificationStatus | null {
  const value = commit?.metadata?.learning_clarification_after ?? commit?.metadata?.learning_clarification;
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
    summary: typeof record.summary === "string" ? record.summary : "",
    key_facts: Array.isArray(record.key_facts)
      ? record.key_facts
          .flatMap((item) => {
            if (!item || typeof item !== "object") {
              return [];
            }
            const raw = item as unknown as Record<string, unknown>;
            if (typeof raw.label !== "string" || typeof raw.value !== "string") {
              return [];
            }
            return [
              {
                label: raw.label,
                value: raw.value,
                evidence: typeof raw.evidence === "string" ? raw.evidence : "",
                category: typeof raw.category === "string" ? (raw.category as LearningRequirementKeyFact["category"]) : null,
              },
            ];
          })
          .slice(0, 5)
      : [],
    checklist: Array.isArray(record.checklist)
      ? record.checklist
          .flatMap((item) => {
            if (!item || typeof item !== "object") {
              return [];
            }
            const raw = item as unknown as Record<string, unknown>;
            if (typeof raw.title !== "string") {
              return [];
            }
            return [
              {
                title: raw.title,
                is_clear: raw.is_clear === true,
                evidence: typeof raw.evidence === "string" ? raw.evidence : "",
              },
            ];
          })
      : [],
    next_question: typeof record.next_question === "string" ? record.next_question : "",
    ready_for_board: record.ready_for_board === true,
    work_mode:
      record.work_mode === "knowledge_board" ||
      record.work_mode === "narrow_topic" ||
      record.work_mode === "practice_artifact" ||
      record.work_mode === "unknown"
        ? record.work_mode
        : null,
    granularity:
      record.granularity === "single_knowledge_point" ||
      record.granularity === "broad_topic" ||
      record.granularity === "practice_artifact" ||
      record.granularity === "unclear"
        ? record.granularity
        : null,
  };
}

export function compactText(value: string, limit = 120) {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= limit) {
    return compact;
  }
  return `${compact.slice(0, limit - 1)}…`;
}

export function nextBranchName(lesson: Lesson) {
  let index = Object.keys(lesson.history_graph.branches).length + 1;
  let name = `branch-${index}`;
  while (lesson.history_graph.branches[name]) {
    index += 1;
    name = `branch-${index}`;
  }
  return name;
}

export function nextEditBranchName(lesson: Lesson) {
  let index = 1;
  let name = `edit-${index}`;
  while (lesson.history_graph.branches[name]) {
    index += 1;
    name = `edit-${index}`;
  }
  return name;
}

export function branchSequenceForCommit(lesson: Lesson, commit: CommitRecord): BranchSequenceOption[] {
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

export function documentsEqual(left: BoardDocument | null | undefined, right: BoardDocument | null | undefined) {
  if (!left || !right) {
    return false;
  }
  return (
    left.title === right.title &&
    JSON.stringify(left.content_json) === JSON.stringify(right.content_json) &&
    left.content_html === right.content_html &&
    left.content_text === right.content_text &&
    JSON.stringify(normalizePageSettings(left.page_settings)) ===
      JSON.stringify(normalizePageSettings(right.page_settings))
  );
}

function htmlToPlainText(value: string) {
  return value
    .replace(/<\/(h[1-6]|p|li|blockquote|tr)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .trim();
}

export function isBoardDocumentEmpty(document: BoardDocument | null | undefined) {
  if (!document) {
    return true;
  }
  return !document.content_text.trim() && !htmlToPlainText(document.content_html).trim();
}
