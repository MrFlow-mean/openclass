import clsx from "clsx";
import { Square, Volume2, VolumeX, X } from "lucide-react";
import type { HTMLAttributes } from "react";

import { SourceImportPanel } from "@/components/course-studio/source-import-panel";
import { VersionControlPanel } from "@/components/course-studio/version-control-panel";
import type { BoardDecision, CommitRecord, Lesson, SelectionRef } from "@/types";

export type CourseStudioSidebarTab = "history" | "sources";

type CourseStudioSidePanelProps = {
  open: boolean;
  resizeHandleProps: HTMLAttributes<HTMLDivElement>;
  isResizing: boolean;
  sidebarTab: CourseStudioSidebarTab;
  onSidebarTabChange: (tab: CourseStudioSidebarTab) => void;
  onClose: () => void;
  activeLesson: Lesson;
  packageId: string;
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
  onError: (message: string) => void;
  onSourceReference?: (selection: SelectionRef) => void;
  speechAutoEnabled: boolean;
  speechIsActive: boolean;
  speechStatusText: string;
  onSpeechAutoToggle: () => void;
  onSpeechStop: () => void;
};

export function CourseStudioSidePanel({
  open,
  resizeHandleProps,
  isResizing,
  sidebarTab,
  onSidebarTabChange,
  onClose,
  activeLesson,
  packageId,
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
  onError,
  onSourceReference,
  speechAutoEnabled,
  speechIsActive,
  speechStatusText,
  onSpeechAutoToggle,
  onSpeechStop,
}: CourseStudioSidePanelProps) {
  return (
    <aside
      className={clsx(
        "relative h-full min-h-0 min-w-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
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
        <h4 className="text-[10px] font-bold uppercase tracking-widest text-gray-500">
          课程工作台辅助
        </h4>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-black"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="border-b border-gray-200 bg-white px-5 py-3">
        <div className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {speechAutoEnabled ? (
                <Volume2 className="h-4 w-4 shrink-0 text-gray-800" />
              ) : (
                <VolumeX className="h-4 w-4 shrink-0 text-gray-400" />
              )}
              <p className="text-xs font-semibold text-gray-800">AI 回复自动播报</p>
            </div>
            <p className="mt-1 truncate text-[10px] leading-4 text-gray-500" title={speechStatusText}>
              {speechStatusText}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {speechIsActive ? (
              <button
                type="button"
                onClick={onSpeechStop}
                className="inline-flex h-7 items-center gap-1 rounded-lg bg-white px-2 text-[11px] font-medium text-gray-700 shadow-sm transition hover:bg-gray-100"
                aria-label="停止播报"
              >
                <Square className="h-3 w-3 fill-current" />
                停止
              </button>
            ) : null}
            <button
              type="button"
              role="switch"
              aria-checked={speechAutoEnabled}
              aria-label="AI 回复自动播报"
              onClick={onSpeechAutoToggle}
              className={clsx(
                "relative h-6 w-11 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400 focus-visible:ring-offset-2",
                speechAutoEnabled ? "bg-black" : "bg-gray-300"
              )}
            >
              <span
                className={clsx(
                  "absolute top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
                  speechAutoEnabled ? "translate-x-5" : "translate-x-1"
                )}
              />
            </button>
          </div>
        </div>
      </div>

      <div className="flex border-b border-gray-200 bg-white">
        {[
          { value: "sources", label: "Sources" },
          { value: "history", label: "History" },
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
        {sidebarTab === "sources" ? (
          <SourceImportPanel packageId={packageId} onError={onError} onSourceReference={onSourceReference} />
        ) : sidebarTab === "history" ? (
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

      </div>
    </aside>
  );
}
