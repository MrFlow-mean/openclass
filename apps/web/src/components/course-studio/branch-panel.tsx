"use client";

import clsx from "clsx";
import { BrainCircuit, GitBranch } from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type { BoardDecision, CommitRecord, Lesson } from "@/types";

type BranchPanelProps = {
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  onCreateBranch: () => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
};

export function BranchPanel({
  activeLesson,
  previewCommit,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onCreateBranch,
  onSwitchBranch,
}: BranchPanelProps) {
  const { texts: txt } = useInterfaceLanguage();
  const b = txt.studio.branchPanel;
  return (
    <div className="space-y-8">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{b.title}</p>
        <div className="mt-4 flex gap-2">
          <input
            value={newBranchName}
            onChange={(event) => onNewBranchNameChange(event.target.value)}
            placeholder={b.newBranchPlaceholder}
            className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm outline-none focus:border-black"
          />
          <button
            type="button"
            onClick={() => void onCreateBranch()}
            className="rounded-xl bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
          >
            <GitBranch className="mr-1.5 inline h-3.5 w-3.5" />
            {b.createBranch}
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-5 text-gray-400">
          {previewCommit
            ? b.fromPreview(previewCommit.label)
            : b.fromCurrent}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.values(activeLesson.history_graph.branches).map((branch) => {
            const branchLabel = txt.studio.branchDisplayName(branch.name);
            return (
              <button
                key={branch.name}
                type="button"
                onClick={() => void onSwitchBranch(branch.name)}
                className={clsx(
                  "rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-[0.16em] transition",
                  activeLesson.history_graph.current_branch === branch.name
                    ? "border-black bg-black text-white"
                    : "border-gray-200 bg-white text-gray-500 hover:text-black"
                )}
              >
                {branchLabel}
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-gray-200 pt-6">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-gray-400" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{b.requirementsTitle}</p>
        </div>
        <p className="mt-4 text-sm leading-7 text-gray-700">
          {activeRequirements?.learning_goal ?? b.requirementsEmpty}
        </p>
        <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
          <p className="text-xs font-semibold text-gray-900">
            {activeRequirements?.action_type ?? activeRequirements?.target_depth ?? b.noPendingTask}
          </p>
          <p className="mt-2 text-[11px] leading-6 text-gray-500">
            {activeRequirements?.action_instruction || activeRequirements?.success_criteria || b.clearAfterDone}
          </p>
        </div>
        {latestBoardDecision ? (
          <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{b.boardDecisionTitle}</p>
            <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
