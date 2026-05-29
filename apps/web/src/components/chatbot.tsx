"use client";

import { useState, type ReactNode } from "react";
import clsx from "clsx";
import katex from "katex";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Sparkles,
  TextQuote,
  X,
} from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { SectionTeachingProgress, SelectionRef } from "@/types";

export type CourseChatMessageView = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "ready" | "pending" | "error";
  statusLabel?: string;
  selection?: SelectionRef | null;
  teachingProgress?: SectionTeachingProgress | null;
  commitId?: string;
  canEdit?: boolean;
  branchAlternatives?: Array<{
    order: number;
    commitId: string;
    branchName: string;
    message: string;
    createdAt: string;
    isCurrent: boolean;
  }>;
};

function selectionPreviewLabel(
  selection: SelectionRef,
  labels: { boardSelection: string; chatSelection: string }
): string {
  return selection.kind === "board" ? labels.boardSelection : labels.chatSelection;
}

function selectionPreviewText(excerpt: string): string {
  return excerpt.replace(/\s+/g, " ").trim();
}

function splitMarkdownBlocks(content: string): Array<{ kind: "text" | "code"; value: string }> {
  const blocks: Array<{ kind: "text" | "code"; value: string }> = [];
  const pattern = /```(?:\w+)?\n?([\s\S]*?)```/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(content)) !== null) {
    if (match.index > cursor) {
      blocks.push({ kind: "text", value: content.slice(cursor, match.index) });
    }
    blocks.push({ kind: "code", value: match[1].trimEnd() });
    cursor = pattern.lastIndex;
  }
  if (cursor < content.length) {
    blocks.push({ kind: "text", value: content.slice(cursor) });
  }
  return blocks.length ? blocks : [{ kind: "text", value: content }];
}

type MathSegment =
  | {
      kind: "text";
      value: string;
    }
  | {
      kind: "math";
      displayMode: boolean;
      value: string;
    };

const CHAT_MATH_PATTERN = /\\\((.+?)\\\)|\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$|\$(?!\d+\$)([^$\n]+?)\$(?!\d)/g;

function splitMathSegments(content: string): MathSegment[] {
  const segments: MathSegment[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  CHAT_MATH_PATTERN.lastIndex = 0;
  while ((match = CHAT_MATH_PATTERN.exec(content)) !== null) {
    if (match.index > cursor) {
      segments.push({ kind: "text", value: content.slice(cursor, match.index) });
    }
    segments.push({
      kind: "math",
      displayMode: Boolean(match[2] || match[3]),
      value: (match[1] ?? match[2] ?? match[3] ?? match[4] ?? "").trim(),
    });
    cursor = CHAT_MATH_PATTERN.lastIndex;
  }

  if (cursor < content.length) {
    segments.push({ kind: "text", value: content.slice(cursor) });
  }

  return segments.length ? segments : [{ kind: "text", value: content }];
}

function renderMath(latex: string, displayMode: boolean) {
  return katex.renderToString(latex, {
    displayMode,
    throwOnError: false,
    strict: "ignore",
  });
}

function TextWithMath({ content }: { content: string }) {
  const nodes: ReactNode[] = [];

  splitMathSegments(content).forEach((segment, index) => {
    if (segment.kind === "text") {
      if (segment.value) {
        nodes.push(segment.value);
      }
      return;
    }

    nodes.push(
      <span
        key={`math-${index}`}
        className={clsx(
          "max-w-full overflow-x-auto align-middle",
          segment.displayMode ? "my-2 block" : "inline-block"
        )}
        dangerouslySetInnerHTML={{ __html: renderMath(segment.value, segment.displayMode) }}
      />
    );
  });

  return <>{nodes}</>;
}

function ChatMessageContent({ content }: { content: string }) {
  return (
    <div className="space-y-3">
      {splitMarkdownBlocks(content).map((block, index) => {
        if (block.kind === "code") {
          return (
            <pre
              key={`${block.kind}-${index}`}
              className="custom-scrollbar overflow-x-auto rounded-lg bg-gray-950 px-3 py-2 text-[12px] leading-5 text-gray-50"
            >
              <code>{block.value}</code>
            </pre>
          );
        }
        return block.value
          .split(/\n{2,}/)
          .map((paragraph) => paragraph.trim())
          .filter(Boolean)
          .map((paragraph, paragraphIndex) => (
            <p key={`${block.kind}-${index}-${paragraphIndex}`} className="whitespace-pre-wrap break-words">
              <TextWithMath content={paragraph} />
            </p>
          ));
      })}
    </div>
  );
}

export function CourseChatMessage({
  message,
  onContinueTeaching,
  onEditMessage,
  onSwitchBranch,
  isBusy = false,
}: {
  message: CourseChatMessageView;
  onContinueTeaching?: () => void;
  onEditMessage?: (message: CourseChatMessageView, nextContent: string) => void | Promise<void>;
  onSwitchBranch?: (branchName: string) => void | Promise<void>;
  isBusy?: boolean;
}) {
  const { texts: txt } = useInterfaceLanguage();
  const m = txt.studio.chatMessage;
  const c = txt.common;
  const [isSelectionExpanded, setIsSelectionExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editDraft, setEditDraft] = useState(message.content);
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false);
  const isAssistant = message.role === "assistant";
  const isPending = message.status === "pending";
  const isError = message.status === "error";
  const selectedExcerpt = message.selection?.excerpt ? selectionPreviewText(message.selection.excerpt) : "";
  const teachingProgress = message.teachingProgress;
  const hasContent = message.content.trim().length > 0;
  const branchAlternatives = message.branchAlternatives ?? [];
  const currentBranchIndex = branchAlternatives.findIndex((branch) => branch.isCurrent);
  const activeBranchIndex = currentBranchIndex >= 0 ? currentBranchIndex : 0;
  const previousBranch = activeBranchIndex > 0 ? branchAlternatives[activeBranchIndex - 1] : null;
  const nextBranch =
    activeBranchIndex >= 0 && activeBranchIndex < branchAlternatives.length - 1
      ? branchAlternatives[activeBranchIndex + 1]
      : null;
  const canEditMessage = !isAssistant && !isPending && message.canEdit && Boolean(message.commitId) && onEditMessage;
  const showBranchControls = !isAssistant && branchAlternatives.length > 1 && onSwitchBranch;

  async function copyMessage() {
    if (!hasContent) {
      return;
    }
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  async function submitEditedMessage() {
    const nextContent = editDraft.trim();
    if (!nextContent || !onEditMessage || isBusy || isSubmittingEdit) {
      return;
    }
    setIsSubmittingEdit(true);
    try {
      await onEditMessage(message, nextContent);
      setIsEditing(false);
    } finally {
      setIsSubmittingEdit(false);
    }
  }

  function switchToBranch(branchName: string | null | undefined) {
    if (!branchName || !onSwitchBranch || isBusy) {
      return;
    }
    void onSwitchBranch(branchName);
  }

  return (
    <article className={clsx("group flex gap-3", !isAssistant && "justify-end")}>
      {isAssistant ? (
        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-900 shadow-sm">
          {isPending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
        </div>
      ) : null}

      <div className={clsx("min-w-0 max-w-[86%] space-y-2", isAssistant && "max-w-[calc(100%-2.5rem)] flex-1")}>
        <div className={clsx("flex items-center gap-2 text-[11px] text-gray-500", !isAssistant && "justify-end")}>
          {!isAssistant ? <MessageSquare className="h-3.5 w-3.5" /> : null}
          <span className="font-medium">{isPending ? message.statusLabel || m.thinking : isAssistant ? "OpenClass" : m.user}</span>
        </div>

        {message.selection && selectedExcerpt ? (
          <div
            className={clsx(
              "rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-gray-700",
              !isAssistant && "ml-auto"
            )}
          >
            <div className="flex items-start gap-2">
              <TextQuote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gray-400" />
              <div className="min-w-0">
                <p className="text-[11px] font-semibold text-gray-500">{selectionPreviewLabel(message.selection, m)}</p>
                <p
                  className={clsx(
                    "mt-1 whitespace-pre-wrap break-words pr-1 text-[12px] leading-5",
                    isSelectionExpanded ? "custom-scrollbar max-h-40 overflow-y-auto" : "max-h-10 overflow-hidden"
                  )}
                >
                  “{selectedExcerpt}”
                </p>
                <button
                  type="button"
                  aria-expanded={isSelectionExpanded}
                  onClick={() => setIsSelectionExpanded((current) => !current)}
                  className="mt-2 inline-flex h-6 items-center gap-1 rounded-md px-1.5 text-[11px] font-semibold text-gray-500 transition hover:bg-white hover:text-gray-900"
                >
                  {isSelectionExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                  {isSelectionExpanded ? m.collapse : m.expand}
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {hasContent || isPending ? (
          <div
            className={clsx(
              "rounded-2xl px-4 py-3 text-[13px] leading-6 shadow-sm",
              isPending
                ? "rounded-tl-md border border-gray-200 bg-white text-gray-600"
                : isError
                  ? "rounded-tl-md border border-rose-200 bg-rose-50 text-rose-800"
                  : isEditing
                    ? "ml-auto rounded-tr-md border border-gray-200 bg-white text-gray-900"
                    : isAssistant
                      ? "rounded-tl-md border border-gray-200 bg-white text-gray-900"
                      : "ml-auto rounded-tr-md bg-[#1a1a1a] text-white"
            )}
          >
            {hasContent ? (
              isEditing ? (
                <form
                  className="space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void submitEditedMessage();
                  }}
                >
                  <textarea
                    value={editDraft}
                    autoFocus
                    disabled={isBusy || isSubmittingEdit}
                    rows={Math.min(8, Math.max(3, editDraft.split(/\r?\n/).length))}
                    onChange={(event) => setEditDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                        event.preventDefault();
                        void submitEditedMessage();
                      }
                    }}
                    className="custom-scrollbar min-h-20 w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2 text-[13px] leading-6 text-gray-900 outline-none transition focus:border-gray-400 disabled:cursor-wait disabled:text-gray-400"
                  />
                  <div className="flex justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        setEditDraft(message.content);
                        setIsEditing(false);
                      }}
                      disabled={isSubmittingEdit}
                      title={m.cancelEdit}
                      aria-label={m.cancelEdit}
                      className="flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 disabled:cursor-wait disabled:opacity-60"
                    >
                      <X className="h-4 w-4" />
                    </button>
                    <button
                      type="submit"
                      disabled={isBusy || isSubmittingEdit || !editDraft.trim()}
                      title={m.submitEdit}
                      aria-label={m.submitEdit}
                      className="flex h-8 w-8 items-center justify-center rounded-md bg-gray-900 text-white transition hover:bg-black disabled:cursor-wait disabled:opacity-60"
                    >
                      {isSubmittingEdit ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    </button>
                  </div>
                </form>
              ) : (
                <ChatMessageContent content={message.content} />
              )
            ) : (
              <div className="flex items-center gap-1.5 py-1">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400" />
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400 [animation-delay:120ms]" />
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400 [animation-delay:240ms]" />
              </div>
            )}
            {isAssistant && teachingProgress && !isPending && !isError ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-200 pt-3 text-[11px] text-gray-500">
                <span>
                  {m.sectionProgress(
                    teachingProgress.section_index,
                    teachingProgress.section_count,
                    teachingProgress.current_section_title
                  )}
                </span>
                {teachingProgress.has_next_section && onContinueTeaching ? (
                  <button
                    type="button"
                    onClick={onContinueTeaching}
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 font-semibold text-gray-700 transition hover:border-gray-300 hover:text-gray-950"
                  >
                    <ArrowRight className="h-3.5 w-3.5" />
                    {m.continueNextSection}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {hasContent && !isEditing ? (
          <div
            className={clsx(
              "flex h-7 items-center gap-1 opacity-0 transition group-hover:opacity-100",
              !isAssistant && "justify-end"
            )}
          >
            {showBranchControls ? (
              <div className="flex items-center gap-0.5 rounded-md border border-gray-200 bg-white px-0.5 text-gray-500 shadow-sm">
                <button
                  type="button"
                  onClick={() => switchToBranch(previousBranch?.branchName)}
                  disabled={!previousBranch || isBusy}
                  title={m.previousBranch}
                  aria-label={m.previousBranch}
                  className="flex h-6 w-6 items-center justify-center rounded text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <span className="min-w-8 text-center text-[11px] font-semibold text-gray-700">
                  {activeBranchIndex + 1}/{branchAlternatives.length}
                </span>
                <button
                  type="button"
                  onClick={() => switchToBranch(nextBranch?.branchName)}
                  disabled={!nextBranch || isBusy}
                  title={m.nextBranch}
                  aria-label={m.nextBranch}
                  className="flex h-6 w-6 items-center justify-center rounded text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : null}
            {canEditMessage ? (
              <button
                type="button"
                onClick={() => {
                  setEditDraft(message.content);
                  setIsEditing(true);
                }}
                disabled={isBusy}
                title={m.editMessage}
                aria-label={m.editMessage}
                className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 disabled:cursor-wait disabled:opacity-60"
              >
                <PencilLine className="h-3.5 w-3.5" />
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void copyMessage()}
              title={copied ? c.copied : c.copy}
              aria-label={copied ? c.copied : c.copy}
              className="flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}
