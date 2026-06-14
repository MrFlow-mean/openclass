"use client";

import { useState } from "react";

import { CommitTimelineItem } from "@/components/course-studio/commit-timeline-item";
import { branchSequenceForCommit } from "@/components/course-studio/history-utils";
import type { CommitRecord, Lesson } from "@/types";

type VersionHistoryPanelProps = {
  activeLesson: Lesson;
  previewCommitId: string | null;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
};

export function VersionHistoryPanel({
  activeLesson,
  previewCommitId,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
}: VersionHistoryPanelProps) {
  const [detailCommitId, setDetailCommitId] = useState<string | null>(null);
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
  const branchLaneByName = new Map(orderedBranches.map((branch, index) => [branch.name, index]));
  const branchHeads = new Map(orderedBranches.map((branch) => [branch.head_commit_id, branch.name]));
  const currentBranch = activeLesson.history_graph.branches[activeLesson.history_graph.current_branch] ?? null;
  const currentHeadCommitId = currentBranch?.head_commit_id ?? activeLesson.history_graph.commits.at(-1)?.id ?? null;
  const childCountByCommitId = activeLesson.history_graph.commits.reduce<Map<string, number>>((counts, commit) => {
    commit.parent_ids.forEach((parentId) => {
      counts.set(parentId, (counts.get(parentId) ?? 0) + 1);
    });
    return counts;
  }, new Map());
  const timelineCommits = [...activeLesson.history_graph.commits].sort((left, right) => {
    const timeDelta = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    return timeDelta || left.id.localeCompare(right.id);
  });
  const latestCommitId =
    [...activeLesson.history_graph.commits].sort((left, right) => {
      const timeDelta = new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
      return timeDelta || right.id.localeCompare(left.id);
    })[0]?.id ?? null;

  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">修订记录</p>
          <span className="text-[10px] font-semibold text-gray-400">版本树 · 从起点往后</span>
        </div>
        <div className="space-y-0">
          {timelineCommits.map((commit, index) => {
            const parentCommit = commitsById.get(commit.parent_ids[0] ?? "");
            const branchLane = branchLaneByName.get(commit.branch_name) ?? 0;
            return (
              <CommitTimelineItem
                key={commit.id}
                commit={commit}
                active={commit.id === previewCommitId}
                latest={commit.id === latestCommitId}
                current={commit.id === currentHeadCommitId}
                first={index === 0}
                last={index === timelineCommits.length - 1}
                branchLane={branchLane}
                isBranchHead={branchHeads.has(commit.id)}
                isCurrentBranch={commit.branch_name === activeLesson.history_graph.current_branch}
                parentLabel={parentCommit ? parentCommit.label : null}
                childCount={childCountByCommitId.get(commit.id) ?? 0}
                detailOpen={detailCommitId === commit.id}
                branchSequence={branchSequenceForCommit(activeLesson, commit)}
                currentBranchName={activeLesson.history_graph.current_branch}
                onPreview={() => void onPreviewCommit(commit)}
                onRestore={() => void onRestoreCommit(commit.id)}
                onBranch={() => void onCreateBranchFromCommit(commit)}
                onSwitchBranch={(branchName) => void onSwitchBranch(branchName)}
                onOpenDetail={() => setDetailCommitId(commit.id)}
                onCloseDetail={() => setDetailCommitId(null)}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}
