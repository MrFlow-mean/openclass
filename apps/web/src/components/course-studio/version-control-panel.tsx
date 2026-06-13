import clsx from "clsx";
import { BrainCircuit, GitBranch } from "lucide-react";

import { CommitTimelineItem } from "@/components/course-studio/commit-timeline-item";
import {
  branchSequenceForCommit,
  compactText,
  formatDate,
} from "@/components/course-studio/history-utils";
import type { BoardDecision, CommitRecord, Lesson } from "@/types";

type VersionControlPanelProps = {
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  activeBoardTask: Lesson["board_task_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  onCreateBranch: () => void | Promise<void>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
};

function branchLabel(branchName: string) {
  return branchName === "main" ? "主分支" : branchName;
}

function sortByCreatedAtDesc(left: CommitRecord, right: CommitRecord) {
  const timeDelta = new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
  return timeDelta || right.id.localeCompare(left.id);
}

export function VersionControlPanel({
  activeLesson,
  previewCommit,
  previewCommitId,
  activeRequirements,
  activeBoardTask,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
}: VersionControlPanelProps) {
  const commitsById = new Map(activeLesson.history_graph.commits.map((commit) => [commit.id, commit]));
  const orderedBranches = Object.values(activeLesson.history_graph.branches).sort((left, right) => {
    if (left.name === "main" && right.name !== "main") {
      return -1;
    }
    if (right.name === "main" && left.name !== "main") {
      return 1;
    }
    const timeDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    return timeDelta || left.name.localeCompare(right.name, "zh-CN", { numeric: true });
  });
  const branchBaseLabel = previewCommit ? previewCommit.label : "当前最新节点";
  const currentBranch = activeLesson.history_graph.branches[activeLesson.history_graph.current_branch] ?? null;
  const currentHeadCommitId = currentBranch?.head_commit_id ?? activeLesson.history_graph.commits.at(-1)?.id ?? null;
  const branchLaneByName = new Map(orderedBranches.map((branch, index) => [branch.name, index]));
  const branchHeads = new Map(orderedBranches.map((branch) => [branch.head_commit_id, branch.name]));
  const childCountByCommitId = activeLesson.history_graph.commits.reduce<Map<string, number>>((counts, commit) => {
    commit.parent_ids.forEach((parentId) => {
      counts.set(parentId, (counts.get(parentId) ?? 0) + 1);
    });
    return counts;
  }, new Map());
  const timelineCommits = [...activeLesson.history_graph.commits].sort(sortByCreatedAtDesc);
  const taskTitle =
    activeBoardTask?.requested_action ??
    activeRequirements?.action_type ??
    activeRequirements?.target_depth ??
    "暂无待执行任务";
  const taskBody =
    activeBoardTask?.target_hint ||
    activeRequirements?.action_instruction ||
    activeRequirements?.success_criteria ||
    "执行完成后，当前清单会归档到历史并清空。";

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">版本管理</p>
            <p className="mt-1 text-[11px] text-gray-400">起点：{branchBaseLabel}</p>
          </div>
          <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.14em] text-gray-500">
            {activeLesson.history_graph.commits.length} commits
          </span>
        </div>

        <div className="flex gap-2">
          <input
            value={newBranchName}
            onChange={(event) => onNewBranchNameChange(event.target.value)}
            placeholder="新分支名"
            className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-black"
          />
          <button
            type="button"
            onClick={() => void onCreateBranch()}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[#1a1a1a] px-3 py-2 text-[11px] font-bold uppercase tracking-wider text-white transition hover:bg-black"
          >
            <GitBranch className="h-3.5 w-3.5" />
            开分支
          </button>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">分支顺序</p>
          <span className="text-[10px] font-semibold text-gray-400">按创建时间排列</span>
        </div>
        <div className="space-y-2">
          {orderedBranches.map((branch, index) => {
            const headCommit = commitsById.get(branch.head_commit_id);
            const isCurrent = branch.name === activeLesson.history_graph.current_branch;
            const baseCommit = commitsById.get(branch.base_commit_id);
            return (
              <button
                key={branch.name}
                type="button"
                disabled={isCurrent}
                onClick={() => void onSwitchBranch(branch.name)}
                className={clsx(
                  "grid w-full grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-3 rounded-lg border p-3 text-left transition",
                  isCurrent
                    ? "border-black bg-black text-white"
                    : "border-gray-200 bg-white text-gray-700 hover:border-gray-300 hover:text-black"
                )}
                aria-current={isCurrent ? "true" : undefined}
              >
                <span
                  className={clsx(
                    "flex h-8 w-8 items-center justify-center rounded-md text-xs font-bold",
                    isCurrent ? "bg-white text-black" : "bg-gray-100 text-gray-500"
                  )}
                >
                  {index + 1}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-xs font-bold">{branchLabel(branch.name)}</span>
                  <span className={clsx("mt-1 block truncate text-[11px]", isCurrent ? "text-gray-300" : "text-gray-500")}>
                    {headCommit ? compactText(headCommit.label || headCommit.message, 80) : "分支起点"}
                  </span>
                  <span className={clsx("mt-1 block truncate text-[10px]", isCurrent ? "text-gray-400" : "text-gray-400")}>
                    从 {baseCommit ? compactText(baseCommit.label || baseCommit.message, 48) : "初始节点"} 分出
                  </span>
                </span>
                <span className={clsx("text-[10px] font-bold uppercase tracking-[0.14em]", isCurrent ? "text-white" : "text-gray-400")}>
                  {isCurrent ? "当前" : formatDate(headCommit?.created_at ?? branch.created_at)}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="space-y-4 border-t border-gray-200 pt-6">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
          <span className="text-[10px] font-semibold text-gray-400">版本树 · 最新在上</span>
        </div>
        {timelineCommits.map((commit, index) => {
          const parentCommit = commitsById.get(commit.parent_ids[0] ?? "");
          const branchLane = branchLaneByName.get(commit.branch_name) ?? 0;
          const branchHeadName = branchHeads.get(commit.id) ?? null;
          return (
          <CommitTimelineItem
            key={commit.id}
            commit={commit}
            active={commit.id === previewCommitId}
            latest={index === 0}
            current={commit.id === currentHeadCommitId}
            branchLabel={branchLabel(commit.branch_name)}
            branchLane={branchLane}
            branchOrder={branchLane + 1}
            isBranchHead={Boolean(branchHeadName)}
            isCurrentBranch={commit.branch_name === activeLesson.history_graph.current_branch}
            parentLabel={parentCommit ? parentCommit.label : null}
            childCount={childCountByCommitId.get(commit.id) ?? 0}
            branchSequence={branchSequenceForCommit(activeLesson, commit)}
            currentBranchName={activeLesson.history_graph.current_branch}
            onPreview={() => void onPreviewCommit(commit)}
            onRestore={() => void onRestoreCommit(commit.id)}
            onBranch={() => void onCreateBranchFromCommit(commit)}
            onSwitchBranch={(branchName) => void onSwitchBranch(branchName)}
          />
          );
        })}
      </section>

      <section className="border-t border-gray-200 pt-6">
        <div className="flex items-center gap-2">
          <BrainCircuit className="h-4 w-4 text-gray-400" />
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前任务</p>
        </div>
        <p className="mt-4 text-sm leading-7 text-gray-700">
          {activeBoardTask?.question_or_topic ?? activeRequirements?.learning_goal ?? "等待下一次任务需求。"}
        </p>
        <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
          <p className="text-xs font-semibold text-gray-900">{taskTitle}</p>
          <p className="mt-2 text-[11px] leading-6 text-gray-500">{taskBody}</p>
        </div>
        {latestBoardDecision ? (
          <div className="mt-4 rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">当前讲义决策</p>
            <p className="mt-2 text-xs font-semibold text-gray-900">{latestBoardDecision.action}</p>
            <p className="mt-2 text-[11px] leading-6 text-gray-500">{latestBoardDecision.reason}</p>
          </div>
        ) : null}
      </section>
    </div>
  );
}
