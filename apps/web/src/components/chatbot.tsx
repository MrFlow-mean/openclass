"use client";

import { useState } from "react";
import clsx from "clsx";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  LoaderCircle,
  MessageSquare,
  Sparkles,
  TextQuote,
} from "lucide-react";

import type { SectionTeachingProgress, SelectionRef } from "@/types";

export type CourseChatMessageView = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "ready" | "pending" | "error";
  selection?: SelectionRef | null;
  teachingProgress?: SectionTeachingProgress | null;
};

function selectionPreviewLabel(selection: SelectionRef): string {
  return selection.kind === "board" ? "选中的讲义" : "引用的对话";
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
              {paragraph}
            </p>
          ));
      })}
    </div>
  );
}

export function CourseChatMessage({
  message,
  onContinueTeaching,
}: {
  message: CourseChatMessageView;
  onContinueTeaching?: () => void;
}) {
  const [isSelectionExpanded, setIsSelectionExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const isAssistant = message.role === "assistant";
  const isPending = message.status === "pending";
  const isError = message.status === "error";
  const selectedExcerpt = message.selection?.excerpt ? selectionPreviewText(message.selection.excerpt) : "";
  const teachingProgress = message.teachingProgress;
  const hasContent = message.content.trim().length > 0;

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
          <span className="font-medium">{isPending ? "正在思考" : isAssistant ? "OpenClass" : "你"}</span>
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
                <p className="text-[11px] font-semibold text-gray-500">{selectionPreviewLabel(message.selection)}</p>
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
                  {isSelectionExpanded ? "收起" : "展开"}
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
                  : isAssistant
                    ? "rounded-tl-md border border-gray-200 bg-white text-gray-900"
                    : "ml-auto rounded-tr-md bg-[#1a1a1a] text-white"
            )}
          >
            {hasContent ? (
              <ChatMessageContent content={message.content} />
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
                  第 {teachingProgress.section_index + 1}/{teachingProgress.section_count} 节
                  {teachingProgress.current_section_title ? `：${teachingProgress.current_section_title}` : ""}
                </span>
                {teachingProgress.has_next_section && onContinueTeaching ? (
                  <button
                    type="button"
                    onClick={onContinueTeaching}
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 font-semibold text-gray-700 transition hover:border-gray-300 hover:text-gray-950"
                  >
                    <ArrowRight className="h-3.5 w-3.5" />
                    继续下一节
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {hasContent ? (
          <button
            type="button"
            onClick={() => void copyMessage()}
            className={clsx(
              "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] font-medium text-gray-400 opacity-0 transition hover:bg-gray-100 hover:text-gray-700 group-hover:opacity-100",
              !isAssistant && "float-right"
            )}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "已复制" : "复制"}
          </button>
        ) : null}
      </div>
    </article>
  );
}
