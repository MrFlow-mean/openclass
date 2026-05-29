"use client";

import { CommitTimelineItem } from "@/components/course-studio/commit-timeline-item";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
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
  const { texts: txt } = useInterfaceLanguage();
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <p className="text-[10px] font-bold uppercase tracking-widest text-gray-400">{txt.studio.history.revisionHistory}</p>
        {[...activeLesson.history_graph.commits].reverse().map((commit, index) => (
          <CommitTimelineItem
            key={commit.id}
            commit={commit}
            active={commit.id === previewCommitId}
            latest={index === 0}
            branchSequence={branchSequenceForCommit(activeLesson, commit)}
            currentBranchName={activeLesson.history_graph.current_branch}
            onPreview={() => void onPreviewCommit(commit)}
            onRestore={() => void onRestoreCommit(commit.id)}
            onBranch={() => void onCreateBranchFromCommit(commit)}
            onSwitchBranch={(branchName) => void onSwitchBranch(branchName)}
          />
        ))}
      </div>
    </div>
  );
}
