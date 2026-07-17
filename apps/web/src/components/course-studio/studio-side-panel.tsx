import clsx from "clsx";
import { X } from "lucide-react";
import type { HTMLAttributes } from "react";

import { SourceImportPanel } from "@/components/course-studio/source-import-panel";
import { GeometryGenerationPanel } from "@/components/course-studio/geometry-generation-panel";
import { VersionControlPanel } from "@/components/course-studio/version-control-panel";
import { VoiceControlPanel } from "@/components/course-studio/voice-control-panel";
import { useGeometryWorkspace } from "@/hooks/course-studio/use-geometry-workspace";
import type { SpeechOptionsResponse } from "@/lib/speech-api";
import type { AIModelSelection, BoardDecision, CommitRecord, Lesson, SelectionRef } from "@/types";

export type CourseStudioSidebarTab = "geometry" | "history" | "sources" | "voice";

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
  geometryReference: SelectionRef | null;
  onGeometryReferenceClear: () => void;
  textModel: AIModelSelection | null;
  speechAutoEnabled: boolean;
  speechIsLoading: boolean;
  speechIsPlaying: boolean;
  speechIsPaused: boolean;
  speechStatusText: string;
  speechOptions: SpeechOptionsResponse;
  speechSelectedVoice: string;
  speechRate: number;
  speechCurrentModel: string;
  speechCurrentText: string;
  speechCurrentTime: number;
  speechDuration: number;
  speechCanSeek: boolean;
  speechCanReplay: boolean;
  onSpeechAutoToggle: () => void;
  onSpeechCancel: () => void;
  onSpeechPause: () => void;
  onSpeechResume: () => void;
  onSpeechReplay: () => void;
  onSpeechSeek: (time: number) => void;
  onSpeechVoiceChange: (voice: string) => void;
  onSpeechRateChange: (rate: number) => void;
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
  geometryReference,
  onGeometryReferenceClear,
  textModel,
  speechAutoEnabled,
  speechIsLoading,
  speechIsPlaying,
  speechIsPaused,
  speechStatusText,
  speechOptions,
  speechSelectedVoice,
  speechRate,
  speechCurrentModel,
  speechCurrentText,
  speechCurrentTime,
  speechDuration,
  speechCanSeek,
  speechCanReplay,
  onSpeechAutoToggle,
  onSpeechCancel,
  onSpeechPause,
  onSpeechResume,
  onSpeechReplay,
  onSpeechSeek,
  onSpeechVoiceChange,
  onSpeechRateChange,
}: CourseStudioSidePanelProps) {
  const geometryWorkspace = useGeometryWorkspace({
    lessonId: activeLesson.id,
    incomingSelection: geometryReference,
    textModel,
    onClearSelection: onGeometryReferenceClear,
  });

  return (
    <aside
      className={clsx(
        "h-full min-h-0 min-w-0 flex-col border-l border-gray-200 bg-[#fcfcfc]",
        open
          ? "fixed inset-y-0 right-0 z-50 flex w-[min(92vw,420px)] shadow-2xl xl:relative xl:inset-auto xl:z-auto xl:w-auto xl:shadow-none"
          : "hidden"
      )}
    >
      <div
        {...resizeHandleProps}
        className={clsx(
          "group absolute inset-y-0 left-[-6px] z-30 hidden w-3 cursor-col-resize items-center justify-center outline-none xl:flex",
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

      <div className="flex border-b border-gray-200 bg-white">
        {[
          { value: "sources", label: "Sources" },
          { value: "history", label: "History" },
          { value: "voice", label: "Voice" },
          { value: "geometry", label: "Geometry" },
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
        {sidebarTab === "geometry" ? (
          <GeometryGenerationPanel
            selection={geometryWorkspace.selection}
            instructions={geometryWorkspace.instructions}
            scene={geometryWorkspace.scene}
            error={geometryWorkspace.error}
            isGenerating={geometryWorkspace.isGenerating}
            onInstructionsChange={geometryWorkspace.setInstructions}
            onGenerate={geometryWorkspace.generate}
            onClear={geometryWorkspace.clear}
          />
        ) : sidebarTab === "sources" ? (
          <SourceImportPanel key={packageId} packageId={packageId} onError={onError} onSourceReference={onSourceReference} />
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
        ) : sidebarTab === "voice" ? (
          <VoiceControlPanel
            autoEnabled={speechAutoEnabled}
            isLoading={speechIsLoading}
            isPlaying={speechIsPlaying}
            isPaused={speechIsPaused}
            statusText={speechStatusText}
            model={speechCurrentModel}
            currentText={speechCurrentText}
            currentTime={speechCurrentTime}
            duration={speechDuration}
            canSeek={speechCanSeek}
            canReplay={speechCanReplay}
            options={speechOptions}
            selectedVoice={speechSelectedVoice}
            speechRate={speechRate}
            onAutoToggle={onSpeechAutoToggle}
            onCancel={onSpeechCancel}
            onPause={onSpeechPause}
            onResume={onSpeechResume}
            onReplay={onSpeechReplay}
            onSeek={onSpeechSeek}
            onVoiceChange={onSpeechVoiceChange}
            onSpeechRateChange={onSpeechRateChange}
          />
        ) : null}

      </div>
    </aside>
  );
}
