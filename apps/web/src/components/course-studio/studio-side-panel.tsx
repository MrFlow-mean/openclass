import clsx from "clsx";
import { X } from "lucide-react";
import type { HTMLAttributes } from "react";

import { ResourcePanel } from "@/components/course-studio/resource-panel";
import { VersionControlPanel } from "@/components/course-studio/version-control-panel";
import type { BoardDecision, CommitRecord, CoursePackage, LearningResourceReference, Lesson, LibraryChapter } from "@/types";

export type CourseStudioSidebarTab = "history" | "library";

type CourseStudioSidePanelProps = {
  open: boolean;
  resizeHandleProps: HTMLAttributes<HTMLDivElement>;
  isResizing: boolean;
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
  isAddingResourceUrl: boolean;
  onAddResourceUrl: (url: string) => void | Promise<void>;
  selectedResourceReference?: LearningResourceReference | null;
  onSelectResourceChapter: (
    resource: CoursePackage["resources"][number],
    chapter: LibraryChapter
  ) => void | Promise<void>;
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
  resizeHandleProps,
  isResizing,
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
  isAddingResourceUrl,
  onAddResourceUrl,
  selectedResourceReference,
  onSelectResourceChapter,
  relatedEdges,
  lessonMap,
  onCreateBranch,
  onPreviewCommit,
  onRestoreCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onOpenLesson,
}: CourseStudioSidePanelProps) {
  const isHistoryTab = sidebarTab === "history";

  return (
    <aside
      className={clsx(
        "relative h-full min-h-0 min-w-0 flex-col border-l",
        isHistoryTab ? "border-[#20242d] bg-[#0f1218]" : "border-gray-200 bg-[#fcfcfc]",
        open ? "hidden xl:flex" : "hidden"
      )}
    >
      <div
        {...resizeHandleProps}
        className={clsx(
          "group absolute inset-y-0 left-[-6px] z-30 flex w-3 cursor-col-resize items-center justify-center outline-none",
          isResizing && "bg-gray-100/60"
        )}
      >
        <span
          className={clsx(
            "h-14 w-1 rounded-full bg-gray-200 opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100",
            isResizing && "bg-gray-400 opacity-100"
          )}
        />
      </div>

      <div
        className={clsx(
          "flex h-12 items-center justify-between border-b px-5",
          isHistoryTab ? "border-[#20242d] bg-[#0f1218]" : "border-gray-200 bg-white"
        )}
      >
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">
          课程工作台辅助
        </h4>
        <button
          type="button"
          onClick={onClose}
          className={clsx(
            "rounded-md p-1.5 transition-colors",
            isHistoryTab ? "text-gray-500 hover:bg-white/10 hover:text-white" : "text-gray-400 hover:bg-gray-100 hover:text-black"
          )}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className={clsx("flex border-b", isHistoryTab ? "border-[#20242d] bg-[#0f1218]" : "border-gray-200 bg-white")}>
        {[
          { value: "history", label: "History" },
          { value: "library", label: "Library" },
        ].map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => onSidebarTabChange(tab.value as CourseStudioSidebarTab)}
            className={clsx(
              "flex-1 py-3 text-[10px] font-bold uppercase tracking-wider transition-colors",
              isHistoryTab
                ? sidebarTab === tab.value
                  ? "border-b-2 border-white text-white"
                  : "text-gray-500 hover:text-white"
                : sidebarTab === tab.value
                  ? "border-b-2 border-black text-black"
                  : "text-gray-400 hover:text-black"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className={clsx("min-h-0 flex-1 overflow-y-auto custom-scrollbar", isHistoryTab ? "p-4" : "p-5")}>
        {sidebarTab === "history" ? (
          <VersionControlPanel
            activeLesson={activeLesson}
            previewCommit={previewCommit}
            previewCommitId={previewCommitId}
            activeRequirements={activeRequirements}
            activeBoardTask={activeBoardTask}
            latestBoardDecision={latestBoardDecision}
            newBranchName={newBranchName}
            onNewBranchNameChange={onNewBranchNameChange}
            onCreateBranch={onCreateBranch}
            onPreviewCommit={onPreviewCommit}
            onRestoreCommit={onRestoreCommit}
            onCreateBranchFromCommit={onCreateBranchFromCommit}
            onSwitchBranch={onSwitchBranch}
          />
        ) : null}

        {sidebarTab === "library" ? (
          <ResourcePanel
            activeLesson={activeLesson}
            resources={resources}
            isUploadingResource={isUploadingResource}
            onUploadResource={onUploadResource}
            isAddingResourceUrl={isAddingResourceUrl}
            onAddResourceUrl={onAddResourceUrl}
            selectedResourceReference={selectedResourceReference}
            onSelectResourceChapter={onSelectResourceChapter}
            relatedEdges={relatedEdges}
            lessonMap={lessonMap}
            onOpenLesson={onOpenLesson}
          />
        ) : null}
      </div>
    </aside>
  );
}
