"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import type { HTMLAttributes } from "react";

import { LessonHistoryGraphPanel } from "@/components/course-studio/lesson-history-graph-panel";
import { ResourcePanel } from "@/components/course-studio/resource-panel";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import type {
  BoardDecision,
  CommitRecord,
  CoursePackage,
  Lesson,
  MergeBranchChoice,
  MergeBranchChoices,
  MergeBranchPreviewResponse,
  MergeBranchSectionKey,
} from "@/types";

export type CourseStudioSidebarTab = "graph" | "library";

type CourseStudioSidePanelProps = {
  resizeHandleProps: HTMLAttributes<HTMLDivElement>;
  isResizing: boolean;
  open: boolean;
  sidebarTab: CourseStudioSidebarTab;
  onSidebarTabChange: (tab: CourseStudioSidebarTab) => void;
  onClose: () => void;
  activeLesson: Lesson;
  previewCommitId: string | null;
  activeRequirements: Lesson["learning_requirements"];
  latestBoardDecision: BoardDecision | null;
  newBranchName: string;
  mergePreview: MergeBranchPreviewResponse | null;
  mergeChoices: MergeBranchChoices;
  onNewBranchNameChange: (value: string) => void;
  busyAction: string | null;
  resources: CoursePackage["resources"];
  relatedEdges: CoursePackage["course_graph"];
  lessonMap: Map<string, Lesson>;
  onPreviewCommit: (commit: CommitRecord) => void | Promise<void>;
  onCreateBranchFromCommit: (commit: CommitRecord) => void | Promise<void>;
  onSwitchBranch: (branchName: string) => void | Promise<void>;
  onOpenMergePreview: (branchName: string) => void | Promise<void>;
  onMergeChoiceChange: (section: MergeBranchSectionKey, choice: MergeBranchChoice) => void;
  onCancelMerge: () => void;
  onConfirmMerge: () => void | Promise<void>;
  onDeleteResource: (resourceId: string, resourceName: string) => void | Promise<void>;
  onOpenLesson: (lessonId: string) => void | Promise<void>;
};

export function CourseStudioSidePanel({
  resizeHandleProps,
  isResizing,
  open,
  sidebarTab,
  onSidebarTabChange,
  onClose,
  activeLesson,
  previewCommitId,
  activeRequirements,
  latestBoardDecision,
  newBranchName,
  mergePreview,
  mergeChoices,
  onNewBranchNameChange,
  busyAction,
  resources,
  relatedEdges,
  lessonMap,
  onPreviewCommit,
  onCreateBranchFromCommit,
  onSwitchBranch,
  onOpenMergePreview,
  onMergeChoiceChange,
  onCancelMerge,
  onConfirmMerge,
  onDeleteResource,
  onOpenLesson,
}: CourseStudioSidePanelProps) {
  const { texts: txt } = useInterfaceLanguage();
  const s = txt.studio.sidePanel;
  return (
    <aside
      className={clsx(
        "relative h-full min-h-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
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
      <div className="flex h-12 items-center justify-between border-b border-gray-200 bg-white px-5">
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">{s.title}</h4>
        <button
          type="button"
          onClick={onClose}
          title={s.close}
          aria-label={s.close}
          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex border-b border-gray-200 bg-white">
        {[
          { value: "graph", label: s.graph },
          { value: "library", label: s.library },
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
            mergePreview={mergePreview}
            mergeChoices={mergeChoices}
            busyAction={busyAction}
            onNewBranchNameChange={onNewBranchNameChange}
            onPreviewCommit={onPreviewCommit}
            onCreateBranchFromCommit={onCreateBranchFromCommit}
            onSwitchBranch={onSwitchBranch}
            onOpenMergePreview={onOpenMergePreview}
            onMergeChoiceChange={onMergeChoiceChange}
            onCancelMerge={onCancelMerge}
            onConfirmMerge={onConfirmMerge}
          />
        ) : null}

        {sidebarTab === "library" ? (
          <ResourcePanel
            activeLesson={activeLesson}
            busyAction={busyAction}
            resources={resources}
            relatedEdges={relatedEdges}
            lessonMap={lessonMap}
            onDeleteResource={onDeleteResource}
            onOpenLesson={onOpenLesson}
          />
        ) : null}
      </div>
    </aside>
  );
}
