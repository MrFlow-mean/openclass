import clsx from "clsx";
import { Eye, GitBranch, RotateCcw } from "lucide-react";

import { BranchSequenceSelector, type BranchSequenceOption } from "@/components/branch-sequence-selector";
import {
  compactText,
  formatDate,
  metadataBool,
  metadataText,
} from "@/components/course-studio/history-utils";
import type { CommitRecord } from "@/types";

type CommitTimelineItemProps = {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
  current: boolean;
  branchLabel: string;
  branchLane: number;
  branchOrder: number;
  isBranchHead: boolean;
  isCurrentBranch: boolean;
  parentLabel: string | null;
  childCount: number;
  branchSequence: BranchSequenceOption[];
  currentBranchName: string;
  onPreview: () => void;
  onRestore: () => void;
  onBranch: () => void;
  onSwitchBranch: (branchName: string) => void;
};

export function CommitTimelineItem({
  commit,
  active,
  latest,
  current,
  branchLabel,
  branchLane,
  branchOrder,
  isBranchHead,
  isCurrentBranch,
  parentLabel,
  childCount,
  branchSequence,
  currentBranchName,
  onPreview,
  onRestore,
  onBranch,
  onSwitchBranch,
}: CommitTimelineItemProps) {
  const isChatFlow = commit.metadata?.kind === "chat_flow";
  const isAutoSave = metadataBool(commit, "autosave") || commit.metadata?.kind === "auto_document_save";
  const userMessage = metadataText(commit, "user_message");
  const assistantMessage = metadataText(commit, "assistant_message");
  const boardAction = metadataText(commit, "board_action");
  const autoApplied = metadataBool(commit, "auto_applied");
  const visibleLane = Math.min(branchLane, 3);
  const laneOffset = visibleLane * 12 + 14;

  return (
    <div className={clsx("relative grid grid-cols-[3.25rem_minmax(0,1fr)] gap-3 pb-4", active && "rounded-lg bg-blue-50/40")}>
      <div className="relative min-h-16">
        <div
          className={clsx("absolute top-0 h-full w-px", current ? "bg-black" : latest ? "bg-gray-500" : "bg-gray-200")}
          style={{ left: laneOffset }}
        />
        {visibleLane > 0 ? (
          <div
            className={clsx("absolute top-3 h-px bg-gray-200", isCurrentBranch && "bg-gray-400")}
            style={{ left: 6, width: laneOffset }}
          />
        ) : null}
        <div
          className={clsx(
            "absolute top-1.5 flex h-6 w-6 items-center justify-center rounded-md border text-[10px] font-bold",
            current
              ? "border-black bg-black text-white"
              : active
                ? "border-blue-300 bg-white text-blue-700"
                : isBranchHead
                  ? "border-gray-300 bg-white text-gray-700"
                  : "border-gray-200 bg-white text-gray-400"
          )}
          style={{ left: laneOffset - 12 }}
          aria-hidden="true"
        >
          {branchOrder}
        </div>
      </div>

      <div
        className={clsx(
          "min-w-0 rounded-lg border bg-white p-3 transition",
          current
            ? "border-black shadow-sm"
            : active
              ? "border-blue-200 shadow-sm"
              : "border-gray-100 hover:border-gray-200"
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={clsx(
              "rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em]",
              isCurrentBranch ? "bg-black text-white" : "bg-gray-100 text-gray-500"
            )}
          >
            {branchLabel}
          </span>
          {current ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-emerald-700">
              Current
            </span>
          ) : null}
          {isBranchHead && !current ? (
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-gray-600">
              Head
            </span>
          ) : null}
          {isChatFlow ? (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-blue-700">
              Flow
            </span>
          ) : null}
          {autoApplied ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-emerald-700">
              Applied
            </span>
          ) : null}
          {isAutoSave ? (
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-stone-600">
              Auto Save
            </span>
          ) : null}
        </div>
        <div className="mt-2 min-w-0">
          <p className={clsx("truncate text-xs font-bold", current ? "text-black" : "text-gray-800")}>{commit.label}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-400">
            <span>{formatDate(commit.created_at)}</span>
            {parentLabel ? <span>基于 {compactText(parentLabel, 42)}</span> : null}
            {childCount > 0 ? <span>{childCount} 个后续节点</span> : null}
          </div>
        </div>
        {isChatFlow && userMessage ? (
          <div className="mt-2 rounded-lg border border-gray-100 bg-white p-3 text-[11px] leading-5 text-gray-600 shadow-sm">
            <p className="font-bold text-gray-400">用户输入</p>
            <p className="mt-1 text-gray-700">{compactText(userMessage)}</p>
            {assistantMessage ? (
              <>
                <p className="mt-3 font-bold text-gray-400">AI 讲解</p>
                <p className="mt-1 text-gray-700">{compactText(assistantMessage)}</p>
              </>
            ) : null}
            {boardAction ? (
              <p className="mt-3 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                Action: {boardAction}
              </p>
            ) : null}
          </div>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-[11px] text-gray-500">{commit.message}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onPreview}
            className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <Eye className="h-3 w-3" />
            Preview
          </button>
          <button
            type="button"
            onClick={onRestore}
            className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <RotateCcw className="h-3 w-3" />
            Restore
          </button>
          <button
            type="button"
            onClick={onBranch}
            className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <GitBranch className="h-3 w-3" />
            Branch
          </button>
        </div>
        <BranchSequenceSelector
          branches={branchSequence}
          currentBranchName={currentBranchName}
          onSelectBranch={onSwitchBranch}
        />
      </div>
    </div>
  );
}
