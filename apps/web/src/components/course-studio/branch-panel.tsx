import clsx from "clsx";
import { BrainCircuit, GitBranch } from "lucide-react";

import type { BoardDecision, CommitRecord, Lesson } from "@/types";

type BranchPanelProps = {
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  activeRequirements: Lesson["learning_requirements"];
  activeBoardTask: Lesson["board_task_requirements"];
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
  activeBoardTask,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onCreateBranch,
  onSwitchBranch,
}: BranchPanelProps) {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">分支管理</p>
        <div className="mt-4 flex gap-2">
          <input
            value={newBranchName}
            onChange={(event) => onNewBranchNameChange(event.target.value)}
            placeholder="新分支名"
            className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm outline-none focus:border-black"
          />
          <button
            type="button"
            onClick={() => void onCreateBranch()}
            className="rounded-xl bg-[#1a1a1a] px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-white"
          >
            <GitBranch className="mr-1.5 inline h-3.5 w-3.5" />
            开分支
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-5 text-gray-400">
          {previewCommit
            ? `当前会从历史节点「${previewCommit.label}」开启分支；未填写名称时会自动生成。`
            : "先在 History 中 Preview 某个节点，或直接从当前最新节点开启分支。未填写名称时会自动生成。"}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {Object.values(activeLesson.history_graph.branches).map((branch) => (
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
              {branch.name}
            </button>
          ))}
        </div>
      </div>

      <div className="border-t border-gray-200 pt-6">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-gray-400" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">需求清单</p>
        </div>
        <p className="mt-4 text-sm leading-7 text-gray-700">
          {activeBoardTask?.question_or_topic ?? activeRequirements?.learning_goal ?? "等待下一次任务需求：说明要操作的位置、动作类型，以及希望怎么讲解或怎么编写。"}
        </p>
        <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
          <p className="text-xs font-semibold text-gray-900">
            {activeBoardTask?.requested_action ?? activeRequirements?.action_type ?? activeRequirements?.target_depth ?? "暂无待执行任务"}
          </p>
          <p className="mt-2 text-[11px] leading-6 text-gray-500">
            {activeBoardTask?.target_hint || activeRequirements?.action_instruction || activeRequirements?.success_criteria || "执行完成后，当前清单会归档到历史并清空。"}
          </p>
        </div>
        {latestBoardDecision ? (
          <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
            <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
