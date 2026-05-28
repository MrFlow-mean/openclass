import clsx from "clsx";
import { X } from "lucide-react";

import { LessonHistoryGraphPanel } from "@/components/course-studio/lesson-history-graph-panel";
import { ResourcePanel } from "@/components/course-studio/resource-panel";
import type { BoardDecision, CommitRecord, CoursePackage, Lesson } from "@/types";

export type CourseStudioSidebarTab = "graph" | "library";

type CourseStudioSidePanelProps = {
  open: boolean;
  sidebarTab: CourseStudioSidebarTab;
  onSidebarTabChange: (tab: CourseStudioSidebarTab) => void;
  onClose: () => void;
  activeLesson: Lesson;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  onNewBranchNameChange: (value: string) => void;
  busyAction: string | null;
  resources: CoursePackage["resources"];
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onRestoreCommit: (commitId: string) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onUploadResource: (file: File | null) => void | Promise<void>;
  onDeleteResource: (resourceId: string, resourceName: string) => void | Promise<void>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function CourseStudioSidePanel({
  open,
  sidebarTab,
  onSidebarTabChange,
  onClose,
  activeLesson,
  previewCommitId,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  onNewBranchNameChange,
  busyAction,
  resources,
  relatedEdges,
  lessonMap,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onUploadResource,
  onDeleteResource,
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
          { value: "graph", label: "Graph" },
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
        {sidebarTab === "graph" ? (
          <LessonHistoryGraphPanel
            activeLesson={activeLesson}
            previewCommitId={previewCommitId}
            activeRequirements={activeRequirements}
            latestBoardDecision={latestBoardDecision}
            newBranchName={newBranchName}
            onNewBranchNameChange={onNewBranchNameChange}
            onPreviewCommit={onPreviewCommit}
            onRestoreCommit={onRestoreCommit}
            onCreateBranchFromCommit={onCreateBranchFromCommit}
            onSwitchBranch={onSwitchBranch}
          />
        ) : null}

        {sidebarTab === "library" ? (
          <ResourcePanel
            activeLesson={activeLesson}
            busyAction={busyAction}
            resources={resources}
            relatedEdges={relatedEdges}
            lessonMap={lessonMap}
            onUploadResource={onUploadResource}
            onDeleteResource={onDeleteResource}
            onOpenLesson={onOpenLesson}
          />
        ) : null}
      </div>
    </aside>
  );
}
