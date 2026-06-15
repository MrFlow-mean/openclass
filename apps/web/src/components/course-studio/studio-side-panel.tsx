import clsx from "clsx";
import { X } from "lucide-react";

import { BranchPanel } from "@/components/course-studio/branch-panel";
import { ResourcePanel } from "@/components/course-studio/resource-panel";
import { VersionHistoryPanel } from "@/components/course-studio/version-history-panel";
import type { BoardDecision, CommitRecord, CoursePackage, Lesson } from "@/types";

export type CourseStudioSidebarTab = "history" | "branch" | "library";

type CourseStudioSidePanelProps = {
  open: boolean;
  sidebarTab: CourseStudioSidebarTab;
  onSidebarTabChange: (tab: CourseStudioSidebarTab) => void;
  onClose: () => void;
  activeLesson: Lesson;
  previewCommit: CommitRecord | null;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  activeBoardTask: Lesson["board_task_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  resources: CoursePackage["resources"];
  isUploadingResource: boolean;
  onUploadResource: (file: File) => void | Promise<void>;
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onCreateBranch: () => void | Promise<void>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function CourseStudioSidePanel({
  open,
  sidebarTab,
  onSidebarTabChange,
  onClose,
  activeLesson,
  previewCommit,
  previewCommitId,
  activeRequirements,
  activeBoardTask,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  resources,
  isUploadingResource,
  onUploadResource,
  relatedEdges,
  lessonMap,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onOpenLesson,
}: CourseStudioSidePanelProps) {
  return (
    <aside
      className={clsx(
        "h-full min-h-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
        open ? "hidden xl:flex" : "hidden"
      )}
    >
      <div className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-5">
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">课程工作台辅助</h4>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex border-b border-gray-200 bg-white">
        {[
          { value: "history", label: "History" },
          { value: "branch", label: "Branch" },
          { value: "library", label: "Library" },
        ].map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => onSidebarTabChange(tab.value as CourseStudioSidebarTab)}
            className={clsx(
              "flex-1 py-3 text-[10px] font-bold uppercase tracking-wider transition-colors",
              sidebarTab === tab.value
                ? "border-b-2 border-black text-black"
                : "text-gray-400 hover:text-black"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-5 custom-scrollbar">
        {sidebarTab === "history" ? (
          <VersionHistoryPanel
            activeLesson={activeLesson}
            previewCommitId={previewCommitId}
            onPreviewCommit={onPreviewCommit}
            onRestoreCommit={onRestoreCommit}
            onCreateBranchFromCommit={onCreateBranchFromCommit}
            onSwitchBranch={onSwitchBranch}
          />
        ) : null}

        {sidebarTab === "branch" ? (
          <BranchPanel
            activeLesson={activeLesson}
            previewCommit={previewCommit}
            activeRequirements={activeRequirements}
            activeBoardTask={activeBoardTask}
            latestBoardDecision={latestBoardDecision}
            newBranchName={newBranchName}
            onNewBranchNameChange={onNewBranchNameChange}
            onCreateBranch={onCreateBranch}
            onSwitchBranch={onSwitchBranch}
          />
        ) : null}

        {sidebarTab === "library" ? (
          <ResourcePanel
            activeLesson={activeLesson}
            resources={resources}
            isUploadingResource={isUploadingResource}
            onUploadResource={onUploadResource}
            relatedEdges={relatedEdges}
            lessonMap={lessonMap}
            onOpenLesson={onOpenLesson}
          />
        ) : null}
      </div>
    </aside>
  );
}
