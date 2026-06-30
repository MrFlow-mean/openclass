import clsx from "clsx";
import { GitBranch } from "lucide-react";

import { BranchSequenceSelector, type BranchSequenceOption } from "@/components/branch-sequence-selector";
import {
  compactText,
  formatDate,
  metadataBool,
  metadataText,
} from "@/components/course-studio/history-utils";
import type { CommitRecord, DiffPreviewItem } from "@/types";

type CommitTimelineItemProps = {
  commit: CommitRecord;
  active: boolean;
  latest: boolean;
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
  const boardPatchDiff = Array.isArray(commit.metadata?.board_patch_diff) ? commit.metadata.board_patch_diff : [];
  const patchRisk = commit.metadata?.board_patch_risk_level;
  const operationCount = boardPatchDiff.length || commit.operations.length;

  return (
    <div className="relative flex gap-4 pl-3">
      <div className={clsx("absolute left-0 top-1.5 h-full w-px", latest ? "bg-black" : "bg-gray-200")} />
      <div
        className={clsx(
          "absolute -left-[4px] top-1.5 h-2 w-2 rounded-full",
          latest ? "bg-black" : active ? "bg-gray-500" : "bg-gray-300"
        )}
      />
      <div className="flex-1 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className={clsx("text-xs font-bold", latest ? "text-black" : "text-gray-800")}>{commit.label}</p>
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
          {operationCount > 0 ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.16em] text-emerald-700">
              {operationCount} Patch{operationCount > 1 ? "es" : ""}
            </span>
          ) : null}
        </div>
        {isChatFlow && userMessage ? (
          <div className="mt-2 rounded-xl border border-gray-100 bg-white p-3 text-[11px] leading-5 text-gray-600 shadow-sm">
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
        {boardPatchDiff.length ? (
          <div className="mt-2 border-l-2 border-emerald-200 pl-3 text-[11px] leading-5 text-gray-600">
            <p className="font-bold text-gray-400">
              Board patch{patchRisk ? ` · ${patchRisk}` : ""}
            </p>
            {boardPatchDiff.slice(0, 3).map((item, index) => (
              <p key={`${commit.id}:patch:${index}`} className="mt-1 text-gray-700">
                {patchOpLabel(item)} {compactText(item.summary || item.after_text || item.before_text || "", 96)}
              </p>
            ))}
          </div>
        ) : null}
        <p className="mt-1 text-[11px] text-gray-400">{formatDate(commit.created_at)}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onPreview}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Preview
          </button>
          <button
            type="button"
            onClick={onRestore}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            Restore
          </button>
          <button
            type="button"
            onClick={onBranch}
            className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-gray-500 hover:border-gray-300 hover:text-black"
          >
            <GitBranch className="mr-1 inline h-3 w-3" />
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

function patchOpLabel(item: DiffPreviewItem) {
  if (item.op === "insert_block") {
    return "+";
  }
  if (item.op === "delete_block") {
    return "-";
  }
  return "~";
}
